"""系统提示词渲染 —— 对应真实 deer-flow 的 deerflow/agents/lead_agent/prompt.py。

真实实现会把 skills 索引、子 agent 额度、工具清单、交互策略、日期等渲染进一个
结构化的系统提示词（XML 块风格，含 <skill_index>、<tool_groups> 等）。
这里只做一个极简版本。
"""

from __future__ import annotations

from typing import Any


def apply_prompt_template(
    *,
    subagent_enabled: bool = False,
    max_concurrent_subagents: int = 0,
    max_total_subagents: int = 0,
    agent_name: str | None = None,
    available_skills: set | None = None,
    memory_enabled: bool = True,
    **kwargs: Any,
) -> str:
    """渲染系统提示词（简化：拼一个可读的字符串）。"""
    _ = kwargs
    subagent_line = "子 agent: " + (
        f"启用 (并发 {max_concurrent_subagents}, 总量 {max_total_subagents})"
        if subagent_enabled
        else "禁用"
    )
    lines = [
        "<system>",
        "你是一个乐于助人的 AI 助手。",
        f"agent 名称: {agent_name or 'default'}",
        subagent_line,
        f"记忆: {'启用' if memory_enabled else '禁用'}",
        f"可用技能: {sorted(available_skills) if available_skills else '（无）'}",
        "</system>",
    ]
    return "\n".join(lines)


def has_bash_tool(tools: list) -> bool:
    """是否包含 bash 工具（真实实现据此决定是否在提示词里给 bash 使用说明）。"""
    return any(getattr(t, "name", None) == "bash" for t in tools)
