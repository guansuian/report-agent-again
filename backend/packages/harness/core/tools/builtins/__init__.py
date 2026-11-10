"""内置工具 —— 对应真实 deer-flow 的 deerflow/tools/builtins/（最小版）。

每个内置工具一个文件，导出的都是 @tool 装饰后的 BaseTool 对象。
"""

from core.tools.builtins.clarification_tool import ask_clarification_tool

__all__ = ["ask_clarification_tool"]
