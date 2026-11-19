"""区域分析：编码筛选、统计口径、分页和图表使用同一份数据。"""

import copy
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.tools.common import ToolDataError, ToolInputError, check_regions
from core.tools.common.validate import get_region_code
from core.tools.report.region_analysis import region_analysis


REGION_TREE = [{
    "code": "440000", "level": "province", "name": "广东省", "children": [
        {"code": "440300", "level": "city", "name": "深圳市", "children": [
            {"code": "440305", "level": "district", "name": "南山区", "children": []},
        ]},
    ],
}, {
    "code": "110000", "level": "province", "name": "北京市", "children": [
        {"code": "110105", "level": "district", "name": "朝阳区", "children": []},
    ],
}]
BASE_ARGS = {"start_time": "2026-01-01", "end_time": "2026-01-31", "region_level": "city"}


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.get.return_value = {"data": {"list": copy.deepcopy(REGION_TREE)}}
    client.post.side_effect = lambda path, json: {
        "code": 200, "message": "成功", "data": {
            "contract_version": "agent-data-v1", "applied_query": copy.deepcopy(json),
            "total": 2, "scope_monitor_count": 200,
            "regions": [
                {"region_code": "440300", "region_name": "深圳市", "monitor_count": 100,
                 "valid_device_count": 10, "avg_monitor_count": 10, "ratio": 50,
                 "rank": 1, "data_status": "complete"},
                {"region_code": "440100", "region_name": "广州市", "monitor_count": 20,
                 "valid_device_count": 2, "avg_monitor_count": 10, "ratio": 10,
                 "rank": 2, "data_status": "complete"},
            ],
        },
    }
    monkeypatch.setattr("core.tools.report.region_analysis.get_company_client", lambda: client)
    monkeypatch.setattr("core.tools.common.regions.get_company_client", lambda: client)
    return client


def call_tool(**args):
    message = region_analysis.invoke({
        "type": "tool_call", "id": "call-region", "name": "region_analysis",
        "args": {**BASE_ARGS, **args},
    })
    return json.loads(message.content), message.artifact


def set_response(client, **changes):
    response = client.post("", json={})
    response["data"].update(changes)
    client.reset_mock(side_effect=True)

    def post(path, json):
        result = copy.deepcopy(response)
        if "applied_query" not in changes:
            result["data"]["applied_query"] = copy.deepcopy(json)
        return result

    client.post.side_effect = post
    return response


@pytest.mark.parametrize("names,expected", [
    ({"province": "广东省"}, "440000"),
    ({"city": "深圳市"}, "440300"),
    ({"district": "南山区"}, "440305"),
    ({"province": "北京市", "city": "北京市", "district": "朝阳区"}, "110105"),
])
def test_region_code_comes_from_deepest_validated_node(names, expected):
    resolved = check_regions(REGION_TREE, **names)
    assert get_region_code(REGION_TREE, resolved) == expected
    assert "code" not in resolved


def test_names_remain_exact_and_invalid_region_never_reaches_statistics(client):
    with pytest.raises(ToolInputError):
        call_tool(city="深圳")
    client.post.assert_not_called()


def test_post_preserves_filters_and_converts_inclusive_date_range(client):
    result, artifact = call_tool(
        province="广东省", habitat_codes=["habitat:park"], mosquito_codes=["mosquito:albopictus"],
        sexes=[2], dev_ids=[101, 102], tenant_ids=[1],
        environment_filters=[{"sensor_type": "temp", "lower": 20, "upper": 30}],
    )
    body = client.post.call_args.kwargs["json"]
    assert client.post.call_args.args == ("/api/v1/statistics/region-analysis",)
    assert body["start_time"] == "2026-01-01T00:00:00+08:00"
    assert body["end_time"] == "2026-02-01T00:00:00+08:00"
    assert body["filters"] == {
        "region_codes": ["440000"], "habitat_codes": ["habitat:park"],
        "mosquito_codes": ["mosquito:albopictus"], "sexes": [2],
        "dev_ids": [101, 102], "tenant_ids": [1],
        "environment_filters": [{"sensor_type": "temp", "lower": 20, "upper": 30,
                                 "lower_inclusive": True, "upper_inclusive": False}],
    }
    assert result["scope_monitor_count"] == 200
    assert result["page_monitor_count"] == 120
    assert artifact["data"]["summary_metric"] == {"label": "本页监测数量合计", "value": 120}
    assert artifact["data"]["rows"][0]["ratio"] == 50


def test_without_region_does_not_fetch_tree(client):
    call_tool()
    client.get.assert_not_called()
    assert client.post.call_args.kwargs["json"]["filters"] == {}


def test_average_sort_chart_uses_average_but_summary_uses_monitor_count(client):
    response = set_response(client)
    response["data"]["regions"][0].update(valid_device_count=20, avg_monitor_count=5)
    response["data"]["regions"][1].update(valid_device_count=1, avg_monitor_count=20)
    result, artifact = call_tool(sort_by="avg_monitor_count", sort_order="asc")
    assert result["metric"] == "设备平均监测量"
    assert artifact["data"]["option"]["series"][0]["data"] == [5, 20]
    assert artifact["data"]["summary_metric"]["value"] == 120
    assert "升序" in artifact["display_name"]


def test_no_records_average_stays_null_and_is_last(client):
    response = set_response(client)
    response["data"]["regions"][1].update(
        monitor_count=0, valid_device_count=0, avg_monitor_count=None, ratio=0, data_status="no_records",
    )
    result, artifact = call_tool(sort_by="avg_monitor_count", include_zero=True)
    assert result["regions"][1]["avg_monitor_count"] is None
    assert artifact["data"]["option"]["series"][0]["data"] == [10, None]


def test_top_zero_and_pagination_do_not_fetch_every_page(client):
    set_response(client, total=5)
    result, artifact = call_tool(top_n=0, page=2, page_size=2)
    client.post.assert_called_once()
    assert result["has_more"] is True
    assert result["total"] == 5
    assert result["returned_count"] == 2
    assert "前0" not in artifact["display_name"]


def test_empty_page_keeps_scope_total_without_generating_chart(client):
    set_response(client, regions=[], total=2)
    result, artifact = call_tool(page=4)
    assert result["total"] == 2
    assert result["scope_monitor_count"] == 200
    assert result["page_monitor_count"] is None
    assert "当前页" in result["message"]
    assert artifact is None


@pytest.mark.parametrize("change", [
    {"contract_version": "old"}, {"total": -1},
    {"scope_monitor_count": None}, {"regions": None},
])
def test_invalid_protocol_or_data_is_rejected(client, change):
    set_response(client, **change)
    with pytest.raises(ToolDataError):
        call_tool()


@pytest.mark.parametrize("applied", [{}, None, {"include_zero": True}, {"include_zero": "false"}])
def test_echo_does_not_override_request_or_block_report(client, applied):
    set_response(client, applied_query=applied)
    result, artifact = call_tool(include_zero=False)
    assert result["query"] == client.post.call_args.kwargs["json"]
    assert result["query"]["include_zero"] is False
    assert result["applied_query"] == applied
    assert artifact["data"]["query"]["include_zero"] is False
    assert {"label": "包含无记录地区", "value": "否"} in artifact["data"]["conditions"]


@pytest.mark.parametrize("missing", ["include_zero", "applied_query"])
def test_missing_echo_field_does_not_block_report(client, missing):
    original_post = client.post.side_effect

    def post(path, json):
        response = original_post(path, json)
        if missing == "applied_query":
            del response["data"]["applied_query"]
        else:
            del response["data"]["applied_query"]["include_zero"]
        return response

    client.post.side_effect = post
    result, artifact = call_tool()
    assert result["query"]["include_zero"] is False
    assert artifact is not None


@pytest.mark.parametrize("row_change", [
    {"ratio": 83.33}, {"avg_monitor_count": None}, {"valid_device_count": 0},
    {"region_code": ""}, {"monitor_count": True},
])
def test_invalid_row_does_not_change_statistics_or_become_zero(client, row_change):
    response = set_response(client)
    response["data"]["regions"][0].update(row_change)
    with pytest.raises(ToolDataError):
        call_tool()


def test_wrong_sort_order_is_not_silently_fixed(client):
    with pytest.raises(ToolDataError):
        call_tool(sort_order="asc")


@pytest.mark.parametrize("args", [
    {"habitat_codes": [" "]}, {"mosquito_codes": ["x", "x"]},
    {"sexes": [2, 2]}, {"dev_ids": [1, 1]},
    {"start_time": "2026-02-01", "end_time": "2026-01-01"},
])
def test_invalid_input_is_rejected_before_network(client, args):
    with pytest.raises(ToolInputError):
        call_tool(**args)
    client.get.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.parametrize("args", [
    {"region_level": "device"}, {"top_n": -1}, {"page": 0}, {"page_size": 201},
    {"species": ["白纹伊蚊"]}, {"sexes": [999]},
    {"environment_filters": [{"sensor_type": "temp", "lower": 30, "upper": 20}]},
])
def test_schema_rejects_invalid_or_old_parameters(args):
    with pytest.raises(ValidationError):
        region_analysis.args_schema.model_validate({**BASE_ARGS, **args})


def test_registration_replaces_old_tool():
    from core.tools.tools import get_available_tools

    names = [tool.name for tool in get_available_tools()]
    assert "region_analysis" in names
    assert "region_rank" not in names
