"""run 事件类型目录 —— 对应 deer-flow 的
deerflow/runtime/events/catalog.py（最小版）。

两个字段分工：
- event_type：细分类，具体发生了什么；
- category：粗分类，属于哪一大类，读历史时按它筛。

| event_type          | category   | 含义          |
| ------------------- | ---------- | ------------- |
| llm.human.input     | message    | 用户发的话    |
| llm.ai.response     | message    | 模型回答      |
| llm.tool.result     | message    | 工具返回结果  |
| run.start           | trace      | 运行开始      |
| run.end             | outputs    | 运行结束      |
| run.error           | error      | 运行失败      |
| subagent.step       | subagent   | 子 agent 步骤 |
| middleware:summarize| middleware | 压缩上下文    |
| context:memory      | context    | 注入记忆      |

记忆方法：一个 category 下有很多 event_type；一个 event_type 只属于一个
category。前端历史接口只查 category="message"，再按 event_type 区分用户消
息 / AI 消息 / 工具结果。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunEventDefinition:
    """一个事件类型和它所属的分类。"""

    event_type: str
    category: str


# message：会被展示成对话消息的事件
LLM_HUMAN_INPUT_EVENT = RunEventDefinition("llm.human.input", "message")
LLM_AI_RESPONSE_EVENT = RunEventDefinition("llm.ai.response", "message")
LLM_TOOL_RESULT_EVENT = RunEventDefinition("llm.tool.result", "message")

# trace / outputs / error：运行轨迹，不直接展示成消息
RUN_START_EVENT = RunEventDefinition("run.start", "trace")
RUN_END_EVENT = RunEventDefinition("run.end", "outputs")
RUN_ERROR_EVENT = RunEventDefinition("run.error", "error")

# 子 agent / 中间件 / 上下文：辅助信息
SUBAGENT_STEP_EVENT = RunEventDefinition("subagent.step", "subagent")
MIDDLEWARE_SUMMARIZE_EVENT = RunEventDefinition("middleware:summarize", "middleware")
MEMORY_CONTEXT_EVENT = RunEventDefinition("context:memory", "context")
