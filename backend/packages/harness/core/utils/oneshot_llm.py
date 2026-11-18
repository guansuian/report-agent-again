"""单次模型调用，参考 DeerFlow 的 utils/oneshot_llm.py。

调用方先准备好提示词、相关记忆和对话文字；这里负责发送消息并返回回复文字。
"""

from langchain_core.messages import HumanMessage, SystemMessage

from core.config.app_config import AppConfig
from core.models.factory import create_chat_model


async def run_oneshot_llm(
    *,
    system_instruction: str,
    user_content: str,
    app_config: AppConfig,
    model_name: str | None = None,
) -> str:
    """调用一次聊天模型；请求失败时直接抛出异常，由调用方处理。

    Args:
        system_instruction: 本次任务的系统要求，例如推荐问题的生成规则。
        user_content: 调用方组装好的资料，可包含相关记忆和最近对话。
        app_config: 应用配置，模型工厂从中读取模型地址和密钥。
        model_name: 可选模型名；省略时由工厂选择配置中的第一个模型。
    """
    # 1. 复用已有模型工厂，不创建 Agent 或绑定业务工具。
    model = create_chat_model(
        name=model_name,
        app_config=app_config,
    )

    # 2. 把调用方准备好的文字包装成两条消息。
    messages = [
        SystemMessage(content=system_instruction),
        HumanMessage(content=user_content),
    ]

    # 3. 调用一次模型；回复文字交给调用方解析，不在这里处理 JSON。
    response = await model.ainvoke(messages)
    return response.text
