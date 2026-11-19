"""区域分析：按新协议查询，返回分析内容和现有格式的柱状图产物。"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import Field

from core.clients import get_company_client
from core.tools.common import (
    DeviceIds, EndDate, Page, PageSize, RegionName, StartDate,
    ToolDataError, ToolInputError, check_regions, get_region_tree, report_tool,
)
from core.tools.common.validate import get_region_code
from core.tools.common.params import EnvironmentFilter

logger = logging.getLogger(__name__)

_LEVEL_NAMES = {"province": "省级区域", "city": "城市", "district": "区县", "street": "街道"}
_METRICS = {"monitor_count": "监测数量", "avg_monitor_count": "设备平均监测量"}
_Codes = Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
_TenantIds = Annotated[list[Annotated[int, Field(gt=0, strict=True)]], Field(min_length=1)]


def _validate_response(response: dict, query: dict) -> dict:
    """检查返回结构、分页、数值和排序；条件回显只用于日志排查。"""
    data = response.get("data")
    if not isinstance(data, dict) or data.get("contract_version") != "agent-data-v1":
        raise ToolDataError("区域分析接口缺少 data 或返回了不一致的协议版本。")
    logger.debug("区域分析接口回显 applied_query=%r", data.get("applied_query"))
    for key in ("total", "scope_monitor_count"):
        if type(data.get(key)) is not int or data[key] < 0:
            raise ToolDataError(f"区域分析 {key} 必须是非负整数。")
    rows = data.get("regions")
    if not isinstance(rows, list) or len(rows) > min(query["page_size"], data["total"]):
        raise ToolDataError("regions 必须是列表，条数不能超过本页大小或总行数。")
    if query["top_n"] > 0 and data["total"] > query["top_n"]:
        raise ToolDataError("Top N 后的总行数 total 超过了请求的 top_n。")

    seen_codes = set()
    previous = None
    null_seen = False
    scope_count = data["scope_monitor_count"]
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("区域分析的每条记录必须是对象。")
        for key in ("region_code", "region_name", "data_status"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ToolDataError(f"区域记录 {key} 必须是非空文本。")
        if row["region_code"] in seen_codes:
            raise ToolDataError("区域分析返回了重复的地区编码。")
        seen_codes.add(row["region_code"])
        for key in ("monitor_count", "valid_device_count", "rank"):
            minimum = 1 if key == "rank" else 0
            if type(row.get(key)) is not int or row[key] < minimum:
                raise ToolDataError(f"区域记录 {key} 必须是大于等于 {minimum} 的整数。")
        for key in ("avg_monitor_count", "ratio"):
            if key not in row:
                raise ToolDataError(f"区域记录缺少 {key}。")
            value = row[key]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ToolDataError(f"区域记录 {key} 必须是非负有限数值或 null。")
        devices = row["valid_device_count"]
        average = row["avg_monitor_count"]
        if devices == 0:
            if average is not None or row["monitor_count"] != 0:
                raise ToolDataError("有效设备数为 0 时，监测合计应为 0，平均量必须为 null。")
        elif average is None or not math.isclose(average, row["monitor_count"] / devices, abs_tol=0.01):
            raise ToolDataError("平均量与监测数量、有效设备数不一致。")
        ratio = row["ratio"]
        if scope_count == 0:
            if ratio is not None:
                raise ToolDataError("完整范围监测数量为 0 时，占比必须为 null。")
        elif ratio is None or ratio > 100 or not math.isclose(ratio, row["monitor_count"] / scope_count * 100, abs_tol=0.01):
            raise ToolDataError("区域占比与完整范围总量不一致，不能按本页或榜单重算。")
        if row["data_status"] == "no_records" and devices != 0:
            raise ToolDataError("无记录地区不应包含有效设备。")

        value = row[query["sort_by"]]
        if value is None:
            null_seen = True
        else:
            if null_seen:
                raise ToolDataError("无法计算的平均量必须排在最后。")
            if previous is not None:
                out_of_order = value > previous if query["sort_order"] == "desc" else value < previous
                if out_of_order:
                    raise ToolDataError("区域分析返回顺序与请求的排序条件不一致。")
            previous = value
    if sum(row["monitor_count"] for row in rows) > scope_count:
        raise ToolDataError("本页监测数量合计超过完整范围总量。")
    return data


def _build_chart(result: dict, region_names: dict[str, str]) -> dict | None:
    """图表展示所选指标；合计单独使用监测数量，不能相加各地区平均量。"""
    rows = result["regions"]
    if not rows:
        return None
    query = result["query"]
    level_name = _LEVEL_NAMES[query["region_level"]]
    region = "/".join(region_names.values()) or "当前账号可见范围"
    order = "降序" if query["sort_order"] == "desc" else "升序"
    ranking = f"前{query['top_n']}名" if query["top_n"] else "全部匹配地区"
    title = f"{region} {level_name}{result['metric']}{order} {ranking}（第{query['page']}页）"
    conditions = [
        {"label": "开始时间", "value": query["start_time"]},
        {"label": "结束时间（不含）", "value": query["end_time"]},
        {"label": "地区范围", "value": region},
        {"label": "统计层级", "value": level_name},
        {"label": "排序", "value": f"{result['metric']}{order}"},
        {"label": "排名范围", "value": ranking},
        {"label": "分页", "value": f"第{query['page']}页，每页{query['page_size']}条"},
        {"label": "包含无记录地区", "value": "是" if query["include_zero"] else "否"},
    ]
    labels = {"region_codes": "地区编码", "habitat_codes": "生境编码", "mosquito_codes": "蚊种编码",
              "sexes": "性别", "dev_ids": "设备 ID", "tenant_ids": "租户 ID", "environment_filters": "环境范围"}
    for key, values in query["filters"].items():
        if key == "sexes":
            value = "、".join({0: "未知", 1: "雄性", 2: "雌性"}[sex] for sex in values)
        elif key == "environment_filters":
            value = json.dumps(values, ensure_ascii=False)
        else:
            value = "、".join(str(item) for item in values)
        conditions.append({"label": labels[key], "value": value})
    option = {
        "title": {"text": title}, "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "name": level_name,
                  "data": [f"{row['rank']}. {row['region_name']}" for row in rows]},
        "yAxis": {"type": "value", "name": result["metric"], "min": 0},
        "series": [{"name": result["metric"], "type": "bar",
                    "data": [row[query["sort_by"]] for row in rows]}],
    }
    columns = [
        {"key": "rank", "label": "名次"}, {"key": "region_name", "label": level_name},
        {"key": "monitor_count", "label": "监测数量"},
        {"key": "valid_device_count", "label": "有效设备数"},
        {"key": "avg_monitor_count", "label": "设备平均监测量"},
        {"key": "ratio", "label": "占完整范围比例（%）"},
        {"key": "data_status", "label": "数据状态（no_records 为无记录）"},
    ]
    return {
        "artifact_type": "chart", "display_name": title,
        "data": {
            "report_type": "region_analysis", "report_label": "区域分析",
            "dimension": {"label": level_name, "unit": "个"}, "chart_types": ["bar"],
            "rows": rows, "columns": columns, "option": option, "query": query,
            "conditions": conditions,
            "summary_metric": {"label": "本页监测数量合计", "value": result["page_monitor_count"]},
            "scope_monitor_count": result["scope_monitor_count"],
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
        },
    }


@report_tool(response_format="content_and_artifact")
def region_analysis(
    start_time: StartDate,
    end_time: EndDate,
    region_level: Literal["province", "city", "district", "street"],
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    habitat_codes: _Codes | None = None,
    mosquito_codes: _Codes | None = None,
    sexes: Annotated[list[Literal[0, 1, 2]], Field(min_length=1)] | None = None,
    dev_ids: DeviceIds | None = None,
    tenant_ids: _TenantIds | None = None,
    environment_filters: Annotated[list[EnvironmentFilter], Field(min_length=1)] | None = None,
    sort_by: Literal["monitor_count", "avg_monitor_count"] = "monitor_count",
    sort_order: Literal["asc", "desc"] = "desc",
    include_zero: bool = False,
    top_n: Annotated[int, Field(ge=0, strict=True)] = 10,
    page: Page = 1,
    page_size: PageSize = 20,
) -> tuple[str, dict | None]:
    """查询指定层级地区的监测总量、设备平均量、占比及排名，生成柱状图。

    地区名称精确匹配并补全上级，内部只发送最深地区的编码。region_level 决定分组层级，
    例如 province=广东省、region_level=city 表示广东各市；不再自动下钻到设备。
    生境与蚊种代码分别从 dictionaries、mosquito_dictionaries 取得，不能填中文名、
    旧蚊种拉丁名或编造代码。多选字段内取并集，不同筛选取交集；省略表示不限制。
    最少排名必须使用 asc，不倒排已截取的最多榜单。只查询当前页，不自动拉取全部页。
    ratio 是 Top N 和分页前完整范围的占比；无法计算的平均量为 null，不当作 0。
    地区输入只定位一条路径；多个不同地区的条件不能合并成一条或擅自省略。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间，包含当天；缺少时间先追问。
        end_time: 结束日期 YYYY-MM-DD，包含当天，内部转为次日零点且不包含该时刻。
        region_level: 分组层级，province省、city市、district区县、street街道，不支持设备层级。
        province: 可选省级全称，如广东省。
        city: 可选城市全称，如深圳市，不能写深圳；可补全上级。
        district: 可选区县全称，重名时需补充上级地区。
        street: 可选街道全称，重名时需补充上级地区。
        habitat_codes: 可选非空生境代码列表，取自 dictionaries 的 code。
        mosquito_codes: 可选非空具体蚊种代码列表，取自 mosquito_dictionaries 的 species 节点。
        sexes: 可选性别列表，0未知、1雄、2雌；省略表示全部。
        dev_ids: 可选已知设备内部 ID 列表，与地区条件取交集。
        tenant_ids: 可选已知租户 ID 列表，仍受用户权限限制，不编造。
        environment_filters: 可选环境范围列表，指定指标、上下限及边界，null 表示无界。
        sort_by: 排序指标，monitor_count监测总量、avg_monitor_count设备平均量。
        sort_order: desc从高到低、asc从低到高，默认 desc。
        include_zero: 是否包含无记录地区，默认 false；无记录不等于实际没有蚊虫。
        top_n: 前 N 项，默认 10；0 表示不限制排名数量，仍按 page 和 page_size 分页。
        page: 页码，从 1 开始，默认 1；翻页保留原查询条件。
        page_size: 每页数量，默认 20，最多 200，不能将本页称为全部结果。
    """
    # 1. 校验输入；不通过时不请求统计接口，也不删除条件重试。
    if start_time > end_time:
        raise ToolInputError("开始日期不能晚于结束日期。")
    filters = {}
    for key, values in (("habitat_codes", habitat_codes), ("mosquito_codes", mosquito_codes),
                        ("sexes", sexes), ("dev_ids", dev_ids), ("tenant_ids", tenant_ids)):
        if values is None:
            continue
        if len(set(values)) != len(values):
            raise ToolInputError(f"{key} 不能包含重复项。")
        if any(isinstance(value, str) and not value.strip() for value in values):
            raise ToolInputError(f"{key} 不能包含空白编码。")
        filters[key] = values
    if environment_filters is not None:
        filters["environment_filters"] = [item.model_dump() for item in environment_filters]

    # 2. 地区保留现有精确匹配方式；名称给展示使用，最深节点编码给新接口。
    region_names = {}
    if any(value is not None for value in (province, city, district, street)):
        tree = get_region_tree()
        region_names = check_regions(tree, province=province, city=city, district=district, street=street)
        filters["region_codes"] = [get_region_code(tree, region_names)]

    # 3. 自然日输入转成带时区的半开区间，不复用旧的 23:59:59 规则。
    beijing = timezone(timedelta(hours=8))
    start = datetime.combine(start_time, time.min, tzinfo=beijing)
    end = datetime.combine(end_time + timedelta(days=1), time.min, tzinfo=beijing)
    query = {
        "contract_version": "agent-data-v1", "start_time": start.isoformat(), "end_time": end.isoformat(),
        "filters": filters, "region_level": region_level, "sort_by": sort_by, "sort_order": sort_order,
        "include_zero": include_zero, "top_n": top_n, "page": page, "page_size": page_size,
    }
    response = get_company_client().post("/api/v1/statistics/region-analysis", json=query)
    data = _validate_response(response, query)

    # 4. 分开保存本页合计和完整范围总量，模型与图表使用相同的原始记录。
    rows = data["regions"]
    message = "区域分析查询完成。"
    if not rows:
        message = "当前页没有记录，请核对页码。" if data["total"] else "未查到匹配地区，不能据此断言实际数量为零。"
    elif all(row["data_status"] == "no_records" for row in rows):
        message = "当前页返回的地区均无有效记录，不能据此断言实际没有蚊虫。"
    result = {
        "message": message, "report_type": "region_analysis", "query": query,
        "applied_query": data.get("applied_query"),
        "metric": _METRICS[sort_by], "ranking_level": region_level, "has_data": bool(rows),
        "total": data["total"], "scope_monitor_count": data["scope_monitor_count"],
        "page_monitor_count": sum(row["monitor_count"] for row in rows) if rows else None,
        "page": page, "page_size": page_size, "returned_count": len(rows),
        "has_more": page * page_size < data["total"], "regions": rows,
        "ratio_scope": "ratio 以 Top N 和分页前的完整查询范围监测数量为分母，100 表示 100%。",
        "scope": "统计受用户权限、时间和全部筛选条件限制；监测识别数量不代表设备实际捕获量。",
        "note": "只展示当前页，保留接口名次和排序。有效设备按 ID 去重；平均量为监测总数除以有效设备数。"
                "无记录与真实零值不同，null 不替换为 0；各地区平均量不能相加，本页监测合计不等于完整范围总量。",
    }
    artifact = _build_chart(result, region_names)
    result["chart_type"] = "bar"
    result["chart_generated"] = artifact is not None
    return json.dumps(result, ensure_ascii=False, allow_nan=False), artifact
