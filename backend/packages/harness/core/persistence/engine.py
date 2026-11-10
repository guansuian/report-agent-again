"""SQLAlchemy engine 生命周期 —— 对应 deer-flow 的 persistence/engine.py（最小版）。

启动时建 async engine + session factory，并自动建库建表；关闭时 dispose。
只支持 postgres（asyncpg 驱动），memory 后端直接跳过（用 MemoryRunEventStore）。

关键点（为什么这么做）：
1. SQLAlchemy 的 async 要用 asyncpg 驱动，URL 是 postgresql+asyncpg://。
   config 里的 app_sqlalchemy_url 已经把 postgres_url 换好前缀。
2. 建库：CREATE DATABASE 不能在事务里，得先连 postgres 维护库单独建，
   建完再连目标库建表。这就是 _auto_create_db 干的事。
3. 建表：Base.metadata.create_all 读 ORM model 自动建（run_events +
   run_event_thread_seq），替代以前手写 SQL 的建表脚本。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from core.persistence import models  # noqa: F401  导入让 model 注册进 Base.metadata
from core.persistence.base import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# SQLSTATE invalid_catalog_name：连接串里的数据库不存在
_MISSING_DATABASE_SQLSTATE = "3D000"


def _to_asyncpg_url(url: str) -> str:
    """把普通 postgres DSN 换成 SQLAlchemy asyncpg 的 URL。

    postgresql://user@host/db  →  postgresql+asyncpg://user@host/db
    （asyncpg 还接受 postgres:// 这种短别名，也一并换）
    """
    if url.startswith("postgresql+asyncpg://"):
        return url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def _is_missing_database_error(exc: BaseException) -> bool:
    """判断异常是不是「数据库不存在」（SQLSTATE 3D000）。

    asyncpg 抛的异常带 sqlstate 属性，但 SQLAlchemy 会包一层 DBAPIError，
    所以要顺着 orig / __cause__ / __context__ 往下找（照抄 deer-flow 的
    _has_sqlstate 思路）。
    """
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        cur = pending.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if getattr(cur, "sqlstate", None) == _MISSING_DATABASE_SQLSTATE:
            return True
        for attr in ("orig", "__cause__", "__context__"):
            inner = getattr(cur, attr, None)
            if isinstance(inner, BaseException):
                pending.append(inner)
    return False


async def _auto_create_db(url: str) -> None:
    """目标库不存在时，连 postgres 维护库 CREATE DATABASE。

    CREATE DATABASE 不能在事务里，所以维护连接用 isolation_level="AUTOCOMMIT"。
    """
    parsed = make_url(url)
    dbname = parsed.database
    if not dbname:
        raise ValueError("连接串里没有库名，无法自动建库")

    maint_url = parsed.set(database="postgres")
    maint_engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        async with maint_engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        logger.info("自动建库：%s", dbname)
    finally:
        await maint_engine.dispose()


async def init_engine(url: str) -> None:
    """建 async engine + session factory，并自动建库建表。

    如果连目标库失败是因为「库不存在」，先建库再重试一次。
    """
    global _engine, _session_factory

    asyncpg_url = _to_asyncpg_url(url)
    _engine = create_async_engine(asyncpg_url)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        if not _is_missing_database_error(exc):
            raise
        # 库不存在 → 先建库，再重建 engine 重试建表
        await _engine.dispose()
        await _auto_create_db(asyncpg_url)
        _engine = create_async_engine(asyncpg_url)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    logger.info("SQLAlchemy engine 就绪：%s", asyncpg_url)


async def init_engine_from_config(config) -> None:
    """按 AppConfig 初始化 engine。memory 后端不需要，直接跳过。"""
    if config.database.backend != "postgres":
        return
    await init_engine(config.database.app_sqlalchemy_url)


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """返回 async session factory，或 None（engine 没初始化 / memory 后端）。"""
    return _session_factory


def get_engine() -> AsyncEngine | None:
    """返回 async engine，或 None。"""
    return _engine


async def close_engine() -> None:
    """关闭 engine（服务器关停时调用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("SQLAlchemy engine 已关闭")
