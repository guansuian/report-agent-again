"""仅在执行期间传递公司接口凭据，不进入 State、检查点或模型参数。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.clients.company_client import CompanyClient


@dataclass(frozen=True)
class RequestUser:
    """本次请求的身份。user_id 仍是调用方标签，权限由公司接口检查 Token。"""

    user_id: str | None
    token: str = field(repr=False)
    device_fingerprint: str | None = field(default=None, repr=False)


_current_user: ContextVar[RequestUser | None] = ContextVar("company_user", default=None)
_current_client: ContextVar[CompanyClient | None] = ContextVar("company_client", default=None)


@contextmanager
def use_company_access(client: CompanyClient | None, user: RequestUser | None):
    """worker 开始执行时设置，退出时恢复；不同任务使用各自的 Token。"""
    client_marker = _current_client.set(client)
    user_marker = _current_user.set(user)
    try:
        yield
    finally:
        _current_user.reset(user_marker)
        _current_client.reset(client_marker)


def get_request_user() -> RequestUser:
    user = _current_user.get()
    if user is None or not user.token:
        raise ValueError("访问公司接口需要本次请求的用户 Token。")
    return user


def get_request_client() -> CompanyClient:
    client = _current_client.get()
    if client is None:
        raise RuntimeError("公司接口客户端尚未放入本次运行上下文。")
    return client
