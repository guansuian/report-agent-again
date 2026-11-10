"""内存版流事件桥 —— 对应 deer-flow 的
deerflow/runtime/stream_bridge/memory.py（最小版）。

存储结构就是一个字典：

    {
        "run-1": _RunStream(events=[event1, event2, ...]),
        "run-2": _RunStream(events=[...]),
    }

每个 run 一个 _RunStream：
- events：事件队列（list）
- condition：让消费者在没有新事件时挂起等待，publish 时唤醒
- ended：生产者是否已调用 publish_end
- next_id：下一条事件的编号

局限：数据只存在当前进程内存里，进程重启就丢，也不能跨进程共享。
单机开发/演示够用；多进程时换 Redis 实现即可，调用方不用改。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from core.runtime.stream_bridge.base import END_SENTINEL, StreamBridge, StreamEvent


@dataclass
class _RunStream:
    """一个 run 的队列状态。"""

    events: list[StreamEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    ended: bool = False
    next_id: int = 1


class MemoryStreamBridge(StreamBridge):
    """用进程内存做队列的 StreamBridge。"""

    def __init__(self) -> None:
        """ 一次 run 一个对列"""
        # key   = run_id（每次运行的唯一标识）
        # value = 这个 run 专属的事件队列（_RunStream：events / condition / ended / next_id）
        self._streams: dict[str, _RunStream] = {}

    def _get_or_create_stream(self, run_id: str) -> _RunStream:
        stream = self._streams.get(run_id)
        if stream is None:
            stream = _RunStream()
            self._streams[run_id] = stream
        return stream

    # ------------------------------------------------------------------
    # 生产者
    # ------------------------------------------------------------------

    async def publish(self, run_id: str, event: str, data) -> None:
        stream = self._get_or_create_stream(run_id)
        async with stream.condition:
            entry = StreamEvent(id=str(stream.next_id), event=event, data=data)
            stream.next_id += 1
            stream.events.append(entry)
            # 唤醒所有正在 wait 的消费者：有新事件了
            stream.condition.notify_all()

    async def publish_end(self, run_id: str) -> None:
        stream = self._get_or_create_stream(run_id)
        async with stream.condition:
            stream.ended = True
            stream.condition.notify_all()

    # ------------------------------------------------------------------
    # 消费者
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """从队列里按顺序取事件，取完且已结束时发 END_SENTINEL。"""
        stream = self._get_or_create_stream(run_id)
        index = self._resolve_start_index(stream, last_event_id)

        while True:
            async with stream.condition:
                # 没事件且没结束：挂起等待 publish 唤醒
                while index >= len(stream.events) and not stream.ended:
                    await stream.condition.wait()

                # 拷贝一份待发事件，释放锁后再 yield，避免阻塞生产者
                pending = stream.events[index:]
                index = len(stream.events)
                ended = stream.ended

            for entry in pending:
                yield entry

            # 已结束且队列取空 → 通知消费者收尾
            if ended and index >= len(stream.events):
                yield END_SENTINEL
                return

    @staticmethod
    def _resolve_start_index(stream: _RunStream, last_event_id: str | None) -> int:
        """断线重连：从 last_event_id 之后继续读；找不到就从头读。"""
        if not last_event_id:
            return 0
        for i, entry in enumerate(stream.events):
            if entry.id == last_event_id:
                return i + 1
        return 0

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    async def cleanup(self, run_id: str) -> None:
        """删除某个 run 的队列，防止内存越积越多。"""
        stream = self._streams.pop(run_id, None)
        if stream is None:
            return
        async with stream.condition:
            stream.ended = True
            stream.condition.notify_all()
