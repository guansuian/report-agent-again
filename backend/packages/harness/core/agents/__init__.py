"""agents 子包 —— 对应真实 deer-flow 的 deerflow/agents/。

这是整个项目的核心：agent 的装配、状态 schema、中间件、记忆都在这里。

对外最重要的入口是 make_lead_agent，供 backend/langgraph.json 声明：
    "lead_agent": "core.agents:make_lead_agent"

它采用**懒加载**：真正 import 时才去加载 lead_agent 子模块，
保证包根部的 import 尽量轻量（真实 deer-flow 就是这么做的，
LangGraph Server 会直接从模块 __dict__ 里解析这个函数）。
"""

from __future__ import annotations

from typing import Any


def make_lead_agent(config: Any) -> Any:
    """构建 lead agent 图。LangGraph Server 的图工厂入口。"""
    from core.agents.lead_agent import make_lead_agent as factory

    return factory(config)


__all__ = ["make_lead_agent"]
