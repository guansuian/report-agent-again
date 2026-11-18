"""查询区域或单街道内的设备排名，返回模型分析数据和柱状图产物。

读取 /api/v1/statistics/region-rank 的 data.regions，保留接口实际返回的层级。
"""

from __future__ import annotations

import json
import math
from typing import Annotated

from pydantic import Field

from core.clients import get_company_client
from core.tools.common.conditions import build_report_conditions
from core.tools.common import (
    SPECIES_CODES,
    EndDate,
    RegionName,
    Sex,
    SpeciesName,
    StartDate,
    ToolDataError,
    ToolInputError,
    check_regions,
    get_region_tree,
    report_tool,
    validate_sex,
)

_LEVEL_NAMES = {
    "province": "省级区域", "city": "城市", "district": "区县",
    "street": "街道", "device": "设备",
}
_SEX_NAMES = {0: "性别未知", 1: "雄性", 2: "雌性"}


def _normalize_rank_rows(rows: list) -> list[dict]:
    """校验排名字段和层级；保留接口顺序、名次、数量及统计口径。"""
    data = []
    for row in rows:
        if not isinstance(row, dict):
            raise ToolDataError("排名记录必须是对象。")
        name = row.get("region")
        level = row.get("level")
        if not isinstance(name, str) or not name.strip():
            raise ToolDataError("排名的区域或设备名称必须是非空文本。")
        if not isinstance(level, str) or level not in _LEVEL_NAMES:
            raise ToolDataError("排名 level 必须是 province、city、district、street 或 device。")
        if data and level != data[0]["level"]:
            raise ToolDataError("接口返回了不同层级的排名，不能合并展示。")

        for field in ("total_count", "device_count", "rank"):
            value = row.get(field)
            minimum = 1 if field == "rank" else 0
            if type(value) is not int or value < minimum:
                raise ToolDataError(f"排名的 {field} 必须是大于等于 {minimum} 的整数。")
        for field in ("device_avg", "ratio"):
            value = row.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ToolDataError(f"排名的 {field} 必须是非负有限数值。")
        if row["ratio"] > 100:
            raise ToolDataError("排名占比 ratio 必须在 0 至 100 之间。")
        if not isinstance(row.get("monitor_address"), str):
            raise ToolDataError("排名的 monitor_address 必须是文本，区域级允许为空字符串。")
        if data and row["total_count"] > data[-1]["total_count"]:
            raise ToolDataError("接口排名未按监测数量降序排列，不能直接作为数量榜单展示。")

        # 不按名称去重：设备没有独立 ID，同名设备不能据此视为同一台。
        data.append({
            "region": name, "level": level, "rank": row["rank"],
            "total_count": row["total_count"], "ratio": row["ratio"],
            "device_count": row["device_count"], "device_avg": row["device_avg"],
            "monitor_address": row["monitor_address"],
        })
    return data


def _build_rank_content(data: list[dict], params: dict, species: list[str] | None) -> dict:
    """组织模型分析数据，明确 Top N、蚊种合计与设备统计的含义。"""
    level = data[0]["level"] if data else None
    note = "按监测数量降序展示；仅统计接口返回项，未返回的区域或设备不补零。"
    note += "device_count 的纳入规则及 device_avg 的计算、取整规则待确认，均保留接口原值。"
    if species is not None and len(species) > 1:
        note += "多个蚊种合计后排名，不包含各蚊种明细；分别比较榜单时需分别查询。"
    if level == "device":
        note += "设备名称和监测地址不是设备 ID，不能直接用于设备 ID 筛选。"

    result = {
        "message": "监测数量排名查询完成。" if data else "未查到匹配的排名，不能据此断言实际数量为零。",
        "report_type": "region_rank",
        "query": params,
        "species": species,
        "metric": "监测数量",
        "ranking_level": level,
        "has_data": bool(data),
        "data": data,
        "returned_count": len(data),
        "total_count": sum(row["total_count"] for row in data) if data else None,
        "ratio_scope": "ratio 是本次返回列表内部的百分比，100 表示 100%；Top N 会改变分母，不能称为全范围占比。",
        "scope": "查询条件及账号权限范围内返回条目的监测统计；合计仅代表本次返回列表，不保证覆盖全范围，不代表设备实际捕获量。",
        "note": note,
    }
    if data and result["total_count"] == 0:
        result["message"] = "接口返回的各项数量均为 0，没有正数最高项，不能据此断言实际没有蚊虫。"
    return result


def _build_rank_chart(result: dict) -> dict | None:
    """模型结果和柱状图使用同一份排名数据；空列表不生成产物。"""
    if not result["has_data"]:
        return None

    rows = result["data"]
    query = result["query"]
    level_name = _LEVEL_NAMES[result["ranking_level"]]
    region = "/".join(query[field] for field in ("province", "city", "district", "street") if field in query)
    region = region or "当前账号可见范围"
    species = result["species"]
    species_name = "、".join(species) if species is not None else "全部类别"
    if species is not None and len(species) > 1:
        species_name += "合计"
    sex_name = _SEX_NAMES[query["sex"]] if "sex" in query else "全部性别"
    display_name = (
        f"{query['start_time']}至{query['end_time']} {region} "
        f"{sex_name}{species_name} {level_name}监测数量前{query['top_n']}名"
    )
    option = {
        "title": {"text": display_name},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category", "name": level_name,
            "data": [f"{row['rank']}. {row['region']}" for row in rows],
        },
        "yAxis": {"type": "value", "name": "监测数量", "min": 0, "minInterval": 1},
        "series": [{"name": "监测数量", "type": "bar", "data": [row["total_count"] for row in rows]}],
    }
    columns = [
        {"key": "rank", "label": "名次"},
        {"key": "region", "label": level_name},
        {"key": "total_count", "label": "监测数量"},
        {"key": "ratio", "label": "返回列表内占比（%）"},
        {"key": "device_count", "label": "接口设备数"},
        {"key": "device_avg", "label": "接口设备均值（口径待确认）"},
    ]
    if result["ranking_level"] == "device":
        columns.append({"key": "monitor_address", "label": "监测地址"})
    return {
        "artifact_type": "chart",
        "display_name": display_name,
        "data": {
            "report_label": "设备排名" if result["ranking_level"] == "device" else "区域排名",
            "dimension": {"label": level_name, "unit": "台" if result["ranking_level"] == "device" else "个"},
            "chart_types": ["bar"],
            "columns": columns,
            "report_type": "region_rank", "ranking_level": result["ranking_level"],
            "option": option, "query": dict(query), "rows": rows,
            "conditions": build_report_conditions(query),
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
        },
    }


@report_tool(response_format="content_and_artifact")
def region_rank(
    start_time: StartDate,
    end_time: EndDate,
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    species: Annotated[list[SpeciesName], Field(min_length=1)] | None = None,
    sex: Sex | None = None,
    top_n: Annotated[int, Field(ge=1, strict=True)] = 10,
) -> tuple[str, dict | None]:
    """按监测数量降序查询区域或单街道设备排名，返回分析数据和柱状图。

    通常不填地区查省级，指定省查城市，指定省市查区县，指定省市区查街道，
    指定完整街道路径查该街道内设备；实际层级以返回的 level 为准。
    不支持指定设备 ID、任意 level、最少数量排名或设备均值排名。
    多蚊种是合计排名；查询各蚊种各自的榜单时应分别调用。

    Args:
        start_time: 开始日期 YYYY-MM-DD，按接口已验证的日期格式传入。
        end_time: 结束日期 YYYY-MM-DD，不能早于开始日期。
        province: 可选省级地区全称，如广东省。
        city: 可选城市全称，如深圳市；工具通过地区树补全上级。
        district: 可选区县全称；重名时需结合上级地区确定。
        street: 可选街道全称；定位后查询该街道内设备，不代表全市或全省设备排名。
        species: 可选非空蚊种中文名列表，单个蚊种也用列表；多项合计排名，省略查询全部类别。
        sex: 可选性别，male雄性、female雌性、unknown未知；省略查询全部性别。
        top_n: 返回监测数量最多的前 N 项，必须是正整数，默认 10；不足 N 项时返回实际结果。
    """
    # 1. 校验输入并补全地区；本接口按文档传日期，不使用带时分秒的 day_range。
    if start_time > end_time:
        raise ToolInputError(f"开始日期 {start_time} 不能晚于结束日期 {end_time}。")
    if species is not None and len(set(species)) != len(species):
        raise ToolInputError("species 蚊种列表不能有重复项。")
    sex_code = validate_sex(sex)
    monitor_type = ",".join(SPECIES_CODES[name] for name in species) if species is not None else None
    region_params = {}
    if any(value is not None for value in (province, city, district, street)):
        region_tree = get_region_tree()
        region_params = check_regions(
            region_tree, province=province, city=city, district=district, street=street,
        )
    params = {
        "start_time": start_time.isoformat(), "end_time": end_time.isoformat(),
        **region_params, "monitor_type": monitor_type, "sex": sex_code, "top_n": top_n,
    }
    params = {key: value for key, value in params.items() if value is not None}

    # 2. 查询接口；HTTP 和业务状态码由 CompanyClient 检查。
    response = get_company_client().get("/api/v1/statistics/region-rank", params=params)
    try:
        rows = response["data"]["regions"]
    except (KeyError, TypeError):
        raise ToolDataError("区域排名接口缺少 data.regions。") from None
    if not isinstance(rows, list):
        raise ToolDataError("data.regions 必须是列表。")
    if len(rows) > top_n:
        raise ToolDataError("接口返回条数超过 top_n，不能直接作为指定的前 N 名展示。")

    # 3. 整理模型分析数据和前端产物，保留接口原始排名及占比。
    data = _normalize_rank_rows(rows)
    result = _build_rank_content(data, params, species)
    artifact = _build_rank_chart(result)
    result["chart_type"] = "bar"
    result["chart_generated"] = artifact is not None
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return content, artifact
