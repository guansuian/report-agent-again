"""工具收集 —— 对应真实 deer-flow 的 deerflow/tools/tools.py:get_available_tools。

真实实现会：
  1. 按 tool_groups / mcp_plugins / subagent_enabled 等开关收集内置工具；
  2. 合并 MCP 提供的工具；
  3. 再交给 apply_tool_authorization 做权限过滤。

这里只收集本项目已实现的工具；以后工具多了，再在这里按开关筛选。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from core.config.app_config import AppConfig
from core.tools.builtins.clarification_tool import ask_clarification_tool
from core.tools.report import species_trend


def get_available_tools(*, app_config: AppConfig | None = None, **kwargs) -> list[BaseTool]:
    """收集本次运行可用的所有工具（当前两个：查报表、向用户提问）。

    真实实现会按 kwargs 里的 model_name / groups / mcp_plugins / subagent_enabled
    等开关做真实的筛选。这里先固定返回全部。

    ask_clarification 也在这里登记：模型只能调用注册过的工具，
    不登记它就没有「反问用户」这个动作可选。
    """
    _ = app_config, kwargs
    return [species_trend, ask_clarification_tool]
