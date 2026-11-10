"""报表工具 —— 垂直报表 agent 的业务工具（第一个最小闭环）。

对应真实 deer-flow 的 deerflow/tools/builtins/ 下的一个工具文件：
每个工具是一个用 @tool 装饰的普通函数，docstring 就是写给模型看的"说明书"。

这里先用假数据跑通「模型决定调工具 → 工具返回 → 模型用结果回答」的闭环，
以后把 _query_metric 换成查真实报表数据库 / 报表系统的实现即可。
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool

# 假数据：以后换成查数据库 / 查报表系统的真实实现
_METRICS = {
    "sales": "本月销售额 120 万元，环比 +8%",
    "users": "本月新增用户 3.4 万，环比 +12%",
    "orders": "本月订单数 1.8 万单，客单价 66 元",
}


@tool
def query_report(
    metric: Annotated[
        str,
        "要查的指标名，可选：sales（销售额）、users（新增用户）、orders（订单数）。",
    ],
) -> str:
    """查询报表指标的最新数据。

    当用户问某个指标（销售额、新增用户、订单数等）的数值时，先调这个工具拿数据，
    再用返回的数字回答，不要自己编数字。指标名要用英文 key（sales/users/orders）。
    """
    return _METRICS.get(metric, f"没有 {metric} 这个指标，可用指标：{', '.join(_METRICS)}")
