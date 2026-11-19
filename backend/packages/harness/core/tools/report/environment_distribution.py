"""自定义环境区间分布：调用新接口，保留原始统计并生成一维或二维报表。"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.clients import get_company_client
from core.tools.common import (
    DeviceIds, EndDate, RegionName, StartDate, ToolDataError, ToolInputError,
    check_regions, get_region_tree, report_tool,
)
from core.tools.common.params import EnvironmentFilter
from core.tools.common.validate import get_region_code

logger = logging.getLogger(__name__)
_SENSOR_NAMES = {"temp": "温度", "hum": "湿度", "wind": "风速", "co2": "CO₂", "light": "光照"}
_Codes = Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
_TenantIds = Annotated[list[Annotated[int, Field(gt=0, strict=True)]], Field(min_length=1)]


class EnvironmentInterval(BaseModel):
    """一个区间，null 表示该侧无界，默认左闭右开。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: Annotated[str, Field(min_length=1)]
    lower: float | None
    upper: float | None
    lower_inclusive: bool = True
    upper_inclusive: bool = False

    @model_validator(mode="after")
    def check_bounds(self):
        if not self.id.strip():
            raise ValueError("区间 id 不能是空白文本。")
        if self.lower is not None and self.upper is not None:
            if self.lower > self.upper:
                raise ValueError("区间下限不能大于上限。")
            if self.lower == self.upper and not (self.lower_inclusive and self.upper_inclusive):
                raise ValueError("相同上下限必须同时包含边界，否则区间为空。")
        return self


class EnvironmentAxis(BaseModel):
    """一个环境指标及其区间；校验时排序，不改变发送和展示的原顺序。"""

    model_config = ConfigDict(extra="forbid")
    sensor_type: Literal["temp", "hum", "wind", "co2", "light"]
    intervals: Annotated[list[EnvironmentInterval], Field(min_length=1)]

    @model_validator(mode="after")
    def check_intervals(self):
        ids = [item.id for item in self.intervals]
        if len(set(ids)) != len(ids):
            raise ValueError("同一指标内的区间 id 不能重复。")
        ordered = sorted(self.intervals, key=lambda item: (
            item.lower if item.lower is not None else -math.inf,
            item.upper if item.upper is not None else math.inf,
        ))
        for previous, current in zip(ordered, ordered[1:]):
            if previous.upper is None or current.lower is None:
                raise ValueError("同一指标的区间不能重叠。")
            if previous.upper > current.lower:
                raise ValueError("同一指标的区间不能重叠。")
            if previous.upper == current.lower and previous.upper_inclusive and current.lower_inclusive:
                raise ValueError("相邻区间不能同时包含同一个边界值。")
        return self


def _interval_label(interval: EnvironmentInterval, unit: str) -> str:
    """显示实际区间和单位，不对数值取整，也不把无界当作 0。"""
    lower = str(interval.lower) if interval.lower is not None else "-∞"
    upper = str(interval.upper) if interval.upper is not None else "+∞"
    left = "[" if interval.lower is not None and interval.lower_inclusive else "("
    right = "]" if interval.upper is not None and interval.upper_inclusive else ")"
    return f"{left}{lower},{upper}{right}{unit}"


def _read_response(response: dict) -> tuple[dict, list[dict], list[dict]]:
    """按返回的区间定义解释组合编号；不比较请求回显，不重算统计。"""
    data = response.get("data")
    if not isinstance(data, dict) or data.get("contract_version") != "agent-data-v1":
        raise ToolDataError("环境分布接口缺少 data 或返回了不一致的协议版本。")
    logger.debug("环境分布接口回显 applied_query=%r", data.get("applied_query"))
    axes = data.get("axes")
    if not isinstance(axes, list) or not 1 <= len(axes) <= 2:
        raise ToolDataError("环境分布 axes 必须包含 1 至 2 个指标。")
    definitions = []
    for axis in axes:
        if not isinstance(axis, dict) or not isinstance(axis.get("unit"), str) or not axis["unit"].strip():
            raise ToolDataError("环境分布每个指标需包含实际单位 unit。")
        try:
            model = EnvironmentAxis.model_validate({"sensor_type": axis.get("sensor_type"), "intervals": axis.get("intervals")})
        except ValidationError:
            raise ToolDataError("环境分布返回的指标或区间定义无效。") from None
        labels = {item.id: _interval_label(item, axis["unit"]) for item in model.intervals}
        definitions.append({"sensor_type": model.sensor_type, "labels": labels})
    if len({axis["sensor_type"] for axis in definitions}) != len(definitions):
        raise ToolDataError("环境分布返回了重复的环境指标。")
    total = data.get("matched_monitor_count")
    if type(total) is not int or total < 0:
        raise ToolDataError("matched_monitor_count 必须是非负整数。")
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list):
        raise ToolDataError("环境分布 groups 必须是列表。")
    groups = []
    seen = set()
    for group in raw_groups:
        if not isinstance(group, dict):
            raise ToolDataError("每个环境分组必须是对象。")
        ids = group.get("interval_ids")
        if not isinstance(ids, list) or len(ids) != len(definitions):
            raise ToolDataError("interval_ids 的数量必须与 axes 一致。")
        labels = []
        for interval_id, axis in zip(ids, definitions):
            if not isinstance(interval_id, str) or interval_id not in axis["labels"]:
                raise ToolDataError("分组引用了该指标中不存在的区间编号。")
            labels.append(axis["labels"][interval_id])
        if tuple(ids) in seen:
            raise ToolDataError("接口返回重复区间组合，不能覆盖或重复统计。")
        seen.add(tuple(ids))
        for field in ("monitor_count", "record_count"):
            if type(group.get(field)) is not int or group[field] < 0:
                raise ToolDataError(f"环境分组 {field} 必须是非负整数。")
        if "percentage" not in group:
            raise ToolDataError("环境分组缺少 percentage。")
        percentage = group["percentage"]
        if percentage is not None and (type(percentage) not in (int, float) or not math.isfinite(percentage) or not 0 <= percentage <= 100):
            raise ToolDataError("percentage 必须是 0 至 100 的有限数值或 null。")
        groups.append({**group, "interval_labels": labels})
    if data.get("quality") is not None and not isinstance(data["quality"], dict):
        raise ToolDataError("环境分布 quality 提供时必须是对象。")
    return data, definitions, groups


def _build_chart(result: dict, definitions: list[dict], region_names: dict[str, str]) -> dict | None:
    """一维使用柱状图或饼图，二维使用现有多系列分组柱状图。"""
    groups = result["groups"]
    if not groups or (result["chart_type"] == "pie" and result["matched_monitor_count"] == 0):
        return None
    first = definitions[0]
    axis_name = _SENSOR_NAMES[first["sensor_type"]] + "区间"
    region = "/".join(region_names.values()) or "当前账号可见范围"
    sensor_names = "＋".join(_SENSOR_NAMES[axis["sensor_type"]] for axis in definitions)
    title = f"{region} {sensor_names}区间监测数量分布"
    counts = {tuple(group["interval_ids"]): group["monitor_count"] for group in groups}
    option = {"title": {"text": title}}
    if result["chart_type"] == "pie":
        option.update(tooltip={"trigger": "item"}, legend={"type": "scroll", "bottom": 0})
        option["series"] = [{"name": "监测数量", "type": "pie", "radius": "55%", "data": [
            {"name": label, "value": counts.get((interval_id,))} for interval_id, label in first["labels"].items()
        ]}]
    else:
        series = []
        if len(definitions) == 1:
            values = [counts.get((interval_id,)) for interval_id in first["labels"]]
            series.append({"name": "监测数量", "type": "bar", "data": values})
        else:
            second = definitions[1]
            for interval_id, label in second["labels"].items():
                values = [counts.get((first_id, interval_id)) for first_id in first["labels"]]
                series.append({"name": _SENSOR_NAMES[second["sensor_type"]] + " " + label, "type": "bar", "data": values})
            option["legend"] = {"type": "scroll", "top": 0, "data": [item["name"] for item in series]}
        option.update(
            tooltip={"trigger": "axis"},
            xAxis={"type": "category", "name": axis_name, "data": list(first["labels"].values())},
            yAxis={"type": "value", "name": "监测数量", "min": 0, "minInterval": 1}, series=series,
        )
    rows = []
    columns = []
    for index, axis in enumerate(definitions):
        columns.append({"key": f"axis_{index}", "label": _SENSOR_NAMES[axis["sensor_type"]] + "区间"})
    for group in groups:
        row = dict(group)
        for index, label in enumerate(group["interval_labels"]):
            row[f"axis_{index}"] = label
        rows.append(row)
    columns.extend([
        {"key": "monitor_count", "label": "监测数量"}, {"key": "record_count", "label": "有效记录条数"},
        {"key": "percentage", "label": "进入分组的监测数量占比（%）"},
    ])
    query = result["query"]
    conditions = [
        {"label": "开始时间", "value": query["start_time"]}, {"label": "结束时间（不含）", "value": query["end_time"]},
        {"label": "地区范围", "value": region},
    ]
    for axis in definitions:
        conditions.append({"label": _SENSOR_NAMES[axis["sensor_type"]] + "区间", "value": "；".join(axis["labels"].values())})
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
            "report_type": "environment_distribution", "report_label": "环境区间分布",
            "dimension": {"label": axis_name, "unit": "组", "count": len(first["labels"])},
            "chart_types": ["bar", "pie"] if len(definitions) == 1 and result["matched_monitor_count"] > 0 else ["bar"],
            "rows": rows, "columns": columns, "option": option, "conditions": conditions, "query": query,
            "summary_metric": {"label": "进入分组的监测数量", "value": result["matched_monitor_count"]},
            "axes": result["axes"], "quality": result["quality"],
            "scope": result["scope"], "ratio_scope": result["ratio_scope"], "note": result["note"],
        },
    }


@report_tool(response_format="content_and_artifact")
def environment_distribution(
    start_time: StartDate,
    end_time: EndDate,
    axes: Annotated[list[EnvironmentAxis], Field(min_length=1, max_length=2)],
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
    chart_type: Literal["bar", "pie"] = "bar",
) -> tuple[str, dict | None]:
    """查询自定义环境区间的监测数量和占比，支持一维分布、二维联合分组。

    用户未明确区间时先用 ask_clarification 追问，不自行编造默认区间。
    axes 的区间 id 是本次请求的区间标识，可自行命名；不同于地区、生境和蚊种字典代码。
    默认左闭右开，null 表示无界，各指标内部区间不能重叠，不对边界取整。
    地区全称精确校验后取编码；生境、蚊种代码分别取自 dictionaries、mosquito_dictionaries。
    一维默认柱状图，也可饼图；二维仅分组柱状图，第一指标横轴、第二指标系列。
    保留后端占比和排除说明，缺失组合不补零。不计算相关系数，不把相关解释为因果。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间，包含当天；缺少时间先追问。
        end_time: 结束日期 YYYY-MM-DD，包含当天，内部转为次日零点且不包含该时刻。
        axes: 1至2个不同环境指标，每个提供 sensor_type 和 intervals；区间提供唯一 id、lower、upper 及可选边界标记。
        province: 可选省级全称，如广东省。
        city: 可选城市全称，如深圳市，不能写深圳；可补全上级。
        district: 可选区县全称，重名时补充上级。
        street: 可选街道全称，重名时补充上级；地区只定位一条路径。
        habitat_codes: 可选非空生境代码列表，从 dictionaries 取得。
        mosquito_codes: 可选非空具体蚊种代码列表，从 mosquito_dictionaries 取得。
        sexes: 可选性别列表，0未知、1雄、2雌；不传表示全部。
        dev_ids: 可选已知设备内部 ID 列表，与其他条件取交集。
        tenant_ids: 可选已知租户 ID 列表，仍受用户权限限制。
        environment_filters: 可选额外环境范围筛选，与 axes 的分组范围共同生效。
        chart_type: bar默认柱状图；一维可选 pie，二维只能 bar；零监测量不生成饼图。
    """
    # 1. 拒绝无效条件，不自动换图、删条件或生成默认区间。
    if start_time > end_time:
        raise ToolInputError("开始日期不能晚于结束日期。")
    if len({axis.sensor_type for axis in axes}) != len(axes):
        raise ToolInputError("axes 中不能重复指定同一种环境指标。")
    if len(axes) == 2 and chart_type == "pie":
        raise ToolInputError("二维环境分布使用分组柱状图，请设置 chart_type=bar。")
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

    # 2. 复用地区校验及带时区日期范围，一次请求只处理本次完整分组结果。
    region_names = {}
    if any(value is not None for value in (province, city, district, street)):
        tree = get_region_tree()
        region_names = check_regions(tree, province=province, city=city, district=district, street=street)
        filters["region_codes"] = [get_region_code(tree, region_names)]
    beijing = timezone(timedelta(hours=8))
    start = datetime.combine(start_time, time.min, tzinfo=beijing)
    end = datetime.combine(end_time + timedelta(days=1), time.min, tzinfo=beijing)
    query = {"contract_version": "agent-data-v1", "start_time": start.isoformat(), "end_time": end.isoformat(),
             "filters": filters, "axes": [axis.model_dump() for axis in axes]}
    response = get_company_client().post("/api/v1/statistics/environment/distribution", json=query)
    data, definitions, groups = _read_response(response)

    # 3. 监测数量与记录条数分开保留，分母直接采用后端 matched_monitor_count。
    quality = data.get("quality")
    quality_notes = []
    if quality is not None:
        for field, label in (("matched_record_count", "进入分组的记录条数"),
                             ("excluded_record_count", "排除的记录条数"), ("reason", "排除说明")):
            if quality.get(field) is not None:
                quality_notes.append(f"{label}：{quality[field]}")
    note = "保留接口原始统计，监测数量不等于记录条数。缺失环境值不当作 0，缺失区间组合不补零。"
    if quality_notes:
        note += "；".join(quality_notes) + "。"
    message = "环境区间分布查询完成。" if groups else "未返回匹配的环境分组，不能据此断言实际数量为零。"
    if chart_type == "pie" and data["matched_monitor_count"] == 0:
        message += "进入分组的监测数量为 0，未生成饼图。"
    result = {
        "message": message, "report_type": "environment_distribution", "query": query,
        "applied_query": data.get("applied_query"), "axes": data["axes"], "groups": groups,
        "matched_monitor_count": data["matched_monitor_count"], "quality": quality,
        "returned_count": len(groups), "has_data": bool(groups), "chart_type": chart_type,
        "scope": "查询时间、用户权限及全部筛选范围内的监测识别统计，不代表实际捕获量或因果关系。",
        "ratio_scope": "percentage 以实际进入指定区间分组的监测数量为分母，不含缺失环境值和区间外数据。",
        "note": note,
    }
    artifact = _build_chart(result, definitions, region_names)
    result["chart_generated"] = artifact is not None
    return json.dumps(result, ensure_ascii=False, allow_nan=False), artifact
