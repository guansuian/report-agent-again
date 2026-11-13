"""查询整段时间的蚊虫监测数量与蚊种构成，返回分析数据和图表产物。

不向 /api/v1/statistics/species-compose 传 time_unit，读取 data.species。
按时间分组的数量变化由 species_trend 工具负责。
"""

from __future__ import annotations

import json
import math
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
    ToolDataError,
    check_device_ids,
    check_regions,
    day_range,
    get_region_tree,
    report_tool,
    validate_sex,
)


def _normalize_compose_rows(rows: list, monitor_type: str | None) -> list[dict]:
    """检查蚊种记录并补充中文名称；数量和占比保留接口原值。"""
    data = []
    seen_codes = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("蚊种记录必须是对象。")
        code = row.get("monitor_type")
        count = row.get("count")
        if not isinstance(code, str) or not code.strip() or code in seen_codes:
            raise ToolDataError("蚊种名称为空或重复。")
        if type(count) is not int or count < 0:
            raise ToolDataError("蚊种数量必须为非负整数。")
        if monitor_type is not None and code != monitor_type:
            raise ToolDataError("接口返回的蚊种与筛选条件不一致。")
        if "ratio" not in row:
            raise ToolDataError("蚊种记录缺少 ratio 占比字段。")
        ratio = row["ratio"]
        if ratio is not None and (
            type(ratio) not in (int, float)
            or not 0 <= ratio <= 100
            or not math.isfinite(ratio)
        ):
            raise ToolDataError("蚊种占比必须是 0 至 100 的有限数值或 null。")

        seen_codes.add(code)
        data.append({
            "monitor_type": code,
            "species_name": SPECIES_BY_CODE.get(code, code),
            "count": count,
            "ratio": ratio,
        })
    return data


def _build_compose_content(data: list[dict], params: dict) -> dict:
    """对返回的类别数量求和，说明占比分母和查询范围。"""
    total = sum(row["count"] for row in data) if data else None
    result = {
        "message": "蚊虫监测数量与种类构成查询完成。" if data else "未查到匹配数据，不能据此断言实际数量为零。",
        "query": params,
        "metric": "监测数量",
        "has_data": bool(data),
        "data": data,
        "returned_count": len(data),
        "total_count": total,
        "ratio_scope": "本次时间、地区、性别、设备及账号权限范围内，筛选结果中的蚊种构成。",
        "note": "ratio 是接口返回的百分数，100 表示 100%，不再乘以 100；null 不当作 0。总量为返回类别的数量合计。",
        "scope": "监测统计包含接口返回的全部类别，包括摇蚊科等，受查询条件及账号权限限制；不代表捕蚊设备的实际捕获量。",
    }
    if "monitor_type" in params:
        result["ratio_scope"] = "已筛选单一蚊种，其占比只属于筛选结果；100% 不代表它占全部蚊种的 100%。查询全部蚊种中的占比时，应省略 species。"
    if data and total == 0:
        result["message"] = "接口返回的各蚊种数量均为 0，无法计算构成比例；不能据此断言实际没有蚊虫。"

    has_region = any(field in params for field in ("province", "city", "district", "street"))
    if "dev_ids" in params:
        result["scope"] += "指定设备合计统计。"
        if has_region:
            result["filter_note"] = "设备与地区取交集，只统计指定设备中位于所选地区的数据。"
            if not data or total == 0:
                result["filter_note"] += "请核对设备归属、时间、其他筛选条件和权限，不能仅凭空数据或全零确定原因。"
    if has_region:
        result["scope"] += "地区已按当前账号的地区树校验，并补全了上级地区。"
    return result


def _build_compose_chart(result: dict, chart_type: str) -> dict | None:
    """用同一份蚊种数量构造饼图或柱状图；空数据和全零饼图不生成产物。"""
    if not result["has_data"]:
        return None
    if chart_type == "pie" and result["total_count"] == 0:
        return None

    rows = result["data"]
    names = [row["species_name"] for row in rows]
    labels = []
    for row in rows:
        label = row["species_name"]
        # 不同原名翻译后可能同名，保留原名来区分，避免前端混淆类别。
        if names.count(label) > 1:
            label += f"（{row['monitor_type']}）"
        labels.append(label)

    query = result["query"]
    start = query["start_time"].split(" ")[0]
    end = query["end_time"].split(" ")[0]
    display_name = f"{start}至{end} 蚊虫监测数量与种类构成"
    option = {"title": {"text": display_name}}
    if chart_type == "pie":
        option["tooltip"] = {"trigger": "item"}
        option["legend"] = {"type": "scroll", "bottom": 0}
        option["series"] = [{
            "name": "监测数量",
            "type": "pie",
            "radius": "55%",
            "data": [
                {"name": label, "value": row["count"]}
                for label, row in zip(labels, rows)
            ],
        }]
    else:
        option["tooltip"] = {"trigger": "axis"}
        option["xAxis"] = {"type": "category", "name": "蚊种", "data": labels}
        option["yAxis"] = {"type": "value", "name": "监测数量", "min": 0, "minInterval": 1}
        option["series"] = [{
            "name": "监测数量",
            "type": "bar",
            "data": [row["count"] for row in rows],
        }]

    chart_data = {
        "option": option,
        "query": dict(query),
        "scope": result["scope"],
        "ratio_scope": result["ratio_scope"],
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
def species_compose(
    start_time: StartDate,
    end_time: EndDate,
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    species: SpeciesName | None = None,
    sex: Sex | None = None,
    dev_ids: DeviceIds | None = None,
    chart_type: Literal["pie", "bar"] = "pie",
) -> tuple[str, dict | None]:
    """查询整段时间的蚊虫监测总量、各蚊种数量与构成，返回分析数据和图表。

    适合“有多少只”“有哪些蚊种”“各占多少”等问题，不提供时间趋势或排名。
    查询某蚊种在全部蚊种中的占比时，必须省略 species，再读取目标蚊种那一项。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间当天零点，含当天。
        end_time: 结束日期 YYYY-MM-DD，北京时间当天 23:59:59，含当天。
        province: 可选省级地区全称，如广东省。
        city: 可选城市全称，如深圳市；可单独填写。
        district: 可选区县全称；重名时结合其他地区条件确定。
        street: 可选街道全称；重名时结合其他地区条件确定。
        species: 可选单一蚊种中文名；查全部构成或在全部蚊种中的占比时省略。
        sex: 可选性别，male雄性、female雌性、unknown未知；省略查询全部性别。
        dev_ids: 可选已知设备 ID 正整数列表，与地区取交集，多台合计。
        chart_type: 图表类型，pie饼图、bar柱状图，默认 pie；各蚊种数量全零时不生成饼图。
    """
    # 1. 检查本地条件；提供地区时才获取地区树，定位并补全名称。
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
        **region_params,
        "monitor_type": monitor_type,
        "sex": sex_code,
        "dev_ids": ",".join(map(str, dev_ids)) if dev_ids is not None else None,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # 2. 不传 time_unit，读取整段时间的 species 汇总。
    response = get_company_client().get("/api/v1/statistics/species-compose", params=params)
    try:
        rows = response["data"]["species"]
    except (KeyError, TypeError):
        raise ToolDataError("种类构成接口缺少 data.species。") from None
    if not isinstance(rows, list):
        raise ToolDataError("data.species 必须是列表。")

    # 3. 模型结果与图表使用同一份数据；告知模型是否实际生成了图表。
    data = _normalize_compose_rows(rows, monitor_type)
    result = _build_compose_content(data, params)
    artifact = _build_compose_chart(result, chart_type)
    result["chart_type"] = chart_type
    result["chart_generated"] = artifact is not None
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return content, artifact
