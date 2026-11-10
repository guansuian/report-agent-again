"""内存版 run 存储 —— 对应 deer-flow 的
deerflow/runtime/runs/store/memory.py（最小版）。

存储结构：

    self._rows = {
        "run-1": {                          # 一条 run 记录，字段见 base.py
            "run_id": "run-1",
            "thread_id": "thread-1",
            "user_id": "u1",
            "status": "success",
            "error": None,
            "metadata": {},
            "created_at": "2026-11-07T10:00:00+00:00",
            "updated_at": "2026-11-07T10:00:03+00:00",
        },
    }

就是个进程内存里的 dict，服务器一重启全没了。

什么时候用它（不是给生产用的）：
    - 配置 database.backend = memory（不想装 postgres）
    - 跑单元测试（不用起数据库，秒级）
    - RunManager() 不传 store 时的默认值（demo_run.py 就靠这个）

单进程足够用。方法内部没有 await，事件循环里天然原子，先不加锁
（和 events/store/memory.py 的做法一致）。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import UTC, datetime

from core.runtime.runs.schemas import RunStatus
from core.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)


class MemoryRunStore(RunStore):
    """用进程内存存 run 记录的 RunStore。"""

    def __init__(self) -> None:
        # key   = run_id（一次 run 的 id）
        # value = 该 run 的记录 dict，字段和 base.py 约定的一致
        self._rows: dict[str, dict] = {}

    async def put(
        self,
        run_id: str,
        *,
        thread_id: str,
        user_id: str | None = None,
        status: RunStatus | str = RunStatus.pending,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        now = datetime.now(UTC).isoformat()
        existing = self._rows.get(run_id)

        record = {
            "run_id": run_id,
            "thread_id": thread_id,
            # 覆盖写时 user_id 为 None 就保留旧归属，不把老数据冲掉
            "user_id": user_id if user_id is not None else (existing["user_id"] if existing else None),
            # StrEnum 转成纯字符串存，和 db 版的 VARCHAR 列保持一致
            "status": str(status),
            "error": error,
            # 深拷贝：调用方之后改传入的 dict，不能影响已存的记录
            "metadata": deepcopy(metadata) if metadata is not None else {},
            # 覆盖写时保留原来的创建时间（upsert 语义：created_at 只有第一次算数）
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        self._rows[run_id] = record
        # 返回快照，避免调用方拿到存储内部的对象
        return deepcopy(record)

    async def get(self, run_id: str) -> dict | None:
        record = self._rows.get(run_id)
        return deepcopy(record) if record is not None else None

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        rows = [
            r
            for r in self._rows.values()
            if r["thread_id"] == thread_id
            and (user_id is None or r.get("user_id") == user_id)
        ]
        # 最新的在前（ISO 8601 字符串按字典序排就是按时间排）
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [deepcopy(r) for r in rows[:limit]]

    async def update_status(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        error: str | None = None,
    ) -> None:
        record = self._rows.get(run_id)
        if record is None:
            # 静默跳过，不抛异常 —— 后台任务不该因为这个把服务器带崩
            logger.warning("update_status: run %s 不存在，跳过", run_id)
            return

        record["status"] = str(status)
        if error is not None:
            record["error"] = error
        record["updated_at"] = datetime.now(UTC).isoformat()

    async def fail_inflight(self, error: str) -> int:
        # 内存版的数据随进程一起消失，启动时不可能留下上次的记录 → 无事可做。
        # 返回 0 表示「清理了 0 条」，调用方不用特判。
        return 0
