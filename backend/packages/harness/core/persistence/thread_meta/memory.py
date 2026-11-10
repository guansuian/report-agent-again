"""内存版会话元信息存储 —— 对应 deer-flow 的
deerflow/persistence/thread_meta/memory.py（最小版）。

存储结构：

    self._rows = {
        "thread-1": {
            "thread_id": "thread-1",
            "user_id": "u1",
            "display_name": "本月销售额",
            "status": "idle",
            "metadata": {"pinned": True},
            "created_at": "2026-11-09T10:00:00+00:00",
            "updated_at": "2026-11-09T10:00:03+00:00",
        },
    }

就是个进程内存里的 dict，服务器一重启全没了。

和 MemoryRunStore / MemoryRunEventStore 一样，什么时候用它：
    - 配置 database.backend = memory（不想装 postgres）
    - 跑单元测试（不用起数据库，秒级）
    - 单进程演示

deer-flow 的 memory 版包了一层 LangGraph BaseStore；最小版没有引入 Store，
直接用自己的 dict，语义（契约）保持不变即可。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from core.persistence.models.thread_meta import DEFAULT_THREAD_STATUS
from core.persistence.thread_meta.base import ThreadMetaStore


def _now_iso() -> str:
    """当前 UTC 时间，ISO 8601 字符串（和 db 版返回格式保持一致）。"""
    return datetime.now(UTC).isoformat()


class MemoryThreadMetaStore(ThreadMetaStore):
    """用进程内存存会话元信息的 ThreadMetaStore。"""

    def __init__(self) -> None:
        # key   = thread_id（会话 id）
        # value = 该会话的记录 dict，字段见 base.py
        self._rows: dict[str, dict] = {}

    async def create(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        existing = self._rows.get(thread_id)
        if existing is not None:
            # 幂等：已存在就原样返回，不覆盖标题 / metadata。
            # 唯一例外：原来没有归属、这次带了 user_id → 认领。
            if existing.get("user_id") is None and user_id is not None:
                existing["user_id"] = user_id
            return deepcopy(existing)

        now = _now_iso()
        record = {
            "thread_id": thread_id,
            "user_id": user_id,
            "display_name": display_name,
            "status": DEFAULT_THREAD_STATUS,
            # 深拷贝：调用方之后改传入的 dict，不能影响已存的记录
            "metadata": deepcopy(metadata) if metadata is not None else {},
            "created_at": now,
            "updated_at": now,
        }
        self._rows[thread_id] = record
        return deepcopy(record)

    async def get(self, thread_id: str) -> dict | None:
        record = self._rows.get(thread_id)
        return deepcopy(record) if record is not None else None

    async def search(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        rows = [
            r
            for r in self._rows.values()
            if (user_id is None or r.get("user_id") == user_id)
            and (status is None or r["status"] == status)
        ]
        # 最近更新的在前。updated_at 相同时用 thread_id 倒序兜底，
        # 保证排序结果稳定（和 db 版的 ORDER BY 一致）。
        rows.sort(key=lambda r: (r["updated_at"], r["thread_id"]), reverse=True)
        return [deepcopy(r) for r in rows[offset : offset + limit]]

    async def update_display_name(self, thread_id: str, display_name: str) -> bool:
        record = self._rows.get(thread_id)
        if record is None:
            return False
        record["display_name"] = display_name
        record["updated_at"] = _now_iso()
        return True

    async def update_status(self, thread_id: str, status: str) -> bool:
        record = self._rows.get(thread_id)
        if record is None:
            return False
        record["status"] = status
        record["updated_at"] = _now_iso()
        return True

    async def update_metadata(
        self,
        thread_id: str,
        metadata: dict[str, Any],
        *,
        touch: bool = True,
    ) -> bool:
        record = self._rows.get(thread_id)
        if record is None:
            return False
        merged = dict(record.get("metadata") or {})
        merged.update(metadata)
        record["metadata"] = merged
        if touch:
            # touch=False 时保持 updated_at 不动（置顶/归档不改变最近活跃顺序）
            record["updated_at"] = _now_iso()
        return True

    async def delete(self, thread_id: str) -> bool:
        return self._rows.pop(thread_id, None) is not None
