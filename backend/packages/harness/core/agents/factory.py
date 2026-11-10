"""SDK 层工厂 —— 对应真实 deer-flow 的 deerflow/agents/factory.py:create_deerflow_agent。

这是「纯参数」入口：不读任何配置文件，调用方直接传入 model / tools / middleware。
它处在 langchain.create_agent（最底层）和 make_lead_agent（读配置的应用层）之间，
供「以库的方式」嵌入 deer-flow 的调用方使用。

关键设计：用 RuntimeFeatures 声明式开关，按需装配中间件 + 自动追加特性工具。
"""

from __future__ import annotations

from typing import Any

from core.agents.features import RuntimeFeatures
from langchain.agents import create_agent


def create_deerflow_agent(
    model,
    tools: list | None = None,
    *,
    system_prompt: str | None = None,
    middleware: list | None = None,
    features: RuntimeFeatures | None = None,
    plan_mode: bool = False,
) -> Any:
    """从纯 Python 参数创建一个 deer-flow agent（简化版）。

    真实实现里 middleware 与 features 互斥：middleware 是「完整接管」，
    features 是「按开关装配」。这里保留这个语义。
    """
    if middleware is not None and features is not None:
        raise ValueError("'middleware' 和 'features' 不能同时传")

    if middleware is not None:
        chain = list(middleware)
    else:
        chain = _assemble_from_features(features or RuntimeFeatures(), plan_mode=plan_mode)

    return create_agent(
        model=model,
        tools=tools,
        middleware=chain,
        system_prompt=system_prompt,
    )


def _assemble_from_features(features: RuntimeFeatures, *, plan_mode: bool) -> list:
    """按特性开关装配中间件链（简化占位）。

    真实实现按固定顺序（Sandbox 基础 → 工具错误处理 → 总结 → Todo → 标题 →
    记忆 → 视觉 → 子 agent → 循环检测 → token 预算 → 澄清）逐个判断：
      - False：跳过
      - True：用内置默认中间件
      - 实例：直接用
    """
    _ = features, plan_mode
    return []
