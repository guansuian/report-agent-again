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

    返回 (start + ' 00:00:00', end + ' 23:59:59')，对应老项目 species_trend_report
    里 f"{start_time.isoformat()} 00:00:00" 的写法。
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


def check_regions(
    *,
    province: str | None = None,
    city: str | None = None,
    district: str | None = None,
    street: str | None = None,
) -> dict[str, str]:
    """校验平铺地区参数：strip 后非空才保留，返回 {接口键: 全称}。

    传了但只含空白算非法（模型常见错误：把「未指定」写成一个空格）。
    返回值只包含用户真的填了的字段，未填的省略，工具据此决定要不要给接口传。
    """
    regions = {"province": province, "city": city, "district": district, "street": street}
    result: dict[str, str] = {}
    for key, value in regions.items():
        if value is None:
            continue
        if not value.strip():
            raise ToolInputError(f"地区参数 {key} 不能为空，未指定的条件请直接省略。")
        result[key] = value.strip()
    return result


def check_device_ids(dev_ids: list[int] | None) -> list[int] | None:
    """校验设备 ID 列表：不能为空、不能有重复；None 原样返回。"""
    if dev_ids is None:
        return None
    if not dev_ids:
        raise ToolInputError("设备 ID 列表不能为空，未指定的条件请直接省略。")
    if len(set(dev_ids)) != len(dev_ids):
        raise ToolInputError("设备 ID 列表不能包含重复项。")
    return dev_ids
