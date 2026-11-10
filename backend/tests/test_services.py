"""services.py 的测试：SSE 格式、后台启动、成功/失败收尾、消息落库。

不装 pytest-asyncio，异步场景统一用 asyncio.run(...) 包一层，
少一个依赖，读起来也更直白。
"""

import asyncio

import app.gateway.services as services
from app.gateway.services import build_run_config, format_sse
from core.runtime.runs.schemas import RunStatus
from tests.helpers import BoomAgent, FakeAgent, make_app, make_body, make_request


# ---------------------------------------------------------------------------
# ① format_sse：文本格式
# ---------------------------------------------------------------------------


def test_format_sse_exact_frame():
    frame = format_sse("values", {"a": 1}, event_id="3")
    # 结尾必须是两个换行：EventSource 靠空行判断「这一帧结束了」
    assert frame == 'event: values\ndata: {"a": 1}\nid: 3\n\n'


def test_format_sse_without_event_id():
    assert format_sse("end", None) == "event: end\ndata: null\n\n"


def test_format_sse_keeps_chinese_readable():
    # ensure_ascii=False：中文原样输出，不转成 你好
    assert "你好" in format_sse("x", "你好")


# ---------------------------------------------------------------------------
# ② build_run_config：给 checkpointer 塞 thread_id
# ---------------------------------------------------------------------------


def test_build_run_config_injects_thread_id():
    cfg = build_run_config(None, "t1")
    assert cfg["configurable"]["thread_id"] == "t1"


def test_build_run_config_preserves_existing_keys():
    # 已有 configurable 字段要保留，只是盖掉 thread_id
    cfg = build_run_config(
        {"configurable": {"model_name": "deepseek-chat"}}, "t2"
    )
    assert cfg["configurable"]["model_name"] == "deepseek-chat"
    assert cfg["configurable"]["thread_id"] == "t2"


def test_build_run_config_does_not_mutate_input():
    # 不能改调用方传进来的原 dict（后面 run_agent 还要往里塞 callbacks）
    original = {"configurable": {"model_name": "deepseek-chat"}}
    build_run_config(original, "t3")
    assert "thread_id" not in original["configurable"]


# ---------------------------------------------------------------------------
# ② start_run + sse_consumer：把假 agent 换进去，跑完整条流
# ---------------------------------------------------------------------------


def _patch_agent(monkeypatch, agent_cls):
    """把 services._make_agent 换成返回假 agent 的工厂（不调真模型、不花钱）。"""

    async def factory(*, config):
        return agent_cls()

    monkeypatch.setattr(services, "_make_agent", factory)


async def _run_scenario(body=None):
    """跑一次完整流程，返回各个检查点。"""
    app = make_app()
    request = make_request(app)

    record = await services.start_run(body or make_body(), "t1", request)
    status_right_after_start = record.status   # 关键：start_run 返回这一刻的状态

    frames = [
        frame
        async for frame in services.sse_consumer(app.state.stream_bridge, record)
    ]
    messages = await app.state.event_store.list_messages("t1")
    return record, status_right_after_start, frames, messages


def _run_scenario_with_timeout(body=None):
    """加超时：如果流没有正常收尾，测试会 5 秒后失败，而不是永远挂着。"""
    return asyncio.run(asyncio.wait_for(_run_scenario(body), timeout=5))


def test_start_run_returns_before_agent_finishes(monkeypatch):
    """start_run 必须「递完单子立刻回来」，不能等 agent 跑完。"""
    _patch_agent(monkeypatch, FakeAgent)
    record, status_right_after_start, _, _ = _run_scenario_with_timeout()

    assert status_right_after_start is RunStatus.pending   # 还没开跑
    assert record.task is not None                         # 后台任务已挂在 record 上


def test_success_stream_and_persisted_messages(monkeypatch):
    _patch_agent(monkeypatch, FakeAgent)
    record, _, frames, messages = _run_scenario_with_timeout()

    assert record.status is RunStatus.success

    # 帧顺序：metadata → values → messages（一段段）→ end
    assert frames[0].startswith("event: metadata")
    assert frames[1].startswith("event: values")
    assert sum(f.startswith("event: messages") for f in frames) == len(FakeAgent.PIECES)
    assert frames[-1].startswith("event: end")

    # journal 把用户问 + AI 答都落了库
    assert [m["event_type"] for m in messages] == ["llm.human.input", "llm.ai.response"]


def test_client_can_turn_off_messages_mode(monkeypatch):
    """只要 values 时，就不该出现 messages 帧（stream_mode 有被真正用上）。"""
    _patch_agent(monkeypatch, FakeAgent)
    _, _, frames, _ = _run_scenario_with_timeout(make_body(stream_mode=["values"]))

    assert frames[1].startswith("event: values")
    assert not any(f.startswith("event: messages") for f in frames)


def test_agent_error_still_ends_the_stream(monkeypatch):
    """agent 抛异常时：终态是 error，但流照样要收尾 —— 否则前端永远转圈。"""
    _patch_agent(monkeypatch, BoomAgent)
    record, _, frames, _ = _run_scenario_with_timeout()

    assert record.status is RunStatus.error
    assert "模型超时了" in record.error
    assert frames[-1].startswith("event: end")
