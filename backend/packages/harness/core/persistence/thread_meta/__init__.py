"""会话元信息存储 —— 对应 deer-flow 的
deerflow/persistence/thread_meta/__init__.py（最小版）。

对外只暴露一个工厂函数 make_thread_store(config)，调用方不用关心
到底用内存版还是数据库版。

为什么 db 版在函数内部才 import：
    sql.py 会拽起 SQLAlchemy 和 ORM model，没配数据库的场景（demo、单测）
    根本用不上，没必要为它付导入代价。和 runs/store、events/store 的工厂
    是同一个写法。
"""

from __future__ import annotations

from core.persistence.thread_meta.base import ThreadMetaStore
from core.persistence.thread_meta.memory import MemoryThreadMetaStore


def make_thread_store(config=None) -> ThreadMetaStore:
    """按配置造会话存储：memory 后端用内存版，postgres 后端用 SQLAlchemy 版。

    对应 deer-flow 的 make_thread_store()。

    Args:
        config: AppConfig。为 None（demo / 单测）或不是 postgres 时走内存版；
                engine 没初始化（backend 配了 postgres 但 init 没跑 / 失败）
                也回退内存版，和 make_run_store / make_run_event_store 一致。
    """
    if config is None or config.database.backend != "postgres":
        return MemoryThreadMetaStore()

    from core.persistence.engine import get_session_factory
    from core.persistence.thread_meta.sql import DbThreadMetaStore

    sf = get_session_factory()
    if sf is None:
        return MemoryThreadMetaStore()
    return DbThreadMetaStore(sf)


__all__ = [
    "MemoryThreadMetaStore",
    "ThreadMetaStore",
    "make_thread_store",
]
