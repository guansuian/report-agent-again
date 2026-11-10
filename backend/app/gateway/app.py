"""HTTP 入口 —— 对应 deer-flow 的 app/gateway/app.py（最小版）。

deer-flow 这个文件有 1492 行（一堆中间件、几十个 router、多进程安全门、
孤儿 run 恢复……）。最小版只做三件事：

    ① lifespan —— 服务器启动时把单例摆到 app.state 上
    ② create_app —— 建 FastAPI 实例、挂路由
    ③ app —— 给 uvicorn 用的入口对象

启动方式（在 backend 目录下）：

    ../.venv/bin/uvicorn app.gateway.app:app --reload --port 8000

拆开看：app.gateway.app 是「模块路径」，最后的 :app 是「模块里的变量名」。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.gateway.routers import thread_runs, threads
from core.clients import CompanyClient
from core.config.app_config import get_app_config
from core.persistence.engine import close_engine, init_engine_from_config
from core.persistence.thread_meta import make_thread_store
from core.runtime.checkpointer.provider import make_checkpointer
from core.runtime.events.store import make_run_event_store
from core.runtime.runs.manager import RunManager
from core.runtime.runs.store import make_run_store
from core.runtime.stream_bridge.memory import MemoryStreamBridge

# 全局日志开关：root logger 默认是 WARNING 且没有 handler，
# 业务代码里的 logger.info(...) 会全部被丢掉（uvicorn 只配了自己的 logger）。
# 这一行让核心模块的 INFO 日志能打到终端，比如 TokenUsageMiddleware 的用量统计。
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """服务器开张 / 打烊。

    yield 之前 = 启动时执行一次；yield 之后 = 关闭时执行一次。

    单例必须全服务器只有一份，所以在这里创建、挂到 app.state 上；
    路由请求进来时，deps.py 的 getter 再去 app.state 上取。

    为什么挂 app.state 而不是写成模块级全局变量：
      - 测试时能建多个互不干扰的 app 实例
      - 以后多进程 / 多 worker 部署，每个进程各有一份，语义清楚
    """
    app.state.stream_bridge = MemoryStreamBridge()      # 事件出口（跑出来的东西往这推）

    # 事件存储 / run 记录都按配置选后端：memory 或 postgres（SQLAlchemy）。
    # postgres 后端必须先 init_engine（建 engine + 自动建库建表），再据此造 store，
    # 所以 event_store 和 run_manager 都必须排在 init_engine 后面。
    cfg = get_app_config()
    await init_engine_from_config(cfg)
    app.state.event_store = make_run_event_store(cfg)
    # 会话元信息存储：会话列表 / 标题 / 状态。memory 或 postgres，和事件存储同一个开关。
    app.state.thread_store = make_thread_store(cfg)
    app.state.run_manager = RunManager(store=make_run_store(cfg))   # run 登记处（谁在跑、跑成没）
    # 启动清账：上次进程崩掉后留在库里的 pending/running 记录，这里统一标成 error，
    # 否则前端会把早就不存在的 run 一直显示成「正在生成中」。
    await app.state.run_manager.recover_orphans()

    # 会话记忆（checkpointer）按配置选后端：memory 或 postgres。
    # async with 保证服务器运行期间连接池一直开着，关闭时才关（yield 之后的退出代码执行）。
    async with make_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer

        # 只共用连接，不在客户端保存用户凭据。with 退出时自动 close。
        with CompanyClient(cfg.company.base_url, timeout=cfg.company.timeout) as client:
            app.state.company_client = client
            yield  # ← 服务器在这段时间里对外服务

    # 服务器关停：关闭 SQLAlchemy engine（postgres 后端时才非空）
    await close_engine()


STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


def create_app(*, static_dir: Path = STATIC_DIR) -> FastAPI:
    """组装 FastAPI 应用。

    抽成函数而不是直接写模块级代码，是为了测试里能反复建新实例，
    互不干扰（模块级 `app = FastAPI(...)` 建一次就固定了）。
    """
    fastapi_app = FastAPI(title="报表 agent", lifespan=lifespan)
    fastapi_app.include_router(thread_runs.router)
    fastapi_app.include_router(thread_runs.runs_router)
    fastapi_app.include_router(threads.router)

    # npm run build 会把页面生成到 backend/static。
    # 放在接口路由之后：/api 和 /docs 先匹配，其余路径再找前端文件。
    # 还没有构建前端时，后端接口仍可单独启动。
    if (static_dir / "index.html").is_file():
        fastapi_app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return fastapi_app


# uvicorn 找的就是这个变量：app.gateway.app:app
app = create_app()
