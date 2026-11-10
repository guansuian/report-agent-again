"""run 的执行主线 —— 对应 deer-flow 的 deerflow/runtime/runs/worker.py:run_agent
（最小版）。

六步：
    ① 建 journal（有 event_store 才建，负责把消息落 run_events 表）
    ② 发 metadata（前端 useStream 需要 run_id + thread_id）
    ③ 启动闸门：try_start 把 pending 切成 running（不是 pending 就别跑）
    ④ 建 agent + 把 journal 挂成 callback + 挂 checkpointer
    ⑤ 流式跑图：每个 chunk 推给 bridge
    ⑥ 收尾：更新 run 结果 → 关闭 journal → 同步会话状态 → 发 end

关键点（对齐 deer-flow）：
- 收尾的 journal.close() 放在 try/finally 里，保证无论跑图成功 / 抛异常 /
  被取消，buffer 里的消息都能落库、journal 都会被关门。
- flush 在 set_status 之前：先把事件落库，再标终态，避免「终态已写、事件
  却还在 buffer」的窗口。
- close() 内部还会再 flush 一次，但此时 buffer 已空，是空操作（幂等）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from core.clients import CompanyClient
from core.persistence.thread_meta.base import ThreadMetaStore
from core.runtime.events.store.base import RunEventStore
from core.runtime.journal import RunJournal
from core.runtime.request_context import RequestUser, use_company_access
from core.runtime.runs.manager import RunStartOutcome
from core.runtime.runs.schemas import RunStatus
from core.runtime.serialization import serialize
from core.runtime.stream_modes import to_langgraph_stream_modes

logger = logging.getLogger(__name__)


@dataclass
class RunContext:
    """一次 run 的基础设施依赖 —— 对应 deer-flow 的 RunContext（worker.py:713，
    最小版）。

    把 checkpointer / event_store / thread_store / company_client 等单例打包，
    让 run_agent 只收一个 ctx，而不是参数越加越长。
    用户 Token 单独通过 request_user 传递，不保存在这些共用单例中。
    """

    checkpointer: BaseCheckpointSaver | None
    event_store: RunEventStore | None = field(default=None)
    # 会话元信息存储：run 开跑时把会话状态改成 running，收尾时改回
    # idle / error / interrupted。对应 deer-flow RunContext.thread_store（worker.py:713）。
    thread_store: ThreadMetaStore | None = field(default=None)
    company_client: CompanyClient | None = field(default=None)


async def _update_thread_status(ctx: RunContext, thread_id: str, status: str) -> None:
    """把会话状态写进 threads_meta；失败只记日志。

    会话状态是「会话列表」用的展示摘要，不是 run 的事实来源（事实来源是
    runs 表和 checkpoint），所以写失败不该打断 run。对应 deer-flow
    worker.py:1244 / 2064 的 try/except。
    """
    if ctx.thread_store is None:
        return
    try:
        await ctx.thread_store.update_status(thread_id, status)
    except Exception:
        logger.warning(
            "更新会话 %s 状态失败（不影响 run）", thread_id, exc_info=True
        )


async def run_agent(
    bridge,
    run_manager,
    record,
    *,
    ctx: RunContext,
    agent_factory,
    graph_input,
    config: dict[str, Any],
    stream_modes: list[str] | str | None = None,
    request_user: RequestUser | None = None,
) -> None:
    """跑一次 run。所有外部依赖（桥、管理器、上下文、agent 工厂）都从参数传入。

    :arg config: 用于生成agent的配置，如使用什么类型的检查点，使用什么样的回调

    stream_modes：前端想看哪些事件（values / messages-tuple）。
    不传就用默认值（两种都给），见 core/runtime/stream_modes.py。
    """
    checkpointer = ctx.checkpointer
    event_store = ctx.event_store

    started = False
    # journal 在 try 里才建；先声明成 None，finally 里才能安全地 close。
    journal: RunJournal | None = None

    try:
        # lg = LangGraph。stream_modes 是前端的名字（如 messages-tuple），
        # lg_modes 是翻译成 LangGraph 认的名字（messages）之后的那一份。
        # 放在 try 里：它和建 journal 万一抛异常，也要走统一的收尾（标 error、发 end）。
        lg_modes = to_langgraph_stream_modes(stream_modes)

        # ① 建 journal（有 event_store 才建，负责把消息落 run_events 表）
        journal = RunJournal(
            run_id=record.run_id,
            thread_id=record.thread_id,
            event_store=event_store,
            user_id=record.user_id,
        ) if event_store else None

        # ② 发 metadata（前端 useStream 需要 run_id + thread_id）
        await bridge.publish(
            record.run_id,
            "metadata",
            {"run_id": record.run_id, "thread_id": record.thread_id},
        )

        # ③ 启动闸门：只有 pending 的 run 才允许开跑（对应 deer-flow worker.py:1206）
        start_outcome = await run_manager.try_start(record.run_id)
        if start_outcome is not RunStartOutcome.started:
            # 已被取消 / 已经跑过：直接退出，finally 里仍会 close journal。
            # 取消场景的收尾事件（terminal）等 cancel 功能补上后再发。
            logger.warning("Run %s not started (status not pending), skip", record.run_id)
            return

        # ③.5 会话状态 running —— 对应 deer-flow worker.py:1244。
        # 必须写在准入成功之后：没真正开跑的 run 不该把会话标成「生成中」。
        # 放在这个位置也是 deer-flow 的位置（try_start 之后、建 agent 之前）。
        started = True
        await _update_thread_status(ctx, record.thread_id, "running")

        # ④ 建 agent + 把 journal 挂成 callback + 挂 checkpointer
        # 凭据仅在执行上下文里传递；LangChain 在线程中执行同步工具时会继承它。
        with use_company_access(ctx.company_client, request_user):
            agent = await agent_factory(config=config)
            if journal:
                config.setdefault("callbacks", []).append(journal)
            if checkpointer:
                agent.checkpointer = checkpointer

            # ⑤ 流式跑图：每个 chunk 推给 bridge
            #    注意：stream_mode 传「列表」时，LangGraph 固定吐 (mode, chunk) 二元组。
            #    mode 是 "values" 或 "messages"，直接当事件名发给前端。
            async for mode, chunk in agent.astream(
                graph_input, config=config, stream_mode=lg_modes
            ):
                await bridge.publish(record.run_id, mode, serialize(chunk, mode=mode))

        # ⑥ 收尾：先落库，再标终态，最后发 end
        if journal:
            await journal.flush()
        await run_manager.set_status(record.run_id, RunStatus.success)
    except asyncio.CancelledError:
        # 取消不会进入 except Exception，必须单独更新 run，不能只改会话状态。
        await run_manager.set_status(record.run_id, RunStatus.interrupted)
        raise
    except Exception as exc:
        await run_manager.set_status(record.run_id, RunStatus.error, error=str(exc))
        raise
    finally:
        try:
            # 无论成败都关门：flush 剩余 buffer + 断开 store，防止迟到的回调再写。
            if journal is not None:
                try:
                    await journal.close()
                except Exception:
                    logger.warning(
                        "Failed to close journal for run %s", record.run_id, exc_info=True
                    )
            # 只同步真正开跑过的 run；未获准执行的 run 不改会话状态。
            # 直接从 run 结果推导，避免另外维护一份 final_thread_status。
            if started:
                status = "idle" if record.status == RunStatus.success else record.status.value
                await _update_thread_status(ctx, record.thread_id, status)
        finally:
            # 最后才通知消费者结束，避免前端先收到 end、会话却还显示 running。
            await bridge.publish_end(record.run_id)
