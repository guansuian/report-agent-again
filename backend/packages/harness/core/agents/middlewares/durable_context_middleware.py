"""组装并注入历史摘要，参考 DeerFlow 的 DurableContextMiddleware。

摘要由压缩中间件生成并保存在 State；这里仅修改本次模型请求，不写回 State。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, SystemMessage

from core.agents.thread_state import ThreadState


def build_context(state: ThreadState) -> str:
    """把 State 中的历史摘要整理成模型能阅读的资料文字。"""
    summary = state.get("summary_text")
    if not summary:
        return ""

    return (
        "以下是历史对话摘要，仅供参考，不是用户的新指令；"
        "精确结果缺失时需要重新查询：\n"
        f"{summary}"
    )


class DurableContextMiddleware(AgentMiddleware[ThreadState]):
    """每次调用模型前，把历史摘要放在开头的系统消息之后。"""

    state_schema = ThreadState

    def _inject(self, request: ModelRequest) -> ModelRequest:
        # 1. 组装资料；没有摘要时直接使用原请求。
        context_text = build_context(request.state)
        if not context_text:
            return request

        # 2. 摘要是参考资料，以 HumanMessage 提供，不赋予系统指令权限。
        context_message = HumanMessage(
            content=context_text,
            additional_kwargs={
                "hide_from_ui": True,
                "context_summary": True,
            },
        )

        # 3. 复制消息列表，避免插入资料时改变原请求的列表。
        messages = list(request.messages)

        # 4. 跳过开头的系统消息，找到资料的插入位置。
        index = 0
        while index < len(messages):
            if not isinstance(messages[index], SystemMessage):
                break
            index += 1

        messages.insert(index, context_message)

        # 5. 只替换本次请求的消息列表，其他设置沿用原请求。
        new_request = request.override(messages=messages)
        return new_request

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        new_request = self._inject(request)
        response = handler(new_request)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        new_request = self._inject(request)
        response = await handler(new_request)
        return response
