"""按时间分组展示蚊虫监测数量 —— 文字版（不含图表）。

对应老项目 report_agent/tools/species_trend_report.py。老工具同时支持
display=text/line/bar 返回 chart artifact；本项目图表产物链路还没接，
这一版只返回文字 summary，display 参数整个去掉，等 chart 链路落地再补。

接口契约沿用老项目的真实路径 /api/v1/statistics/species-compose，
参数名也保持一致（monitor_type 传拉丁代码、dev_ids 逗号拼接、
start_time/end_time 带 00:00:00 / 23:59:59）。唯一没搬的是
normalize_region_names（地区名 → 地区代码），地区名暂时原样透传，
带地区名的查询要等 services/regions.py 落地后才准确。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

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
    report_tool,
    validate_sex,
)


def _point_order(time_unit: str, point: str, start: date, end: date):
    """校验时间标签的格式与范围，返回用于排序去重的键；不合法抛 ValueError。

    对应老工具里内联的那段校验：day 要落在 [start, end] 内，hour 只能是
    0~23，week 按 ISO 周（周一至周日）且与查询区间相交，month / year 看
    前缀是否落在范围内。返回的键在 time_unit 内可比较，保证排序稳定。
    """
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
    # year
    if not re.fullmatch(r"[0-9]{4}", point) or not (start.year <= int(point) <= end.year):
        raise ValueError(point)
    return point


@report_tool()
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
) -> dict:
    """查询按时间分组的蚊虫监测数量，返回文字结果。

    小时为跨日同小时合计；周采用 ISO 周一至周日；不计算同比环比。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间当天零点，含当天。
        end_time: 结束日期 YYYY-MM-DD，北京时间当天 23:59:59，含当天。
        time_unit: 时间粒度，hour小时 / day日 / week ISO周 / month月 / year年，默认 day。
        province: 可选省级地区全称。
        city: 可选城市全称。
        district: 可选区县全称。
        street: 可选街道全称。
        species: 可选单一蚊种中文名，省略查全部类别。
        sex: 可选性别，male雄性 / female雌性 / unknown未知，省略查全部性别。
        dev_ids: 可选已知设备 ID 正整数列表，与地区取交集，多台合计。
    """
    start_str, end_str = day_range(start_time, end_time)
    regions = check_regions(province=province, city=city, district=district, street=street)
    dev_ids = check_device_ids(dev_ids)
    sex_code = validate_sex(sex)
    monitor_type = SPECIES_CODES.get(species) if species is not None else None

    params = {
        "start_time": start_str,
        "end_time": end_str,
        **regions,
        "monitor_type": monitor_type,
        "sex": sex_code,
        "time_unit": time_unit,
        "dev_ids": ",".join(map(str, dev_ids)) if dev_ids is not None else None,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # CompanyClientError 不在这里包：ToolErrorHandlingMiddleware 会统一兜住
    # 并转成 error ToolMessage，错误文案已经由 client 翻译成中文。
    response = get_company_client().get("/api/v1/statistics/species-compose", params=params)

    try:
        rows = response["data"]["trend_points"]
    except (KeyError, TypeError):
        raise ToolDataError("种类构成接口缺少 data.trend_points。") from None
    if not isinstance(rows, list):
        raise ToolDataError("data.trend_points 必须是列表。")

    data: list[dict] = []
    seen: set = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("时间分组记录必须是对象。")
        point, total, species_list = row.get("time_point"), row.get("total_count"), row.get("species")
        # 后端确认小时 24 组无需处理，统计、峰值前统一排除。
        if time_unit == "hour" and point == "24":
            continue
        if not isinstance(point, str) or not point.strip():
            raise ToolDataError("时间分组标签必须为非空文本。")
        try:
            order = _point_order(time_unit, point, start_time, end_time)
        except ValueError:
            raise ToolDataError("接口时间标签与请求粒度或日期范围不符。") from None
        if order in seen:
            raise ToolDataError("接口返回重复时间分组，不能重复统计。")
        seen.add(order)
        if type(total) is not int or total < 0 or not isinstance(species_list, list):
            raise ToolDataError("时间分组需包含非负整数 total_count 及 species 列表。")

        detail: list[dict] = []
        codes: set = set()
        for item in species_list:
            if not isinstance(item, dict):
                raise ToolDataError("蚊种记录必须是对象。")
            code, count = item.get("monitor_type"), item.get("count")
            if not isinstance(code, str) or not code.strip() or code in codes:
                raise ToolDataError("蚊种名称为空或重复。")
            if type(count) is not int or count < 0:
                raise ToolDataError("蚊种数量必须为非负整数。")
            if monitor_type is not None and code != monitor_type:
                raise ToolDataError("接口返回的蚊种与筛选条件不一致。")
            codes.add(code)
            detail.append(
                {"monitor_type": code, "species_name": SPECIES_BY_CODE.get(code, code), "count": count}
            )
        if sum(item["count"] for item in detail) != total:
            raise ToolDataError("时间分组总量与蚊种数量合计不一致。")

        entry = {"time_point": point, "total_count": total, "species": detail, "_order": order}
        if time_unit == "week":
            week_start = date.fromisocalendar(int(point[:4]), int(point[-2:]), 1)
            week_end = week_start + timedelta(days=6)
            entry.update(
                {
                    "week_start": week_start.isoformat(),
                    "week_end": week_end.isoformat(),
                    "query_start": max(start_time, week_start).isoformat(),
                    "query_end": min(end_time, week_end).isoformat(),
                    "partial_week": start_time > week_start or end_time < week_end,
                }
            )
        data.append(entry)

    data.sort(key=lambda row: row["_order"])
    for row in data:
        del row["_order"]

    note = "仅展示查询范围内接口返回的分组；缺失分组不补零，首尾周期可能不完整，今天的数据可能尚未完整。"
    if time_unit == "hour":
        note += "小时标签为查询范围内同一小时的合计，不是跨日连续时间趋势；仅统计 0 至 23 小时，按后端确认规则忽略 24 组。"
    elif time_unit == "week":
        note += "周编号采用 ISO 周（周一至周日），编号中的年份为 ISO 周年份；week_start/week_end 为完整周，query_start/query_end 为本次覆盖日期，partial_week 标记查询是否覆盖不足整周。"

    peak = max((row["total_count"] for row in data), default=None)
    summary = {
        "message": "蚊虫时间分组查询完成。" if data else "未查到匹配的时间分组，不能据此断言实际数量为零。",
        "query": params,
        "time_unit": time_unit,
        "metric": "监测数量",
        "has_data": bool(data),
        "data": data,
        "returned_count": len(data),
        "total_count": sum(row["total_count"] for row in data) if data else None,
        "peak": (
            {
                "count": peak,
                "time_points": [row["time_point"] for row in data if row["total_count"] == peak],
            }
            if peak is not None
            else None
        ),
        "note": note,
        "scope": "返回时间分组的监测统计，包含接口返回的全部类别，受账号权限与筛选条件限制；不代表实际捕获量。",
    }
    if data and peak == 0:
        summary["message"] = "接口返回的各时间分组均为 0；没有正数峰值，不据此断言实际没有蚊虫。"
    if dev_ids is not None:
        summary["scope"] += "指定设备合计统计。"
        if regions:
            summary["filter_note"] = "设备与地区取交集，只统计指定设备中位于所选地区的数据。"
            if not data or peak == 0:
                summary["filter_note"] += "请核对设备是否属于所选地区；也可能是时间、其他条件或权限导致，不能仅凭空数据或全零确定原因。"
    if regions:
        summary["scope"] += "地区名当前按原样透传（尚未标准化为接口代码）。"
    return summary
