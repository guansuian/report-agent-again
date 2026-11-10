"""验证从 HTTP 请求头到后台 run、同步工具的凭据传递，不访问真实公司接口。"""

import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.tools import tool

import app.gateway.services as services
from app.gateway.deps import get_company_request_user
from core.clients import CompanyClient, get_company_client
from core.config.company_config import CompanyConfig
from core.runtime.request_context import RequestUser, get_request_user
from core.runtime.runs.schemas import RunStatus
from tests.helpers import make_app, make_body, make_request


def test_gateway_reads_headers_without_putting_token_into_body():
    request = SimpleNamespace(headers={
        "Authorization": "Bearer test-token", "X-Device-Fingerprint": "test-device",
    })
    user = get_company_request_user(request, user_id="u1")
    assert user == RequestUser("u1", "test-token", "test-device")
    assert "test-token" not in repr(user)
    assert get_company_request_user(SimpleNamespace(headers={}), user_id="u1") is None


@pytest.mark.parametrize("header", ["Basic value", "Bearer", "Bearer ", "Bearer a b"])
def test_gateway_rejects_malformed_header(header):
    with pytest.raises(HTTPException) as error:
        get_company_request_user(SimpleNamespace(headers={"Authorization": header}), user_id="u1")
    assert error.value.status_code == 401


def test_invalid_header_does_not_create_a_run():
    app = make_app()
    with TestClient(app) as client:
        response = client.post("/api/threads/t1/runs/stream",
                               headers={"Authorization": "Basic value"},
                               json={"user_id": "u1", "input": {"messages": []}})
    assert response.status_code == 401
    assert asyncio.run(app.state.run_manager.list_by_thread("t1")) == []


def test_concurrent_workers_pass_each_users_token_to_sync_tools(monkeypatch):
    async def scenario():
        app = make_app()
        requests = []
        arrivals = 0
        both_started = asyncio.Event()

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"code": 200, "data": {"count": 12}})

        @tool
        def company_probe() -> dict:
            """查询测试用公司接口。"""
            return get_company_client().get("/api/count")

        class ProbeAgent:
            async def astream(self, graph_input, config=None, stream_mode=None):
                nonlocal arrivals
                # 两个 run 同时进入，防止顺序执行掩盖共享 Token 的问题。
                arrivals += 1
                if arrivals == 2:
                    both_started.set()
                await both_started.wait()
                # 同步 @tool 的 ainvoke 会在线程池执行，验证上下文确实传到了工具线程。
                result = await company_probe.ainvoke({}, config=config)
                yield "values", result

        async def factory(*, config):
            return ProbeAgent()

        monkeypatch.setattr(services, "_make_agent", factory)
        with CompanyClient("https://company.test", transport=httpx.MockTransport(handle)) as client:
            app.state.company_client.close()
            app.state.company_client = client

            async def start(user_id, token):
                request = make_request(app)
                request.headers = {"Authorization": f"Bearer {token}"}
                return await services.start_run(make_body(user_id=user_id), f"thread-{user_id}", request)

            records = await asyncio.gather(start("u1", "test-token-a"), start("u2", "test-token-b"))
            await asyncio.gather(*(record.task for record in records))
            assert all(record.status is RunStatus.success for record in records)
            assert sorted(request.headers["Authorization"] for request in requests) == [
                "Bearer test-token-a", "Bearer test-token-b",
            ]
            # 发送给前端、存入事件历史的内容不能包含凭据。
            for record in records:
                frames = [frame async for frame in services.sse_consumer(app.state.stream_bridge, record)]
                history = await app.state.event_store.list_messages(record.thread_id)
                assert "test-token" not in str(frames) + json.dumps(history, default=str)
            with pytest.raises(ValueError, match="Token"):
                get_request_user()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_lifespan_closes_company_connection(monkeypatch):
    module = importlib.import_module("app.gateway.app")
    app = make_app()
    cfg = SimpleNamespace(company=CompanyConfig(base_url="https://company.test"))

    async def no_op(*args):
        pass

    @asynccontextmanager
    async def checkpoint():
        yield app.state.checkpointer

    monkeypatch.setattr(module, "get_app_config", lambda: cfg)
    monkeypatch.setattr(module, "init_engine_from_config", no_op)
    monkeypatch.setattr(module, "close_engine", no_op)
    monkeypatch.setattr(module, "make_run_event_store", lambda _: app.state.event_store)
    monkeypatch.setattr(module, "make_thread_store", lambda _: app.state.thread_store)
    monkeypatch.setattr(module, "make_run_store", lambda _: app.state.run_manager._store)
    monkeypatch.setattr(module, "make_checkpointer", checkpoint)

    async def scenario():
        app.state.company_client.close()
        async with module.lifespan(app):
            client = app.state.company_client
            assert not client._http.is_closed
        assert client._http.is_closed

    asyncio.run(scenario())
