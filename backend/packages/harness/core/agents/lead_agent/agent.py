import logging

from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph

from core.agents.middlewares.clarification_middleware import ClarificationMiddleware
from core.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from core.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from core.config.app_config import AppConfig, get_app_config
from core.models.factory import create_chat_model
from core.tools import get_available_tools

logger = logging.getLogger(__name__)


def make_lead_agent(config: dict) -> CompiledStateGraph:
    # 1. 读取系统级配置
    app_config = get_app_config()

    # 2. 提取本次运行的配置，复制一份，避免修改传入的数据
    runtime_config = dict(config.get("configurable") or {})

    # 3. 合并 context，同名配置以 context 为准
    runtime_config.update(config.get("context") or {})

    # 4. 选择模型：请求里指定的 model_name 优先；名字无效或没传，回退到配置里第一个模型
    requested = runtime_config.get("model_name")
    default_name = app_config.models[0].name
    if requested and app_config.get_model_config(requested):
        model_name = requested
    else:
        if requested and requested != default_name:
            logger.warning("模型 %r 不在配置中，回退到默认模型 %r", requested, default_name)
        model_name = default_name

    # 5. 创建模型实例（装配内部一律 attach_tracing=False，避免重复 trace）
    model = create_chat_model(model_name, attach_tracing=False, app_config=app_config)

    # 6. 收集工具（对应 deer-flow 的 get_available_tools，当前只有一个 query_report）
    tools = get_available_tools(app_config=app_config)

    # 7. 构建中间件链（对应 deer-flow 的 build_middlewares）
    middlewares = build_middlewares(app_config)

    # 8. 组装成可运行的图（对应 langchain.agents.create_agent）
    return create_agent(
        model=model,
        tools=tools,
        middleware=middlewares,
        system_prompt=(
            "你是报表查询助手。用户问指标数值时，先调 query_report 拿数据，再用结果回答，不要编数字。"
            "信息不足时（没说维度、时间范围、口径），先用 ask_clarification 问清楚，不要猜。"
        ),
    )

def build_middlewares(app_config: AppConfig) -> list:
    """构建中间件链 —— 对应真实 deer-flow 的 build_middlewares（最小版）。

    真实实现按「一个 if 一个 append」的模式，根据配置 / 运行时 / 能力开关
    决定加哪些中间件；顺序即语义，先注册的在最外层。
    """
    middlewares = [
        ToolErrorHandlingMiddleware(),   # 工具报错兜底：工具异常 → error ToolMessage，不崩
    ]
    if app_config.token_usage_enabled:   # 配置开关：config.yaml 里的 token_usage_enabled
        middlewares.append(TokenUsageMiddleware())
    # 必须放最后（最内层，贴着工具层）：
    #   - wrap_tool_call：要抢在工具执行前截住 ask_clarification
    #   - after_model：LangChain 的 after_model 是倒序跑的，放最后 = 模型一输出完它先看到，
    #     能在别的中间件改 AIMessage 之前把同轮的兄弟工具删掉
    #   - 放最前的话，_handle 抛异常时外面的 ToolErrorHandling 接不住，整个 run 会报错
    middlewares.append(ClarificationMiddleware())
    return middlewares