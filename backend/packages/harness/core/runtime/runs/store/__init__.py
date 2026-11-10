"""run 记录的存储。

对外只暴露一个工厂函数 make_run_store(config)，调用方不用关心
到底用内存版还是数据库版。
"""

from core.runtime.runs.store.base import RunStore
from core.runtime.runs.store.memory import MemoryRunStore


def make_run_store(config=None) -> RunStore:
    """按配置造 run 存储：memory 后端用内存版，postgres 后端用 SQLAlchemy 版。

    对应 deer-flow 的 make_run_store()。config 是 AppConfig，靠
    database.backend 决定用哪个实现。

    注意：db 版在函数内部才 import，不在文件顶部 import。
    因为 db.py 会拽起 SQLAlchemy 和 model，没配数据库的场景（demo、单测）
    根本用不上，没必要为它付导入代价。
    """
    if config is None or config.database.backend != "postgres":
        return MemoryRunStore()

    from core.persistence.engine import get_session_factory
    from core.runtime.runs.store.db import DbRunStore

    sf = get_session_factory()
    if sf is None:
        # engine 没初始化（backend 配了 postgres 但 init 没跑/失败）→ 回退内存版
        return MemoryRunStore()
    return DbRunStore(sf)


__all__ = [
    "MemoryRunStore",
    "RunStore",
    "make_run_store",
]
