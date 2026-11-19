"""周期比较：两段明确日期共享筛选条件，只返回分析数据。"""

from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.clients import get_company_client
from core.tools.common import (
    DeviceIds, EndDate, RegionName, StartDate,
    ToolDataError, ToolInputError, check_regions, get_region_tree, report_tool,
)
from core.tools.common.params import EnvironmentFilter
from core.tools.common.validate import get_region_code

_Codes = Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
_TenantIds = Annotated[list[Annotated[int, Field(gt=0, strict=True)]], Field(min_length=1)]


class ComparisonPeriod(BaseModel):
    """一个包含首尾自然日的比较周期。"""

    model_config = ConfigDict(extra="forbid")
    start_time: StartDate
    end_time: EndDate

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_time > self.end_time:
            raise ValueError("周期的开始日期不能晚于结束日期。")
        return self

    def to_query(self) -> dict[str, str]:
        """将自然日转成北京时间的左闭右开区间。"""
        beijing = timezone(timedelta(hours=8))
        start = datetime.combine(self.start_time, time.min, tzinfo=beijing)
        end = datetime.combine(self.end_time + timedelta(days=1), time.min, tzinfo=beijing)
        return {"start_time": start.isoformat(), "end_time": end.isoformat()}


def _validate_response(response: dict) -> dict:
    """只检查结果结构和类型，不重算数值，也不核对条件回显。"""
    data = response.get("data")
    if not isinstance(data, dict) or data.get("contract_version") != "agent-data-v1":
        raise ToolDataError("周期比较接口缺少 data 或返回了不一致的协议版本。")
    fields = ("current_monitor_count", "previous_monitor_count", "difference",
              "growth_rate", "equal_duration", "growth_reason")
    for key in fields:
        if key not in data:
            raise ToolDataError(f"周期比较结果缺少 {key}。")
    for key in ("current_monitor_count", "previous_monitor_count"):
        value = data[key]
        if value is not None and (type(value) is not int or value < 0):
            raise ToolDataError(f"{key} 必须是非负整数或 null。")
    if data["difference"] is not None and type(data["difference"]) is not int:
        raise ToolDataError("difference 必须是整数或 null。")
    rate = data["growth_rate"]
    if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate)):
        raise ToolDataError("growth_rate 必须是有限数值或 null。")
    if type(data["equal_duration"]) is not bool:
        raise ToolDataError("equal_duration 必须是布尔值。")
    if data["growth_reason"] is not None and not isinstance(data["growth_reason"], str):
        raise ToolDataError("growth_reason 必须是文本或 null。")
    return data


@report_tool
def period_compare(
    current_period: ComparisonPeriod,
    previous_period: ComparisonPeriod,
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
) -> str:
    """比较明确指定的两个周期，返回监测数量、差值和增长率，不生成图表。

    两期共用完全相同的筛选条件，不能分别指定不同地区。比较多个地区的增长情况时，
    分别调用本工具。必须明确两段日期；缺少或有歧义先追问，不擅自平移日期。
    地区名称精确匹配并补全上级；只定位一条地区路径，不合并多个地区或省略条件。
    生境和蚊种编码从 dictionaries、mosquito_dictionaries 获取，不编造或填写中文名。
    同一字段内多选取并集，不同筛选取交集；省略表示不限制。
    增长率 25 表示 25%；null 不等于 0%，须结合 growth_reason 解释。
    不等长周期可以比较总量，但不能直接称为等长环比。结果可用文字或 Markdown 表格展示。

    Args:
        current_period: 本期日期对象，包含 start_time 和 end_time（YYYY-MM-DD），首尾均包含。
        previous_period: 对比期日期对象，包含 start_time 和 end_time，首尾均包含，必须明确指定。
        province: 可选省级全称，如广东省。
        city: 可选城市全称，如深圳市，不能写深圳；可补全上级。
        district: 可选区县全称，重名时需补充上级地区。
        street: 可选街道全称，重名时需补充上级地区。
        habitat_codes: 可选非空生境代码列表，取自 dictionaries 的 code。
        mosquito_codes: 可选非空具体蚊种代码列表，取自 mosquito_dictionaries 的 species 节点。
        sexes: 可选性别列表，0未知、1雄、2雌；省略表示全部。
        dev_ids: 可选已知设备内部 ID 列表，与地区条件取交集。
        tenant_ids: 可选已知租户 ID 列表，仍受用户权限限制，不编造。
        environment_filters: 可选环境范围列表，指定指标、上下限及边界，null 表示无界。
    """
    # 1. 两个周期使用同一组筛选，输入不合法时不删除条件重试。
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
    if any(value is not None for value in (province, city, district, street)):
        tree = get_region_tree()
        region_names = check_regions(tree, province=province, city=city, district=district, street=street)
        filters["region_codes"] = [get_region_code(tree, region_names)]

    # 2. 分别转换两段日期，一次请求直接取得后端计算的比较结果。
    query = {
        "contract_version": "agent-data-v1",
        "current_period": current_period.to_query(),
        "previous_period": previous_period.to_query(),
        "filters": filters,
    }
    response = get_company_client().post("/api/v1/statistics/period-compare", json=query)
    data = _validate_response(response)

    # 3. 原样保留零值、null 和原因，避免把无法计算说成没有变化。
    note = (
        "growth_rate 的单位为百分比，25 表示 25%；null 不表示 0%。"
        "PREVIOUS_PERIOD_ZERO 表示上期为零，无法计算增长率；NO_RECORDS 表示存在无有效记录的周期。"
        "监测数量受账号权限及筛选条件限制，不代表实际捕获量。"
    )
    if not data["equal_duration"]:
        note += "两期不等长，不能直接称为等长环比。"
    result = {
        "query": query, "applied_query": data.get("applied_query"),
        "current_monitor_count": data["current_monitor_count"],
        "previous_monitor_count": data["previous_monitor_count"],
        "difference": data["difference"], "growth_rate": data["growth_rate"],
        "equal_duration": data["equal_duration"], "growth_reason": data["growth_reason"],
        "note": note,
    }
    return json.dumps(result, ensure_ascii=False, allow_nan=False)
