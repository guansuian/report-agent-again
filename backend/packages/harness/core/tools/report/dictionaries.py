"""查询当前用户可见的生境字典，只返回内容。"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field

from core.clients import get_company_client
from core.tools.common import Page, PageSize, ToolDataError, ToolInputError, report_tool


@report_tool
def dictionaries(
    keyword: Annotated[str, Field(min_length=1)] | None = None,
    page: Page = 1,
    page_size: PageSize = 10,
) -> str:
    """查询生境的标准名称和代码，供统计筛选和追问消歧使用。

    keyword 是模糊检索，不传时分页查询当前用户可见的生境。
    多个候选或用户名称有歧义时，用 ask_clarification 确认，
    不默认选第一项，不编造代码。新统计接口使用 code，旧接口使用 query_value；
    query_value 为 null 的节点不能直接用于旧接口。生境是平铺列表，不构造层级。
    翻页保留原筛选条件；不把本页结果当成全部字典。不生成图表或表单产物。

    Args:
        keyword: 可选生境标准名称或别名的模糊关键词，如公园、医院。
        page: 页码，从 1 开始，默认 1。
        page_size: 每页数量，默认 10，最多 200。
    """
    # 1. 保留用户明确给出的条件，不发送空白筛选值。
    if keyword is not None and not keyword.strip():
        raise ToolInputError("keyword 不能是空白文本，未指定时请省略。")
    params = {
        "contract_version": "agent-data-v1", "kind": "habitat",
        "page": page, "page_size": page_size,
    }
    if keyword is not None:
        params["keyword"] = keyword

    # 2. 认证和业务状态码由现有客户端处理；这里核对协议及实际生效条件。
    response = get_company_client().get("/api/v1/agent/dictionaries", params=params)
    data = response.get("data")
    if not isinstance(data, dict):
        raise ToolDataError("字典接口缺少 data 对象。")
    if data.get("contract_version") != "agent-data-v1":
        raise ToolDataError("字典接口返回的 contract_version 与请求不一致。")
    applied_query = data.get("applied_query")
    if not isinstance(applied_query, dict):
        raise ToolDataError("字典接口缺少实际生效条件 applied_query。")
    for field, value in params.items():
        if applied_query.get(field) != value:
            raise ToolDataError(f"字典接口的实际生效条件 {field} 与请求不一致。")

    # 3. 保留生境节点的原始代码及空值，不用名称替换代码。
    version = data.get("version")
    total = data.get("total")
    items = data.get("items")
    if not isinstance(version, str) or not version.strip():
        raise ToolDataError("字典接口缺少非空的字典版本 version。")
    if type(total) is not int or total < 0:
        raise ToolDataError("字典 total 必须是非负整数。")
    if not isinstance(items, list) or len(items) > page_size or len(items) > total:
        raise ToolDataError("字典 items 必须是列表，条数不能超过 page_size 或 total。")
    for item in items:
        if not isinstance(item, dict):
            raise ToolDataError("每条字典记录必须是对象。")
        for field in ("code", "name", "level"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ToolDataError(f"字典节点 {field} 必须是非空文本。")
        for field in ("parent_code", "query_value"):
            if field not in item or (item[field] is not None and not isinstance(item[field], str)):
                raise ToolDataError(f"字典节点 {field} 必须是文本或 null。")
        aliases = item.get("aliases")
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            raise ToolDataError("字典节点 aliases 必须是文本列表。")
        if type(item.get("has_children")) is not bool:
            raise ToolDataError("字典节点 has_children 必须是布尔值。")

    # 4. 区分全部匹配数和当前页，候选确认交给 AI 与已有追问工具。
    message = "字典查询完成，请根据标准名称和候选节点选择正确代码。"
    if total == 0:
        message = "当前用户可见范围内未找到匹配字典项，请确认名称或查询条件。"
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
