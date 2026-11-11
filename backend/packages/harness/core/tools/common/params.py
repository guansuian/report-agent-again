"""共享参数类型别名 —— 所有业务工具共用一套结构约束。

为什么集中定义：
    每个报表工具都要用日期 / 地区 / 时间粒度 / 性别 / 设备 / 分页，
    如果每个工具自己写 Annotated[date, Field(...)]，约束不一致的风险很高，
    比如一个工具允许 page_size=500、另一个只到 100。集中定义，语义只写一次。

约定：
    - 别名只承载「结构约束」（类型 + 边界），语义说明写在工具的 docstring 里，
      因为 parse_docstring=True 时 docstring 的 Args 描述会覆盖 Field(description=...)。
    - 日期统一用 date，语义是「北京时间自然日、首尾都包含」，工具内部再转成
      接口需要的字符串区间（见 validate.day_range）。
    - sex / time_unit / species 这类「闭集」用 Literal，把非法值挡在 schema 层。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field

# 日期：只约束类型，语义（北京时间、含首尾）交给工具 docstring。
StartDate = Annotated[date, Field(description="开始日期 YYYY-MM-DD，含当天")]
EndDate = Annotated[date, Field(description="结束日期 YYYY-MM-DD，含当天")]

# 地区：平铺的省级 / 市级 / 区县 / 街道全称；可选，传了就必须非空。
RegionName = Annotated[str, Field(min_length=1, max_length=64)]

# 性别：对外暴露可读字符串，工具内部再映射成公司接口的 0/1/2（见 validate.validate_sex）。
Sex = Literal["male", "female", "unknown"]

# 时间粒度：小时为跨日同小时合计；周采用 ISO 周一至周日。
TimeUnit = Literal["hour", "day", "week", "month", "year"]

# 设备：正整数 ID 列表，多台合计；与地区取交集。
DeviceIds = Annotated[
    list[Annotated[int, Field(gt=0)]],
    Field(min_length=1, max_length=100),
]

# 分页：字典类接口用，页码从 1 起，每页最多 200。
Page = Annotated[int, Field(ge=1)]
PageSize = Annotated[int, Field(ge=1, le=200)]
