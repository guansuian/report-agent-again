"""验证 run 收尾顺序和取消；只用内存存储，不调用真实模型和数据库。"""

import asyncio

import pytest

import app.gateway.services as services
import core.runtime.runs.worker as worker
from core.runtime.runs.schemas import RunStatus
from tests.helpers import BoomAgent, FakeAgent, make_app, make_body, make_request


@pytest.mark.parametrize(
    "agent_cls, run_status, thread_status",
    [(FakeAgent, RunStatus.success, "idle"), (BoomAgent, RunStatus.error, "error")],
)
def test_end_is_sent_after_journal_close_and_status_sync(
    monkeypatch, agent_cls, run_status, thread_status
):
    """在真正发送 end 的那一刻检查，不能等整个后台任务结束后才检查。"""
    async def scenario():
        app = make_app()
        journals = []
        snapshots = []
        original_journal = worker.RunJournal

        class ObservedJournal(original_journal):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.close_completed = False
                journals.append(self)

            async def close(self, **kwargs):
                await super().close(**kwargs)
                self.close_completed = True

        async def factory(*, config):
            return agent_cls()

        bridge = app.state.stream_bridge
        original_end = bridge.publish_end

        async def observed_end(run_id):
            snapshots.append((
                await app.state.run_manager.fetch(run_id),
                await app.state.thread_store.get("t1"),
                journals[0].close_completed,
            ))
            await original_end(run_id)

        monkeypatch.setattr(worker, "RunJournal", ObservedJournal)
        monkeypatch.setattr(services, "_make_agent", factory)
        monkeypatch.setattr(bridge, "publish_end", observed_end)

        record = await services.start_run(make_body(), "t1", make_request(app))
        frames = [frame async for frame in services.sse_consumer(bridge, record)]
        await record.task

        assert frames[-1].startswith("event: end")
        assert len(snapshots) == 1
        run, thread, journal_closed = snapshots[0]
        assert run["status"] == run_status.value
        assert thread["status"] == thread_status
        assert journal_closed
        if run_status is RunStatus.error:
            assert "模型超时了" in run["error"]
        else:
            assert await app.state.event_store.list_messages("t1")

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_cancellation_persists_both_statuses_before_end(monkeypatch):
    async def scenario():
        app = make_app()
        entered = asyncio.Event()

        class WaitingAgent:
            async def astream(self, *args, **kwargs):
                yield "values", {"messages": []}
                entered.set()
                await asyncio.Event().wait()

        async def factory(*, config):
            return WaitingAgent()

        monkeypatch.setattr(services, "_make_agent", factory)
        record = await services.start_run(make_body(), "t1", make_request(app))
        await entered.wait()
        assert (await app.state.thread_store.get("t1"))["status"] == "running"

        record.task.cancel()
        frames = [
            frame async for frame in services.sse_consumer(app.state.stream_bridge, record)
        ]
        # 收到 end 后立刻检查存储记录，确认不是只改了内存里的 RunRecord。
        run = await app.state.run_manager._store.get(record.run_id)
        thread = await app.state.thread_store.get("t1")
        assert frames[-1].startswith("event: end")
        assert record.status is RunStatus.interrupted
        assert run["status"] == "interrupted"
        assert thread["status"] == "interrupted"
        # 取消异常仍然向外传播，不能把取消伪装成一次成功返回。
        with pytest.raises(asyncio.CancelledError):
            await record.task

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_direct_worker_failure_sets_run_status():
    """不经过网关，也必须能正确记录失败并结束流。"""
    async def scenario():
        app = make_app()
        record = await app.state.run_manager.create_run("r1", "t1")
        await app.state.thread_store.create("t1")

        async def factory(*, config):
            raise RuntimeError("创建 Agent 失败")

        with pytest.raises(RuntimeError, match="创建 Agent 失败"):
            await worker.run_agent(
                app.state.stream_bridge,
                app.state.run_manager,
                record,
                ctx=worker.RunContext(
                    checkpointer=app.state.checkpointer,
                    event_store=app.state.event_store,
                    thread_store=app.state.thread_store,
                ),
                agent_factory=factory,
                graph_input={},
                config={},
            )

        assert record.status is RunStatus.error
        assert (await app.state.thread_store.get("t1"))["status"] == "error"
        frames = [
            frame async for frame in services.sse_consumer(app.state.stream_bridge, record)
        ]
        assert frames[-1].startswith("event: end")

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
