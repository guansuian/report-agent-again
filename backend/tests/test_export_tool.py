"""导出工具：通过真实 ToolNode 验证产物选择、原查询复用和文件产物。"""

import asyncio
import copy
import json
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from core.agents.thread_state import ThreadState
from core.clients import CompanyClientError
from core.tools.common import ToolDataError, ToolInputError
from core.tools.export.create_export import create_export


@pytest.fixture
def company_client(monkeypatch):
    client = Mock()
    client.post.return_value = {
        "code": 200,
        "data": {
            "export_id": "exp_001", "status": "queued", "format": "xlsx",
            "file_name": "广东省区域分析.xlsx",
        },
    }
    monkeypatch.setattr("core.tools.export.create_export.get_company_client", lambda: client)
    return client


@pytest.fixture
def report_state():
    query = {
        "contract_version": "agent-data-v1",
        "start_time": "2026-11-01T00:00:00+08:00",
        "end_time": "2026-11-21T00:00:00+08:00",
        "filters": {"region_codes": ["440000"], "mosquito_codes": ["mosquito-1"], "sexes": [2]},
        "region_level": "city", "sort_by": "avg_monitor_count", "sort_order": "asc",
        "include_zero": False, "top_n": 10, "page": 2, "page_size": 5,
    }
    selected = {
        "handle": "art_001", "tool_name": "region_analysis", "tool_call_id": "report-call-1",
        "artifact_type": "chart", "display_name": "广东省区域分析",
        "data": {"query": query}, "created_at": "2026-11-24T00:00:00+00:00",
    }
    later = copy.deepcopy(selected)
    later["handle"] = "art_002"
    later["tool_call_id"] = "report-call-2"
    later["data"]["query"]["filters"]["region_codes"] = ["440300"]
    return {"tool_artifacts": [selected, later], "tool_artifact_processed": []}


def run_export(state, args=None, *, asynchronous=False):
    """由 ToolNode 注入 runtime，测试时也不手动给工具拼运行信息。"""
    if args is None:
        args = {"artifact_handle": "art_001"}
    graph_input = dict(state)
    graph_input["messages"] = [AIMessage(content="", tool_calls=[{
        "name": "create_export", "id": "export-call-1", "args": args, "type": "tool_call",
    }])]
    builder = StateGraph(ThreadState)
    builder.add_node("tools", ToolNode([create_export]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    if asynchronous:
        return asyncio.run(graph.ainvoke(graph_input))
    return graph.invoke(graph_input)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_selected_artifact_uses_complete_original_query(company_client, report_state, asynchronous):
    before = copy.deepcopy(report_state)
    result = run_export(report_state, asynchronous=asynchronous)
    query = before["tool_artifacts"][0]["data"]["query"]
    company_client.post.assert_called_once_with("/api/v1/agent/exports", json={
        "contract_version": "agent-data-v1", "source": "region_analysis",
        "query": query, "format": "xlsx",
    })
    message = result["messages"][-1]
    assert isinstance(message, ToolMessage)
    assert message.status == "success" and message.tool_call_id == "export-call-1"
    assert message.artifact == {
        "artifact_type": "export", "display_name": "广东省区域分析.xlsx",
        "data": {
            "export_id": "exp_001", "status": "queued", "format": "xlsx",
            "file_name": "广东省区域分析.xlsx", "artifact_handle": "art_001", "source": "region_analysis",
        },
    }
    assert json.loads(message.content)["export_id"] == "exp_001"
    assert report_state == before
    assert result["tool_artifacts"] == before["tool_artifacts"]
    company_client.get.assert_not_called()


def test_posted_query_does_not_share_nested_data_with_state(company_client, report_state):
    before = copy.deepcopy(report_state)
    run_export(report_state)
    posted_query = company_client.post.call_args.kwargs["json"]["query"]
    posted_query["filters"]["sexes"].append(1)
    assert report_state == before


@pytest.mark.parametrize("source", ["region_analysis", "habitat_analysis", "environment_distribution"])
def test_supported_artifact_source_is_sent_to_company(company_client, report_state, source):
    report_state["tool_artifacts"][0]["tool_name"] = source
    run_export(report_state)
    assert company_client.post.call_args.kwargs["json"]["source"] == source


def test_csv_and_nonlatest_artifact_can_be_selected(company_client, report_state):
    company_client.post.return_value["data"].update(format="csv", file_name="区域分析.csv")
    result = run_export(report_state, {"artifact_handle": "art_001", "format": "csv"})
    assert company_client.post.call_args.kwargs["json"]["format"] == "csv"
    assert result["messages"][-1].artifact["data"]["format"] == "csv"


@pytest.mark.parametrize("handle", ["art_missing", "report-call-1", "exp_001"])
def test_unknown_or_wrong_kind_of_id_does_not_create_task(company_client, report_state, handle):
    with pytest.raises(ToolInputError, match="真实编号"):
        run_export(report_state, {"artifact_handle": handle})
    company_client.post.assert_not_called()


def test_unsupported_old_source_is_not_converted(company_client, report_state):
    report_state["tool_artifacts"][0]["tool_name"] = "species_trend"
    with pytest.raises(ToolInputError, match="暂不支持导出"):
        run_export(report_state)
    company_client.post.assert_not_called()


@pytest.mark.parametrize("query", [None, {}, {"contract_version": "old"}])
def test_missing_original_query_does_not_create_task(company_client, report_state, query):
    report_state["tool_artifacts"][0]["data"]["query"] = query
    with pytest.raises(ToolDataError, match="原始查询条件"):
        run_export(report_state)
    company_client.post.assert_not_called()


@pytest.mark.parametrize("status,description", [
    ("queued", "排队"), ("processing", "正在生成"), ("ready", "已就绪"),
    ("failed", "已失败"), ("expired", "已过期"),
])
def test_task_status_is_not_misreported_as_success(company_client, report_state, status, description):
    company_client.post.return_value["data"]["status"] = status
    message = run_export(report_state)["messages"][-1]
    assert message.artifact["data"]["status"] == status
    assert description in json.loads(message.content)["message"]
    company_client.post.assert_called_once()
    company_client.get.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("export_id", "../outside"), ("status", "unknown"),
    ("format", "pdf"), ("file_name", "x\r\nheader"),
])
def test_invalid_company_result_does_not_produce_file_artifact(company_client, report_state, field, value):
    company_client.post.return_value["data"][field] = value
    with pytest.raises(ToolDataError):
        run_export(report_state)
    company_client.post.assert_called_once()


def test_query_echo_does_not_override_original_query(company_client, report_state):
    company_client.post.return_value["data"]["applied_query"] = {"query": {"top_n": 1}}
    run_export(report_state)
    assert company_client.post.call_args.kwargs["json"]["query"]["top_n"] == 10


def test_company_failure_is_not_retried(company_client, report_state):
    company_client.post.side_effect = CompanyClientError("模拟请求超时。")
    with pytest.raises(CompanyClientError, match="超时"):
        run_export(report_state)
    company_client.post.assert_called_once()
    company_client.get.assert_not_called()


def test_model_only_supplies_handle_and_file_format():
    schema = create_export.tool_call_schema
    assert set(schema.model_json_schema()["properties"]) == {"artifact_handle", "format"}
    for args in ({"artifact_handle": ""}, {"artifact_handle": "art_001", "format": "pdf"}):
        with pytest.raises(ValidationError):
            schema.model_validate(args)


def test_export_tool_is_registered_once_with_artifact_response():
    from core.tools.tools import get_available_tools

    assert get_available_tools().count(create_export) == 1
    assert create_export.response_format == "content_and_artifact"
