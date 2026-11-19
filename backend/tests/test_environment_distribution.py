"""环境分布：边界规则、联合分组及原始统计在图表和分析中保持一致。"""

import copy
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.tools.common import ToolDataError, ToolInputError
from core.tools.report.environment_distribution import EnvironmentAxis, environment_distribution


TEMP_AXIS = {"sensor_type": "temp", "intervals": [
    {"id": "t1", "lower": 20, "upper": 25},
    {"id": "t2", "lower": 25, "upper": 30},
]}
HUM_AXIS = {"sensor_type": "hum", "intervals": [
    {"id": "h1", "lower": 60, "upper": 80},
    {"id": "h2", "lower": 80, "upper": 100},
]}
BASE_ARGS = {"start_time": "2026-01-01", "end_time": "2026-01-03", "axes": [TEMP_AXIS]}


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.get.return_value = {"data": {"list": [{
        "code": "440000", "name": "广东省", "level": "province", "children": [{
            "code": "440300", "name": "深圳市", "level": "city", "children": [],
        }],
    }]}}

    def post(path, json):
        axes = copy.deepcopy(json["axes"])
        axes[0]["unit"] = "℃"
        return {"code": 200, "message": "成功", "data": {
            "contract_version": "agent-data-v1", "axes": axes, "matched_monitor_count": 100,
            "groups": [
                {"interval_ids": ["t1"], "monitor_count": 80, "record_count": 8, "percentage": 80},
                {"interval_ids": ["t2"], "monitor_count": 20, "record_count": 2, "percentage": 20},
            ],
            "quality": {"matched_record_count": 10, "excluded_record_count": 3,
                        "reason": "2条缺少环境数据，1条未落入指定区间"},
        }}

    client.post.side_effect = post
    monkeypatch.setattr("core.tools.report.environment_distribution.get_company_client", lambda: client)
    monkeypatch.setattr("core.tools.common.regions.get_company_client", lambda: client)
    return client


def call_tool(**args):
    message = environment_distribution.invoke({
        "type": "tool_call", "id": "call-environment", "name": "environment_distribution",
        "args": {**BASE_ARGS, **args},
    })
    return json.loads(message.content), message.artifact


def set_response(client, **changes):
    original_post = client.post.side_effect

    def post(path, json):
        response = original_post(path, json)
        response["data"].update(copy.deepcopy(changes))
        return response

    client.post.side_effect = post


def test_post_passes_all_filters_and_half_open_dates(client):
    result, artifact = call_tool(
        city="深圳市", habitat_codes=["park"], mosquito_codes=["aedes"],
        sexes=[2], dev_ids=[101], tenant_ids=[1],
        environment_filters=[{"sensor_type": "wind", "upper": 5}],
    )
    query = client.post.call_args.kwargs["json"]
    assert client.post.call_args.args == ("/api/v1/statistics/environment/distribution",)
    assert query["start_time"] == "2026-01-01T00:00:00+08:00"
    assert query["end_time"] == "2026-01-04T00:00:00+08:00"
    assert query["filters"] == {
        "region_codes": ["440300"], "habitat_codes": ["park"], "mosquito_codes": ["aedes"],
        "sexes": [2], "dev_ids": [101], "tenant_ids": [1],
        "environment_filters": [{"sensor_type": "wind", "lower": None, "upper": 5,
                                 "lower_inclusive": True, "upper_inclusive": False}],
    }
    assert query["axes"][0]["intervals"][0]["upper_inclusive"] is False
    assert result["matched_monitor_count"] == 100
    assert result["groups"][0]["record_count"] == 8
    assert result["groups"][0]["percentage"] == 80
    assert result["quality"]["excluded_record_count"] == 3
    assert "缺少环境数据" in artifact["data"]["note"]
    assert artifact["data"]["option"]["series"][0]["data"] == [80, 20]
    assert artifact["data"]["summary_metric"]["value"] == 100


def test_one_axis_can_generate_pie_and_preserves_backend_percentage(client):
    set_response(client, groups=[
        {"interval_ids": ["t1"], "monitor_count": 80, "record_count": 8, "percentage": 79.99},
        {"interval_ids": ["t2"], "monitor_count": 20, "record_count": 2, "percentage": 20.01},
    ])
    result, artifact = call_tool(chart_type="pie")
    assert result["groups"][0]["percentage"] == 79.99
    assert artifact["data"]["rows"][1]["percentage"] == 20.01
    assert artifact["data"]["option"]["series"][0]["type"] == "pie"
    assert [item["value"] for item in artifact["data"]["option"]["series"][0]["data"]] == [80, 20]


def test_two_axes_produce_grouped_bars_and_leave_missing_combination_null(client):
    original_post = client.post.side_effect

    def post(path, json):
        response = original_post(path, json)
        response["data"]["axes"][1]["unit"] = "%"
        response["data"]["groups"] = [
            {"interval_ids": ["t2", "h1"], "monitor_count": 20, "record_count": 2, "percentage": 20},
            {"interval_ids": ["t1", "h2"], "monitor_count": 0, "record_count": 1, "percentage": 0},
            {"interval_ids": ["t1", "h1"], "monitor_count": 80, "record_count": 8, "percentage": 80},
        ]
        return response

    client.post.side_effect = post
    result, artifact = call_tool(axes=[TEMP_AXIS, HUM_AXIS])
    option = artifact["data"]["option"]
    assert [item["data"] for item in option["series"]] == [[80, 20], [0, None]]
    assert len(option["legend"]["data"]) == 2
    assert artifact["data"]["dimension"]["count"] == 2
    assert artifact["data"]["chart_types"] == ["bar"]
    assert result["groups"][0]["interval_ids"] == ["t2", "h1"]
    assert artifact["data"]["rows"][0]["axis_1"].endswith("%")


def test_unbounded_interval_labels_use_actual_unit_and_boundaries(client):
    axis = {"sensor_type": "temp", "intervals": [
        {"id": "t1", "lower": None, "upper": 20},
        {"id": "t2", "lower": 20, "upper": None, "lower_inclusive": False},
    ]}
    result, artifact = call_tool(axes=[axis])
    labels = artifact["data"]["option"]["xAxis"]["data"]
    assert labels[0].startswith("(-∞,")
    assert labels[1].startswith("(20") and "+∞)℃" in labels[1]
    assert result["axes"][0]["intervals"][0]["lower"] is None


@pytest.mark.parametrize("intervals", [
    [{"id": "a", "lower": 20, "upper": 30}, {"id": "b", "lower": 25, "upper": 35}],
    [{"id": "a", "lower": 20, "upper": 25, "upper_inclusive": True}, {"id": "b", "lower": 25, "upper": 30}],
    [{"id": "a", "lower": None, "upper": None}, {"id": "b", "lower": 20, "upper": 30}],
    [{"id": "same", "lower": 20, "upper": 25}, {"id": "same", "lower": 25, "upper": 30}],
    [{"id": "a", "lower": 30, "upper": 20}],
    [{"id": "a", "lower": 20, "upper": 20}],
])
def test_invalid_or_overlapping_intervals_are_rejected(intervals):
    with pytest.raises(ValidationError):
        EnvironmentAxis.model_validate({"sensor_type": "temp", "intervals": intervals})


def test_adjacent_open_boundaries_and_single_point_do_not_overlap():
    EnvironmentAxis.model_validate(TEMP_AXIS)
    EnvironmentAxis.model_validate({"sensor_type": "temp", "intervals": [
        {"id": "range", "lower": 20, "upper": 30, "lower_inclusive": False},
        {"id": "point", "lower": 20, "upper": 20, "upper_inclusive": True},
    ]})


@pytest.mark.parametrize("args", [
    {"axes": [TEMP_AXIS, TEMP_AXIS]}, {"axes": [TEMP_AXIS, HUM_AXIS], "chart_type": "pie"},
    {"start_time": "2026-02-01"}, {"city": "深圳"}, {"mosquito_codes": ["x", "x"]},
])
def test_invalid_inputs_do_not_reach_statistics(client, args):
    with pytest.raises(ToolInputError):
        call_tool(**args)
    client.post.assert_not_called()


@pytest.mark.parametrize("args", [{"axes": []}, {"axes": [TEMP_AXIS] * 3}, {"sensor_type": "temp"}])
def test_schema_rejects_invalid_or_old_arguments(args):
    with pytest.raises(ValidationError):
        environment_distribution.args_schema.model_validate({**BASE_ARGS, **args})


@pytest.mark.parametrize("changes", [
    {"groups": None}, {"matched_monitor_count": -1},
    {"groups": [{"interval_ids": ["unknown"], "monitor_count": 1, "record_count": 1, "percentage": 1}]},
    {"groups": [{"interval_ids": ["t1", "extra"], "monitor_count": 1, "record_count": 1, "percentage": 1}]},
])
def test_invalid_response_cannot_be_displayed_as_other_intervals(client, changes):
    set_response(client, **changes)
    with pytest.raises(ToolDataError):
        call_tool()


def test_empty_groups_do_not_generate_a_fake_chart(client):
    set_response(client, groups=[], matched_monitor_count=0)
    result, artifact = call_tool()
    assert result["groups"] == []
    assert result["chart_generated"] is False
    assert artifact is None


def test_zero_monitor_count_does_not_turn_missing_groups_into_zero(client):
    set_response(client, groups=[{"interval_ids": ["t1"], "monitor_count": 0, "record_count": 1, "percentage": None}],
                 matched_monitor_count=0)
    result, artifact = call_tool()
    assert result["groups"][0]["record_count"] == 1
    assert artifact["data"]["option"]["series"][0]["data"] == [0, None]
    assert artifact["data"]["chart_types"] == ["bar"]


def test_pie_keeps_missing_interval_null_when_switching_back_to_bar(client):
    set_response(client, groups=[{"interval_ids": ["t1"], "monitor_count": 100, "record_count": 10, "percentage": 100}])
    _, artifact = call_tool(chart_type="pie")
    points = artifact["data"]["option"]["series"][0]["data"]
    assert [point["value"] for point in points] == [100, None]
    assert len(points) == artifact["data"]["dimension"]["count"]


def test_tool_registration_replaces_fixed_analysis():
    from core.tools.tools import get_available_tools

    names = [tool.name for tool in get_available_tools()]
    assert "environment_distribution" in names
    assert "environment_fixed_analysis" not in names
