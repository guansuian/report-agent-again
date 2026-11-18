"""查询当前设备概览，按已确定的业务口径计算数量和运行率。"""

from __future__ import annotations

import json
from typing import Literal

from core.clients import get_company_client
from core.tools.common import (
    RegionName,
    ToolDataError,
    check_regions,
    get_region_tree,
    report_tool,
)

_DEVICE_TYPE_NAMES = {"all": "全部设备", "mosquito": "监测设备", "catch": "捕蚊设备"}


def _calculate_statistics(total: int, online: int, offline: int) -> dict:
    """所有设备类型采用相同计算方式；总数为零时，比例返回 null。"""
    return {
        "total_devices": total,
        "online_devices": online,
        "offline_devices": offline,
        "running_rate": round(online / total * 100, 2) if total > 0 else None,
        "not_running_rate": round(offline / total * 100, 2) if total > 0 else None,
    }


def _build_overview_statistics(payload: dict) -> dict:
    """校验参与计算的六个数量，不采用接口的 total_devices 和 online_rate。"""
    if not isinstance(payload, dict):
        raise ToolDataError("设备概览接口的 data 必须是对象。")
    count_fields = (
        "online_devices", "offline_devices", "monitor_devices",
        "online_monitor_devices", "catch_devices", "online_catch_devices",
    )
    for field in count_fields:
        value = payload.get(field)
        if type(value) is not int or value < 0:
            raise ToolDataError(f"设备概览的 {field} 必须是非负整数。")
    if payload["online_monitor_devices"] > payload["monitor_devices"]:
        raise ToolDataError("监测设备在线数不能大于监测设备总数。")
    if payload["online_catch_devices"] > payload["catch_devices"]:
        raise ToolDataError("捕蚊设备在线数不能大于捕蚊设备总数。")

    # 全部设备总数按业务规则相加；各类型离线数按各自总数减在线数。
    total = payload["online_devices"] + payload["offline_devices"]
    monitor_offline = payload["monitor_devices"] - payload["online_monitor_devices"]
    catch_offline = payload["catch_devices"] - payload["online_catch_devices"]
    return {
        "all": _calculate_statistics(total, payload["online_devices"], payload["offline_devices"]),
        "mosquito": _calculate_statistics(
            payload["monitor_devices"], payload["online_monitor_devices"], monitor_offline,
        ),
        "catch": _calculate_statistics(
            payload["catch_devices"], payload["online_catch_devices"], catch_offline,
        ),
    }


def _build_overview_chart(result: dict, chart_type: str) -> dict | None:
    """图表和模型回答读取同一类型的统计；总数为零时不生成图表。"""
    summary = result["summary"]
    if summary["total_devices"] == 0:
        return None

    rows = [
        {"status": "在线（正常运行）", "count": summary["online_devices"], "ratio": summary["running_rate"]},
        {"status": "离线（不运行）", "count": summary["offline_devices"], "ratio": summary["not_running_rate"]},
    ]
    query = result["query"]
    region = "/".join(query[field] for field in ("province", "city", "district", "street") if field in query)
    region = region or "当前账号可见范围"
    device_type_name = _DEVICE_TYPE_NAMES[result["device_type"]]
    title = f"当前{region} {device_type_name}运行状态"
    metric = "设备数量（台）"
    option = {"title": {"text": title}}
    if chart_type == "pie":
        option["tooltip"] = {"trigger": "item"}
        option["legend"] = {"bottom": 0}
        option["series"] = [{
            "name": metric, "type": "pie", "radius": "55%",
            "data": [{"name": row["status"], "value": row["count"]} for row in rows],
        }]
    else:
        option["tooltip"] = {"trigger": "axis"}
        option["xAxis"] = {"type": "category", "name": "运行状态", "data": [row["status"] for row in rows]}
        option["yAxis"] = {"type": "value", "name": metric, "min": 0, "minInterval": 1}
        option["series"] = [{"name": metric, "type": "bar", "data": [row["count"] for row in rows]}]

    return {
        "artifact_type": "chart", "display_name": title,
        "data": {
            "report_label": "设备概览", "report_type": "device_overview",
            "dimension": {"label": "运行状态", "unit": "类"}, "chart_types": ["bar", "pie"],
            "columns": [
                {"key": "status", "label": "运行状态"},
                {"key": "count", "label": "设备数量（台）"},
                {"key": "ratio", "label": "占所选类型设备总数（%）"},
            ],
            "rows": rows, "option": option, "query": dict(query),
            "device_type": result["device_type"], "summary": dict(summary),
            "conditions": [
                {"label": "地区范围", "value": region},
                {"label": "设备类型", "value": device_type_name},
            ],
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
        },
    }


@report_tool(response_format="content_and_artifact")
def device_overview(
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    device_type: Literal["all", "mosquito", "catch"] = "all",
    chart_type: Literal["bar", "pie"] = "bar",
) -> tuple[str, dict | None]:
    """查询当前设备总数、运行和不运行数量及比例，默认生成在线/离线柱状图。

    不需要时间；不提供历史状态、新增/删除数量或单台设备明细。
    一次请求返回全部及两种设备类型的统计；device_type 仅选择本地展示范围。
    用户仅更换图表时，沿用此前的地区和设备类型，不能恢复为全部设备。
    前端已有报表的图形切换使用同一份数据，无需重新查询。

    Args:
        province: 可选省份全称，如广东省；省略全部地区条件时查当前账号可见范围。
        city: 可选城市全称，如深圳市；通过地区树校验并补全上级。
        district: 可选区县全称；同名时需结合上级地区确定。
        street: 可选街道全称；同名时需结合上级地区确定。
        device_type: 展示类型，all全部设备、mosquito监测设备、catch捕蚊设备；默认 all，不传公司接口。
        chart_type: 图表类型，bar默认柱状图、pie饼图；仅比较所选类型的在线/离线数量，不传公司接口。
    """
    # 1. 请求参数仅包含地区；凭据由已有 CompanyClient 从本轮上下文读取。
    params = {}
    if any(value is not None for value in (province, city, district, street)):
        region_tree = get_region_tree()
        params = check_regions(
            region_tree, province=province, city=city, district=district, street=street,
        )
    response = get_company_client().get("/api/v1/dashboard/device/overview", params=params)
    try:
        payload = response["data"]
    except (KeyError, TypeError):
        raise ToolDataError("设备概览接口缺少 data。") from None

    # 2. 统一计算各类型统计，再选择本次展示类型，避免混用不同类型的分子、分母。
    by_device_type = _build_overview_statistics(payload)
    summary = by_device_type[device_type]
    result = {
        "message": "设备概览查询完成。",
        "query": params, "device_type": device_type,
        "summary": summary, "by_device_type": by_device_type,
        "scope": "所选地区及当前账号可见范围内的当前设备状态，不代表历史状态或账号权限之外的全部设备。",
        "ratio_scope": "比例为所选设备类型的在线/离线数除以对应总数，单位为百分数，保留两位小数；总数为零时为 null，表示无法计算。",
        "note": "在线即正常运行，离线按不运行统计。全部设备展示总数按在线数加离线数计算，不采用接口 total_devices；比例重新计算，不采用接口 online_rate。"
                "两种设备类型的数量之和不要求等于全部设备数，不将差额归入任何类型，也不将离线数或差额当作删除数量。",
    }
    if summary["total_devices"] == 0:
        result["message"] = "所选类型的设备总数为 0，运行率和不运行率无法计算，不生成图表。"

    # 3. 模型内容和图表共用 summary；产物沿用现有登记与发送流程。
    artifact = _build_overview_chart(result, chart_type)
    result["chart_type"] = chart_type
    result["chart_generated"] = artifact is not None
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return content, artifact
