"""网关的业务流程层 —— 对应 deer-flow 的 app/gateway/services.py（最小版）。

deer-flow 的 services.py 有 3201 行、68 个函数。这里只保留跑通主线的三个：

    format_sse    一条事件 → 一段 SSE 文本
    start_run     登记 run，把跑图交给后台任务
    sse_consumer  从流桥读事件，一帧帧吐给前端

谁调谁：

    浏览器 ──POST──> router ──> start_run ──create_task──> run_agent
                                     │                        │
                                     │ 立刻返回 record         │ publish
                                     ▼                        ▼
                              StreamingResponse <── sse_consumer <── 流桥

为什么拆出来：router 只管「收请求、回响应」。怎么跑一次 run 的流程放在这里，
以后命令行 / 定时任务想触发一次 run，直接调 start_run 就行，不用造 HTTP 请求。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request

from app.gateway.deps import (
    get_company_request_user,
    get_run_context,
    get_run_manager,
    get_stream_bridge,
    get_thread_store,
)
from app.gateway.run_models import RunCreateRequest
from core.persistence.thread_meta.base import ThreadMetaStore
from core.runtime.runs.manager import RunRecord
from core.runtime.runs.worker import run_agent
from core.runtime.stream_bridge.base import END_SENTINEL, StreamBridge
from core.runtime.stream_modes import normalize_stream_modes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ① 事件 → SSE 文本
# ---------------------------------------------------------------------------


def format_sse(event: str, data: Any, *, event_id: str | None = None) -> str:
    """把一条事件变成浏览器 EventSource 认的文本帧。

    格式固定，最后必须有一个空行 —— EventSource 靠空行判断「这一帧结束了」：

        event: values
        data: {"messages": [...]}
        id: 3
        <空行>

    参数说明：
        event    —— 事件名，如 metadata / values / end
        data     —— 事件内容，会被转成 JSON 放进 data: 行
        event_id —— 可选的事件编号；断线重连时浏览器会把它放回请求头
    """
    # default=str      : 遇到 datetime 等不能直接 JSON 化的对象就转字符串，别崩
    # ensure_ascii=False: 中文原样输出，不会变成 你好
    payload = json.dumps(data, default=str, ensure_ascii=False)
    parts = [f"event: {event}", f"data: {payload}"]
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append("")   # 空行
    parts.append("")   # 再一个，保证整帧以 \n\n 结尾
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# ② 登记 run，扔到后台
# ---------------------------------------------------------------------------


async def _make_agent(*, config: dict[str, Any]):
    """把同步的 make_lead_agent 包成异步工厂（run_agent 里要 await 它）。

    延迟 import：模块顶层 import 会顺带加载配置和模型工厂，配置一有问题，
    连「只是 import services」都会失败。放到真正要用时再加载，错误更晚也更明确。
    """
    from core.agents.lead_agent.agent import make_lead_agent

    return make_lead_agent(config)


def build_run_config(config: dict[str, Any] | None, thread_id: str) -> dict[str, Any]:
    """复制一份 config，并写入 thread_id —— checkpointer 靠它定位会话。

    对应 deer-flow 的 build_run_config（services.py:1477）。

    为什么不用 body.config 原对象、要层层复制：
      - 后面 run_agent 还会往 config 里塞 callbacks，改原对象会污染请求体
      - configurable 可能是 None / 已有字段，得先展开再盖 thread_id

    config["configurable"]["thread_id"]：key = "thread_id"（LangGraph 固定认这个名字），
    value = 本次 run 的 thread_id（同一个会话多次请求要传同一个，才能续上历史）。
    """
    merged = dict(config or {})
    configurable = dict(merged.get("configurable") or {})
    configurable["thread_id"] = thread_id
    merged["configurable"] = configurable
    return merged


# ---------------------------------------------------------------------------
# ②.5 会话元信息：登记
# ---------------------------------------------------------------------------


async def ensure_thread_meta(
    thread_store: ThreadMetaStore,
    thread_id: str,
    *,
    user_id: str | None = None,
) -> None:
    """确保会话在 threads_meta 里有一条记录（没有就建）。

    对应 deer-flow 的 _ensure_thread_metadata（services.py:258）。那边还要处理
    归属、项目空间、标题、删除竞态；最小版只做「没有就建」，并且靠 store 的
    create 幂等来容忍重复调用。

    user_id：会话还没有归属时顺手认领（store 的 create 只填空，不覆盖已有归属）。

    失败只记日志、不阻断 run：会话元信息是「列表展示」用的，不该因为它写库
    失败就让用户的问题跑不了（deer-flow 对非必需场景也是 non-fatal）。
    """
    try:
        await thread_store.create(thread_id, user_id=user_id)
    except Exception:
        logger.warning("会话 %s 登记失败（不阻断 run）", thread_id, exc_info=True)


async def start_run(
    body: RunCreateRequest,
    thread_id: str,
    request: Request,
) -> RunRecord:
    """登记一次 run，把跑图扔到后台，立刻返回 record。

    对应 deer-flow 的 start_run（services.py:2142）。那边还有鉴权、幂等、
    并发准入、租约、配置合并等一大套；最小版只保留主线。
    """
    # ① 从 app.state 取单例（deps.py 的 getter）
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    run_ctx = get_run_context(request)
    thread_store = get_thread_store(request)
    # 只从请求头读取，不能放进 body.config / State，否则可能被保存或发给模型。
    request_user = get_company_request_user(request, user_id=body.user_id)

    # ② 登记：状态 pending，run_id 每跑一次换一个
    run_id = str(uuid.uuid4())
    record = await run_mgr.create_run(run_id, thread_id, user_id=body.user_id)

    # ②.5 会话登记：第一次在这个 thread 跑时，往 threads_meta 插一行。
    # 必须在返回前做完，否则前端拿到响应、去查会话列表时还看不到这个会话。
    await ensure_thread_meta(thread_store, thread_id, user_id=body.user_id)

    graph_input = body.input or {}
    config = build_run_config(body.config, thread_id)   # 塞 thread_id，供 checkpointer 定位会话
    stream_modes = normalize_stream_modes(body.stream_mode)   # 没传就是默认的两种

    async def worker() -> None:
        """后台调用 run_agent；状态更新和流结束统一由 run_agent 负责。"""
        try:
            await run_agent(
                bridge,
                run_mgr,
                record,
                ctx=run_ctx,
                agent_factory=_make_agent,
                graph_input=graph_input,
                config=config,
                stream_modes=stream_modes,
                request_user=request_user,
            )
        except Exception:
            logger.exception("Run %s failed", run_id)

    # ③ create_task 而不是 await：把活交给后台，这里立刻返回。
    #    把 task 存到 record 上，任务才不会被 Python 垃圾回收掉；
    #    以后要做「取消 run」，就是 record.task.cancel()。
    record.task = asyncio.create_task(worker())
    return record


# ---------------------------------------------------------------------------
# ③ 流桥 → SSE 帧
# ---------------------------------------------------------------------------


async def sse_consumer(bridge: StreamBridge, record: RunRecord) -> AsyncIterator[str]:
    """从流桥读事件，一帧帧 yield 给 StreamingResponse。

    对应 deer-flow 的 sse_consumer（services.py:2966）。那边还有心跳、断线策略、
    StreamGap 补齐等；最小版只保留「读到结束哨兵就收工」。
    """
    async for entry in bridge.subscribe(record.run_id):
        if entry is END_SENTINEL:
            yield format_sse("end", None)
            return
        yield format_sse(entry.event, entry.data, event_id=entry.id or None)
