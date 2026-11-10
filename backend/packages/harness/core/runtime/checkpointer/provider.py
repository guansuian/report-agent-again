"""checkpointer 工厂 —— 对应 deer-flow 的
deerflow/runtime/checkpointer/async_provider.py（最小版）。

提供一个 async 上下文管理器 make_checkpointer()：进管理器时按 config.yaml 的
database.backend 选后端，出管理器时自动关掉连接池。

支持的后端：memory（默认）、postgres。

用法（FastAPI lifespan）：
    async with make_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer

为什么用 async 上下文管理器而不是普通函数：
    连接池、连接这类资源要「进时开、出时关」。async with 保证无论服务器
    正常关闭还是异常退出，退出代码（关池）都会执行，不会泄漏连接。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver

from core.config.app_config import get_app_config

logger = logging.getLogger(__name__)


def _build_postgres_pool(dsn: str):
    """按 DSN 建一个 psycopg 异步连接池（照抄 deer-flow 的关键参数）。"""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    # autocommit：每条语句自己提交，不手动管事务（AsyncPostgresSaver 靠它）
    # prepare_threshold=0：禁用预处理语句缓存，省得和 autocommit 打架
    # row_factory=dict_row：查出的行是 dict，AsyncPostgresSaver 按 key 取值
    # keepalives*：TCP 心跳，防止连接被中间网络设备静默掐掉
    kwargs = {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "keepalives": 1,
        "keepalives_idle": 60,
        "keepalives_interval": 10,
        "keepalives_count": 6,
    }
    return AsyncConnectionPool(
        dsn,
        kwargs=kwargs,
        check=AsyncConnectionPool.check_connection,
    )


@contextlib.asynccontextmanager
async def make_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    """按配置造一个 checkpointer，用完自动清理。

    - database.backend = "postgres" → AsyncPostgresSaver（落库，重启还在）
    - 其他（默认 "memory"）      → MemorySaver（内存，重启就丢）
    """
    cfg = get_app_config()

    if cfg.database.backend == "postgres":
        dsn = cfg.database.postgres_url
        if not dsn:
            raise ValueError(
                "database.postgres_url 没配置，postgres 后端需要这条连接串"
            )

        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = _build_postgres_pool(dsn)
        async with pool:
            # setup() 会建 LangGraph 自己的 checkpoint 表
            # （checkpoints / checkpoint_writes / checkpoint_blobs），缺才建，
            # 和建表脚本的 threads_meta/runs/run_events 是两码事。
            saver = AsyncPostgresSaver(conn=pool)
            await saver.setup()
            yield saver
        return

    from langgraph.checkpoint.memory import MemorySaver

    yield MemorySaver()
