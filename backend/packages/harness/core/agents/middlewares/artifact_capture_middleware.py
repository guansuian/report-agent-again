"""在调用模型前登记工具产物，参考 DeerFlow 的 ArtifactCaptureMiddleware。

读取 State.messages 中尚未检查的工具消息，返回产物和处理标记的更新。
不包装工具返回的 Command，不修改 messages，也不直接写数据库。
接入时必须排在上下文压缩中间件之前，避免工具消息先被压缩掉。
"""

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from core.agents.thread_state import ArtifactEntry, ArtifactTrim, ThreadState
from core.config.tool_artifact_config import ToolArtifactConfig
from core.tools.artifact_registry import extract_artifacts_from_result


class ArtifactCaptureMiddleware(AgentMiddleware[ThreadState]):
    """登记图表 JSON；同一条工具消息只提取一次，超限时裁剪最早的产物。"""

    state_schema = ThreadState

    def __init__(self, config: ToolArtifactConfig) -> None:
        super().__init__()
        self._config = config

    def _capture(self, state: ThreadState) -> dict[str, Any] | None:
        if not self._config.enabled:
            return None

        # 网关创建 run 时写入；get_config() 读取当前图执行时的配置。
        config = get_config()
        thread_id = config["configurable"]["thread_id"]

        messages = state.get("messages", [])
        existing = state.get("tool_artifacts", [])
        existing_handles = {entry["handle"] for entry in existing}
        processed = set(state.get("tool_artifact_processed", []))
        newly_processed: list[str] = []
        new_entries: list[ArtifactEntry] = []

        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            if not message.id:
                raise ValueError("工具消息缺少 id，请先通过 State.messages 保存消息。")
            if message.id in processed:
                continue

            entries = extract_artifacts_from_result(message, thread_id=thread_id)

            # 即使没有图表，也记下检查标记，下次不再重复提取。
            processed.add(message.id)
            newly_processed.append(message.id)
            for entry in entries:
                if entry["handle"] not in existing_handles:
                    new_entries.append(entry)
                    existing_handles.add(entry["handle"])

        update: dict[str, Any] = {}
        if newly_processed:
            update["tool_artifact_processed"] = newly_processed

        if new_entries:
            artifact_updates: list[ArtifactEntry | ArtifactTrim] = list(new_entries)
            if len(existing) + len(new_entries) > self._config.max_entries:
                # reducer 执行裁剪；处理标记保留，防止旧产物重新登记。
                artifact_updates.append({"op": "trim_to", "keep": self._config.max_entries})
            update["tool_artifacts"] = artifact_updates

        return update or None

    def before_model(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        """同步入口：框架调用，返回值由框架合并到 State。"""
        return self._capture(state)

    async def abefore_model(self, state: ThreadState, runtime: Runtime) -> dict[str, Any] | None:
        """异步入口：与同步入口共用提取逻辑，没有额外的网络或数据库调用。"""
        return self._capture(state)
