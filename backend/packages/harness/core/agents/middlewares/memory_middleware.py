"""本轮结束后提交长期记忆，参考 DeerFlow 的 MemoryMiddleware。

与 DeerFlow 的 mem0 分支不同：只提交本轮用户输入和最终 AI 回答，不重交历史。
本轮输入由 Worker 经智能体工厂传入；执行后的 State 由框架传给 aafter_agent。
这里只整理消息，哪些内容值得长期保存，由 mem0 的提炼规则决定。
"""

from __future__ import annotations

import asyncio
import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from core.agents.memory.manager import MemoryManager
from core.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# 后台写记忆的任务必须保住引用：asyncio 只保存弱引用，
# 任务被回收时连异常一起消失。
_background_tasks: set[asyncio.Task] = set()


def _log_write_failure(task: asyncio.Task) -> None:
    """后台写记忆失败只记日志；这一轮已经正常结束，不再影响用户。"""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("长期记忆写入失败，本轮已正常结束", exc_info=error)


async def flush_background_writes(timeout: float) -> None:
    """服务关闭前等后台写记忆收尾；超时就放弃，不拖住进程退出。"""
    if not _background_tasks:
        return

    pending = list(_background_tasks)
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("长期记忆写入未在 %.0f 秒内完成，放弃剩余任务", timeout)


class MemoryMiddleware(AgentMiddleware[ThreadState]):
    """每次 run 单独创建；共用 Manager，但用户 ID 和本轮输入各自保存。"""

    state_schema = ThreadState

    def __init__(
        self,
        manager: MemoryManager,
        user_id: str,
        input_messages: list[dict[str, str]],
    ) -> None:
        super().__init__()
        self._manager = manager
        self._user_id = user_id

        # Worker 从本次请求的 graph_input["messages"] 取得真实用户输入，
        # 经工厂整理为 role/content 格式后传入，不传 State 中的整段历史。
        # 用户填写的追问答案也属于真实输入，即使前端不单独显示它。
        # 复制字典，避免执行期间原输入被改动，影响本轮记忆提炼。
        self._input_messages = [dict(message) for message in input_messages]

    def _collect_messages(self, state: ThreadState) -> list[dict[str, str]]:
        """组合本轮真实用户文字和最后一条正常 AI 回答。"""
        messages: list[dict[str, str]] = []

        # 1. 用户消息只从构造时收到的本轮输入中取，不倒序扫描历史。
        # 只提交 role/content 字段，不附带请求头或 additional_kwargs；
        # 用户填写表单后的答案正文仍保留，内容取舍由 mem0 的提炼规则决定。
        for message in self._input_messages:
            if message["role"] != "user":
                continue
            text = message["content"].strip()
            if text:
                messages.append({"role": "user", "content": text})

        if not messages:
            return messages

        # 2. 框架把执行后的 State 传进来，最后一条才可能是本轮最终回答。
        # 不收集中间的“准备调用工具”“报错后重试”等 AI 消息。
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage):
            # 追问等场景可能以 ToolMessage 结束；此时只提交本轮用户输入。
            return messages

        additional_kwargs = last_message.additional_kwargs
        if additional_kwargs.get("hide_from_ui"):
            return messages
        if (
            last_message.tool_calls
            or last_message.invalid_tool_calls
            or additional_kwargs.get("tool_calls")
            or additional_kwargs.get("function_call")
        ):
            # 与 DeerFlow 一样：异常或原始格式的工具调用也不算最终回答。
            return messages

        # text 是 LangChain 提供的纯文字属性，也能从内容块列表中提取文字。
        # 不把工具参数、思考块、图片等内容塞进 mem0。
        reply_text = last_message.text.strip()
        if reply_text:
            messages.append({"role": "assistant", "content": reply_text})

        return messages

    async def aafter_agent(self, state: ThreadState, runtime: Runtime) -> None:
        """框架在本轮智能体执行结束后调用，不是每次模型返回都调用。"""
        messages = self._collect_messages(state)
        if not messages:
            return None

        # 3. 最终交给 Manager 的只有：用户 ID，以及本轮的 role/content 列表。
        # 放到后台任务里执行：mem0 要调用一次提炼模型，等它会让整轮 run 迟迟不结束。
        # 失败不再抛给用户，由 _log_write_failure 记日志。
        task = asyncio.create_task(
            self._manager.add(user_id=self._user_id, messages=messages)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        task.add_done_callback(_log_write_failure)
        return None  # 记忆由 mem0 保存，这个中间件不修改 State。
