"""交互策略 —— 对应真实 deer-flow 的 deerflow/agents/interaction_policy.py。

决定这次运行是否允许「澄清」（向用户提问）、哪些工具被禁用等。
这里只保留一个最小占位。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunInteractionPolicy:
    allows_clarification: bool = True
    disabled_tool_names: set[str] = field(default_factory=set)


def resolve_run_interaction_policy(config) -> RunInteractionPolicy:
    """从 config 解析交互策略（简化：默认全允许）。"""
    _ = config
    return RunInteractionPolicy()
