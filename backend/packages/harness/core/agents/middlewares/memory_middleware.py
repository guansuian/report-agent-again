"""本轮结束后提交长期记忆，参考 DeerFlow 的 MemoryMiddleware。

与 DeerFlow 的 mem0 分支不同：只提交本轮用户输入和最终 AI 回答，不重交历史。
本轮输入由 Worker 经智能体工厂传入；执行后的 State 由框架传给 aafter_agent。
这里只整理消息，哪些内容值得长期保存，由 mem0 的提炼规则决定。
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from core.agents.memory.manager import MemoryManager
from core.agents.thread_state import ThreadState


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
        # await 会等待 mem0 处理；异常向外抛出，不在这里吞掉或自动重试。
        await self._manager.add(user_id=self._user_id, messages=messages)
        return None  # 记忆由 mem0 保存，这个中间件不修改 State。
