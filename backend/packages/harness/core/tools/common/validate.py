"""语义校验：类型管不了的约束，集中在这里。

pydantic 的 Field / Literal 只能管「结构」约束（类型、范围、枚举），
管不了「开始日期不晚于结束日期」「设备 ID 不重复」「性别字符串转接口编码」
这类跨字段 / 需要映射的语义。这些集中写在这里，错误文案统一成：
    「参数名 + 哪里错了 + 怎么改」，方便模型读懂后重试。
"""

from __future__ import annotations

from datetime import date

from core.tools.common.errors import ToolInputError

# 性别字符串 → 公司接口编码（老项目约定：0 未知 / 1 雄性 / 2 雌性）。
_SEX_CODES = {"unknown": 0, "male": 1, "female": 2}


def day_range(start: date, end: date) -> tuple[str, str]:
    """校验起止日期，返回北京时间自然日的接口字符串区间（首尾都包含）。

    返回 (start + ' 00:00:00', end + ' 23:59:59')。
    """
    if start > end:
        raise ToolInputError(f"开始日期 {start} 不能晚于结束日期 {end}。")
    return f"{start.isoformat()} 00:00:00", f"{end.isoformat()} 23:59:59"


def validate_sex(sex: str | None) -> int | None:
    """把可读的性别字符串映射成公司接口编码；None（全部性别）原样返回。"""
    if sex is None:
        return None
    try:
        return _SEX_CODES[sex]
    except KeyError:
        raise ToolInputError(
            f"性别参数 {sex!r} 不合法，只能是 male / female / unknown。"
        ) from None


# 地区层级的可读名称，用于报错文案。
_LEVEL_NAMES = {"province": "省份", "city": "城市", "district": "区县", "street": "街道"}
_MUNICIPALITIES = {"北京市", "上海市", "天津市", "重庆市"}


def check_regions(
    region_tree: list[dict],
    *,
    province: str | None = None,
    city: str | None = None,
    district: str | None = None,
    street: str | None = None,
) -> dict[str, str]:
    """按名字在地区树里定位，返回接口直接可用的地区参数：{层级: 地区全称}。

    region_tree 是 /api/v1/agent/regions 返回的 data.list。
    四个参数可以任意组合；没给的上级由树自动补全。
    所有条件一起参与匹配，最后才判断是否有歧义。

    常见输入的实际结果（基于当前树，按从省到最深的顺序写出返回的地区名称）：
        西湖区 + 桃源街道         -> 江西省 / 南昌市 / 西湖区 / 桃源街道  # 用街道消歧
        鼓楼区 + 湖南路街道       -> 江苏省 / 南京市 / 鼓楼区 / 湖南路街道
        广东省 + 南山区           -> 广东省 / 深圳市 / 南山区   # 补出深圳市
        北京市 + 北京市 + 朝阳区   -> 北京市 / 朝阳区            # 直辖市的重复 city 被忽略
        广东省 + 广东省           -> 报错：找不到城市「广东省」
        衡阳市                   -> 湖南省 / 衡阳市             # 补出湖南省
        广东省 + 福田街道          -> 广东省 / 深圳市 / 福田区 / 福田街道  # 跳两级也能查
        西湖区                   -> 报错：匹配到 2 个（杭州、南昌）
        城北街道                 -> 报错：匹配到 5 个
    """

    # 第一步：把树拍平，每个节点带上从根到它父级的节点列表（不只是名字，后面要校验 level）。
    def flatten(nodes: list[dict], ancestors: tuple[dict, ...] = ()):
        for node in nodes:
            yield node, ancestors
            child_ancestors = ancestors + (node,)
            yield from flatten(node.get("children") or [], child_ancestors)

    all_nodes = list(flatten(region_tree))

    # 第二步：整理用户给的条件，去掉 None，校验空白。
    ordered = [
        ("province", province),
        ("city", city),
        ("district", district),
        ("street", street),
    ]
    conditions: list[tuple[str, str]] = []
    for level, raw_name in ordered:
        if raw_name is None:
            continue
        name = raw_name.strip()          # 树里有 " 洪山区 " 这种脏名字
        if not name:
            raise ToolInputError(f"地区参数 {level} 不能为空，未指定的条件请直接省略。")
        conditions.append((level, name))
    if not conditions:
        raise ToolInputError("请至少指定一个地区条件。")

    # 第三步：只有明确的直辖市，且树中没有 city 层时，才忽略重复的 city。
    # 普通省份即使没有子节点，也不能忽略用户填写的错误城市条件。
    province_name = next((n for l, n in conditions if l == "province"), None)
    city_name = next((n for l, n in conditions if l == "city"), None)
    if province_name in _MUNICIPALITIES and city_name == province_name:
        province_node = next(
            (n for n, _ in all_nodes
             if n["level"] == "province" and n["name"].strip() == province_name),
            None,
        )
        has_city_level = any(
            child["level"] == "city"
            for child in (province_node or {}).get("children") or []
        )
        if province_node is not None and not has_city_level:
            conditions = [(level, name) for level, name in conditions if level != "city"]

    # 第四步：以最深的那条条件为准，取出全部同名节点。
    deepest_level, deepest_name = conditions[-1]
    candidates = [
        (node, ancestors)
        for node, ancestors in all_nodes
        if node["level"] == deepest_level and node["name"].strip() == deepest_name
    ]
    if not candidates:
        raise ToolInputError(
            f"当前账号可见范围内没有{_LEVEL_NAMES[deepest_level]}「{deepest_name}」。"
            f"请确认名称完整（如「武汉市」而不是「武汉」）。"
        )

    # 第五步：其余条件必须出现在候选节点的祖先链上，一次性筛掉不一致的组合。
    upper_conditions = conditions[:-1]

    def satisfies(node: dict, ancestors: tuple[dict, ...]) -> bool:
        chain = list(ancestors) + [node]
        for level, name in upper_conditions:
            found = any(c["level"] == level and c["name"].strip() == name for c in chain)
            if not found:
                return False
        return True

    matches = [
        (node, ancestors)
        for node, ancestors in candidates
        if satisfies(node, ancestors)
    ]

    if not matches:
        given = "、".join(f"{_LEVEL_NAMES[level]}={name}" for level, name in conditions)
        raise ToolInputError(f"没有同时满足这些条件的地区：{given}。请检查上下级关系。")

    if len(matches) > 1:
        options = "；".join(
            "/".join(c["name"].strip() for c in ancestors + (node,))
            for node, ancestors in matches
        )
        raise ToolInputError(f"地区匹配到 {len(matches)} 个：{options}。请补充上级地区。")

    # 第六步：只剩一条路径，按从省到最深的顺序写成接口参数。
    node, ancestors = matches[0]
    resolved: dict[str, str] = {}
    for item in ancestors + (node,):
        resolved[item["level"]] = item["name"].strip()
    return resolved


def check_device_ids(dev_ids: list[int] | None) -> list[int] | None:
    """校验设备 ID 列表：不能为空、不能有重复；None 原样返回。"""
    if dev_ids is None:
        return None
    if not dev_ids:
        raise ToolInputError("设备 ID 列表不能为空，未指定的条件请直接省略。")
    if len(set(dev_ids)) != len(dev_ids):
        raise ToolInputError("设备 ID 列表不能包含重复项。")
    return dev_ids
