"""环境相关性：调用配对统计接口，返回系数及不可计算原因。"""

from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.clients import get_company_client
from core.tools.common import (
    DeviceIds, EndDate, RegionName, StartDate, ToolDataError, ToolInputError,
    check_regions, get_region_tree, report_tool,
)
from core.tools.common.validate import get_region_code

_Codes = Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
_TenantIds = Annotated[list[Annotated[int, Field(gt=0, strict=True)]], Field(min_length=1)]


class SensorRange(BaseModel):
    """聚合后的环境均值范围；指标由工具的 sensor_type 指定。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lower: float | None = None
    upper: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = False

    @model_validator(mode="after")
    def check_bounds(self):
        if self.lower is not None and self.upper is not None:
            if self.lower > self.upper:
                raise ValueError("环境均值范围的下限不能大于上限。")
            if self.lower == self.upper and not (self.lower_inclusive and self.upper_inclusive):
                raise ValueError("相同上下限必须同时包含边界，否则范围为空。")
        return self


def _validate_response(response: dict) -> dict:
    """检查结果结构，不重算相关系数，不比较请求条件回显。"""
    data = response.get("data")
    if not isinstance(data, dict) or data.get("contract_version") != "agent-data-v1":
        raise ToolDataError("环境相关性接口缺少 data 或返回了不一致的协议版本。")
    for key in ("method", "pair_count", "coefficient", "reason"):
        if key not in data:
            raise ToolDataError(f"环境相关性结果缺少 {key}。")
    if data["method"] != "pearson":
        raise ToolDataError("环境相关性 method 必须为 pearson。")
    if type(data["pair_count"]) is not int or data["pair_count"] < 0:
        raise ToolDataError("pair_count 必须是非负整数。")
    coefficient = data["coefficient"]
    if coefficient is not None:
        if type(coefficient) not in (int, float) or not math.isfinite(coefficient) or not -1 <= coefficient <= 1:
            raise ToolDataError("coefficient 必须是 -1 至 1 的有限数值或 null。")
        if data["pair_count"] < 3:
            raise ToolDataError("有效配对少于 3 组时，相关系数应为 null，不能据此判断相关性。")
    if data["reason"] is not None and not isinstance(data["reason"], str):
        raise ToolDataError("reason 必须是文本或 null。")
    return data


@report_tool
def environment_correlation(
    start_time: StartDate,
    end_time: EndDate,
    sensor_type: Literal["temp", "hum", "wind", "co2", "light"],
    time_unit: Literal["hour", "day"] = "hour",
    sensor_range: SensorRange | None = None,
    province: RegionName | None = None,
    city: RegionName | None = None,
    district: RegionName | None = None,
    street: RegionName | None = None,
    habitat_codes: _Codes | None = None,
    mosquito_codes: _Codes | None = None,
    sexes: Annotated[list[Literal[0, 1, 2]], Field(min_length=1)] | None = None,
    dev_ids: DeviceIds | None = None,
    tenant_ids: _TenantIds | None = None,
) -> str:
    """分析环境均值与蚊虫监测数量的 Pearson 线性相关性，只返回数据，不生成图表。

    后端按同一设备、同一小时或天，将环境均值与监测总量配对后计算；
    不使用 environment_distribution 的区间汇总结果计算，不将缺失样本补零。
    sensor_range 筛选聚合后的环境均值，不能用 environment_filters 代替。
    地区精确匹配完整名称，只定位一条路径；生境、蚊种编码分别从 dictionaries、
    mosquito_dictionaries 获取，不编造代码。同一字段多选取并集，不同筛选取交集。
    系数接近 1 表示正向线性关系，接近 -1 表示反向，接近 0 表示线性关系较弱；
    不能据此断言完全无关系。null 表示无法计算，不是 0；相关不代表因果。

    Args:
        start_time: 开始日期 YYYY-MM-DD，北京时间，包含当天；缺少日期先追问。
        end_time: 结束日期 YYYY-MM-DD，包含当天，内部转为次日零点且不包含该时刻。
        sensor_type: 环境指标，temp温度、hum湿度、wind风速、co2二氧化碳、light光照。
        time_unit: 配对粒度，hour小时、day天，默认 hour。
        sensor_range: 可选环境均值范围，包含 lower、upper 和边界标记；默认左闭右开，null 表示无界。
        province: 可选省级全称，如广东省。
        city: 可选城市全称，如深圳市，不能写深圳；可补全上级。
        district: 可选区县全称，重名时需补充上级地区。
        street: 可选街道全称，重名时需补充上级地区。
        habitat_codes: 可选非空生境代码列表，取自 dictionaries 的 code。
        mosquito_codes: 可选非空具体蚊种代码列表，取自 mosquito_dictionaries 的 species 节点。
        sexes: 可选性别列表，0未知、1雄、2雌；省略表示全部。
        dev_ids: 可选已知设备内部 ID 列表，与地区条件取交集。
        tenant_ids: 可选已知租户 ID 列表，仍受用户权限限制，不编造。
    """
    # 1. 校验业务条件；环境范围单独传给 sensor_range，不放入 filters。
    if start_time > end_time:
        raise ToolInputError("开始日期不能晚于结束日期。")
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
    if any(value is not None for value in (province, city, district, street)):
        tree = get_region_tree()
        region_names = check_regions(tree, province=province, city=city, district=district, street=street)
        filters["region_codes"] = [get_region_code(tree, region_names)]

    # 2. 自然日转换后调用专用接口，配对、去重和计算均由公司后端完成。
    beijing = timezone(timedelta(hours=8))
    start = datetime.combine(start_time, time.min, tzinfo=beijing)
    end = datetime.combine(end_time + timedelta(days=1), time.min, tzinfo=beijing)
    query = {
        "contract_version": "agent-data-v1", "start_time": start.isoformat(), "end_time": end.isoformat(),
        "filters": filters, "sensor_type": sensor_type, "time_unit": time_unit,
    }
    if sensor_range is not None:
        query["sensor_range"] = sensor_range.model_dump()
    response = get_company_client().post("/api/v1/statistics/environment/correlation", json=query)
    data = _validate_response(response)

    # 3. 原样保留系数和不可计算原因，交给 AI 用文字或表格说明。
    result = {
        "query": query, "applied_query": data.get("applied_query"),
        "method": data["method"], "pair_count": data["pair_count"],
        "coefficient": data["coefficient"], "reason": data["reason"],
        "note": "按同设备、同小时或天的环境均值与监测总量配对，pair_count 是有效配对数。"
                "null 表示无法计算，不能当作 0；NO_DATA 表示无有效数据，INSUFFICIENT_PAIRS 表示少于 3 组，"
                "ZERO_VARIANCE 表示至少一个变量没有变化。系数接近 0 只表示线性关系较弱，相关不代表因果。"
                "结果受账号权限和筛选条件限制，监测数量不代表实际捕获量。",
    }
    return json.dumps(result, ensure_ascii=False, allow_nan=False)
