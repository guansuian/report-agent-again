"""内存版事件存储 —— 对应 deer-flow 的
deerflow/runtime/events/store/memory.py（最小版）。

存储结构：

    self._events = {
        "thread-1": [event1, event2, ...],   # 全部事件，按 seq 升序
    }
    self._messages = {
        "thread-1": [event1, ...],           # 只含 category="message" 的投影
    }
    self._seq_counters = {"thread-1": 3}     # 最后分配的 seq

为什么单独维护 _messages：前端历史接口只读消息，每次从全部事件里筛选
太浪费；单独存一份投影，读的时候直接拿。

单进程足够用。方法内部没有 await，事件循环里天然原子，先不加锁。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from core.runtime.events.store.base import RunEventStore


class MemoryRunEventStore(RunEventStore):
    """用进程内存存 run 事件的 RunEventStore。"""

    def __init__(self) -> None:
        # key = thread_id（会话 id）
        # value = 该 thread 的全部事件，按 seq 升序（message 和非 message 都在）
        self._events: dict[str, list[dict]] = {}

        # key = thread_id（会话 id）
        # value = 该 thread 中 category="message" 的事件投影，专供历史消息接口读
        self._messages: dict[str, list[dict]] = {}

        # key = thread_id（会话 id）
        # value = 该 thread 已分配的最大 seq（下一个事件取它 +1）
        self._seq_counters: dict[str, int] = {}

    def _next_seq(self, thread_id: str) -> int:
        """取该 thread 的下一个 seq（线程内严格递增）。"""
        seq = self._seq_counters.get(thread_id, 0) + 1
        self._seq_counters[thread_id] = seq
        return seq

    def _put_one(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_type: str,
        category: str,
        content: str | dict = "",
        metadata: dict | None = None,
        created_at: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        record = {
            "thread_id": thread_id,
            "run_id": run_id,
            "user_id": user_id,
            "event_type": event_type,
            "category": category,
            # 深拷贝：调用方之后修改传入对象，不能影响已存的记录
            "content": deepcopy(content),
            "metadata": deepcopy(metadata) if metadata is not None else {},
            "seq": self._next_seq(thread_id),
            "created_at": created_at or datetime.now(UTC).isoformat(),
        }
        self._events.setdefault(thread_id, []).append(record)
        if category == "message":
            self._messages.setdefault(thread_id, []).append(record)
        return record

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    async def put(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_type: str,
        category: str,
        content: str | dict = "",
        metadata: dict | None = None,
        created_at: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        record = self._put_one(
            thread_id=thread_id,
            run_id=run_id,
            event_type=event_type,
            category=category,
            content=content,
            metadata=metadata,
            created_at=created_at,
            user_id=user_id,
        )
        # 返回快照，避免调用方拿到存储内部的对象
        return deepcopy(record)

    async def put_batch(self, events: list[dict]) -> list[dict]:
        return [deepcopy(self._put_one(**event)) for event in events]

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    async def list_messages(
        self,
        thread_id: str,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        messages = self._messages.get(thread_id, [])
        # user_id 为 None = 不筛选（单用户模式）
        if user_id is not None:
            messages = [event for event in messages if event.get("user_id") == user_id]

        if after_seq is not None:
            # 向后翻页：seq > after_seq，最多 limit 条
            window = [
                event
                for event in messages
                if event["seq"] > after_seq
                and (before_seq is None or event["seq"] < before_seq)
            ]
            return deepcopy(window[:limit])

        if before_seq is not None:
            # 向前翻页：seq < before_seq 的最后 limit 条
            window = [event for event in messages if event["seq"] < before_seq]
            return deepcopy(window[-limit:])

        # 不传游标：返回最新的 limit 条
        return deepcopy(messages[-limit:])

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------

    async def delete_by_thread(self, thread_id: str) -> int:
        events = self._events.pop(thread_id, [])
        self._messages.pop(thread_id, None)
        self._seq_counters.pop(thread_id, None)
        return len(events)
