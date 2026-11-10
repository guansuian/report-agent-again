"""deps.py 的测试：单例 getter 的「有 / 没有」两条路。"""

import pytest
from fastapi import FastAPI, HTTPException

from app.gateway.deps import (
    get_company_client,
    get_checkpointer,
    get_event_store,
    get_run_context,
    get_run_manager,
    get_stream_bridge,
    get_thread_store,
)
from tests.helpers import make_app, make_request


def test_getters_raise_503_when_shelf_is_empty():
    """服务器没摆货（lifespan 没跑）→ 503，而不是 AttributeError。"""
    app = FastAPI()          # 什么 state 都没设
    request = make_request(app)

    for getter in (
        get_stream_bridge,
        get_run_manager,
        get_event_store,
        get_checkpointer,
        get_thread_store,
        get_company_client,
    ):
        with pytest.raises(HTTPException) as exc_info:
            getter(request)
        assert exc_info.value.status_code == 503


def test_getters_return_the_singletons():
    """摆好货之后，取到的应该就是 app.state 上那个对象本身（不是拷贝）。"""
    app = make_app()
    request = make_request(app)

    assert get_stream_bridge(request) is app.state.stream_bridge
    assert get_run_manager(request) is app.state.run_manager
    assert get_event_store(request) is app.state.event_store
    assert get_checkpointer(request) is app.state.checkpointer
    assert get_thread_store(request) is app.state.thread_store
    assert get_company_client(request) is app.state.company_client


def test_run_context_has_checkpointer():
    """接上会话记忆：checkpointer 从货架拿，event_store 也从货架拿。"""
    app = make_app()
    request = make_request(app)

    ctx = get_run_context(request)
    assert ctx.checkpointer is app.state.checkpointer
    assert ctx.event_store is app.state.event_store
    assert ctx.thread_store is app.state.thread_store
    assert ctx.company_client is app.state.company_client
