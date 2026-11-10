"""中间件链 —— 对应真实 deer-flow 的 deerflow/agents/middlewares/（约 50 个文件，每个中间件一个文件）。

中间件是 deer-flow 最重要的扩展机制：它们围绕 LLM 调用（before_model / after_model）
插入横切逻辑，例如记忆写入、标题生成、循环检测、token 统计、澄清拦截等。

真实 deer-flow 里每个中间件是一个独立文件（title_middleware.py、
memory_middleware.py、summarization_middleware.py ...），这里为了教学，
把代表性的几个集中定义在同一个文件里，每个类只写职责说明。

**顺序即语义**：deer-flow 对中间件顺序极其敏感（见 agent.py:build_middlewares 的注释）。
LangChain 按「后注册的先执行 after_model」的顺序调度，顺序错一个都会出问题。
"""

from __future__ import annotations


class Middleware:
    """中间件基类（简化占位）。

    真实基类是 langchain.agents.middleware.AgentMiddleware，暴露 before_model /
    after_model / wrap_tool_call 等钩子。这里只做占位，接受任意 kwargs。
    """

    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"


# ---- 下面是 deer-flow 真实中间件链里的代表性成员（各取一句说明） ----

class DynamicContextMiddleware(Middleware):
    """把当前日期（以及可选记忆）以 <system-reminder> 注入首条 HumanMessage，
    这样系统提示词可以完全静态化，便于复用 prefix-cache。"""


class SkillActivationMiddleware(Middleware):
    """当用户以 /skill-name 开头时，加载对应的 SKILL.md 全文。"""


class DurableContextMiddleware(Middleware):
    """捕获子 agent 委派记录和已加载的技能文件，注入 durable 上下文通道。"""


class SummarizationMiddleware(Middleware):
    """当对话过长时，用模型把历史压缩成 summary_text。"""


class TodoMiddleware(Middleware):
    """plan 模式下提供 write_todos 工具，跟踪多步任务。"""


class TokenUsageMiddleware(Middleware):
    """统计本次运行的 token 用量。"""


class TitleMiddleware(Middleware):
    """首轮交换后，用模型给会话生成一个标题。"""


class MemoryMiddleware(Middleware):
    """把对话写入长期记忆（真实实现支持多种 backend：mem0 / honcho / deermem）。"""


class ViewImageMiddleware(Middleware):
    """当模型支持视觉时，把图片细节注入到 LLM 请求前。"""


class SubagentLimitMiddleware(Middleware):
    """限制并行 / 总共委派的子 agent 数量。"""


class LoopDetectionMiddleware(Middleware):
    """检测并打破重复的工具调用死循环。"""


class TokenBudgetMiddleware(Middleware):
    """强制执行每次运行的 token 预算上限。"""


class ModelContentCompatibilityMiddleware(Middleware):
    """在请求视图里清理模型不兼容的内容块（读时修复）。"""


class TerminalResponseMiddleware(Middleware):
    """保证模型真空中止时仍有一个可展示的兜底回复。"""


class SafetyFinishReasonMiddleware(Middleware):
    """当 provider 以安全原因终止时，抑制工具执行。"""


class ClarificationMiddleware(Middleware):
    """永远排在最后：拦截模型的「澄清请求」，转成向用户提问。"""
