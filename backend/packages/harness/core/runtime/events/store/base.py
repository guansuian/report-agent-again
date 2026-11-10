"""RunEventStore 抽象接口 —— 对应 deer-flow 的
deerflow/runtime/events/store/base.py（最小版）。

它只负责一件事：把 run 产生的事件按 thread 存起来，并能按顺序读回来。
不关心事件是什么内容，也不关心谁调用它。

三条必须保证的约定（和 deer-flow 一致）：
1. put() 之后，事件必须能被 list_messages() 查到；
2. seq 在同一个 thread 内严格递增；
3. list_messages() 只返回 category="message" 的事件。

事件记录（dict）的字段：
    thread_id   属于哪个会话
    run_id      属于哪一次运行
    user_id     归属用户；None 表示未归属
    event_type  事件类型，如 llm.ai.response / llm.tool.result
    category    分类，message / trace / outputs ...
    content     事件内容（JSON 可序列化）
    metadata    附加信息（token 用量、工具名等）
    seq         线程内递增序号，前端排序的依据
    created_at  创建时间（ISO 8601）
"""

from __future__ import annotations

import abc
from typing import Any


class RunEventStore(abc.ABC):
    """事件存储的抽象接口。"""

    @abc.abstractmethod
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
        """写入一条事件，分配 seq，返回写进去的那条记录。

        user_id 是归属标签（可空），只用于过滤，不做权限判断。
        """

    @abc.abstractmethod
    async def put_batch(self, events: list[dict]) -> list[dict]:
        """批量写入。events 里每个 dict 的键和 put() 的参数一致。

        RunJournal 的 flush 缓冲用的就是它。
        """

    @abc.abstractmethod
    async def list_messages(
        self,
        thread_id: str,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """读取 thread 下的消息（只包含 category="message"），按 seq 升序。

        分页规则：
        - after_seq：返回 seq > after_seq 的前 limit 条（向后翻页）
        - before_seq：返回 seq < before_seq 的最后 limit 条（向前翻页）
        - 都不传：返回最新的 limit 条

        user_id 不为 None 时只返回该用户的事件。
        """

    @abc.abstractmethod
    async def delete_by_thread(self, thread_id: str) -> int:
        """删除某个 thread 的全部事件，返回删除条数。"""
