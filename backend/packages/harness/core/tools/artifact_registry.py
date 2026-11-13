"""从工具消息中提取图表产物，参考 DeerFlow 的 tools/artifact_registry.py。

本项目只读取 artifact 中的 artifact_type、display_name、data，
不扫描正文中的文件路径，也不把追问表单登记为报表产物。
这里只构造登记记录，不修改 State、不写数据库。
"""

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

from langchain_core.messages import ToolMessage

from core.agents.thread_state import ArtifactEntry


def generate_handle(thread_id: str, message_id: str) -> str:
    """用会话 ID 和工具消息 ID 生成稳定编号；同一条消息重复提取时编号不变。"""
    identity = json.dumps([thread_id, message_id], ensure_ascii=False)
    digest = sha256(identity.encode("utf-8")).hexdigest()
    return f"art_{digest[:16]}"


def extract_artifacts_from_result(
    result: ToolMessage,
    *,
    thread_id: str,
) -> list[ArtifactEntry]:
    """从一条工具消息中提取图表，返回零条或一条产物记录。

    result 由捕获中间件从 State.messages 取出；框架已为消息分配 id。
    thread_id 由调用方提供，用于区分不同会话。
    artifact 必须包含 artifact_type="chart"、display_name 和 data 字典。
    data 的内部结构由工具和前端约定，这里不改写。

    错误结果、无 artifact、非图表结果返回空列表。
    已声明为图表但缺少必要数据时直接报错，不生成不完整的产物记录。
    """
    if result.status == "error":
        return []

    artifact = result.artifact
    if not isinstance(artifact, dict) or artifact.get("artifact_type") != "chart":
        return []

    display_name = artifact.get("display_name")
    data = artifact.get("data")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("图表产物的 display_name 必须是非空字符串。")
    if not isinstance(data, dict):
        raise ValueError("图表产物的 data 必须是字典。")
    if not result.id:
        raise ValueError("工具消息缺少 id，请从 State.messages 中读取后再提取产物。")
    if not result.name:
        raise ValueError("工具消息缺少 name，无法记录产物来自哪个工具。")

    handle = generate_handle(thread_id, result.id)
    created_at = datetime.now(UTC).isoformat()

    # 复制 JSON，避免后续修改登记数据时连带修改原工具消息。
    entry: ArtifactEntry = {
        "handle": handle,
        "tool_name": result.name,
        "tool_call_id": result.tool_call_id,
        "artifact_type": "chart",
        "display_name": display_name,
        "data": deepcopy(data),
        "created_at": created_at,
    }
    return [entry]
