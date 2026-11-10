"""网关依赖：取 app.state 上的单例，读取请求头中的公司接口身份。

单例由 app.py 的 lifespan 创建和关闭；这里只负责取用，缺失时返回 503。
用户 Token 只交给本次执行上下文，不存到共用单例中。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException, Request
from langgraph.checkpoint.base import BaseCheckpointSaver

from core.clients import CompanyClient
from core.persistence.thread_meta.base import ThreadMetaStore
from core.runtime.events.store.base import RunEventStore
from core.runtime.request_context import RequestUser
from core.runtime.runs.manager import RunManager
from core.runtime.runs.worker import RunContext
from core.runtime.stream_bridge.base import StreamBridge


def _require(attr: str, label: str) -> Callable[[Request], Any]:
    """生成一个 getter：从 app.state 取单例，取不到就报 503。

    对应 deer-flow 的 _require（deps.py:1092）。这些 getter 长得一模一样，
    用这个工厂批量生成，不用手写三遍。
    """

    def getter(request: Request) -> Any:
        val = getattr(request.app.state, attr, None)
        if val is None:
            raise HTTPException(status_code=503, detail=f"{label} 还没就位")
        return val

    getter.__name__ = f"get_{attr}"
    return getter


# key = app.state 上的属性名；value = 取不到时给前端的提示。
get_stream_bridge: Callable[[Request], StreamBridge] = _require("stream_bridge", "流桥")
get_run_manager: Callable[[Request], RunManager] = _require("run_manager", "run 登记处")
get_event_store: Callable[[Request], RunEventStore] = _require("event_store", "事件存储")
get_checkpointer: Callable[[Request], BaseCheckpointSaver] = _require("checkpointer", "会话记忆")
get_thread_store: Callable[[Request], ThreadMetaStore] = _require("thread_store", "会话存储")
get_company_client: Callable[[Request], CompanyClient] = _require("company_client", "公司接口客户端")


def get_company_request_user(request: Request, *, user_id: str | None) -> RequestUser | None:
    """从请求头读取公司 Token。未传时仍可使用模拟工具，实际查公司接口时才报缺失。"""
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if not separator or scheme.lower() != "bearer" or not token or any(c.isspace() for c in token):
        raise HTTPException(
            status_code=401, detail="Authorization 格式应为 Bearer <用户Token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return RequestUser(
        user_id=user_id, token=token,
        device_fingerprint=request.headers.get("X-Device-Fingerprint"),
    )


def get_run_context(request: Request) -> RunContext:
    """把 app.state 上的单例拼成 RunContext（run_agent 要的 ctx）。

    对应 deer-flow 的 get_run_context（deps.py:1250）。checkpointer 从 app.state 拿，
    拿不到报 503 —— 宁可明确报错，也不要静默失去会话记忆。
    """
    return RunContext(
        checkpointer=get_checkpointer(request),
        event_store=get_event_store(request),
        thread_store=get_thread_store(request),
        company_client=get_company_client(request),
    )
