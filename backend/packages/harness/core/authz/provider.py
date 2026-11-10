"""授权提供者抽象 —— 对应真实 deer-flow 的 deerflow/authz/provider.py + rbac.py。

真实实现有 AuthorizationProvider 接口，内置 RBAC 实现，也支持自定义 provider。
授权判定按 resource + action + target 三元组进行。这里只保留一个极简接口 +
一个「全部允许」的示例实现。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AuthzDecision:
    allow: bool


class AuthorizationProvider:
    """授权提供者接口（简化）。"""

    def authorize(self, resource: str, action: str, target: str) -> AuthzDecision:
        raise NotImplementedError

    def filter_resources(self, resource: str, names: list[str]) -> list[str]:
        """返回 names 里当前身份可见/可用的子集。"""
        raise NotImplementedError


class AllowAllProvider(AuthorizationProvider):
    """示例：全部允许。真实 deer-flow 的 RBAC 会按角色/资源做真实判定。"""

    def authorize(self, resource: str, action: str, target: str) -> AuthzDecision:
        return AuthzDecision(allow=True)

    def filter_resources(self, resource: str, names: list[str]) -> list[str]:
        return list(names)
