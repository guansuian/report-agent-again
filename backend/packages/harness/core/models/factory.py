"""模型工厂 —— 对应真实 deer-flow 的 deerflow/models/factory.py:create_chat_model。

真实实现会：
  1. 从 config.models 里按名字找到 ModelConfig；
  2. 用 ``use`` 字段（如 "langchain_openai:ChatOpenAI"）反射出具体模型类；
  3. 把 thinking / reasoning_effort / 各 provider 的归一化参数塞进构造函数；
  4. 按 attach_tracing 决定是否给模型挂 tracing callback；
  5. 返回一个 langchain 的 BaseChatModel 实例。

这里简化：只走 OpenAI 兼容接口，用 ChatOpenAI，从 ModelConfig 的
name / api_key / base_url 直接构造真模型。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from core.config.app_config import AppConfig, get_app_config


def create_chat_model(
    name: str | None = None,
    thinking_enabled: bool = False,
    *,
    app_config: AppConfig | None = None,
    attach_tracing: bool = True,
    **kwargs,
) -> ChatOpenAI:
    """按名字创建一个聊天模型实例（ChatOpenAI）。

    attach_tracing 的含义（对应真实 deer-flow 的 INVARIANT 约定）：tracing
    callback 只应在 graph 根节点挂一次，所以 lead agent 装配内部调用时传
    attach_tracing=False，避免同一次 LLM 调用产生重复 trace。这里还没接
    tracing，参数先保留占位。

    thinking_enabled 同样先保留占位：deepseek 的 reasoning 参数不在本次
    最小闭环里，跑通主链路后再接。
    """
    config = app_config or get_app_config()
    if name is None:
        name = config.models[0].name
    model_config = config.get_model_config(name)
    if model_config is None:
        raise ValueError(f"Model {name!r} not found in config")

    # 默认请求流式 Token 用量；调用方传了 stream_usage 时，保留传入的值。
    kwargs.setdefault("stream_usage", True)

    return ChatOpenAI(
        model=name,
        api_key=model_config.api_key,
        base_url=model_config.base_url,
        **kwargs,
    )
