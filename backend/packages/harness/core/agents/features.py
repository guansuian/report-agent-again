"""运行时特性开关 —— 对应真实 deer-flow 的 deerflow/agents/features.py:RuntimeFeatures。

SDK 层 create_deerflow_agent 用这套声明式开关来「按需装配中间件」。
每个特性（sandbox / memory / vision / subagent / ...）可以是：
  - False：不启用
  - True：用内置默认中间件
  - 一个 AgentMiddleware 实例：自定义替换
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeFeatures:
    sandbox: object = True
    memory: object = True
    vision: object = True
    subagent: object = True
    summarization: object = True
    auto_title: object = True
    loop_detection: object = True
    token_budget: object = False
    guardrail: object = False
