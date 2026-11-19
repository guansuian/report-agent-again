"""生境分析：四种分组共用一次查询，原始 rows 用于分析和表格，多系列用于图表。"""

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
from core.tools.common.params import EnvironmentFilter
from core.tools.common.validate import get_region_code

logger = logging.getLogger(__name__)
_Codes = Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
_TenantIds = Annotated[list[Annotated[int, Field(gt=0, strict=True)]], Field(min_length=1)]
_METRICS = {"monitor_count": "监测数量", "avg_monitor_count": "设备平均监测量"}
_GROUP_NAMES = {"habitat": "生境汇总", "habitat_species": "生境蚊种构成",
                "time_habitat": "生境趋势", "time_habitat_species": "生境蚊种趋势"}


def _read_rows(response: dict, group_by: str) -> dict:
    """只检查绘图和分析所需的结构，保留接口数值、顺序及空值。"""
    data = response.get("data")
    if not isinstance(data, dict) or data.get("contract_version") != "agent-data-v1":
        raise ToolDataError("生境分析接口缺少 data 或返回了不一致的协议版本。")
    logger.debug("生境分析接口回显 applied_query=%r", data.get("applied_query"))
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ToolDataError("生境分析接口的 rows 必须是列表。")
    # 文档的生境返回示例仅约定 rows，不强制要求区域接口的总量字段。
    if data.get("total") is not None and (type(data["total"]) is not int or data["total"] < 0):
        raise ToolDataError("生境分析 total 提供时必须是非负整数。")
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("每条生境分组记录必须是对象。")
        text_fields = ["habitat_code", "habitat_name"]
        if "species" in group_by:
            text_fields.extend(["mosquito_code", "mosquito_name"])
        for field in text_fields:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ToolDataError(f"生境分组 {field} 必须是非空文本。")
        for field in ("monitor_count", "valid_device_count"):
            if type(row.get(field)) is not int or row[field] < 0:
                raise ToolDataError(f"生境分组 {field} 必须是非负整数。")
        for field in ("avg_monitor_count", "ratio"):
            if field not in row:
                raise ToolDataError(f"生境分组缺少 {field}。")
            value = row[field]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ToolDataError(f"生境分组 {field} 必须是非负有限数值或 null。")
        if row["ratio"] is not None and row["ratio"] > 100:
            raise ToolDataError("生境分组 ratio 必须在 0 至 100 之间或为 null。")
        if group_by.startswith("time_"):
            try:
                start = datetime.fromisoformat(row["bucket_start"])
                end = datetime.fromisoformat(row["bucket_end"])
                if start.utcoffset() is None or end.utcoffset() is None or start >= end:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ToolDataError("生境趋势需提供带时区且开始早于结束的 bucket_start、bucket_end。") from None
    return data


def _build_chart(result: dict, region_names: dict[str, str]) -> dict | None:
    """按分组组织轴和系列，不把平铺 rows 改成另一套接口返回结构。"""
    rows = result["rows"]
    if not rows:
        return None
    query = result["query"]
    group_by = query["group_by"]
    trend = group_by.startswith("time_")
    species = "species" in group_by
    axes = {}
    groups = {}
    for row in rows:
        # 时间轴的身份包含区间起止，避免把不同时间段误合并。
        if trend:
            axis = (row["bucket_start"], row["bucket_end"])
            width = 16 if query["time_unit"] == "hour" else 10
            label = row["bucket_start"][:width].replace("T", " ")
            key = (row["habitat_code"], row["mosquito_code"]) if species else row["habitat_code"]
            name = f"{row['habitat_name']} · {row['mosquito_name']}" if species else row["habitat_name"]
        else:
            axis = row["habitat_code"]
            label = row["habitat_name"]
            key = row["mosquito_code"] if species else "metric"
            name = row["mosquito_name"] if species else result["metric"]
        axes[axis] = label
        if key not in groups:
            groups[key] = {"name": name, "values": {}}
        if axis in groups[key]["values"]:
            raise ToolDataError("接口返回重复分组，不能覆盖或自行合并数量、平均量。")
        groups[key]["values"][axis] = row[result["metric_key"]]

    axis_keys = list(axes)
    if trend:
        axis_keys.sort(key=lambda item: datetime.fromisoformat(item[0]))
    series = []
    for group in groups.values():
        # 缺失组合使用 null，不把没有返回的数据当成真实的 0。
        values = [group["values"].get(axis) for axis in axis_keys]
        series.append({"name": group["name"], "type": result["chart_type"],
                       "data": values, "connectNulls": False})
    dimension = {"label": "时间" if trend else "生境", "unit": "段" if trend else "类", "count": len(axis_keys)}
    region = "/".join(region_names.values()) or "当前账号可见范围"
    title = f"{region} {_GROUP_NAMES[group_by]} · {result['metric']}"
    if not trend:
        title += f"（第{query['page']}页）"
    option = {
        "title": {"text": title}, "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "name": dimension["label"], "data": [axes[axis] for axis in axis_keys]},
        "yAxis": {"type": "value", "name": result["metric"], "min": 0}, "series": series,
    }
    if len(series) > 1:
        option["legend"] = {"type": "scroll", "top": 0, "data": [item["name"] for item in series]}

    columns = [{"key": "habitat_name", "label": "生境"}]
    if trend:
        columns.extend([{"key": "bucket_start", "label": "时间段开始"}, {"key": "bucket_end", "label": "时间段结束（不含）"}])
    if species:
        columns.append({"key": "mosquito_name", "label": "蚊种"})
    columns.extend([
        {"key": "monitor_count", "label": "监测数量"}, {"key": "valid_device_count", "label": "有效设备数"},
        {"key": "avg_monitor_count", "label": "设备平均监测量"}, {"key": "ratio", "label": "组内占比（%）"},
    ])
    conditions = [
        {"label": "开始时间", "value": query["start_time"]}, {"label": "结束时间（不含）", "value": query["end_time"]},
        {"label": "地区范围", "value": region}, {"label": "分组方式", "value": _GROUP_NAMES[group_by]},
        {"label": "展示指标", "value": result["metric"]},
    ]
    if trend:
        conditions.append({"label": "时间粒度", "value": query["time_unit"]})
    else:
        order = "降序" if query["sort_order"] == "desc" else "升序"
        ranking = f"前{query['top_n']}项" if query["top_n"] else "不限制排名项数"
        conditions.extend([
            {"label": "排序", "value": f"{result['metric']}{order}"}, {"label": "排名范围", "value": ranking},
            {"label": "分页", "value": f"第{query['page']}页，每页{query['page_size']}条"},
            {"label": "包含无记录生境", "value": "是" if query["include_zero"] else "否"},
        ])
    labels = {"region_codes": "地区编码", "habitat_codes": "生境编码", "mosquito_codes": "蚊种编码",
              "sexes": "性别", "dev_ids": "设备 ID", "tenant_ids": "租户 ID", "environment_filters": "环境范围"}
    for key, values in query["filters"].items():
        if key == "sexes":
            value = "、".join({0: "未知", 1: "雄性", 2: "雌性"}[item] for item in values)
        elif key == "environment_filters":
            value = json.dumps(values, ensure_ascii=False)
        else:
            value = "、".join(str(item) for item in values)
        conditions.append({"label": labels[key], "value": value})
    return {
        "artifact_type": "chart", "display_name": title,
        "data": {
            "report_type": "habitat_analysis", "report_label": "生境分析", "dimension": dimension,
            "chart_types": ["line", "bar"] if trend else ["bar"], "query": query,
            "rows": rows, "columns": columns, "conditions": conditions, "option": option,
            "summary_metric": {"label": "返回分组监测数量合计" if trend else "本页监测数量合计",
                               "value": result["returned_monitor_count"]},
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
        },
    }


@report_tool(response_format="content_and_artifact")
def habitat_analysis(
    start_time: StartDate,
    end_time: EndDate,
    group_by: Literal["habitat", "habitat_species", "time_habitat", "time_habitat_species"] = "habitat",
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
    metric: Literal["monitor_count", "avg_monitor_count"] = "monitor_count",
    time_unit: Literal["hour", "day", "week", "month"] | None = None,
    sort_order: Literal["asc", "desc"] | None = None,
    include_zero: bool | None = None,
    top_n: Annotated[int, Field(ge=0, strict=True)] | None = None,
    page: Page | None = None,
    page_size: PageSize | None = None,
    chart_type: Literal["bar", "line"] | None = None,
) -> tuple[str, dict | None]:
    """查询生境汇总、蚊种构成或趋势，返回分析数据和一个多系列报表。

    无时间分组生成柱状图，可按总量或平均量排名；趋势默认折线图，也可选柱状图。
    地区全称精确校验后取最深节点编码。生境、蚊种编码分别取自 dictionaries、
    mosquito_dictionaries，不传生境筛选时查询全部可见生境，不要求用户先选择生境。
    不自动拉取全部页；不在本地重算占比、平均量、有效设备数，也不把缺失组合补零。
    蚊种构成占比属于该生境内部，生境占比属于同一时段；两者不能混用。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间，包含当天；缺少时间先追问。
        end_time: 结束日期 YYYY-MM-DD，包含当天，内部转为次日零点且不包含该时刻。
        group_by: habitat生境汇总、habitat_species生境蚊种构成、time_habitat生境趋势、time_habitat_species生境蚊种趋势。
        province: 可选省级全称，如广东省。
        city: 可选城市全称，如深圳市，不能写深圳；可补全上级。
        district: 可选区县全称，重名时补充上级地区。
        street: 可选街道全称，重名时补充上级地区；地区只定位一条路径。
        habitat_codes: 可选非空生境代码列表，从 dictionaries 取得；不传表示不限制生境。
        mosquito_codes: 可选非空具体蚊种代码列表，从 mosquito_dictionaries 的 species 节点取得。
        sexes: 可选性别列表，0未知、1雄、2雌；不传表示全部。
        dev_ids: 可选已知设备内部 ID 列表，与其他条件取交集。
        tenant_ids: 可选已知租户 ID 列表，仍受当前用户权限限制。
        environment_filters: 可选环境范围列表，指定指标、上下限及是否包含边界。
        metric: 展示指标，monitor_count监测数量、avg_monitor_count设备平均量；非趋势同时作为后端排序指标。
        time_unit: 趋势必填 hour/day/week/month；非趋势不传。
        sort_order: 仅非趋势可用，desc降序、asc升序，默认 desc；趋势按时间升序。
        include_zero: 仅非趋势可用，是否包含无记录生境，默认 false。
        top_n: 仅非趋势可用，前 N 个分组组合，默认 0 不限制；趋势不传。
        page: 仅非趋势可用，页码从 1 开始，默认 1；翻页保留全部筛选条件。
        page_size: 仅非趋势可用，每页默认 20，最多 200；当前页不是完整范围。
        chart_type: 趋势默认 line，也可 bar；非趋势只支持 bar。
    """
    # 1. 明确分组与参数的搭配，不静默忽略趋势请求中的排名或分页条件。
    trend = group_by.startswith("time_")
    if start_time > end_time:
        raise ToolInputError("开始日期不能晚于结束日期。")
    if trend and time_unit is None:
        raise ToolInputError("趋势查询必须提供 time_unit：hour、day、week 或 month。")
    if not trend and time_unit is not None:
        raise ToolInputError("非趋势分组不接受 time_unit，请改用 time_habitat 或 time_habitat_species。")
    if trend and any(value is not None for value in (sort_order, include_zero, top_n, page, page_size)):
        raise ToolInputError("趋势查询不使用排序指标、Top N、分页或 include_zero，请移除这些参数。")
    selected_chart = chart_type if chart_type is not None else ("line" if trend else "bar")
    if not trend and selected_chart != "bar":
        raise ToolInputError("非趋势生境分析使用柱状图，请设置 chart_type=bar。")
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

    # 2. 地区沿用现有校验，结束日转换与区域分析保持相同口径。
    region_names = {}
    if any(value is not None for value in (province, city, district, street)):
        tree = get_region_tree()
        region_names = check_regions(tree, province=province, city=city, district=district, street=street)
        filters["region_codes"] = [get_region_code(tree, region_names)]
    beijing = timezone(timedelta(hours=8))
    start = datetime.combine(start_time, time.min, tzinfo=beijing)
    end = datetime.combine(end_time + timedelta(days=1), time.min, tzinfo=beijing)
    query = {"contract_version": "agent-data-v1", "start_time": start.isoformat(), "end_time": end.isoformat(),
             "filters": filters, "group_by": group_by}
    if trend:
        query["time_unit"] = time_unit
    else:
        query.update(sort_by=metric, sort_order=sort_order if sort_order is not None else "desc",
                     include_zero=include_zero if include_zero is not None else False,
                     top_n=top_n if top_n is not None else 0,
                     page=page if page is not None else 1, page_size=page_size if page_size is not None else 20)

    # 3. 一次查询一次处理；applied_query 只作排查信息，不逐字段比较。
    response = get_company_client().post("/api/v1/statistics/habitat-analysis", json=query)
    data = _read_rows(response, group_by)
    rows = data["rows"]
    total = data.get("total")
    ratio_scope = "同一时段内各生境的占比" if trend else "查询范围内各生境的占比"
    if "species" in group_by:
        ratio_scope = "同一时段、该生境内部的蚊种构成占比" if trend else "该生境内部的蚊种构成占比"
    result = {
        "message": "生境分析查询完成。" if rows else "当前查询未返回分组，请核对条件和页码，不能据此断言实际数量为零。",
        "report_type": "habitat_analysis", "query": query, "applied_query": data.get("applied_query"),
        "group_by": group_by, "metric_key": metric, "metric": _METRICS[metric],
        "has_data": bool(rows), "rows": rows, "returned_count": len(rows),
        "returned_monitor_count": sum(row["monitor_count"] for row in rows) if rows else None,
        "total": total, "page": query.get("page"), "page_size": query.get("page_size"),
        "has_more": query["page"] * query["page_size"] < total if not trend and total is not None else None,
        "chart_type": selected_chart,
        "ratio_scope": ratio_scope + "；分母受全部筛选约束，在 Top N 和分页前计算，100 表示 100%。",
        "scope": "统计受账号权限、时间及全部筛选条件限制；监测识别数量不代表设备实际捕获量。",
        "note": "保留接口原始统计。缺失组合不补零，平均量为 null 时不当作 0。有效设备数不能跨蚊种或时间直接相加，平均量不能相加。"
                "展示合计仅属于返回分组，不代表完整范围总量；接口未提供总行数时，不推断是否还有下一页。",
    }
    artifact = _build_chart(result, region_names)
    result["chart_generated"] = artifact is not None
    return json.dumps(result, ensure_ascii=False, allow_nan=False), artifact
