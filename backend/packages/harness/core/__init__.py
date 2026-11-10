"""core —— deer-flow 后端的简化教学版。

真实 deer-flow 里这个包叫 ``deerflow``，位于
``backend/packages/harness/deerflow/``。这里改名为 ``core``，只保留
「lead agent 装配」这条主链路的结构，其余子包用注释/占位说明职责。

目录一览（每个目录对应真实 deer-flow 的一个子包）：

    core/agents/        agent 装配（factory / lead_agent / middlewares / memory） ★核心
    core/models/        模型工厂（按 config 创建聊天模型）
    core/tools/         工具收集与注册
    core/authz/         授权（模型 / 工具 / 技能三层）
    core/tracing/       可观测性（Langfuse / LangSmith）
    core/config/        配置（AppConfig 及几十个配置 dataclass）
    core/runtime/       运行时（checkpoint / runs / 事件流）
    core/persistence/   持久化（数据库层）
    core/skills/        技能系统
    core/subagents/     子 agent
    core/sandbox/       沙箱执行
    core/mcp/           MCP 集成
    core/extensions/    扩展插件机制
    其余见各子包 __init__.py 的说明。

入口（backend/langgraph.json）：
    "lead_agent": "core.agents:make_lead_agent"
"""

__version__ = "0.1.0"
