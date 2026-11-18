"""直接查询设备监测数量排名，保留重复设备 ID 的原始条目。"""

from __future__ import annotations

import json
import math
from collections import Counter
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
    ToolInputError,
    check_device_ids,
    check_regions,
    day_range,
    get_region_tree,
    report_tool,
    validate_sex,
)
from core.tools.common.conditions import build_report_conditions

_DEVICE_TYPE_NAMES = {"mosquito": "监测设备", "catch": "捕蚊设备"}


def _normalize_device_rows(rows: list) -> list[dict]:
    """校验字段和数量降序；不按 ID 或名称合并，不重算接口名次与占比。"""
    data = []
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("设备排名记录必须是对象。")
        name = row.get("device_name")
        if not isinstance(name, str) or not name.strip():
            raise ToolDataError("设备名称必须是非空文本。")
        for field in ("device_id", "rank", "total_count"):
            value = row.get(field)
            minimum = 0 if field == "total_count" else 1
            if type(value) is not int or value < minimum:
                raise ToolDataError(f"设备排名的 {field} 必须是大于等于 {minimum} 的整数。")
        ratio = row.get("ratio")
        if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 100:
            raise ToolDataError("设备排名占比 ratio 必须是 0 至 100 之间的有限数值。")
        if "monitor_address" not in row or (
            row["monitor_address"] is not None and not isinstance(row["monitor_address"], str)
        ):
            raise ToolDataError("设备排名需包含 monitor_address，允许文本或 null。")
        if data and row["total_count"] > data[-1]["total_count"]:
            raise ToolDataError("接口设备排名未按监测数量降序排列。")

        data.append({
            "device_id": row["device_id"], "device_name": name,
            "rank": row["rank"], "total_count": row["total_count"],
            "ratio": ratio, "monitor_address": row["monitor_address"],
        })
    return data


def _build_device_content(data: list[dict], params: dict, species: list[str] | None) -> dict:
    """统计返回条目及不同设备数，让模型区分条目排名与完整的每设备排名。"""
    device_counts = Counter(row["device_id"] for row in data)
    duplicate_ids = [device_id for device_id, count in device_counts.items() if count > 1]
    note = "保留接口原始条目、顺序、名次及占比；Top N 指前 N 条结果，不保证是 N 台不同设备。"
    note += "返回列表不保证覆盖全部匹配数据，未返回设备不补零，也不能用于推断数量最少的设备。"
    if duplicate_ids:
        note += "本次存在重复设备 ID，未合并条目；仅合并截取后的 Top N 不能得到完整的每台设备总量排名。"
    if species is not None and len(species) > 1:
        note += "多个蚊种合计后排名，不是各蚊种分别排名。"
    if "dev_ids" in params and "dev_names" in params:
        note += "设备 ID 与完整名称同时传入，取两项条件的交集。"

    result = {
        "message": "设备排名查询完成。" if data else "未查到匹配的排名，不能据此断言实际数量为零。",
        "report_type": "device_rank", "query": params, "species": species,
        "metric": "监测数量", "has_data": bool(data), "data": data,
        "returned_count": len(data), "unique_device_count": len(device_counts),
        "duplicate_device_ids": duplicate_ids,
        "total_count": sum(row["total_count"] for row in data) if data else None,
        "ratio_scope": "ratio 为本次返回榜单内的百分比，100 表示 100%；Top N 会改变分母，不代表全省或全部设备的占比。",
        "scope": "查询条件及账号权限范围内返回条目的监测统计；合计仅代表本次榜单，不代表设备实际捕获量。",
        "note": note,
    }
    if data and result["total_count"] == 0:
        result["message"] = "接口返回的各条目数量均为 0，没有正数最高项，不能据此断言实际没有蚊虫。"
    return result


def _build_device_chart(result: dict, chart_type: str) -> dict | None:
    """模型和图表使用同一份条目；空结果及全零饼图不生成产物。"""
    if not result["has_data"]:
        return None
    if chart_type == "pie" and result["total_count"] == 0:
        return None

    rows = result["data"]
    query = result["query"]
    # 公共函数处理日期、地区、蚊种、性别和 ID；本工具单独标注设备类型、名称与条数。
    common_query = {
        key: value for key, value in query.items()
        if key not in ("device_type", "dev_names", "top_n")
    }
    conditions = build_report_conditions(common_query)
    if result["species"] is not None and len(result["species"]) > 1:
        conditions[0]["value"] += "（合计）"
    device_type_name = _DEVICE_TYPE_NAMES[query["device_type"]] if "device_type" in query else "全部设备类型"
    conditions.append({"label": "设备类型", "value": device_type_name})
    if "dev_names" in query:
        conditions.append({"label": "完整设备名称", "value": query["dev_names"]})
    conditions.append({"label": "排名范围", "value": f"前 {query['top_n']} 条"})

    region = "/".join(query[field] for field in ("province", "city", "district", "street") if field in query)
    region = region or "当前账号可见范围"
    start = query["start_time"].split(" ")[0]
    end = query["end_time"].split(" ")[0]
    species_name = conditions[0]["value"]
    sex_name = conditions[1]["value"]
    title = f"{start}至{end} {region} {sex_name}{species_name} {device_type_name}监测数量前{query['top_n']}条"
    labels = [f"{row['rank']}. {row['device_name']}（ID {row['device_id']}）" for row in rows]
    option = {"title": {"text": title}}
    if chart_type == "pie":
        option["tooltip"] = {"trigger": "item"}
        option["legend"] = {"type": "scroll", "bottom": 0}
        option["series"] = [{
            "name": "监测数量", "type": "pie", "radius": "55%",
            "data": [{"name": label, "value": row["total_count"]} for label, row in zip(labels, rows)],
        }]
    else:
        option["tooltip"] = {"trigger": "axis"}
        option["xAxis"] = {"type": "category", "name": "排名条目", "data": labels}
        option["yAxis"] = {"type": "value", "name": "监测数量", "min": 0, "minInterval": 1}
        option["series"] = [{"name": "监测数量", "type": "bar", "data": [row["total_count"] for row in rows]}]

    return {
        "artifact_type": "chart", "display_name": title,
        "data": {
            "report_label": "设备排名", "report_type": "device_rank",
            "dimension": {"label": "排名条目", "unit": "条"},
            "chart_types": ["bar", "pie"] if result["total_count"] > 0 else ["bar"],
            "columns": [
                {"key": "rank", "label": "接口名次"},
                {"key": "device_id", "label": "设备 ID"},
                {"key": "device_name", "label": "设备名称"},
                {"key": "total_count", "label": "监测数量"},
                {"key": "ratio", "label": "本次榜单内占比（%）"},
                {"key": "monitor_address", "label": "监测地址"},
            ],
            "rows": rows, "option": option, "query": dict(query), "conditions": conditions,
            "returned_count": result["returned_count"],
            "unique_device_count": result["unique_device_count"],
            "duplicate_device_ids": result["duplicate_device_ids"],
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
        },
    }


@report_tool(response_format="content_and_artifact")
def device_rank(
    start_time: StartDate,
    end_time: EndDate,
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    species: Annotated[list[SpeciesName], Field(min_length=1)] | None = None,
    sex: Sex | None = None,
    device_type: Literal["mosquito", "catch"] | None = None,
    dev_ids: DeviceIds | None = None,
    dev_names: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)] | None = None,
    top_n: Annotated[int, Field(ge=1, strict=True)] = 10,
    chart_type: Literal["bar", "pie"] = "bar",
) -> tuple[str, dict | None]:
    """直接查询指定范围内的设备监测数量排名，默认生成柱状图。

    只指定省或市即可查设备，不必逐层查到街道；省略地区查当前账号可见范围。
    Top N 是前 N 条排名结果，同一设备 ID 可能多次出现，本工具保留并提示，不合并。
    多蚊种是合计排名；不支持最少数量排名、租户筛选或每台设备的蚊种明细。
    用户要求严格的每台设备总量排名时，不能将可能重复、截断的榜单当作完整结果。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间当天零点，含当天；缺少时间先追问。
        end_time: 结束日期 YYYY-MM-DD，北京时间当天 23:59:59，含当天。
        province: 可选省级地区全称，如广东省。
        city: 可选城市全称，如深圳市；通过地区树校验并补全上级。
        district: 可选区县全称；同名时需结合上级地区确定。
        street: 可选街道全称，不是查询设备排名的必填条件。
        species: 可选非空蚊种中文名列表；多种合计后排名，省略查询全部类别。
        sex: 可选性别，male雄性、female雌性、unknown未知；省略查询全部性别。
        device_type: 可选设备类型，mosquito监测设备、catch捕蚊设备；省略不限制类型，数量仍按监测统计解释。
        dev_ids: 可选已知设备内部 ID 正整数列表，不是设备编号；与地区、名称条件取交集。
        dev_names: 可选完整设备名称列表，不支持名称片段；单个名称不能包含英文逗号。
        top_n: 返回数量最多的前 N 条，正整数，默认 10；不保证 N 台不同设备或完整榜单。
        chart_type: 图表类型，bar默认柱状图、pie榜单内部构成饼图；全零时不生成饼图。
    """
    # 1. 校验输入；列表参数转换成接口使用的英文逗号文本。
    start, end = day_range(start_time, end_time)
    if species is not None and len(set(species)) != len(species):
        raise ToolInputError("species 蚊种列表不能有重复项。")
    if dev_names is not None:
        if any(not name.strip() or "," in name for name in dev_names):
            raise ToolInputError("设备名称必须是非空完整名称，单个名称不能包含英文逗号。")
        if len(set(dev_names)) != len(dev_names):
            raise ToolInputError("设备名称列表不能有重复项。")
    device_ids = check_device_ids(dev_ids)
    monitor_type = ",".join(SPECIES_CODES[name] for name in species) if species is not None else None
    sex_code = validate_sex(sex)
    region_params = {}
    if any(value is not None for value in (province, city, district, street)):
        region_tree = get_region_tree()
        region_params = check_regions(
            region_tree, province=province, city=city, district=district, street=street,
        )
    params = {
        "start_time": start, "end_time": end, **region_params,
        "monitor_type": monitor_type, "sex": sex_code, "device_type": device_type,
        "dev_ids": ",".join(str(device_id) for device_id in device_ids) if device_ids is not None else None,
        "dev_names": ",".join(dev_names) if dev_names is not None else None,
        "top_n": top_n,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # 2. 查询并校验排名；HTTP 及业务状态码交给现有 CompanyClient 检查。
    response = get_company_client().get("/api/v1/statistics/device-rank", params=params)
    try:
        rows = response["data"]["devices"]
    except (KeyError, TypeError):
        raise ToolDataError("设备排名接口缺少 data.devices。") from None
    if not isinstance(rows, list):
        raise ToolDataError("data.devices 必须是列表。")
    if len(rows) > top_n:
        raise ToolDataError("接口返回条数超过 top_n，不能直接作为指定的前 N 条展示。")
    data = _normalize_device_rows(rows)

    # 3. 同时返回模型分析数据和前端产物，不合并重复 ID，不向接口传 chart_type。
    result = _build_device_content(data, params, species)
    artifact = _build_device_chart(result, chart_type)
    if chart_type == "pie" and data and result["total_count"] == 0:
        result["message"] += "未生成饼图，可改用柱状图展示。"
    result["chart_type"] = chart_type
    result["chart_generated"] = artifact is not None
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return content, artifact
