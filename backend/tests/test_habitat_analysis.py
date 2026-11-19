"""生境分析四种分组：只整理展示，不重算接口统计或补零。"""

import copy
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.tools.common import ToolDataError, ToolInputError
from core.tools.report.habitat_analysis import habitat_analysis


BASE_ARGS = {"start_time": "2026-01-01", "end_time": "2026-01-03"}


def row(habitat="park", species=None, day=None, count=100, average=10, ratio=25):
    return {
        "habitat_code": habitat, "habitat_name": {"park": "公园", "hospital": "医院"}[habitat],
        "bucket_start": f"2026-01-{day:02}T00:00:00+08:00" if day else None,
        "bucket_end": f"2026-01-{day + 1:02}T00:00:00+08:00" if day else None,
        "mosquito_code": species,
        "mosquito_name": {"aedes": "白纹伊蚊", "culex": "致倦库蚊"}.get(species),
        "monitor_count": count, "valid_device_count": 10,
        "avg_monitor_count": average, "ratio": ratio,
    }


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.get.return_value = {"data": {"list": [{
        "code": "440000", "name": "广东省", "level": "province", "children": [{
            "code": "440300", "name": "深圳市", "level": "city", "children": [],
        }],
    }]}}
    client.post.side_effect = lambda path, json: {
        "code": 200, "message": "成功", "data": {
            "contract_version": "agent-data-v1", "rows": [row(), row("hospital", count=50, ratio=12.5)],
        },
    }
    monkeypatch.setattr("core.tools.report.habitat_analysis.get_company_client", lambda: client)
    monkeypatch.setattr("core.tools.common.regions.get_company_client", lambda: client)
    return client


def call_tool(**args):
    message = habitat_analysis.invoke({
        "type": "tool_call", "id": "call-habitat", "name": "habitat_analysis",
        "args": {**BASE_ARGS, **args},
    })
    return json.loads(message.content), message.artifact


def set_rows(client, rows, **metadata):
    client.post.side_effect = lambda path, json: {
        "code": 200, "message": "成功",
        "data": {"contract_version": "agent-data-v1", "rows": copy.deepcopy(rows), **metadata},
    }


def test_habitat_summary_posts_codes_and_returns_original_ratios(client):
    result, artifact = call_tool(
        city="深圳市", habitat_codes=["park"], mosquito_codes=["aedes"],
        sexes=[2], dev_ids=[101], tenant_ids=[1],
        environment_filters=[{"sensor_type": "temp", "lower": 20, "upper": 30}],
    )
    client.post.assert_called_once()
    assert client.post.call_args.args == ("/api/v1/statistics/habitat-analysis",)
    query = client.post.call_args.kwargs["json"]
    assert query["start_time"] == "2026-01-01T00:00:00+08:00"
    assert query["end_time"] == "2026-01-04T00:00:00+08:00"
    assert query["filters"]["region_codes"] == ["440300"]
    assert query["filters"]["habitat_codes"] == ["park"]
    assert query["filters"]["mosquito_codes"] == ["aedes"]
    assert query["filters"]["environment_filters"][0]["upper_inclusive"] is False
    assert "time_unit" not in query
    assert result["rows"][0]["ratio"] == 25
    assert artifact["data"]["option"]["series"][0]["data"] == [100, 50]
    assert artifact["data"]["summary_metric"]["value"] == 150
    assert result["total"] is None
    assert result["has_more"] is None


def test_species_composition_uses_multiple_series_and_does_not_fill_missing_group(client):
    rows = [row(species="aedes", count=80), row(species="culex", count=20),
            row("hospital", species="aedes", count=30)]
    set_rows(client, rows)
    result, artifact = call_tool(group_by="habitat_species")
    option = artifact["data"]["option"]
    assert option["xAxis"]["data"] == ["公园", "医院"]
    assert [series["name"] for series in option["series"]] == ["白纹伊蚊", "致倦库蚊"]
    assert [series["data"] for series in option["series"]] == [[80, 30], [20, None]]
    assert artifact["data"]["dimension"]["count"] == 2
    assert result["rows"] == rows
    assert "该生境" in result["ratio_scope"]


def test_habitat_trend_sorts_time_axis_and_omits_ranking_parameters(client):
    set_rows(client, [row(day=3, count=30), row("hospital", day=1, count=50), row(day=1, count=10)])
    result, artifact = call_tool(group_by="time_habitat", time_unit="day")
    query = client.post.call_args.kwargs["json"]
    for key in ("sort_by", "sort_order", "top_n", "page", "page_size", "include_zero"):
        assert key not in query
    option = artifact["data"]["option"]
    assert option["xAxis"]["data"] == ["2026-01-01", "2026-01-03"]
    assert [series["data"] for series in option["series"]] == [[10, 30], [50, None]]
    assert all(series["type"] == "line" and series["connectNulls"] is False for series in option["series"])
    assert result["chart_type"] == "line"
    assert result["page"] is None


def test_habitat_species_trend_series_key_contains_both_codes(client):
    set_rows(client, [row(species="aedes", day=1, count=80), row("hospital", species="aedes", day=1, count=30),
                      row(species="culex", day=2, count=20)])
    _, artifact = call_tool(group_by="time_habitat_species", time_unit="day")
    series = artifact["data"]["option"]["series"]
    assert [item["name"] for item in series] == ["公园 · 白纹伊蚊", "医院 · 白纹伊蚊", "公园 · 致倦库蚊"]
    assert [item["data"] for item in series] == [[80, None], [30, None], [None, 20]]


def test_average_metric_is_not_summed_or_recalculated(client):
    # 保留后端平均量，不要求客户端通过明细重新证明其统计过程。
    set_rows(client, [row(average=1.25), row("hospital", count=50, average=None)])
    result, artifact = call_tool(metric="avg_monitor_count", sort_order="asc")
    assert client.post.call_args.kwargs["json"]["sort_by"] == "avg_monitor_count"
    assert artifact["data"]["option"]["series"][0]["data"] == [1.25, None]
    assert artifact["data"]["summary_metric"]["value"] == 150
    assert result["metric"] == "设备平均监测量"


def test_summary_pagination_and_top_n_are_done_by_backend(client):
    set_rows(client, [row()], total=4, applied_query={})
    result, artifact = call_tool(top_n=4, page=2, page_size=1)
    assert client.post.call_args.kwargs["json"]["top_n"] == 4
    assert result["total"] == 4
    assert result["has_more"] is True
    assert result["query"]["page"] == 2
    assert artifact is not None
    client.post.assert_called_once()


def test_empty_response_does_not_become_zero_or_fake_chart(client):
    set_rows(client, [])
    result, artifact = call_tool()
    assert result["returned_monitor_count"] is None
    assert result["rows"] == []
    assert result["chart_generated"] is False
    assert artifact is None


@pytest.mark.parametrize("args", [
    {"group_by": "time_habitat"},
    {"group_by": "habitat", "time_unit": "day"},
    {"group_by": "time_habitat", "time_unit": "day", "top_n": 10},
    {"group_by": "time_habitat", "time_unit": "day", "sort_order": "desc"},
    {"group_by": "time_habitat", "time_unit": "day", "page": 1},
    {"chart_type": "line"}, {"start_time": "2026-02-01"},
    {"habitat_codes": [" "]}, {"mosquito_codes": ["a", "a"]},
])
def test_conflicting_or_invalid_parameters_are_rejected_before_network(client, args):
    with pytest.raises(ToolInputError):
        call_tool(**args)
    client.get.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.parametrize("change", [
    {"contract_version": "old"}, {"rows": None}, {"rows": [{"habitat_name": "公园"}]},
])
def test_invalid_response_structure_is_rejected(client, change):
    set_rows(client, [], **{key: value for key, value in change.items() if key != "rows"})
    if "rows" in change:
        set_rows(client, change["rows"])
    with pytest.raises(ToolDataError):
        call_tool()


def test_duplicate_groups_are_not_silently_overwritten(client):
    set_rows(client, [row(), row()])
    with pytest.raises(ToolDataError):
        call_tool()


@pytest.mark.parametrize("args", [{"group_by": "other"}, {"time_unit": "year"}, {"chart_type": "pie"},
                                  {"habitat_codes": []}, {"species": "白纹伊蚊"}])
def test_schema_rejects_invalid_and_old_arguments(args):
    with pytest.raises(ValidationError):
        habitat_analysis.args_schema.model_validate({**BASE_ARGS, **args})


def test_tool_registration_replaces_habitat_ratio():
    from core.tools.tools import get_available_tools

    names = [tool.name for tool in get_available_tools()]
    assert "habitat_analysis" in names
    assert "habitat_ratio" not in names
