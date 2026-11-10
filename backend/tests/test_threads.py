"""会话元信息路由 + run 联动测试。

覆盖三件事：
1. HTTP CRUD：POST / GET / GET one / PATCH / DELETE
2. 删除时连带删消息事件（以及老数据：只有事件、没有 threads_meta 行）
3. run 生命周期：start_run 自动登记会话，成功回 idle，失败落 error
"""

import asyncio

import app.gateway.services as services
from core.runtime.runs.schemas import RunStatus
from fastapi.testclient import TestClient
from tests.helpers import BoomAgent, FakeAgent, make_app, make_body, make_request


# ---------------------------------------------------------------------------
# ① CRUD
# ---------------------------------------------------------------------------


def test_create_list_get_patch_delete():
    app = make_app()
    client = TestClient(app)

    # 新建：显式给 thread_id
    resp = client.post(
        "/api/threads",
        json={"thread_id": "t1", "display_name": "销售额", "metadata": {"pinned": True}},
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["thread_id"] == "t1"
    assert created["status"] == "idle"

    # 列表
    resp = client.get("/api/threads")
    assert resp.status_code == 200
    assert [row["thread_id"] for row in resp.json()] == ["t1"]

    # 详情
    resp = client.get("/api/threads/t1")
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "销售额"

    # 改标题 + 合并 metadata
    resp = client.patch(
        "/api/threads/t1",
        json={"display_name": "本月销售额", "metadata": {"archived": False}},
    )
    assert resp.status_code == 200
    patched = resp.json()
    assert patched["display_name"] == "本月销售额"
    assert patched["metadata"] == {"pinned": True, "archived": False}

    # 删除
    assert client.delete("/api/threads/t1").status_code == 204
    assert client.get("/api/threads/t1").status_code == 404
    assert client.get("/api/threads").json() == []


def test_create_without_thread_id_generates_uuid():
    app = make_app()
    client = TestClient(app)

    resp = client.post("/api/threads", json={"display_name": "新对话"})

    assert resp.status_code == 200
    assert len(resp.json()["thread_id"]) == 36   # uuid4 的字符串长度


def test_create_is_idempotent_over_http():
    app = make_app()
    client = TestClient(app)

    first = client.post("/api/threads", json={"thread_id": "t1", "display_name": "第一次"})
    second = client.post("/api/threads", json={"thread_id": "t1", "display_name": "第二次"})

    assert first.status_code == second.status_code == 200
    assert second.json()["display_name"] == "第一次"


def test_user_id_roundtrip_over_http():
    app = make_app()
    client = TestClient(app)

    assert client.post(
        "/api/threads", json={"thread_id": "t1", "user_id": "u1"}
    ).status_code == 200
    assert client.post(
        "/api/threads", json={"thread_id": "t2", "user_id": "u2"}
    ).status_code == 200

    # 不带 user_id = 不筛选
    assert len(client.get("/api/threads").json()) == 2
    rows = client.get("/api/threads", params={"user_id": "u1"}).json()
    assert [r["thread_id"] for r in rows] == ["t1"]
    assert rows[0]["user_id"] == "u1"

    # 空白 user_id 在边界上挡掉
    assert client.post("/api/threads", json={"user_id": "   "}).status_code == 422


def test_patch_and_get_missing_thread_return_404():
    client = TestClient(make_app())

    assert client.get("/api/threads/missing").status_code == 404
    assert client.patch("/api/threads/missing", json={"display_name": "x"}).status_code == 404
    assert client.delete("/api/threads/missing").status_code == 404


def test_blank_display_name_is_rejected():
    client = TestClient(make_app())

    assert client.post("/api/threads", json={"display_name": "   "}).status_code == 422
    assert (
        client.patch("/api/threads/t1", json={"display_name": "   "}).status_code == 422
    )


# ---------------------------------------------------------------------------
# ② 删除连带清事件
# ---------------------------------------------------------------------------


def _seed_event(app, thread_id: str, content: str = "你好") -> None:
    async def run() -> None:
        await app.state.event_store.put(
            thread_id=thread_id,
            run_id="r1",
            event_type="llm.human.input",
            category="message",
            content=content,
        )

    asyncio.run(run())


def test_delete_thread_also_deletes_events():
    app = make_app()
    client = TestClient(app)
    client.post("/api/threads", json={"thread_id": "t1"})
    _seed_event(app, "t1")

    assert client.delete("/api/threads/t1").status_code == 204
    assert asyncio.run(app.state.event_store.list_messages("t1")) == []


def test_delete_legacy_thread_that_only_has_events():
    """功能上线前建的会话没有 threads_meta 行，也应该能删。"""
    app = make_app()
    client = TestClient(app)
    _seed_event(app, "legacy")

    assert client.delete("/api/threads/legacy").status_code == 204
    assert asyncio.run(app.state.event_store.list_messages("legacy")) == []


# ---------------------------------------------------------------------------
# ③ run 生命周期联动
# ---------------------------------------------------------------------------


def _patch_agent(monkeypatch, agent_cls):
    """把 services._make_agent 换成假 agent（不调真模型、不花钱）。"""

    async def factory(*, config):
        return agent_cls()

    monkeypatch.setattr(services, "_make_agent", factory)


def _run_to_completion(app, thread_id: str = "t1", body=None):
    """跑一次完整 run，等 SSE 流收尾。"""
    request = make_request(app)

    async def scenario():
        record = await services.start_run(body or make_body(), thread_id, request)
        async for _ in services.sse_consumer(app.state.stream_bridge, record):
            pass
        return record

    return asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_start_run_registers_thread_and_returns_to_idle(monkeypatch):
    _patch_agent(monkeypatch, FakeAgent)
    app = make_app()

    record = _run_to_completion(app)
    thread = asyncio.run(app.state.thread_store.get("t1"))

    assert record.status is RunStatus.success
    assert thread is not None
    assert thread["status"] == "idle"


def test_agent_error_marks_thread_error(monkeypatch):
    _patch_agent(monkeypatch, BoomAgent)
    app = make_app()

    record = _run_to_completion(app)
    thread = asyncio.run(app.state.thread_store.get("t1"))

    assert record.status is RunStatus.error
    assert thread is not None
    assert thread["status"] == "error"


def test_start_run_does_not_overwrite_existing_thread_title(monkeypatch):
    _patch_agent(monkeypatch, FakeAgent)
    app = make_app()
    client = TestClient(app)
    client.post("/api/threads", json={"thread_id": "t1", "display_name": "人工起的标题"})

    _run_to_completion(app)
    thread = asyncio.run(app.state.thread_store.get("t1"))

    assert thread["display_name"] == "人工起的标题"
    assert thread["status"] == "idle"


def test_start_run_propagates_user_id_to_run_events_and_thread(monkeypatch):
    """请求体里的 user_id 要一路写到 runs / run_events / threads_meta。"""
    _patch_agent(monkeypatch, FakeAgent)
    app = make_app()

    record = _run_to_completion(app, body=make_body(user_id="u1"))

    assert record.user_id == "u1"
    run_row = asyncio.run(app.state.run_manager._store.get(record.run_id))
    assert run_row["user_id"] == "u1"
    thread = asyncio.run(app.state.thread_store.get("t1"))
    assert thread["user_id"] == "u1"
    events = asyncio.run(app.state.event_store.list_messages("t1"))
    assert events
    assert all(event["user_id"] == "u1" for event in events)
