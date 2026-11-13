"""工具参数层测试。

覆盖 common/ 里的共享类型、蚊种闭集、语义校验、report_tool 装饰器，
以及 species_compose 工具的 schema 与时间标签校验。

对应真实 deer-flow 里用「扫描测试」守护工具参数约定的思路
（见 test_tool_args_schema_no_pydantic_warning.py），但这里是对每个
约定显式断言，而不是只扫一遍有没有 description。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

from core.tools.common import (
    SPECIES_BY_CODE,
    SPECIES_CODES,
    DeviceIds,
    Page,
    PageSize,
    SpeciesName,
    ToolInputError,
    check_device_ids,
    check_regions,
    day_range,
    report_tool,
    validate_sex,
)
from core.tools.report.species_compose import _point_order, species_compose


# ---------------------------------------------------------------------------
# 蚊种闭集与中英文映射
# ---------------------------------------------------------------------------

def test_species_literal_matches_codes_keys():
    """SpeciesName 闭集必须和 SPECIES_CODES 的键完全一致，且互不重复。"""
    assert set(SpeciesName.__args__) == set(SPECIES_CODES)
    assert len(SPECIES_CODES) == 8
    # 拉丁代码也互不重复，否则反向映射会丢蚊种。
    assert len(set(SPECIES_CODES.values())) == len(SPECIES_CODES)


def test_species_by_code_is_inverse():
    assert len(SPECIES_BY_CODE) == len(SPECIES_CODES)
    for name, code in SPECIES_CODES.items():
        assert SPECIES_BY_CODE[code] == name


# ---------------------------------------------------------------------------
# 语义校验函数
# ---------------------------------------------------------------------------

def test_day_range_beijing_bounds():
    assert day_range(date(2026, 1, 1), date(2026, 1, 3)) == (
        "2026-01-01 00:00:00",
        "2026-01-03 23:59:59",
    )


def test_day_range_rejects_reversed():
    with pytest.raises(ToolInputError):
        day_range(date(2026, 2, 1), date(2026, 1, 1))


def test_validate_sex_mapping():
    assert validate_sex("male") == 1
    assert validate_sex("female") == 2
    assert validate_sex("unknown") == 0
    assert validate_sex(None) is None


def test_validate_sex_rejects_invalid():
    with pytest.raises(ToolInputError):
        validate_sex("both")


# 只保留定位要用到的字段，模拟 /api/v1/agent/regions 返回的树。
_REGION_TREE = [
    {
        "level": "province", "name": "浙江省", "code": "浙江", "children": [
            {"level": "city", "name": "杭州市", "code": "杭州", "children": [
                {"level": "district", "name": "西湖区", "code": "杭州西湖", "children": []},
            ]},
        ],
    },
    {
        "level": "province", "name": "江西省", "code": "江西", "children": [
            {"level": "city", "name": "南昌市", "code": "南昌", "children": [
                {"level": "district", "name": "西湖区", "code": "南昌西湖", "children": []},
            ]},
        ],
    },
    {
        "level": "province", "name": "北京市", "code": "北京", "children": [
            {"level": "district", "name": "朝阳区", "code": "北京朝阳", "children": []},
        ],
    },
]


def test_check_regions_strips_and_fills_parent():
    # 输入带空格也能定位，返回父级补全后的接口参数。
    assert check_regions(_REGION_TREE, province=" 浙江省 ", city="杭州市") == {
        "province": "浙江省",
        "city": "杭州市",
    }
    # 只给区县时，城市由树补出。
    assert check_regions(_REGION_TREE, district="西湖区", province="浙江省") == {
        "province": "浙江省",
        "city": "杭州市",
        "district": "西湖区",
    }
    # 直辖市没有 city 层，重复填写的 city 被忽略。
    assert check_regions(_REGION_TREE, province="北京市", city="北京市", district="朝阳区") == {
        "province": "北京市",
        "district": "朝阳区",
    }


def test_check_regions_rejects_blank_and_ambiguous():
    with pytest.raises(ToolInputError):
        check_regions(_REGION_TREE, city="   ")
    # 西湖区在树里出现两次，不给上级无法确定。
    with pytest.raises(ToolInputError):
        check_regions(_REGION_TREE, district="西湖区")


def test_check_device_ids_passthrough():
    assert check_device_ids([1, 2, 3]) == [1, 2, 3]
    assert check_device_ids(None) is None


def test_check_device_ids_rejects_empty_and_duplicate():
    with pytest.raises(ToolInputError):
        check_device_ids([])
    with pytest.raises(ToolInputError):
        check_device_ids([1, 1, 2])


# ---------------------------------------------------------------------------
# report_tool 装饰器
# ---------------------------------------------------------------------------

def test_report_tool_forbids_extra():
    @report_tool()
    def f(x: int) -> str:
        """取 x。

        Args:
            x: 值。
        """
        return str(x)

    assert f.args_schema.model_config.get("extra") == "forbid"
    with pytest.raises(ValidationError):
        f.args_schema.model_validate({"x": 1, "oops": 2})


def test_report_tool_rejects_mismatched_docstring():
    with pytest.raises(ValueError):
        @report_tool()
        def g(x: int) -> str:
            """取 x。

            Args:
                y: docstring 里出现、函数签名里没有的参数。
            """
            return str(x)


# ---------------------------------------------------------------------------
# 共享参数别名（结构约束）
# ---------------------------------------------------------------------------

class _PagedArgs(BaseModel):
    page: Page
    page_size: PageSize


def test_pagination_alias_bounds():
    assert _PagedArgs(page=1, page_size=200).page_size == 200
    with pytest.raises(ValidationError):
        _PagedArgs(page=0, page_size=10)  # page ge=1
    with pytest.raises(ValidationError):
        _PagedArgs(page=1, page_size=201)  # page_size le=200


class _DeviceArgs(BaseModel):
    dev_ids: DeviceIds


def test_device_ids_alias_constraints():
    assert _DeviceArgs(dev_ids=[1, 2]).dev_ids == [1, 2]
    with pytest.raises(ValidationError):
        _DeviceArgs(dev_ids=[])  # min_length=1
    with pytest.raises(ValidationError):
        _DeviceArgs(dev_ids=[0])  # gt=0


# ---------------------------------------------------------------------------
# species_compose 的 schema 与时间标签校验
# ---------------------------------------------------------------------------

def test_species_compose_schema_shape():
    schema = species_compose.args_schema
    assert list(schema.model_fields) == [
        "start_time",
        "end_time",
        "time_unit",
        "province",
        "city",
        "district",
        "street",
        "species",
        "sex",
        "dev_ids",
    ]
    assert schema.model_config.get("extra") == "forbid"


def test_species_compose_rejects_undeclared_and_bad_enum():
    schema = species_compose.args_schema
    base = {"start_time": date(2026, 1, 1), "end_time": date(2026, 1, 3)}
    with pytest.raises(ValidationError):
        schema.model_validate({**base, "oops": 1})
    with pytest.raises(ValidationError):
        schema.model_validate({**base, "species": "白纹"})  # 不在闭集
    with pytest.raises(ValidationError):
        schema.model_validate({**base, "sex": "both"})
    with pytest.raises(ValidationError):
        schema.model_validate({**base, "time_unit": "quarter"})


def test_point_order_day():
    assert _point_order("day", "2026-01-02", date(2026, 1, 1), date(2026, 1, 3)) == date(2026, 1, 2).toordinal()
    with pytest.raises(ValueError):
        _point_order("day", "2026-02-10", date(2026, 1, 1), date(2026, 1, 31))


def test_point_order_hour():
    assert _point_order("hour", "23", date(2026, 1, 1), date(2026, 1, 3)) == 23
    assert _point_order("hour", "0", date(2026, 1, 1), date(2026, 1, 3)) == 0
    with pytest.raises(ValueError):
        _point_order("hour", "24", date(2026, 1, 1), date(2026, 1, 3))


def test_point_order_week_month_year():
    assert _point_order("week", "2026-02", date(2026, 1, 1), date(2026, 1, 31)) == date.fromisocalendar(2026, 2, 1).toordinal()
    assert _point_order("month", "2026-01", date(2026, 1, 1), date(2026, 1, 31)) == "2026-01"
    assert _point_order("year", "2026", date(2026, 1, 1), date(2026, 12, 31)) == "2026"
    with pytest.raises(ValueError):
        _point_order("month", "2026-13", date(2026, 1, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        _point_order("year", "2027", date(2026, 1, 1), date(2026, 12, 31))
