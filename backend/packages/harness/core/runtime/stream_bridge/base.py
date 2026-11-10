"""StreamBridge 抽象协议 —— 对应 deer-flow 的
deerflow/runtime/stream_bridge/base.py（最小版）。

作用：解耦「跑 agent 的 worker（生产者）」和「发 SSE 的 HTTP 接口（消费者）」。
两边互不认识，只跟桥打交道。

    producer                          consumer
    worker.run_agent                  sse_consumer (HTTP)
          │                                 │
          │ publish(run_id, event, data)    │ subscribe(run_id)
          ▼                                 ▼
       ┌──────────────────────────────────────┐
       │           StreamBridge               │
       │   { run_id: [event, event, ...] }    │
       └──────────────────────────────────────┘

单机用 MemoryStreamBridge（内存队列）；以后要多进程，再写一个 Redis 实现，
run_agent 和 sse_consumer 一行都不用改。

最小实现只要求三个方法：publish / publish_end / subscribe。
心跳、StreamGap、租约等能力等真正需要时再加。
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """一条流事件（最终会变成 SSE 的一帧）。

    id:    同一 run 内递增的编号，对应 SSE 的 ``id:`` 字段（断线重连用）
    event: 事件名，如 ``metadata`` / ``values`` / ``messages`` / ``end``
    data:  可 JSON 序列化的内容，对应 SSE 的 ``data:`` 字段
    """

    id: str
    event: str
    data: Any


# 结束哨兵：生产者调用 publish_end 后，消费者会收到它并退出循环。
# id 为空，因为它不对应任何真实事件，不需要出现在前端的事件序列里。
END_SENTINEL = StreamEvent(id="", event="__end__", data=None)


class StreamBridge(abc.ABC):
    """流事件桥的抽象接口。

    - 生产者（run_agent）调用 publish / publish_end 写事件；
    - 消费者（sse_consumer）调用 subscribe 读事件。

    单机用 MemoryStreamBridge；多进程时可换成 Redis 实现，调用方不变。
    """

    @abc.abstractmethod
    async def publish(self, run_id: str, event: str, data: Any) -> None:
        """生产者：把一条事件写进 run_id 对应的队列。"""

    @abc.abstractmethod
    async def publish_end(self, run_id: str) -> None:
        """生产者：宣告该 run 不会再有新事件。

        消费者收到 END_SENTINEL 后结束迭代。
        """

    @abc.abstractmethod
    def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """消费者：异步迭代 run_id 的事件，直到收到 END_SENTINEL。

        last_event_id 用于断线重连：从该 id 之后继续读。
        最小版可以先忽略这个参数。
        """

    async def cleanup(self, run_id: str) -> None:
        """释放某个 run 的队列资源（默认什么都不做）。

        MemoryStreamBridge 建议实现它，防止长期运行后字典越积越大。
        """
