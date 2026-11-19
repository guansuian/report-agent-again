"""字典工具：查询条件不丢失，代码与节点结构保持接口原值。"""

import copy
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.tools.common import ToolDataError, ToolInputError
from core.tools.report.dictionaries import dictionaries


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.get.side_effect = lambda path, params: {
        "code": 200,
        "message": "成功",
        "data": {
            "contract_version": "agent-data-v1",
            "applied_query": dict(params),
            "version": "dictionary-v1",
            "total": 2,
            "items": [{
                "code": "habitat:park", "name": "公园", "parent_code": None,
                "level": "habitat", "aliases": [], "has_children": False,
                "query_value": "公园",
            }],
        },
    }
    monkeypatch.setattr("core.tools.report.dictionaries.get_company_client", lambda: client)
    return client


def test_query_preserves_filters_and_pagination(client):
    result = json.loads(dictionaries.invoke({
        "keyword": "公园", "page": 2, "page_size": 1,
    }))
    client.get.assert_called_once_with("/api/v1/agent/dictionaries", params={
        "contract_version": "agent-data-v1", "kind": "habitat", "keyword": "公园",
        "page": 2, "page_size": 1,
    })
    assert result["total"] == 2
    assert result["returned_count"] == 1
    assert result["has_more"] is False
    assert result["items"][0]["code"] == "habitat:park"
    assert result["items"][0]["query_value"] == "公园"


def test_defaults_query_only_habitats(client):
    result = json.loads(dictionaries.invoke({}))
    assert result["has_more"] is False
    assert result["items"][0]["parent_code"] is None
    assert result["items"][0]["has_children"] is False
    client.get.assert_called_once_with("/api/v1/agent/dictionaries", params={
        "contract_version": "agent-data-v1", "kind": "habitat", "page": 1, "page_size": 10,
    })


def test_blank_filter_is_rejected_before_request(client):
    with pytest.raises(ToolInputError):
        dictionaries.invoke({"keyword": "   "})
    client.get.assert_not_called()


@pytest.mark.parametrize("change", [
    {"contract_version": "old-version"},
    {"applied_query": {"kind": "habitat"}},
    {"total": -1},
    {"items": None},
    {"items": [{"name": "公园"}]},
])
def test_invalid_response_is_not_returned_as_candidates(client, change):
    params = {"contract_version": "agent-data-v1", "kind": "habitat", "page": 1, "page_size": 10}
    response = client.get("", params)
    response["data"].update(copy.deepcopy(change))
    client.get.side_effect = None
    client.get.return_value = response
    with pytest.raises(ToolDataError):
        dictionaries.invoke({})


def test_empty_page_keeps_total_and_does_not_claim_no_matches(client):
    response = client.get("", {})
    response["data"].update(total=20, items=[], applied_query={
        "contract_version": "agent-data-v1", "kind": "habitat", "page": 4, "page_size": 10,
    })
    client.get.side_effect = None
    client.get.return_value = response
    result = json.loads(dictionaries.invoke({"page": 4}))
    assert result["total"] == 20
    assert result["items"] == []
    assert "当前页" in result["message"]


@pytest.mark.parametrize("args", [
    {"kind": "region"}, {"kind": "mosquito"}, {"kind": "habitat"},
    {"parent_code": "440000"}, {"page": 0}, {"page_size": 201}, {"user_id": "user-1"},
])
def test_schema_rejects_invalid_arguments(args):
    with pytest.raises(ValidationError):
        dictionaries.args_schema.model_validate(args)


def test_tool_is_registered():
    from core.tools.tools import get_available_tools

    assert dictionaries in get_available_tools()
