"""工具公共层 —— 所有业务工具共享的参数类型、错误、校验和蚊种字典。

对应真实 deer-flow 里分散在各工具文件里的公共约定，这里集中成一层：
    errors.py         —— 工具自己抛的两类错误（参数错 / 接口数据形状错）
    params.py         —— 共享的参数类型别名（日期、地区、性别、时间粒度…）
    species_names.py  —— 蚊种中文闭集 + 中文→拉丁映射
    validate.py       —— 类型管不了的语义校验（日期先后、性别映射、地区非空…）

业务工具从这一个包 import，避免每个工具自己写一遍 Field(ge=1) / 中文蚊种 Literal。
"""

from core.tools.common.decorators import report_tool
from core.tools.common.errors import ToolDataError, ToolInputError
from core.tools.common.params import (
    DeviceIds,
    EndDate,
    Page,
    PageSize,
    RegionName,
    Sex,
    StartDate,
    TimeUnit,
)
from core.tools.common.regions import get_region_tree
from core.tools.common.species_names import (
    SPECIES_BY_CODE,
    SPECIES_CODES,
    SpeciesName,
)
from core.tools.common.validate import (
    check_device_ids,
    check_regions,
    day_range,
    validate_sex,
)

__all__ = [
    "report_tool",
    "ToolInputError",
    "ToolDataError",
    "StartDate",
    "EndDate",
    "RegionName",
    "Sex",
    "TimeUnit",
    "DeviceIds",
    "Page",
    "PageSize",
    "SpeciesName",
    "SPECIES_CODES",
    "SPECIES_BY_CODE",
    "day_range",
    "validate_sex",
    "check_regions",
    "check_device_ids",
    "get_region_tree",
]
