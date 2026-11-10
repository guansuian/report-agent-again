"""工具授权过滤 —— 对应真实 deer-flow 的 deerflow/authz/tool_filter.py:apply_tool_authorization。

在工具真正绑定到模型之前，用授权 provider 把当前身份无权使用的工具过滤掉。
这里用一个「全部允许」的 provider 简化演示。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from core.authz.provider import AllowAllProvider, AuthorizationProvider
from core.config.app_config import AppConfig, get_app_config


def apply_tool_authorization(
    tools: list[BaseTool],
    *,
    app_config: AppConfig | None = None,
    **kwargs,
) -> tuple[list[BaseTool], AuthorizationProvider]:
    """对工具做授权过滤，返回 (过滤后的工具, 本次使用的 provider)。"""
    config = app_config or get_app_config()
    _ = kwargs
    if not config.authorization.enabled:
        # 授权关闭时直接放行（真实 deer-flow 同样如此）
        return list(tools), AllowAllProvider()

    # 真实实现会按配置解析出具体 provider（RBAC 或自定义）
    provider: AuthorizationProvider = AllowAllProvider()
    allowed = [t for t in tools if provider.authorize("tool", "execute", t.name).allow]
    return allowed, provider
