"""蚊种字典工具：固定查询蚊类，支持蚊属展开并保留真实编码。"""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.tools.common import ToolDataError, ToolInputError
from core.tools.report.mosquito_dictionaries import mosquito_dictionaries


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.get.side_effect = lambda path, params: {
        "code": 200,
        "message": "成功",
        "data": {
            "contract_version": "agent-data-v1", "applied_query": dict(params),
            "version": "mosquito-v1", "total": 1,
            "items": [{
                "code": "mosquito-demo-aedes", "name": "伊蚊属", "parent_code": None,
                "level": "genus", "aliases": [], "has_children": True, "query_value": None,
            }],
        },
    }
    monkeypatch.setattr("core.tools.report.mosquito_dictionaries.get_company_client", lambda: client)
    return client


def test_root_query_returns_genus_without_inventing_species_code(client):
    result = json.loads(mosquito_dictionaries.invoke({}))
    client.get.assert_called_once_with("/api/v1/agent/dictionaries", params={
        "contract_version": "agent-data-v1", "kind": "mosquito", "page": 1, "page_size": 10,
    })
    assert result["items"][0]["level"] == "genus"
    assert result["items"][0]["has_children"] is True
    assert result["items"][0]["query_value"] is None


def test_keyword_and_parent_query_preserves_filters_and_species_code(client):
    params = {
        "contract_version": "agent-data-v1", "kind": "mosquito", "keyword": "白纹",
        "parent_code": "mosquito-demo-aedes", "page": 2, "page_size": 1,
    }
    response = client.get("", params)
    response["data"]["total"] = 3
    response["data"]["items"] = [{
        "code": "mosquito-demo-albopictus", "name": "白纹伊蚊",
        "parent_code": "mosquito-demo-aedes", "level": "species",
        "aliases": ["亚洲虎蚊"], "has_children": False, "query_value": "Aedes albopictus",
    }]
    client.reset_mock(side_effect=True)
    client.get.return_value = response
    result = json.loads(mosquito_dictionaries.invoke({
        "keyword": "白纹", "parent_code": "mosquito-demo-aedes", "page": 2, "page_size": 1,
    }))
    client.get.assert_called_once_with("/api/v1/agent/dictionaries", params=params)
    assert result["total"] == 3
    assert result["returned_count"] == 1
    assert result["has_more"] is True
    assert result["items"] == response["data"]["items"]
    assert result["items"][0]["code"] != result["items"][0]["query_value"]


@pytest.mark.parametrize("field", ["keyword", "parent_code"])
def test_blank_filter_is_rejected_before_request(client, field):
    with pytest.raises(ToolInputError):
        mosquito_dictionaries.invoke({field: "   "})
    client.get.assert_not_called()


@pytest.mark.parametrize("change", [
    {"contract_version": "old-version"},
    {"applied_query": {"kind": "habitat"}},
    {"version": ""}, {"total": -1}, {"items": None},
    {"items": [{"code": "missing-node-fields"}]},
])
def test_invalid_response_is_not_returned_as_codes(client, change):
    params = {"contract_version": "agent-data-v1", "kind": "mosquito", "page": 1, "page_size": 10}
    response = client.get("", params)
    response["data"].update(change)
    client.get.side_effect = None
    client.get.return_value = response
    with pytest.raises(ToolDataError):
        mosquito_dictionaries.invoke({})


@pytest.mark.parametrize("total,page,expected_message", [
    (0, 1, "未找到"), (20, 4, "当前页"),
])
def test_empty_results_distinguish_no_matches_from_empty_page(client, total, page, expected_message):
    params = {"contract_version": "agent-data-v1", "kind": "mosquito", "page": page, "page_size": 10}
    response = client.get("", params)
    response["data"].update(total=total, items=[])
    client.get.side_effect = None
    client.get.return_value = response
    result = json.loads(mosquito_dictionaries.invoke({"page": page}))
    assert result["total"] == total
    assert result["items"] == []
    assert expected_message in result["message"]


@pytest.mark.parametrize("args", [{"kind": "habitat"}, {"page": 0}, {"page_size": 201}])
def test_schema_rejects_invalid_arguments(args):
    with pytest.raises(ValidationError):
        mosquito_dictionaries.args_schema.model_validate(args)


def test_tool_is_registered_separately_from_habitat_dictionary():
    from core.tools.report.dictionaries import dictionaries
    from core.tools.tools import get_available_tools

    tools = get_available_tools()
    assert mosquito_dictionaries in tools
    assert dictionaries in tools
    assert "kind" not in mosquito_dictionaries.args_schema.model_fields
    assert "parent_code" not in dictionaries.args_schema.model_fields
