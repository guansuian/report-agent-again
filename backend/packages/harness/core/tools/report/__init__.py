"""业务报表工具 —— 垂直报表 agent 的领域工具，一个文件一个工具。

对应老项目 report_agent/tools/ 下的 species_trend_report.py 等平铺文件；
本项目收进 report/ 子包，与 common/（共享参数）和 builtins/（框架工具）
分层。每个工具用 @report_tool() 装饰，遵守 common 里的参数/校验/错误约定。
"""

from core.tools.report.species_trend import species_trend

__all__ = ["species_trend"]
