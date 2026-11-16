"""读取用户长期记忆，参考 DeerFlow 的 DynamicContextMiddleware。

首次注入时读取记忆并将快照写入 State；同日复用，跨天只补充日期提醒。
日期标记与记忆消息随检查点保存，不在中间件对象里记录“是否读过”。
"""

from __future__ import annotations

from datetime import date

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from core.agents.memory.manager import MemoryManager
from core.agents.thread_state import ThreadState


def is_dynamic_context_reminder(message: BaseMessage) -> bool:
    """识别日期提醒和记忆快照，供后续压缩中间件保留这些资料。"""
    return isinstance(message, (SystemMessage, HumanMessage)) and bool(
        message.additional_kwargs.get("dynamic_context_reminder")
    )


def _last_injected_date(messages: list[BaseMessage]) -> str | None:
    """倒序找到最新日期标记；不从消息正文猜日期，不兼容旧标记格式。"""
    for message in reversed(messages):
        if not isinstance(message, SystemMessage):
            continue
        if not is_dynamic_context_reminder(message):
            continue
        reminder_date = message.additional_kwargs.get("reminder_date")
        if reminder_date:
            return reminder_date
    return None


class DynamicContextMiddleware(AgentMiddleware[ThreadState]):
    """每个 run 单独创建，沿用当前会话 State 中已有的记忆快照。"""

    state_schema = ThreadState

    def __init__(self, manager: MemoryManager, user_id: str, query: str) -> None:
        super().__init__()
        # Worker 经智能体工厂传入：共用的管理器、本轮用户 ID 和问题文字。
        # user_id 决定查谁的记忆，query 用于搜索相关内容；不按 thread_id 查记忆。
        self._manager = manager
        self._user_id = user_id
        self._query = query

    async def abefore_agent(self, state: ThreadState, runtime: Runtime) -> dict | None:
        """框架在本轮开始时传入恢复后的 State，包括此前保存的隐藏资料。"""
        messages = state["messages"]
        today = date.today().isoformat()
        last_date = _last_injected_date(messages)

        # 1. 已注入过，日期也没变：沿用原快照，不再次请求 mem0。
        if last_date == today:
            return None

        # 2. 找到本轮真实用户消息，用来确定提醒的插入位置。
        # 跳过内部隐藏资料；用户填写的追问答案仍是有效输入，即使前端隐藏它。
        current_user = None
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            extra = message.additional_kwargs
            if is_dynamic_context_reminder(message) or extra.get("context_summary"):
                continue
            if extra.get("hide_from_ui") and not extra.get("human_input_response"):
                continue
            current_user = message
            break
        if current_user is None:
            return None

        # 3. 没有日期标记才查询长期记忆。跨天时跳过查询，只补新日期。
        # 查询失败直接抛出，不写入“已读取”的标记，也不自动重试。
        memory_text = ""
        if last_date is None:
            memory_text = await self._manager.get_context(
                user_id=self._user_id,
                query=self._query,
            )

        # 4. State 中的消息 ID 由框架分配。沿用用户消息 ID，让 add_messages
        # 把原位置替换为日期提醒，再在后面补上记忆和用户消息的新副本。
        # 例如：[用户问题] → [日期提醒, 隐藏记忆, 用户问题]。
        message_id = current_user.id
        updates: list[BaseMessage] = [
            SystemMessage(
                id=message_id,
                content=f"当前日期是：{today}",
                additional_kwargs={
                    "hide_from_ui": True,
                    "dynamic_context_reminder": True,
                    "reminder_date": today,
                },
            )
        ]

        if memory_text:
            # 长期记忆是参考资料，用 HumanMessage 承载，不赋予系统指令权限。
            # 不设置 reminder_date，避免把记忆正文误当作日期标记。
            updates.append(
                HumanMessage(
                    id=f"{message_id}__memory",
                    content=f"<memory>\n{memory_text}\n</memory>",
                    additional_kwargs={
                        "hide_from_ui": True,
                        "dynamic_context_reminder": True,
                    },
                )
            )

        # model_copy 保留原问题内容和表单答案等附加字段，只更换消息 ID。
        user_copy = current_user.model_copy(update={"id": f"{message_id}__user"})
        updates.append(user_copy)

        # 5. 更新 State，而非仅修改本次请求；检查点随后保存快照与提醒标记。
        # 首次没有查到记忆时也保存日期标记，同一会话后续不反复查询。
        return {"messages": updates}
