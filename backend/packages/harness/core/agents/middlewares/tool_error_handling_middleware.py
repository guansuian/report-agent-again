"""工具报错兜底中间件 —— 对应真实 deer-flow 的
deerflow/agents/middlewares/tool_error_handling_middleware.py 里的
ToolErrorHandlingMiddleware（极简版）。

它只干一件事：包住工具调用。工具抛异常时，不崩掉整次运行，而是把异常
转成一条 status="error" 的 ToolMessage 喂回模型，让模型换个说法或重试。

真实实现还有 skill 元数据盖章、task 工具特殊状态等一大堆业务；这里只保留
「try/except → 转 error ToolMessage」这个最小闭环。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

_MISSING_TOOL_CALL_ID = "missing_tool_call_id"
_RECOVERY_HINT = "Continue with available context, or choose an alternative tool."


class ToolErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """把工具异常转成 error ToolMessage，让运行继续而不是崩掉。"""

    def _build_error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        """把异常包成一条带 status="error" 的 ToolMessage。"""
        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        tool_call_id = str(request.tool_call.get("id") or _MISSING_TOOL_CALL_ID)
        detail = str(exc).strip() or exc.__class__.__name__
        if len(detail) > 500:  # 错误信息太长会撑爆上下文，截断
            detail = detail[:497] + "..."
        return ToolMessage(
            content=f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}. {_RECOVERY_HINT}",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            return handler(request)
        except GraphBubbleUp:
            # LangGraph 的控制流信号（interrupt/pause/resume），必须原样上抛，不能吞
            raise
        except Exception as exc:
            logger.exception("工具执行失败: name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            logger.exception("工具执行失败(async): name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)
