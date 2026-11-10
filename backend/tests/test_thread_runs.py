"""thread_runs.py 路由测试：读历史。

不经过 POST stream（它会起后台任务、要事件循环），而是直接往 event_store 里
塞两条消息，再打 GET /messages，验证「写完能读回、顺序对、能翻页」。
"""

import asyncio

from fastapi.testclient import TestClient

from tests.helpers import make_app


def _seed(app) -> None:
    """往事件存储塞两条消息（人 + AI），模拟跑完一轮。"""

    async def run() -> None:
        store = app.state.event_store
        await store.put(
            thread_id="t1",
            run_id="r1",
            event_type="llm.human.input",
            category="message",
            content="查一下销售额",
        )
        await store.put(
            thread_id="t1",
            run_id="r1",
            event_type="llm.ai.response",
            category="message",
            content="123 万",
        )

    asyncio.run(run())


def test_list_messages_returns_persisted_messages():
    app = make_app()
    _seed(app)
    client = TestClient(app)

    resp = client.get("/api/threads/t1/messages")

    assert resp.status_code == 200
    body = resp.json()
    assert [m["event_type"] for m in body] == ["llm.human.input", "llm.ai.response"]
    assert [m["seq"] for m in body] == [1, 2]
    assert body[1]["content"] == "123 万"


def test_list_messages_limit_returns_latest():
    # limit=1 应该返回最新一条（AI 回复），不是最早一条
    app = make_app()
    _seed(app)
    client = TestClient(app)

    resp = client.get("/api/threads/t1/messages", params={"limit": 1})

    body = resp.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "llm.ai.response"


def test_list_messages_empty_thread():
    app = make_app()
    client = TestClient(app)

    resp = client.get("/api/threads/empty/messages")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# user_id 过滤
# ---------------------------------------------------------------------------


def test_list_messages_filters_by_user_id():
    app = make_app()

    async def seed() -> None:
        store = app.state.event_store
        for user_id in ("u1", "u2"):
            await store.put(
                thread_id="t1",
                run_id=f"r-{user_id}",
                user_id=user_id,
                event_type="llm.human.input",
                category="message",
                content=user_id,
            )

    asyncio.run(seed())
    client = TestClient(app)

    # 不传 user_id = 不筛选
    assert len(client.get("/api/threads/t1/messages").json()) == 2
    rows = client.get("/api/threads/t1/messages", params={"user_id": "u2"}).json()
    assert [m["content"] for m in rows] == ["u2"]
    assert rows[0]["user_id"] == "u2"


def _seed_runs(app) -> None:
    async def run() -> None:
        await app.state.run_manager.create_run("r1", "t1", user_id="u1")
        await app.state.run_manager.create_run("r2", "t1", user_id="u2")

    asyncio.run(run())


def test_list_runs_filters_by_user_id():
    app = make_app()
    _seed_runs(app)
    client = TestClient(app)

    all_rows = client.get("/api/threads/t1/runs").json()
    assert {row["run_id"] for row in all_rows} == {"r1", "r2"}

    rows = client.get("/api/threads/t1/runs", params={"user_id": "u1"}).json()
    assert [row["run_id"] for row in rows] == ["r1"]


def test_get_run_filters_by_user_id():
    app = make_app()
    _seed_runs(app)
    client = TestClient(app)

    # 归属对不上：返回 404，不暴露「这个 run 存在」
    assert client.get("/api/runs/r2", params={"user_id": "u1"}).status_code == 404
    assert client.get("/api/runs/r2", params={"user_id": "u2"}).json()["run_id"] == "r2"
    # 不传 = 不筛选
    assert client.get("/api/runs/r2").json()["user_id"] == "u2"
