"""周期比较：两期共享筛选、保留零值和不可计算原因，只返回内容。"""

import copy
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.tools.common import ToolDataError, ToolInputError
from core.tools.report.period_compare import period_compare


BASE_ARGS = {
    "current_period": {"start_time": "2026-11-12", "end_time": "2026-11-18"},
    "previous_period": {"start_time": "2026-11-05", "end_time": "2026-11-11"},
}


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.get.return_value = {"data": {"list": [{
        "code": "440000", "name": "广东省", "level": "province", "children": [{
            "code": "440300", "name": "深圳市", "level": "city", "children": [],
        }],
    }]}}
    client.post.return_value = {
        "code": 200, "message": "成功", "data": {
            "contract_version": "agent-data-v1",
            "current_monitor_count": 1000, "previous_monitor_count": 800,
            "difference": 200, "growth_rate": 25, "equal_duration": True, "growth_reason": None,
        },
    }
    monkeypatch.setattr("core.tools.report.period_compare.get_company_client", lambda: client)
    monkeypatch.setattr("core.tools.common.regions.get_company_client", lambda: client)
    return client


def call_tool(**args):
    return json.loads(period_compare.invoke({**BASE_ARGS, **args}))


def test_one_post_compares_two_explicit_periods_with_same_filters(client):
    result = call_tool(city="深圳市", habitat_codes=["park"], mosquito_codes=["aedes"],
                       sexes=[2], dev_ids=[101], tenant_ids=[1],
                       environment_filters=[{"sensor_type": "temp", "lower": 20, "upper": 30}])
    client.post.assert_called_once_with("/api/v1/statistics/period-compare", json={
        "contract_version": "agent-data-v1",
        "current_period": {"start_time": "2026-11-12T00:00:00+08:00", "end_time": "2026-11-19T00:00:00+08:00"},
        "previous_period": {"start_time": "2026-11-05T00:00:00+08:00", "end_time": "2026-11-12T00:00:00+08:00"},
        "filters": {"region_codes": ["440300"], "habitat_codes": ["park"], "mosquito_codes": ["aedes"],
                    "sexes": [2], "dev_ids": [101], "tenant_ids": [1],
                    "environment_filters": [{"sensor_type": "temp", "lower": 20, "upper": 30,
                                             "lower_inclusive": True, "upper_inclusive": False}]},
    })
    assert result["difference"] == 200
    assert result["growth_rate"] == 25
    assert result["query"] == client.post.call_args.kwargs["json"]


@pytest.mark.parametrize("values", [
    (600, 800, -200, -25, None),
    (800, 800, 0, 0, None),
    (1000, 0, 1000, None, "PREVIOUS_PERIOD_ZERO"),
    (0, 0, 0, None, "PREVIOUS_PERIOD_ZERO"),
    (None, 800, None, None, "NO_RECORDS"),
    (0, 800, None, None, "NO_RECORDS"),
])
def test_result_is_preserved_without_recomputing_or_replacing_null(client, values):
    fields = ("current_monitor_count", "previous_monitor_count", "difference", "growth_rate", "growth_reason")
    client.post.return_value["data"].update(zip(fields, values))
    result = call_tool()
    assert tuple(result[field] for field in fields) == values
    client.get.assert_not_called()


def test_unequal_periods_are_not_adjusted_or_called_equal_duration_change(client):
    client.post.return_value["data"]["equal_duration"] = False
    previous = {"start_time": "2026-11-01", "end_time": "2026-11-11"}
    result = call_tool(previous_period=previous)
    assert result["query"]["previous_period"]["start_time"] == "2026-11-01T00:00:00+08:00"
    assert result["equal_duration"] is False
    assert "不等长" in result["note"]


def test_echo_is_informational_and_no_artifact_is_generated(client):
    client.post.return_value["data"]["applied_query"] = {}
    message = period_compare.invoke({"type": "tool_call", "id": "compare-1", "name": "period_compare", "args": BASE_ARGS})
    assert message.artifact is None
    assert json.loads(message.content)["applied_query"] == {}


@pytest.mark.parametrize("args", [
    {"habitat_codes": [" "]}, {"mosquito_codes": ["x", "x"]},
    {"sexes": [2, 2]}, {"city": "深圳"},
])
def test_invalid_filters_do_not_reach_comparison_endpoint(client, args):
    with pytest.raises(ToolInputError):
        call_tool(**args)
    client.post.assert_not_called()


@pytest.mark.parametrize("args", [
    {"current_period": {"start_time": "2026-11-19", "end_time": "2026-11-18"}},
    {"previous_period": {"start_time": "2026-11-11"}},
    {"previous_period": None}, {"chart_type": "bar"}, {"sexes": [999]},
])
def test_schema_rejects_incomplete_or_invalid_periods_and_unsupported_arguments(args):
    with pytest.raises(ValidationError):
        period_compare.args_schema.model_validate({**BASE_ARGS, **args})


@pytest.mark.parametrize("field,value", [
    ("current_monitor_count", -1), ("previous_monitor_count", True),
    ("difference", "200"), ("growth_rate", float("nan")),
    ("equal_duration", "true"), ("growth_reason", {}),
])
def test_invalid_response_types_are_not_returned_as_business_values(client, field, value):
    client.post.return_value["data"][field] = value
    with pytest.raises(ToolDataError):
        call_tool()


def test_missing_result_is_not_reported_as_zero(client):
    del client.post.return_value["data"]["growth_rate"]
    with pytest.raises(ToolDataError):
        call_tool()


def test_comparison_period_does_not_get_invented_from_current_period():
    args = copy.deepcopy(BASE_ARGS)
    del args["previous_period"]
    with pytest.raises(ValidationError):
        period_compare.args_schema.model_validate(args)


def test_tool_is_registered_and_content_only():
    from core.tools.tools import get_available_tools

    assert period_compare in get_available_tools()
    assert period_compare.response_format == "content"
