"""环境相关性：请求口径、有效结果和无法计算的情况。"""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.tools.common import ToolDataError, ToolInputError
from core.tools.report.environment_correlation import environment_correlation


BASE_ARGS = {"start_time": "2026-11-01", "end_time": "2026-11-18", "sensor_type": "temp"}


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.get.return_value = {"data": {"list": [{
        "code": "440000", "name": "广东省", "level": "province", "children": [{
            "code": "440300", "name": "深圳市", "level": "city", "children": [],
        }],
    }]}}
    client.post.return_value = {"code": 200, "message": "成功", "data": {
        "contract_version": "agent-data-v1", "method": "pearson", "pair_count": 20,
        "coefficient": 0.75, "reason": None,
    }}
    monkeypatch.setattr("core.tools.report.environment_correlation.get_company_client", lambda: client)
    monkeypatch.setattr("core.tools.common.regions.get_company_client", lambda: client)
    return client


def call_tool(**args):
    return json.loads(environment_correlation.invoke({**BASE_ARGS, **args}))


def test_request_converts_dates_and_sends_mean_range_separately_from_filters(client):
    result = call_tool(city="深圳市", time_unit="day", habitat_codes=["park"],
                       mosquito_codes=["aedes"], sexes=[2], dev_ids=[101], tenant_ids=[1],
                       sensor_range={"lower": 20, "upper": 30})
    client.post.assert_called_once_with("/api/v1/statistics/environment/correlation", json={
        "contract_version": "agent-data-v1",
        "start_time": "2026-11-01T00:00:00+08:00", "end_time": "2026-11-19T00:00:00+08:00",
        "sensor_type": "temp", "time_unit": "day",
        "filters": {"region_codes": ["440300"], "habitat_codes": ["park"],
                    "mosquito_codes": ["aedes"], "sexes": [2], "dev_ids": [101], "tenant_ids": [1]},
        "sensor_range": {"lower": 20, "upper": 30, "lower_inclusive": True, "upper_inclusive": False},
    })
    assert result["query"] == client.post.call_args.kwargs["json"]
    assert result["coefficient"] == 0.75
    assert "相关不代表因果" in result["note"]


def test_default_hour_and_no_implicit_range_or_filters(client):
    result = call_tool()
    assert result["query"]["time_unit"] == "hour"
    assert result["query"]["filters"] == {}
    assert "sensor_range" not in result["query"]
    client.get.assert_not_called()


@pytest.mark.parametrize("sensor_type", ["temp", "hum", "wind", "co2", "light"])
def test_all_documented_sensors(client, sensor_type):
    assert call_tool(sensor_type=sensor_type)["query"]["sensor_type"] == sensor_type


@pytest.mark.parametrize("coefficient", [-1, -0.4, 0, 1])
def test_signed_and_zero_coefficients_are_preserved(client, coefficient):
    client.post.return_value["data"]["coefficient"] = coefficient
    assert call_tool()["coefficient"] == coefficient


@pytest.mark.parametrize("pair_count,reason", [
    (0, "NO_DATA"), (2, "INSUFFICIENT_PAIRS"), (20, "ZERO_VARIANCE"), (5, "其他数据质量原因"),
])
def test_null_and_reasons_are_preserved_without_recomputation(client, pair_count, reason):
    client.post.return_value["data"].update(pair_count=pair_count, coefficient=None, reason=reason)
    result = call_tool()
    assert result["coefficient"] is None
    assert result["reason"] == reason
    assert result["pair_count"] == pair_count
    client.post.assert_called_once()


def test_one_sided_mean_range_preserves_explicit_boundary(client):
    result = call_tool(sensor_range={"lower": None, "upper": 30, "upper_inclusive": True})
    assert result["query"]["sensor_range"] == {
        "lower": None, "upper": 30, "lower_inclusive": True, "upper_inclusive": True,
    }


def test_echo_is_informational_and_tool_returns_no_artifact(client):
    client.post.return_value["data"]["applied_query"] = {"time_unit": "other"}
    message = environment_correlation.invoke({
        "type": "tool_call", "id": "correlation-1", "name": "environment_correlation", "args": BASE_ARGS,
    })
    assert message.artifact is None
    assert json.loads(message.content)["applied_query"] == {"time_unit": "other"}


@pytest.mark.parametrize("args", [
    {"sensor_type": "pressure"}, {"time_unit": "week"}, {"environment_filters": []},
    {"chart_type": "scatter"}, {"sensor_range": {"lower": 30, "upper": 20}},
    {"sensor_range": {"lower": 20, "upper": 20}}, {"sensor_range": {"lower": float("inf")}},
    {"sensor_range": {"sensor_type": "hum"}}, {"habitat_codes": []},
])
def test_invalid_schema_does_not_reach_endpoint(client, args):
    with pytest.raises(ValidationError):
        call_tool(**args)
    client.post.assert_not_called()


@pytest.mark.parametrize("args", [
    {"start_time": "2026-11-19"}, {"city": "深圳"}, {"habitat_codes": [" "]},
    {"mosquito_codes": ["aedes", "aedes"]}, {"sexes": [2, 2]},
    {"dev_ids": [1, 1]}, {"tenant_ids": [1, 1]},
])
def test_invalid_dates_and_filters_do_not_reach_endpoint(client, args):
    with pytest.raises(ToolInputError):
        call_tool(**args)
    client.post.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("method", "spearman"), ("pair_count", -1), ("pair_count", True),
    ("coefficient", 1.1), ("coefficient", -1.1), ("coefficient", True),
    ("coefficient", "0.75"), ("coefficient", float("nan")),
    ("coefficient", float("inf")), ("reason", {}),
])
def test_invalid_response_is_not_reported_as_a_correlation(client, field, value):
    client.post.return_value["data"][field] = value
    with pytest.raises(ToolDataError):
        call_tool()


@pytest.mark.parametrize("field", ["method", "pair_count", "coefficient", "reason"])
def test_missing_response_field_is_not_defaulted(client, field):
    del client.post.return_value["data"][field]
    with pytest.raises(ToolDataError):
        call_tool()


def test_too_few_pairs_cannot_have_a_numeric_coefficient(client):
    client.post.return_value["data"]["pair_count"] = 2
    with pytest.raises(ToolDataError):
        call_tool()


def test_registered_and_content_only():
    from core.tools.tools import get_available_tools

    assert environment_correlation in get_available_tools()
    assert environment_correlation.response_format == "content"
