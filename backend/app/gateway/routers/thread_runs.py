"""run 相关的 HTTP 路由 —— 对应 deer-flow 的 app/gateway/routers/thread_runs.py（最小版）。

deer-flow 这个文件有 1923 行、几十个接口（runs 列表 / wait / join / cancel ...）。
最小版提供流式运行、消息历史、run 查询和会话用量汇总。其中：

    POST /api/threads/{thread_id}/runs/stream
    GET  /api/threads/{thread_id}/token-usage

职责只有三件事：收请求 → 调用服务或管理器 → 返回 SSE 或 JSON。
业务逻辑一律不写在这里。

路径怎么读：

    /api/threads/t1/runs/stream
    └──┬──┘ └──┬──┘ └─┬─┘ └─┬──┘
      前缀    会话 id   资源   动作
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.gateway.deps import get_event_store, get_run_manager, get_stream_bridge
from app.gateway.run_models import RunCreateRequest
from app.gateway.services import sse_consumer, start_run

router = APIRouter(prefix="/api/threads", tags=["runs"])
# run 详情按 id 查，路径不挂在 threads 下面，所以单独一个 router。
runs_router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("/{thread_id}/runs/stream")
async def stream_run(
    thread_id: str,
    body: RunCreateRequest,
    request: Request,
) -> StreamingResponse:
    """创建一次 run，并用 SSE 把事件流式推回浏览器。

    三个参数由 FastAPI 自动准备：
        thread_id —— 从 URL 里取
        body      —— 请求体 JSON，按 RunCreateRequest 校验
        request   —— 这次请求本身，deps.py 的 getter 要用它去 app.state 拿单例
    """
    bridge = get_stream_bridge(request)          # ① 拿流桥（后面要给 sse_consumer 用）
    record = await start_run(body, thread_id, request)   # ② 后台开跑，立刻返回 record

    # ③ 把「读流桥」包成一个流式响应交回去
    return StreamingResponse(
        sse_consumer(bridge, record),
        media_type="text/event-stream",          # 告诉浏览器：这不是普通 JSON，是事件流
        headers={
            # 别缓存：缓存了就不是「来一条看一条」了
            "Cache-Control": "no-cache",
            # 让 nginx 之类的反向代理别攒着：攒着发就失去流式的意义
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{thread_id}/messages")
async def list_messages(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    before_seq: int | None = Query(default=None, ge=1),
    after_seq: int | None = Query(default=None, ge=1),
    user_id: str | None = Query(default=None, max_length=64),
) -> list[dict]:
    """返回一个 thread 里可显示的消息（跨所有 run），按 seq 升序。

    对应 deer-flow 的 list_thread_messages（thread_runs.py:1480）。那边还叠了
    鉴权、反馈、隐藏 run、user 过滤等一大套；最小版只做「把事件存储里的消息
    读回来」，分页参数原样透传给 event_store.list_messages。

    为什么要读 event_store 而不是 checkpointer：
      - event_store 已经按 category="message" 投影好了（只有人/AI 消息）
      - 自带 limit / before_seq / after_seq 分页
      - checkpointer 存的是 LangGraph 原始 state，含 system/tool 消息，还要自己翻
    """
    store = get_event_store(request)
    return await store.list_messages(
        thread_id,
        limit=limit,
        before_seq=before_seq,
        after_seq=after_seq,
        user_id=user_id,
    )


@router.get("/{thread_id}/runs")
async def list_thread_runs(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    user_id: str | None = Query(default=None, max_length=64),
) -> list[dict]:
    """返回这个会话跑过的 run，最新的在前。

    数据来自 runs 表（重启后还在），不是内存。
    对应 deer-flow 的 list_thread_runs（那边还叠了鉴权、keyset 翻页、隐藏 run）。
    """
    return await get_run_manager(request).list_by_thread(
        thread_id, limit=limit, user_id=user_id
    )


@router.get("/{thread_id}/token-usage")
async def thread_token_usage(
    thread_id: str,
    request: Request,
    user_id: str | None = Query(default=None, max_length=64),
) -> dict:
    """返回当前会话已结束 run 的 Token 合计及运行次数。

    前端打开会话、运行结束后调用；传入 user_id 时只汇总该用户的记录。
    没有符合条件的运行记录时，五项统计值均为 0。
    """
    usage = await get_run_manager(request).aggregate_tokens_by_thread(
        thread_id, user_id=user_id
    )
    return {"thread_id": thread_id, **usage}


@runs_router.get("/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    user_id: str | None = Query(default=None, max_length=64),
) -> dict:
    """按 run_id 查一次 run 的详情（状态、错误、时间）。

    查不到返回 404 —— 前端拿到 404 就知道这个 id 是错的，不用猜。
    user_id 传了就按归属过滤，对不上同样返回 404（不暴露「存在但不属于你」）。
    """
    row = await get_run_manager(request).fetch(run_id, user_id=user_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} 不存在")
    return row
