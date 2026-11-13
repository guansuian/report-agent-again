"""按时间分组查询蚊虫监测数量，返回模型分析数据和 ECharts 图表产物。

读取 /api/v1/statistics/species-compose 的 data.trend_points。
整段时间的蚊种构成由 species_compose 工具单独负责。
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Literal

from core.clients import get_company_client
from core.tools.common import (
    SPECIES_BY_CODE,
    SPECIES_CODES,
    DeviceIds,
    EndDate,
    RegionName,
    Sex,
    SpeciesName,
    StartDate,
    TimeUnit,
    ToolDataError,
    check_device_ids,
    check_regions,
    day_range,
    get_region_tree,
    report_tool,
    validate_sex,
)


def _point_order(time_unit: str, point: str, start: date, end: date):
    """检查时间标签，返回用于排序和判断重复的键。"""
    if time_unit == "day":
        parsed = date.fromisoformat(point)
        if point != parsed.isoformat() or not (start <= parsed <= end):
            raise ValueError(point)
        return parsed.toordinal()
    if time_unit == "hour":
        if not re.fullmatch(r"[0-9]{1,2}", point) or not (0 <= int(point) <= 23):
            raise ValueError(point)
        return int(point)
    if time_unit == "week":
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", point):
            raise ValueError(point)
        week_start = date.fromisocalendar(int(point[:4]), int(point[-2:]), 1)
        week_end = week_start + timedelta(days=6)
        if not (week_start <= end and week_end >= start):
            raise ValueError(point)
        return week_start.toordinal()
    if time_unit == "month":
        parsed = date.fromisoformat(point + "-01")
        if point != parsed.strftime("%Y-%m") or not (
            start.strftime("%Y-%m") <= point <= end.strftime("%Y-%m")
        ):
            raise ValueError(point)
        return point
    if not re.fullmatch(r"[0-9]{4}", point) or not (start.year <= int(point) <= end.year):
        raise ValueError(point)
    return point


def _normalize_species(rows: list, monitor_type: str | None) -> list[dict]:
    """检查一个时间分组的蚊种明细，补充中文名称，不改动原始数量。"""
    detail = []
    seen_codes = set()
    for item in rows:
        if not isinstance(item, dict):
            raise ToolDataError("蚊种记录必须是对象。")
        code = item.get("monitor_type")
        count = item.get("count")
        if not isinstance(code, str) or not code.strip() or code in seen_codes:
            raise ToolDataError("蚊种名称为空或重复。")
        if type(count) is not int or count < 0:
            raise ToolDataError("蚊种数量必须为非负整数。")
        if monitor_type is not None and code != monitor_type:
            raise ToolDataError("接口返回的蚊种与筛选条件不一致。")
        seen_codes.add(code)
        detail.append({
            "monitor_type": code,
            "species_name": SPECIES_BY_CODE.get(code, code),
            "count": count,
        })
    return detail


def _normalize_trend_rows(
    rows: list,
    time_unit: str,
    start: date,
    end: date,
    monitor_type: str | None,
) -> list[dict]:
    """检查时间分组和数量合计，按时间排序，保留周的实际覆盖范围。"""
    data = []
    seen_points = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("时间分组记录必须是对象。")
        point = row.get("time_point")
        total = row.get("total_count")
        species_rows = row.get("species")
        # 沿用现有工具口径：小时只统计 0 至 23，排除接口的 24 组。
        if time_unit == "hour" and point == "24":
            continue
        if not isinstance(point, str) or not point.strip():
            raise ToolDataError("时间分组标签必须为非空文本。")
        try:
            order = _point_order(time_unit, point, start, end)
        except ValueError:
            raise ToolDataError("接口时间标签与请求粒度或日期范围不符。") from None
        if order in seen_points:
            raise ToolDataError("接口返回重复时间分组，不能重复统计。")
        seen_points.add(order)
        if type(total) is not int or total < 0 or not isinstance(species_rows, list):
            raise ToolDataError("时间分组需包含非负整数 total_count 及 species 列表。")

        detail = _normalize_species(species_rows, monitor_type)
        if sum(item["count"] for item in detail) != total:
            raise ToolDataError("时间分组总量与蚊种数量合计不一致。")

        entry = {"time_point": point, "total_count": total, "species": detail, "_order": order}
        if time_unit == "week":
            week_start = date.fromisocalendar(int(point[:4]), int(point[-2:]), 1)
            week_end = week_start + timedelta(days=6)
            entry.update({
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "query_start": max(start, week_start).isoformat(),
                "query_end": min(end, week_end).isoformat(),
                "partial_week": start > week_start or end < week_end,
            })
        data.append(entry)

    data.sort(key=lambda row: row["_order"])
    for row in data:
        del row["_order"]
    return data


def _build_trend_content(data: list[dict], params: dict) -> dict:
    """计算返回分组的总量和峰值，组织给模型看的结果及统计口径。"""
    time_unit = params["time_unit"]
    note = "仅展示查询范围内接口返回的分组；缺失分组不补零，首尾周期可能不完整，今天的数据可能尚未完整。"
    if time_unit == "hour":
        note += "小时标签为查询范围内同一小时的合计，不是跨日连续时间趋势；仅统计 0 至 23 小时。"
    elif time_unit == "week":
        note += "周编号采用 ISO 周（周一至周日）；week_start/week_end 为完整周，query_start/query_end 为本次覆盖日期，partial_week 表示不足整周。"

    peak = max((row["total_count"] for row in data), default=None)
    peak_info = None
    if peak is not None:
        peak_info = {
            "count": peak,
            "time_points": [row["time_point"] for row in data if row["total_count"] == peak],
        }
    result = {
        "message": "蚊虫时间分组查询完成。" if data else "未查到匹配的时间分组，不能据此断言实际数量为零。",
        "query": params,
        "time_unit": time_unit,
        "metric": "监测数量",
        "has_data": bool(data),
        "data": data,
        "returned_count": len(data),
        "total_count": sum(row["total_count"] for row in data) if data else None,
        "peak": peak_info,
        "note": note,
        "scope": "返回时间分组中符合筛选条件的监测统计，受账号权限限制；不代表实际捕获量。",
    }
    if data and peak == 0:
        result["message"] = "接口返回的各时间分组均为 0；没有正数峰值，不据此断言实际没有蚊虫。"

    has_region = any(field in params for field in ("province", "city", "district", "street"))
    if "dev_ids" in params:
        result["scope"] += "指定设备合计统计。"
        if has_region:
            result["filter_note"] = "设备与地区取交集，只统计指定设备中位于所选地区的数据。"
            if not data or peak == 0:
                result["filter_note"] += "请核对设备归属、时间、其他筛选条件和权限，不能仅凭空数据或全零确定原因。"
    if has_region:
        result["scope"] += "地区已按当前账号的地区树校验，并补全了上级地区。"
    return result


def _build_trend_chart(result: dict, chart_type: str) -> dict | None:
    """使用与模型结果相同的数据构造 ECharts 配置；无数据时不生成图表。"""
    if not result["has_data"]:
        return None

    rows = result["data"]
    time_unit = result["time_unit"]
    labels = []
    for row in rows:
        label = row["time_point"]
        if time_unit == "week":
            label += f"（{row['query_start']}至{row['query_end']}）"
        labels.append(label)

    unit_labels = {"hour": "同小时合计分布", "day": "按日", "week": "按 ISO 周", "month": "按月", "year": "按年"}
    query = result["query"]
    start = query["start_time"].split(" ")[0]
    end = query["end_time"].split(" ")[0]
    display_name = f"{start}至{end} 蚊虫监测数量（{unit_labels[time_unit]}）"
    option = {
        "title": {"text": display_name},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "name": "小时" if time_unit == "hour" else "时间分组",
            "data": labels,
        },
        "yAxis": {"type": "value", "name": "监测数量", "min": 0, "minInterval": 1},
        "series": [{
            "name": "监测数量",
            "type": chart_type,
            "data": [row["total_count"] for row in rows],
        }],
    }
    chart_data = {
        "option": option,
        "query": dict(query),
        "scope": result["scope"],
        "note": result["note"],
    }
    if "filter_note" in result:
        chart_data["filter_note"] = result["filter_note"]
    return {
        "artifact_type": "chart",
        "display_name": display_name,
        "data": chart_data,
    }


@report_tool(response_format="content_and_artifact")
def species_trend(
    start_time: StartDate,
    end_time: EndDate,
    time_unit: TimeUnit = "day",
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    species: SpeciesName | None = None,
    sex: Sex | None = None,
    dev_ids: DeviceIds | None = None,
    chart_type: Literal["line", "bar"] = "line",
) -> tuple[str, dict | None]:
    """按时间分组查询蚊虫监测数量，返回分析数据及折线图或柱状图。

    适合每天、每月等数量变化及峰值查询；不提供地区排名或同比环比。
    小时是跨日同小时合计，周沿用项目当前的 ISO 周口径。
    整段时间的总数量或蚊种构成应使用专门的构成工具。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间当天零点，含当天。
        end_time: 结束日期 YYYY-MM-DD，北京时间当天 23:59:59，含当天。
        time_unit: 分组粒度，hour小时合计、day日、week ISO周、month月、year年，默认 day。
        province: 可选省级地区全称，如广东省。
        city: 可选城市全称，如深圳市；可单独填写。
        district: 可选区县全称，如南山区；重名时结合其他地区条件确定。
        street: 可选街道全称；重名时结合其他地区条件确定。
        species: 可选单一蚊种中文名，省略查询全部类别。
        sex: 可选性别，male雄性、female雌性、unknown未知；省略查询全部性别。
        dev_ids: 可选已知设备 ID 正整数列表，与地区取交集，多台合计。
        chart_type: 图表类型，line折线图、bar柱状图，默认 line；图表展示各分组总数量。
    """
    # 1. 检查本地参数，再获取地区树，构造接口需要的查询条件。
    start_str, end_str = day_range(start_time, end_time)
    dev_ids = check_device_ids(dev_ids)
    sex_code = validate_sex(sex)
    monitor_type = SPECIES_CODES[species] if species is not None else None
    region_params = {}
    if any(value is not None for value in (province, city, district, street)):
        region_tree = get_region_tree()
        region_params = check_regions(
            region_tree, province=province, city=city, district=district, street=street,
        )
    params = {
        "start_time": start_str,
        "end_time": end_str,
        "time_unit": time_unit,
        **region_params,
        "monitor_type": monitor_type,
        "sex": sex_code,
        "dev_ids": ",".join(map(str, dev_ids)) if dev_ids is not None else None,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # 2. 请求接口；请求失败由 CompanyClient 和错误中间件处理。
    response = get_company_client().get("/api/v1/statistics/species-compose", params=params)
    try:
        rows = response["data"]["trend_points"]
    except (KeyError, TypeError):
        raise ToolDataError("种类构成接口缺少 data.trend_points。") from None
    if not isinstance(rows, list):
        raise ToolDataError("data.trend_points 必须是列表。")

    # 3. 整理数据；模型结果和图表都使用同一份已校验数据。
    data = _normalize_trend_rows(rows, time_unit, start_time, end_time, monitor_type)
    result = _build_trend_content(data, params)
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    artifact = _build_trend_chart(result, chart_type)
    return content, artifact
