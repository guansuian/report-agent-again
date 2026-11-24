"""按已有产物创建公司导出任务，文件状态查询和下载由网关另行提供。

参考 DeerFlow present_files 的分工：工具返回产物信息，前端负责展示和下载。
本项目文件由公司接口异步生成，不在工具中轮询，也不读取文件字节。
"""

from __future__ import annotations

import json
from copy import deepcopy
from re import fullmatch
from typing import Annotated, Literal

from langchain.tools import ToolRuntime
from pydantic import Field

from core.clients import get_company_client
from core.tools.common import ToolDataError, ToolInputError, report_tool

# 只列出已有图表产物、且公司导出接口支持的来源，不转换旧统计接口的参数。
_EXPORT_SOURCES = {
    "region_analysis": "region_analysis",
    "habitat_analysis": "habitat_analysis",
    "environment_distribution": "environment_distribution",
}

_STATUS_MESSAGES = {
    "queued": "导出任务已创建，文件正在排队，尚不能下载。",
    "processing": "导出任务已创建，文件正在生成，尚不能下载。",
    "ready": "导出文件已就绪，可通过文件卡片下载。",
    "failed": "导出任务已失败，当前没有可下载文件，请查询任务状态了解原因。",
    "expired": "导出文件已过期，当前不能下载。",
}


def _read_export_query(state: dict, artifact_handle: str) -> tuple[str, dict]:
    """从当前会话的产物登记中读取原查询，不从摘要或模型参数重建条件。"""
    artifact = None
    for entry in state.get("tool_artifacts", []):
        if entry["handle"] == artifact_handle:
            artifact = entry
            break
    if artifact is None:
        raise ToolInputError("当前会话找不到该产物，请使用可导出产物清单中的真实编号。")

    source = _EXPORT_SOURCES.get(artifact["tool_name"])
    if source is None:
        raise ToolInputError("该产物暂不支持导出，请选择区域分析、生境分析或环境区间分布产物。")

    query = artifact["data"].get("query")
    if not isinstance(query, dict) or query.get("contract_version") != "agent-data-v1":
        raise ToolDataError("产物缺少有效的原始查询条件，不能创建导出任务。")

    # 保留时间、筛选、排序、Top N 和原分页；取消展示分页由公司导出接口负责。
    # 复制后提交，避免 HTTP 客户端处理请求时改变 State 中的原产物。
    return source, deepcopy(query)


def _read_export_result(response: dict) -> dict:
    """只检查创建任务所需的返回字段，不核对 applied_query 条件回显。"""
    data = response.get("data")
    if not isinstance(data, dict):
        raise ToolDataError("导出接口缺少 data 对象，不能确认任务是否创建成功。")

    export_id = data.get("export_id")
    if not isinstance(export_id, str) or not fullmatch(r"[A-Za-z0-9_-]{1,128}", export_id):
        raise ToolDataError("导出接口返回的任务编号无效。")

    status = data.get("status")
    if not isinstance(status, str) or status not in _STATUS_MESSAGES:
        raise ToolDataError("导出接口返回了未知的任务状态。")
    if data.get("format") not in ("csv", "xlsx"):
        raise ToolDataError("导出接口返回的文件格式必须是 csv 或 xlsx。")

    file_name = data.get("file_name")
    if not isinstance(file_name, str) or not file_name.strip() or "\r" in file_name or "\n" in file_name:
        raise ToolDataError("导出接口返回的文件名无效。")

    return {
        "export_id": export_id,
        "status": status,
        "format": data["format"],
        "file_name": file_name,
    }


@report_tool(response_format="content_and_artifact")
def create_export(
    artifact_handle: Annotated[str, Field(min_length=1)],
    runtime: ToolRuntime,
    format: Literal["csv", "xlsx"] = "xlsx",
) -> tuple[str, dict]:
    """按已有报表创建 CSV 或 Excel 导出任务，返回文件产物，不等待文件生成。

    用户明确要求导出时使用，从本会话可导出产物清单选择真实编号，不猜测。
    目前支持区域分析、生境分析、环境区间分布；选择不明确时先追问。
    工具读取产物保存的原始 query，不需要模型重新填写查询条件。
    导出取消展示分页，保留原时间、筛选、排序和 Top N，不保证数据快照与旧报表相同。
    queued/processing 仅表示准备中，只有 ready 才表示文件可下载。
    请求失败时不自动重试；创建任务可能已生效，不能盲目重复创建。

    Args:
        artifact_handle: 当前会话可导出产物清单中的产物编号，不是工具调用 ID 或导出任务 ID。
        runtime: 框架自动注入的运行信息，其中 state 保存本会话产物，模型无需填写。
        format: 文件格式，xlsx 为 Excel，csv 为 CSV；默认 xlsx。
    """
    # 1. ToolNode 自动注入 runtime，模型只提供产物编号和文件格式。
    # 不接收模型提交的 State 或 query，避免重新组装时遗漏原筛选条件。
    source, query = _read_export_query(runtime.state, artifact_handle)

    # 2. 使用当前请求的公司客户端和用户身份，只创建一次任务。
    body = {
        "contract_version": "agent-data-v1",
        "source": source,
        "query": query,
        "format": format,
    }
    response = get_company_client().post("/api/v1/agent/exports", json=body)
    export_data = _read_export_result(response)
    export_data["artifact_handle"] = artifact_handle
    export_data["source"] = source

    # 3. content 给模型说明当前状态，artifact 给前端恢复文件卡片。
    # RunJournal 会保存完整 ToolMessage；这里不修改 State，也不直接写数据库。
    result = {
        "message": _STATUS_MESSAGES[export_data["status"]],
        **export_data,
        "note": "导出保留原查询条件、排序和 Top N，取消展示分页。状态以公司接口最新查询为准。",
    }
    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
    artifact = {
        "artifact_type": "export",
        "display_name": export_data["file_name"],
        "data": export_data,
    }
    return content, artifact
