"""会话状态，对应 DeerFlow 的 agents/thread_state.py。"""

from typing import Annotated, Any, Literal

from langchain.agents import AgentState
from typing_extensions import NotRequired, TypedDict


_ARTIFACT_MAX_ENTRIES_CEILING = 1000


class ArtifactEntry(TypedDict):
    """一条工具产物记录；data 保存给前端展示的 JSON，不是文件路径。"""

    handle: str                  # 产物编号，同一个编号的记录会被替换
    tool_name: str               # 哪个工具生成的
    tool_call_id: str            # 哪次工具调用生成的
    artifact_type: str           # 产物类型，例如 chart
    display_name: str            # 展示名称，例如“每日蚊虫监测趋势”
    data: dict[str, Any]         # 产物的完整 JSON，具体结构由工具与前端约定
    created_at: str              # 生成时间，使用 ISO 格式字符串


class ArtifactTrim(TypedDict):
    """中间件提交的裁剪指令，只用于合并，不作为产物记录保存。"""

    op: Literal["trim_to"]
    keep: int                    # 保留最后多少条；由 max_entries 配置提供


def merge_tool_artifacts(
    existing: list[ArtifactEntry] | None,
    new: list[ArtifactEntry | ArtifactTrim] | None,
) -> list[ArtifactEntry]:
    """同编号用新记录替换，不同编号追加；收到裁剪指令时移除最早的记录。

    框架传入原有记录 existing 和本次更新 new，再把返回值写回 State。
    具体数量上限由中间件通过 ArtifactTrim 传入，这里另保留 1000 条的硬上限。
    """
    if not new:
        return existing or []

    # 字典按首次插入顺序保存；替换相同编号的值不会改变位置。
    by_handle: dict[str, ArtifactEntry] = {}
    for entry in existing or []:
        by_handle[entry["handle"]] = entry

    keep = _ARTIFACT_MAX_ENTRIES_CEILING
    for entry in new:
        if entry.get("op") == "trim_to":
            keep = entry["keep"]
        else:
            by_handle[entry["handle"]] = entry

    merged = list(by_handle.values())
    keep = min(keep, _ARTIFACT_MAX_ENTRIES_CEILING)
    if keep <= 0:
        return []
    return merged[-keep:]


def merge_processed_artifacts(
    existing: list[str] | None,
    new: list[str] | None,
) -> list[str]:
    """合并已检查的工具消息标记；产物被裁剪后也保留标记，避免重复登记。"""
    merged = list(existing or [])
    seen = set(merged)
    for message_id in new or []:
        if message_id not in seen:
            merged.append(message_id)
            seen.add(message_id)
    return merged


class ThreadState(AgentState):
    """继承 messages，补充随检查点保存的摘要和工具产物登记。

    summary_text 可以暂时不存在；每次压缩成功后，用新摘要覆盖旧摘要。
    它不是一条聊天消息，后续由中间件加入模型请求。
    """

    summary_text: NotRequired[str | None]
    tool_artifacts: Annotated[list[ArtifactEntry], merge_tool_artifacts]
    tool_artifact_processed: Annotated[list[str], merge_processed_artifacts]
