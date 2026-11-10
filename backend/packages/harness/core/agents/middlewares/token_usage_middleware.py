"""token 用量统计中间件 —— 对应真实 deer-flow 的
deerflow/agents/middlewares/token_usage_middleware.py 里的
TokenUsageMiddleware（极简版）。

它只干一件事：模型返回后（after_model 钩子），从最后一条 AIMessage 的
usage_metadata 里读出 input/output/total tokens，打到日志里，用于成本统计。

真实实现还会给 AI 步骤做"归属标注"（attribution，记录这一步调了哪些工具、
是不是 final_answer 等）；这里只保留「读 usage_metadata → 打日志」这个最小闭环。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class TokenUsageMiddleware(AgentMiddleware[AgentState]):
    """模型返回后统计 token 用量，打到日志里。"""

    def _log_usage(self, state: AgentState) -> None:
        messages = state.get("messages", [])
        if not messages:
            return
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return  # 最后一条不是模型回复（比如是工具结果），跳过
        usage = getattr(last, "usage_metadata", None)
        if not usage:
            return
        # 明细：输入里多少命中 prompt 缓存（便宜）、输出里多少是思考 token
        detail_parts = []
        input_details = usage.get("input_token_details") or {}
        output_details = usage.get("output_token_details") or {}
        if input_details:
            detail_parts.append(f"input_token_details={input_details}")
        if output_details:
            detail_parts.append(f"output_token_details={output_details}")
        detail_suffix = f" {' '.join(detail_parts)}" if detail_parts else ""
        logger.info(
            "token 用量: input=%s output=%s total=%s%s",
            usage.get("input_tokens", "?"),
            usage.get("output_tokens", "?"),
            usage.get("total_tokens", "?"),
            detail_suffix,
        )

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._log_usage(state)
        return None  # 纯日志，不改状态

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._log_usage(state)
        return None
