"""MemoryThreadMetaStore 的行为测试 —— 把 base.py 里写的契约钉住。

不测 DbThreadMetaStore：它需要一台 postgres，单元测试不该依赖外部服务。
db 版和 memory 版实现的是同一份接口，语义由这里这套用例约束；
db 版在接真库联调时再用同样的问题验收。
"""

import asyncio

from core.persistence.thread_meta.memory import MemoryThreadMetaStore


def _run(coro):
    """不装 pytest-asyncio，异步场景统一用 asyncio.run 包一层。"""
    return asyncio.run(coro)


def test_create_and_get():
    store = MemoryThreadMetaStore()

    record = _run(store.create("t1", display_name="销售额", metadata={"pinned": True}))

    assert record["thread_id"] == "t1"
    assert record["display_name"] == "销售额"
    assert record["status"] == "idle"
    assert record["metadata"] == {"pinned": True}
    assert record["created_at"] == record["updated_at"]

    assert _run(store.get("t1")) == record


def test_create_is_idempotent_and_does_not_overwrite():
    store = MemoryThreadMetaStore()
    first = _run(store.create("t1", display_name="第一次", metadata={"a": 1}))

    second = _run(store.create("t1", display_name="第二次", metadata={"b": 2}))

    # 已存在就原样返回：标题和 metadata 都不该被第二次调用覆盖
    assert second == first
    assert _run(store.get("t1"))["display_name"] == "第一次"


def test_missing_thread_returns_none_and_false():
    store = MemoryThreadMetaStore()

    assert _run(store.get("nope")) is None
    assert _run(store.update_display_name("nope", "x")) is False
    assert _run(store.update_status("nope", "error")) is False
    assert _run(store.update_metadata("nope", {"x": 1})) is False
    assert _run(store.delete("nope")) is False


def test_search_is_newest_first_and_paginates():
    async def scenario():
        store = MemoryThreadMetaStore()
        await store.create("t1", display_name="旧")
        await asyncio.sleep(0.002)
        await store.create("t2", display_name="新")

        assert [r["thread_id"] for r in await store.search()] == ["t2", "t1"]

        # 更新 t1 的标题后，它应该排到最前面（最近更新时间变了）
        await asyncio.sleep(0.002)
        await store.update_display_name("t1", "更新过")
        assert [r["thread_id"] for r in await store.search()] == ["t1", "t2"]

        # 分页：limit + offset
        assert [r["thread_id"] for r in await store.search(limit=1, offset=0)] == ["t1"]
        assert [r["thread_id"] for r in await store.search(limit=1, offset=1)] == ["t2"]

    _run(scenario())


def test_search_filters_by_status():
    async def scenario():
        store = MemoryThreadMetaStore()
        await store.create("idle-one")
        await store.create("error-one")
        await store.update_status("error-one", "error")

        rows = await store.search(status="error")
        assert [r["thread_id"] for r in rows] == ["error-one"]

    _run(scenario())


def test_update_display_name_and_status():
    async def scenario():
        store = MemoryThreadMetaStore()
        await store.create("t1")

        assert await store.update_display_name("t1", "本月销售额") is True
        assert await store.update_status("t1", "running") is True

        record = await store.get("t1")
        assert record["display_name"] == "本月销售额"
        assert record["status"] == "running"

    _run(scenario())


def test_update_metadata_merges_and_touch_controls_timestamp():
    async def scenario():
        store = MemoryThreadMetaStore()
        await store.create("t1", metadata={"pinned": True})

        assert await store.update_metadata("t1", {"archived": False}) is True
        record = await store.get("t1")
        # 合并而不是覆盖：原来的 pinned 还在
        assert record["metadata"] == {"pinned": True, "archived": False}

        before = record["updated_at"]
        await asyncio.sleep(0.002)
        await store.update_metadata("t1", {"note": "只改标记"}, touch=False)
        after = await store.get("t1")

        assert after["metadata"] == {"pinned": True, "archived": False, "note": "只改标记"}
        # touch=False：不刷新 updated_at，会话不会因此顶到列表最前
        assert after["updated_at"] == before

    _run(scenario())


def test_delete():
    store = MemoryThreadMetaStore()
    _run(store.create("t1"))

    assert _run(store.delete("t1")) is True
    assert _run(store.get("t1")) is None
    assert _run(store.delete("t1")) is False


# ---------------------------------------------------------------------------
# user_id：归属标签 + 列表过滤（不是权限边界）
# ---------------------------------------------------------------------------


def test_create_stores_user_id_and_search_filters_by_it():
    async def scenario():
        store = MemoryThreadMetaStore()
        await store.create("t1", user_id="u1")
        await store.create("t2", user_id="u2")
        await store.create("t3")   # 未归属

        assert (await store.get("t1"))["user_id"] == "u1"
        assert [r["thread_id"] for r in await store.search(user_id="u1")] == ["t1"]
        # user_id=None = 不筛选，三行都返回
        assert len(await store.search()) == 3
        assert len(await store.search(user_id=None)) == 3

    _run(scenario())


def test_create_claims_unowned_thread_but_never_overwrites_owner():
    async def scenario():
        store = MemoryThreadMetaStore()

        # 先有无主记录，第一次带 user_id 的 create 认领它
        await store.create("t1")
        claimed = await store.create("t1", user_id="u1")
        assert claimed["user_id"] == "u1"

        # 已有归属时，另一个人再 create 不会抢走
        still_u1 = await store.create("t1", user_id="u2")
        assert still_u1["user_id"] == "u1"

        # 已有归属时，不带 user_id 的 create 也不会把归属清空
        still_u1 = await store.create("t1")
        assert still_u1["user_id"] == "u1"

    _run(scenario())
