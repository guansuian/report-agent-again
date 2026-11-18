"""查询生境监测数量、占比和排名，获取未传 Top N 的列表后再选择展示项。"""

from __future__ import annotations

import json
import math
from typing import Annotated, Literal

from pydantic import Field

from core.clients import get_company_client
from core.tools.common import (
    SPECIES_CODES,
    DeviceIds,
    EndDate,
    RegionName,
    Sex,
    SpeciesName,
    StartDate,
    ToolDataError,
    check_device_ids,
    check_regions,
    day_range,
    get_region_tree,
    report_tool,
    validate_sex,
)
from core.tools.common.conditions import build_report_conditions


def _normalize_habitat_rows(rows: list) -> list[dict]:
    """保留名称及接口统计，按数量排序；占比以全部返回项为分母重新计算。"""
    data = []
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("生境记录必须是对象。")
        if "area_type" not in row or (row["area_type"] is not None and not isinstance(row["area_type"], str)):
            raise ToolDataError("生境 area_type 必须是文本或 null。")
        for field in ("count", "device_count", "rank"):
            value = row.get(field)
            minimum = 1 if field == "rank" else 0
            if type(value) is not int or value < minimum:
                raise ToolDataError(f"生境的 {field} 必须是大于等于 {minimum} 的整数。")
        average = row.get("device_avg")
        if type(average) not in (int, float) or not math.isfinite(average) or average < 0:
            raise ToolDataError("生境的 device_avg 必须是非负有限数值。")
        if "ratio" not in row:
            raise ToolDataError("生境记录缺少 ratio 字段。")
        source_ratio = row["ratio"]
        if source_ratio is not None and (
            type(source_ratio) not in (int, float)
            or not math.isfinite(source_ratio) or not 0 <= source_ratio <= 100
        ):
            raise ToolDataError("生境的接口 ratio 必须是 0 至 100 的有限数值或 null。")

        raw_name = row["area_type"]
        display_name = raw_name if raw_name is not None and raw_name.strip() else "未填写生境"
        data.append({
            "area_type": raw_name, "habitat_name": display_name,
            "count": row["count"], "device_count": row["device_count"],
            "device_avg": average, "rank": row["rank"], "source_ratio": source_ratio,
        })

    total = sum(row["count"] for row in data)
    for row in data:
        row["ratio"] = round(row["count"] / total * 100, 2) if total > 0 else None
    # 数量相同的条目保持原顺序；不按名称合并，也不把并列项说成唯一最高项。
    data.sort(key=lambda row: row["count"], reverse=True)
    return data


def _build_habitat_content(data: list[dict], params: dict, top_n: int | None) -> dict:
    """模型始终拿到全部返回生境，Top N 只决定前端展示的条目。"""
    displayed = data if top_n is None else data[:top_n]
    total = sum(row["count"] for row in data) if data else None
    result = {
        "message": "生境监测数量与分布查询完成。" if data else "当前条件下未查到生境统计，不能据此断言监测数量为零。",
        "report_type": "habitat_ratio", "query": params, "metric": "监测数量",
        "has_data": bool(data), "data": data, "returned_count": len(data),
        "total_count": total, "top_n": top_n,
        "displayed_count": len(displayed),
        "displayed_total_count": sum(row["count"] for row in displayed) if displayed else None,
        "truncated": len(displayed) < len(data),
        "ratio_scope": "ratio 按本次未传 Top N 的接口返回列表中全部生境数量合计计算，单位为百分数，保留两位小数；本地截取不改变该分母，source_ratio 仅保留接口原值。",
        "scope": "查询时间、地区、蚊种、性别、设备及账号权限范围内，接口返回生境的监测统计，不代表实际捕获量、蚊虫密度或风险排名。",
        "note": "不维护固定生境名单，不合并近似名称；空名称显示为未填写生境，与其他类别分开。未返回的生境不能当作零。"
                "device_count 和 device_avg 保留接口原值，统计口径及均值公式待确认，不据此进行平均捕获量排名。"
                "生境按监测数量降序展示，数量相同需说明并列；rank 保留接口名次。",
    }
    if data and total == 0:
        result["message"] = "接口返回的各生境数量均为 0，占比无法计算，没有正数最高项；不能据此断言实际没有蚊虫。"
    if result["truncated"]:
        result["note"] += "图表仅展示前 N 项；表格 ratio 仍为全部返回生境占比，饼图扇区为前 N 项内部构成，不能混用。"
    if "dev_ids" in params:
        result["scope"] += "指定设备合计统计。"
        if any(key in params for key in ("province", "city", "district", "street")):
            result["filter_note"] = "设备与地区条件取交集；空结果不能直接确定设备不属于该地区，也可能与时间、其他条件或权限有关。"
    return result


def _build_habitat_chart(result: dict, chart_type: str) -> dict | None:
    """沿用统一产物格式；空列表不画图，全零仅支持柱状图。"""
    if not result["has_data"] or (chart_type == "pie" and result["total_count"] == 0):
        return None
    rows = result["data"] if result["top_n"] is None else result["data"][:result["top_n"]]
    query = result["query"]
    conditions = build_report_conditions(query)
    region = "/".join(query[field] for field in ("province", "city", "district", "street") if field in query)
    region = region or "当前账号可见范围"
    start = query["start_time"].split(" ")[0]
    end = query["end_time"].split(" ")[0]
    title = f"{start}至{end} {region} {conditions[1]['value']}{conditions[0]['value']} 生境监测数量与分布"
    display_scope = "全部返回生境" if result["top_n"] is None else f"前 {result['top_n']} 项，实际展示 {len(rows)} 项"
    conditions.append({"label": "展示范围", "value": display_scope})
    if result["truncated"]:
        # 前端可本地切换柱状图和饼图，标题同时注明两种图的占比边界。
        title += f"（前{result['top_n']}项；饼图为前N项内部构成）"
    labels = [f"{row['rank']}. {row['habitat_name']}" for row in rows]
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
        option["xAxis"] = {"type": "category", "name": "生境", "data": labels}
        option["yAxis"] = {"type": "value", "name": "监测数量", "min": 0, "minInterval": 1}
        option["series"] = [{"name": "监测数量", "type": "bar", "data": [row["count"] for row in rows]}]
    return {
        "artifact_type": "chart", "display_name": title,
        "data": {
            "report_label": "生境分布", "report_type": "habitat_ratio",
            "dimension": {"label": "生境", "unit": "类"},
            "chart_types": ["bar", "pie"] if result["total_count"] > 0 else ["bar"],
            "columns": [
                {"key": "rank", "label": "接口名次"},
                {"key": "habitat_name", "label": "生境名称"},
                {"key": "count", "label": "监测数量"},
                {"key": "ratio", "label": "全部返回生境占比（%）"},
                {"key": "device_count", "label": "接口设备数"},
                {"key": "device_avg", "label": "接口设备均值（口径待确认）"},
            ],
            "rows": rows, "option": option, "query": dict(query), "conditions": conditions,
            "total_count": result["total_count"], "displayed_total_count": result["displayed_total_count"],
            "top_n": result["top_n"], "truncated": result["truncated"],
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
            "filter_note": result.get("filter_note"),
        },
    }


@report_tool(response_format="content_and_artifact")
def habitat_ratio(
    start_time: StartDate,
    end_time: EndDate,
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    species: SpeciesName | None = None,
    sex: Sex | None = None,
    dev_ids: DeviceIds | None = None,
    top_n: Annotated[int, Field(ge=1, strict=True)] | None = None,
    chart_type: Literal["bar", "pie"] = "bar",
) -> tuple[str, dict | None]:
    """查询各生境的蚊虫监测数量、占比和排名，默认生成全部返回生境的柱状图。

    不需要用户提供生境名称，不接受 area_type、habitats 或关键词筛选。
    用户问公园数量、比较医院与公园时，省略 top_n，从返回 data 中读取完整名称对应项。
    名称不完整或有歧义时，根据实际返回名称调用 ask_clarification 确认，不自动修正或合并名称。
    未返回目标生境时说明当前条件下未找到，不当作 0，也不据此判断其少于其他生境。
    用户未明确要求前 N 项时不截取；多个生境数量相同应说明并列，不独断唯一第一名。
    不提供每日生境趋势、设备均值排名、实际捕获量、密度或风险排名。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间当天零点，含当天；缺少时间先追问。
        end_time: 结束日期 YYYY-MM-DD，北京时间当天 23:59:59，含当天。
        province: 可选省级地区全称；省略全部地区条件时查询当前账号可见范围。
        city: 可选城市全称，如深圳市；通过地区树校验并补全上级。
        district: 可选区县全称；同名时需结合上级地区确定。
        street: 可选街道全称；同名时需结合上级地区确定。
        species: 可选单一蚊种中文名，省略查询全部类别，不支持多蚊种列表。
        sex: 可选 male雄性、female雌性、unknown未知；省略查询全部性别，包含未知性别。
        dev_ids: 可选已知设备 ID 正整数列表，多台合计，与地区条件取交集。
        top_n: 可选正整数；仅在用户要求前 N 项时设置，本地截取展示，不传公司接口，不改变全列表占比分母。
        chart_type: bar默认柱状图、pie饼图；前 N 项饼图只代表展示项内部构成，全零不生成饼图。
    """
    # 1. 复用现有日期、地区、蚊种、性别和设备校验；Top N 仅用于本地展示。
    start, end = day_range(start_time, end_time)
    device_ids = check_device_ids(dev_ids)
    region_params = {}
    if any(value is not None for value in (province, city, district, street)):
        region_tree = get_region_tree()
        region_params = check_regions(
            region_tree, province=province, city=city, district=district, street=street,
        )
    params = {
        "start_time": start, "end_time": end, **region_params,
        "monitor_type": SPECIES_CODES[species] if species is not None else None,
        "sex": validate_sex(sex),
        "dev_ids": ",".join(str(device_id) for device_id in device_ids) if device_ids is not None else None,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # 2. 不向接口发送 top_n，先取得全部返回生境，避免丢失用户关注的条目。
    response = get_company_client().get("/api/v1/statistics/habitat-ratio", params=params)
    try:
        rows = response["data"]["habitats"]
    except (KeyError, TypeError):
        raise ToolDataError("生境统计接口缺少 data.habitats。") from None
    if not isinstance(rows, list):
        raise ToolDataError("data.habitats 必须是列表。")
    data = _normalize_habitat_rows(rows)

    # 3. 返回全部生境供模型分析；图表可按用户要求截取，但全列表占比不改变。
    result = _build_habitat_content(data, params, top_n)
    artifact = _build_habitat_chart(result, chart_type)
    if chart_type == "pie" and data and result["total_count"] == 0:
        result["message"] += "未生成饼图，可改用柱状图展示。"
    result["chart_type"] = chart_type
    result["chart_generated"] = artifact is not None
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return content, artifact
