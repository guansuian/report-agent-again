"""查询当前用户可见的蚊类字典，保留公司接口返回的蚊种编码。"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field

from core.clients import get_company_client
from core.tools.common import Page, PageSize, ToolDataError, ToolInputError, report_tool


@report_tool
def mosquito_dictionaries(
    keyword: Annotated[str, Field(min_length=1)] | None = None,
    parent_code: Annotated[str, Field(min_length=1)] | None = None,
    page: Page = 1,
    page_size: PageSize = 10,
) -> str:
    """查询蚊种标准名称、别名和编码，供统计筛选和追问消歧使用。

    固定查询蚊类字典，不传关键词和父节点时查根节点；关键词模糊检索，
    parent_code 查询父节点的直接子节点。level=genus 是蚊属，需用其 code
    继续查询子节点，取得 level=species 的具体蚊种，不直接把蚊属当蚊种。
    新统计接口 mosquito_codes 使用返回的 code，旧接口 monitor_type 使用 query_value；
    两者不能混用，query_value 为 null 时不能用于旧接口，不从本地映射推测新编码。
    俗称有多个候选时，用 ask_clarification 确认，不默认选第一项。
    翻页保留原筛选条件，不将本页当作全部结果。不生成图表或表单产物。

    Args:
        keyword: 可选蚊种标准名称或别名的模糊关键词，如白纹伊蚊。
        parent_code: 可选蚊属等父节点的真实代码，必须取自接口，查询其直接子节点。
        page: 页码，从 1 开始，默认 1。
        page_size: 每页数量，默认 10，最多 200。
    """
    # 1. 保留筛选条件，蚊类字典类型由工具固定，不让模型选择。
    for field, value in (("keyword", keyword), ("parent_code", parent_code)):
        if value is not None and not value.strip():
            raise ToolInputError(f"{field} 不能是空白文本，未指定时请省略。")
    params = {
        "contract_version": "agent-data-v1", "kind": "mosquito",
        "page": page, "page_size": page_size,
    }
    if keyword is not None:
        params["keyword"] = keyword
    if parent_code is not None:
        params["parent_code"] = parent_code

    # 2. 复用现有认证客户端，核对接口版本和实际生效的查询条件。
    response = get_company_client().get("/api/v1/agent/dictionaries", params=params)
    data = response.get("data")
    if not isinstance(data, dict):
        raise ToolDataError("蚊种字典接口缺少 data 对象。")
    if data.get("contract_version") != "agent-data-v1":
        raise ToolDataError("蚊种字典接口返回的 contract_version 与请求不一致。")
    applied_query = data.get("applied_query")
    if not isinstance(applied_query, dict):
        raise ToolDataError("蚊种字典接口缺少实际生效条件 applied_query。")
    for field, value in params.items():
        if applied_query.get(field) != value:
            raise ToolDataError(f"蚊种字典接口的实际生效条件 {field} 与请求不一致。")

    # 3. 保留蚊属、蚊种以及父节点关系，不替换代码或自行展开整棵树。
    version = data.get("version")
    total = data.get("total")
    items = data.get("items")
    if not isinstance(version, str) or not version.strip():
        raise ToolDataError("蚊种字典接口缺少非空的字典版本 version。")
    if type(total) is not int or total < 0:
        raise ToolDataError("蚊种字典 total 必须是非负整数。")
    if not isinstance(items, list) or len(items) > page_size or len(items) > total:
        raise ToolDataError("蚊种字典 items 必须是列表，条数不能超过 page_size 或 total。")
    for item in items:
        if not isinstance(item, dict):
            raise ToolDataError("每条蚊种字典记录必须是对象。")
        for field in ("code", "name"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ToolDataError(f"蚊种字典节点 {field} 必须是非空文本。")
        if item.get("level") not in ("genus", "species"):
            raise ToolDataError("蚊种字典节点 level 必须是 genus 或 species。")
        for field in ("parent_code", "query_value"):
            if field not in item or (item[field] is not None and not isinstance(item[field], str)):
                raise ToolDataError(f"蚊种字典节点 {field} 必须是文本或 null。")
        aliases = item.get("aliases")
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            raise ToolDataError("蚊种字典节点 aliases 必须是文本列表。")
        if type(item.get("has_children")) is not bool:
            raise ToolDataError("蚊种字典节点 has_children 必须是布尔值。")

    # 4. 返回本页节点，供 AI 继续展开蚊属、确认候选或使用具体蚊种代码。
    message = "蚊种字典查询完成；蚊属需继续展开到具体蚊种，多个候选需确认。"
    if total == 0:
        message = "当前用户可见范围内未找到匹配蚊种，请确认名称或查询条件。"
    elif not items:
        message = "当前页没有记录，请保留原筛选条件并查询有效页码。"
    result = {
        "message": message, "contract_version": data["contract_version"],
        "applied_query": applied_query, "version": version,
        "total": total, "page": page, "page_size": page_size,
        "returned_count": len(items), "has_more": page * page_size < total,
        "items": items,
    }
    return json.dumps(result, ensure_ascii=False, allow_nan=False)
