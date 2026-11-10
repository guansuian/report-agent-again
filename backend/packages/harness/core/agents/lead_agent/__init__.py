"""lead agent 子包 —— 对应真实 deer-flow 的 deerflow/agents/lead_agent/。

包含真正「把 agent 装配起来」的工厂（agent.py）和提示词渲染（prompt.py）。
"""

from core.agents.lead_agent.agent import make_lead_agent

__all__ = ["make_lead_agent"]
