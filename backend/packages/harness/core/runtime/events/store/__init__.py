"""run 事件存储。"""

from core.runtime.events.store.base import RunEventStore
from core.runtime.events.store.memory import MemoryRunEventStore


def make_run_event_store(config=None) -> RunEventStore:
    """按配置造事件存储：memory 后端用内存版，postgres 后端用 SQLAlchemy 版。

    对应 deer-flow 的 make_run_event_store()。config 是 AppConfig，靠
    database.backend 决定用哪个实现。
    """
    if config is None or config.database.backend != "postgres":
        return MemoryRunEventStore()

    from core.persistence.engine import get_session_factory
    from core.runtime.events.store.db import DbRunEventStore

    sf = get_session_factory()
    if sf is None:
        # engine 没初始化（backend 配了 postgres 但 init 没跑/失败）→ 回退内存版
        return MemoryRunEventStore()
    return DbRunEventStore(sf)


__all__ = [
    "MemoryRunEventStore",
    "RunEventStore",
    "make_run_event_store",
]
