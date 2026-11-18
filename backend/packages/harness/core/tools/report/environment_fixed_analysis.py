"""读取后台固定环境区间，返回监测数量、占比、最多区间和图表产物。"""

from __future__ import annotations

import json
import math
from typing import Literal

from core.clients import get_company_client
from core.tools.common import (
    SPECIES_BY_CODE,
    SPECIES_CODES,
    EndDate,
    RegionName,
    SpeciesName,
    StartDate,
    ToolDataError,
    check_regions,
    day_range,
    get_region_tree,
    report_tool,
)

_SENSOR_NAMES = {"temp": "温度", "hum": "湿度", "wind": "风速", "co2": "CO₂", "light": "光照"}


def _normalize_environment_rows(payload: dict, sensor_type: str, monitor_type: str | None) -> list[dict]:
    """检查指标、单一蚊种分组和区间；保留后台边界、名称与顺序。"""
    if not isinstance(payload, dict) or payload.get("sensor_type") != sensor_type:
        raise ToolDataError("环境接口返回的 sensor_type 缺失或与查询指标不一致。")
    groups = payload.get("type_groups")
    if not isinstance(groups, list):
        raise ToolDataError("环境接口的 type_groups 必须是列表。")
    if not groups:
        return []
    if len(groups) != 1 or not isinstance(groups[0], dict):
        raise ToolDataError("本工具一次读取一个蚊种分组，不能合并接口返回的多个分组。")
    expected_type = monitor_type if monitor_type is not None else "all"
    if groups[0].get("monitor_type") != expected_type:
        raise ToolDataError("环境接口返回的蚊种分组与筛选条件不一致。")
    rows = groups[0].get("data_points")
    if not isinstance(rows, list):
        raise ToolDataError("环境分组的 data_points 必须是列表。")

    data = []
    seen_ranges = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("环境区间记录必须是对象。")
        label = row.get("label")
        lower = row.get("range_min")
        upper = row.get("range_max")
        count = row.get("capture_count")
        if not isinstance(label, str) or not label.strip():
            raise ToolDataError("环境区间 label 必须是非空文本。")
        for bound in (lower, upper):
            if type(bound) not in (int, float) or not math.isfinite(bound):
                raise ToolDataError("环境区间上下限必须是有限数值。")
        if lower > upper:
            raise ToolDataError("环境区间下限不能大于上限。")
        if (lower, upper) in seen_ranges:
            raise ToolDataError("接口返回重复环境区间，不能重复统计。")
        if type(count) is not int or count < 0:
            raise ToolDataError("环境区间 capture_count 必须是非负整数。")
        seen_ranges.add((lower, upper))
        data.append({"label": label, "range_min": lower, "range_max": upper, "count": count})
    return data


def _build_environment_content(data: list[dict], params: dict) -> dict:
    """按返回区间合计计算占比，保留所有并列最多区间。"""
    total = sum(row["count"] for row in data) if data else None
    for row in data:
        row["ratio"] = round(row["count"] / total * 100, 2) if total > 0 else None

    maximum = max((row["count"] for row in data), default=0)
    peak = None
    if maximum > 0:
        peak = {
            "count": maximum,
            "ranges": [
                {"label": row["label"], "range_min": row["range_min"], "range_max": row["range_max"]}
                for row in data if row["count"] == maximum
            ],
        }
    result = {
        "message": "环境区间分布查询完成。" if data else "未查到匹配的环境区间，不能据此断言实际数量为零。",
        "query": params,
        "sensor_type": params["sensor_type"],
        "sensor_name": _SENSOR_NAMES[params["sensor_type"]],
        "metric": "监测数量",
        "has_data": bool(data),
        "data": data,
        "returned_count": len(data),
        "total_count": total,
        "peak": peak,
        "ratio_scope": "分母是本次返回的全部环境区间数量之和；占比保留两位小数，不代表全平台或全部蚊虫中的占比。",
        "scope": "查询时间、地区、蚊种及账号权限范围内，接口返回环境区间中的监测统计，不代表设备实际捕获量。",
        "note": "区间名称、边界及顺序保留后台原值；单位和小数值归属规则待确认，不自行换算或调整。区间标签不代表因果关系，本工具不计算相关性。",
    }
    if data and total == 0:
        result["message"] = "接口返回的各区间数量均为 0，占比无法计算，没有正数最多区间；不能据此断言实际没有蚊虫。"
    return result


def _build_environment_chart(result: dict, chart_type: str) -> dict | None:
    """复用统一产物格式；无数据或全零饼图不生成产物。"""
    if not result["has_data"]:
        return None
    if chart_type == "pie" and result["total_count"] == 0:
        return None

    rows = result["data"]
    query = result["query"]
    sensor_name = result["sensor_name"]
    region = "/".join(query[field] for field in ("province", "city", "district", "street") if field in query)
    region = region or "当前账号可见范围"
    species_name = SPECIES_BY_CODE[query["monitor_type"]] if "monitor_type" in query else "全部类别"
    start = query["start_time"].split(" ")[0]
    end = query["end_time"].split(" ")[0]
    title = f"{start}至{end} {region} {species_name}{sensor_name}区间分布"
    labels = [f"{row['label']}（{row['range_min']}–{row['range_max']}）" for row in rows]
    option = {"title": {"text": title}}
    if chart_type == "pie":
        option["tooltip"] = {"trigger": "item"}
        option["legend"] = {"type": "scroll", "bottom": 0}
        option["series"] = [{
            "name": "监测数量", "type": "pie", "radius": "55%",
            "data": [{"name": label, "value": row["count"]} for label, row in zip(labels, rows)],
        }]
    else:
        option["tooltip"] = {"trigger": "axis"}
        option["xAxis"] = {"type": "category", "name": f"{sensor_name}区间", "data": labels}
        option["yAxis"] = {"type": "value", "name": "监测数量", "min": 0, "minInterval": 1}
        option["series"] = [{"name": "监测数量", "type": "bar", "data": [row["count"] for row in rows]}]

    # 本工具没有性别筛选，展示其实际支持的条件。
    conditions = [
        {"label": "环境指标", "value": sensor_name},
        {"label": "蚊种", "value": species_name},
        {"label": "开始时间", "value": query["start_time"]},
        {"label": "结束时间", "value": query["end_time"]},
        {"label": "地区范围", "value": region},
    ]
    return {
        "artifact_type": "chart",
        "display_name": title,
        "data": {
            "report_label": "环境区间分布",
            "dimension": {"label": f"{sensor_name}区间", "unit": "组"},
            "chart_types": ["bar", "pie"] if result["total_count"] > 0 else ["bar"],
            "columns": [
                {"key": "label", "label": "后台区间名称"},
                {"key": "range_min", "label": "区间下限（接口原值）"},
                {"key": "range_max", "label": "区间上限（接口原值）"},
                {"key": "count", "label": "监测数量"},
                {"key": "ratio", "label": "占比（%）"},
            ],
            "rows": rows, "option": option, "query": dict(query), "conditions": conditions,
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
        },
    }


@report_tool(response_format="content_and_artifact")
def environment_fixed_analysis(
    start_time: StartDate,
    end_time: EndDate,
    sensor_type: Literal["temp", "hum", "wind", "co2", "light"],
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    species: SpeciesName | None = None,
    chart_type: Literal["bar", "pie"] = "bar",
) -> tuple[str, dict | None]:
    """查询固定环境区间对应的蚊虫监测数量、占比和并列最多区间。

    每次一种环境指标、一个蚊种或全部类别，默认生成柱状图。
    区间由后台划分，不支持自定义区间、温湿度联合分组、相关性或因果分析。
    不提供性别、设备 ID、生境筛选，用户要求这些条件时不能忽略后直接查询。
    比较不同蚊种需分别调用，先核对区间一致，不支持多蚊种同图。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间当天零点，含当天；缺少时间时先追问。
        end_time: 结束日期 YYYY-MM-DD，北京时间当天 23:59:59，含当天。
        sensor_type: 单一环境指标，temp温度、hum湿度、wind风速、co2二氧化碳、light光照。
        province: 可选省级地区全称，如广东省。
        city: 可选城市全称，如深圳市；通过地区树校验并补全上级。
        district: 可选区县全称；同名时需结合上级地区确定，区县筛选效果尚待实测。
        street: 可选街道全称；同名时需结合上级地区确定，街道筛选效果尚待实测。
        species: 可选单一蚊种中文名，省略查询全部类别。
        chart_type: 图表类型，bar默认柱状图、pie饼图；全零时不生成饼图。
    """
    # 1. 校验日期和地区，构造公司接口参数；图表类型只在本工具内使用。
    start, end = day_range(start_time, end_time)
    monitor_type = SPECIES_CODES[species] if species is not None else None
    region_params = {}
    if any(value is not None for value in (province, city, district, street)):
        region_tree = get_region_tree()
        region_params = check_regions(
            region_tree, province=province, city=city, district=district, street=street,
        )
    params = {
        "start_time": start, "end_time": end, "sensor_type": sensor_type,
        **region_params, "monitor_type": monitor_type,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # 2. 读取并校验接口分组，保留后台区间，不重新划分或排序。
    response = get_company_client().get("/api/v1/statistics/environment-fixed-analysis", params=params)
    try:
        payload = response["data"]
    except (KeyError, TypeError):
        raise ToolDataError("环境区间接口缺少 data。") from None
    data = _normalize_environment_rows(payload, sensor_type, monitor_type)

    # 3. 模型结果与图表使用相同数量；只有正数总量才能计算占比和绘制饼图。
    result = _build_environment_content(data, params)
    artifact = _build_environment_chart(result, chart_type)
    if chart_type == "pie" and data and result["total_count"] == 0:
        result["message"] += "未生成饼图，可改用柱状图展示。"
    result["chart_type"] = chart_type
    result["chart_generated"] = artifact is not None
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return content, artifact
