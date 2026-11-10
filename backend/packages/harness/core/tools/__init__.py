"""工具子系统 —— 对应真实 deer-flow 的 deerflow/tools/。

负责收集/注册 agent 可用的工具。真实实现还有 builtins/（内置工具）和
skill_manage_tool 等；这里只导出收集入口 get_available_tools。
"""

from core.tools.tools import get_available_tools

__all__ = ["get_available_tools"]
