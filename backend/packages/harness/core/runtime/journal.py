"""RunJournal —— 用 LangChain 回调把 run 事件写进 RunEventStore。

对应 deer-flow 的 deerflow/runtime/journal.py（最小版）。

它夹在「LangChain 回调机制」和「RunEventStore」之间：把回调里的模型消息 /
工具结果统一成 run 事件，攒进 buffer，再批量写库。

关键设计（跟 deer-flow 对齐）：

- 钩子是同步的（BaseCallbackHandler 的 on_* 都是 def，不能 await），所以钩子里
  只动内存（塞 buffer），真正写库交给事件循环。
- 写库主链：buffer 满 flush_threshold → _flush_sync → _start_write 用
  create_task 投给事件循环；没有事件循环时（get_running_loop 抛 RuntimeError）
  放回 buffer，等 flush 兜底。
- run_agent 的 finally 里 await flush()，保证最后一批一定落库，一条不丢。

最小版只做 4 个钩子 + 写库主链：

| 钩子 | 抓什么 | 事件 |
| ---- | ------ | ---- |
| on_chat_model_start | 第一条用户消息 | llm.human.input |
| on_llm_end | 模型回答 + 记住本轮工具调用 | llm.ai.response |
| on_tool_end | 工具结果（工具真跑时） | llm.tool.result |
| on_chain_end | 根图收尾，补写中间件短路漏掉的工具结果 | llm.tool.result |

on_chain_end 为什么要存在（照 deer-flow journal.py:948 的
_should_reconcile_tool_message 注释）：中间件可能自己回答一个工具调用并短路
执行（如 ask_clarification），此时 LangChain 不会触发 on_tool_end，结果就
跑不进库 —— 用户这一轮看到了，刷新就没了（deer-flow issue #4666）。
on_chain_end 在根图收尾时，用 on_llm_end 记下的「本轮工具调用名单」把这类
漏写的 ToolMessage 捞出来补写。

以后要复杂化（token 统计、latency、caller 区分、证据封存等）时，在对应钩子里加
逻辑、往 metadata 里塞字段即可，写库主链不用动。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from core.runtime.events.catalog import (
    LLM_AI_RESPONSE_EVENT,
    LLM_HUMAN_INPUT_EVENT,
    LLM_TOOL_RESULT_EVENT,
)
from core.runtime.events.store.base import RunEventStore

logger = logging.getLogger(__name__)


class RunJournal(BaseCallbackHandler):
    """LangChain 回调处理器：把 run 事件捕获进 RunEventStore。"""

    def __init__(
        self,
        run_id: str,
        thread_id: str,
        event_store: RunEventStore,
        *,
        user_id: str | None = None,
        flush_threshold: int = 20,
    ) -> None:
        super().__init__()
        self.run_id = run_id
        self.thread_id = thread_id
        # 归属标签：从 run 记录透传，写进每条事件，便于按人过滤。
        self.user_id = user_id
        self._store: RunEventStore | None = event_store
        self._flush_threshold = flush_threshold
        self._closed = False

        # 写缓冲：钩子里只往这里 append，不直接碰异步存储
        self._buffer: list[dict] = []
        # 在途写入：task -> 原始 batch。flush 前先等它们落地，避免重叠写。
        self._owned_writes: dict[asyncio.Task[None], list[dict]] = {}
        # 串行化显式的 flush / close，避免和 _flush_sync 抢 buffer。
        self._operation_lock = asyncio.Lock()

        # 只抓第一条用户消息，后续 on_chat_model_start 不再重复。
        self._first_human_message_captured = False

        # 本轮模型发起过哪些工具调用：tool_call_id -> 工具名。
        # on_llm_end 记，on_chain_end 用它把「本轮新造但 on_tool_end 没写到」的
        # 工具结果从整条历史里筛出来（中间件短路场景，如 ask_clarification）。
        self._current_run_tool_call_names: dict[str, str] = {}
        # 已经写进库的工具结果身份：{"tool:<tool_call_id>", "message:<message_id>"}。
        # 防重复写。
        self._persisted_tool_message_identities: set[str] = set()

    # ------------------------------------------------------------------
    # LangChain 回调（同步，只能动内存 / 投递任务）
    # ------------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """抓本次 run 的用户消息。只在第一次模型调用时落一次。

        调用时机：每次「真要向模型发一次请求」之前触发一次（不是按节点、
        不是按对话轮次，而是按模型调用次数）。

        为什么倒序遍历（对齐 deer-flow journal.py:553）：
        多轮会话里，发给模型的是**完整历史**，前面几轮的用户消息也在列表
        里。列表按时间正序排，所以「本次 run 的用户输入」在**最后**。
        正序会抓到第 1 轮的老问题，倒序才抓到本轮的新问题。
        """
        if self._first_human_message_captured or not messages:
            return
        for batch in reversed(messages):
            for message in reversed(batch):
                if isinstance(message, HumanMessage):
                    self._first_human_message_captured = True
                    self._put(
                        event_type=LLM_HUMAN_INPUT_EVENT.event_type,
                        category=LLM_HUMAN_INPUT_EVENT.category,
                        content=message.model_dump(),
                    )
                    return

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """抓模型回答。response.generations 里每条 ChatGeneration 带一个 message。"""
        if self._closed:
            return
        for generation in response.generations:
            for gen in generation:
                message = getattr(gen, "message", None)
                if message is None:
                    continue
                # 记住本轮模型发起过哪些工具调用，供 on_chain_end 兜底用。
                self._remember_current_run_tool_calls(message)
                usage = getattr(message, "usage_metadata", None)
                usage_dict = deepcopy(dict(usage)) if usage else {}
                self._put(
                    event_type=LLM_AI_RESPONSE_EVENT.event_type,
                    category=LLM_AI_RESPONSE_EVENT.category,
                    content=message.model_dump(),
                    metadata={"usage": usage_dict},
                )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """抓工具结果。工具真跑才会触发；中间件短路的情况由 on_chain_end 兜底。"""
        if isinstance(output, ToolMessage):
            self._persist_tool_result_message(output)

    def on_chain_end(
        self,
        outputs: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """根图收尾时，补写「中间件短路导致 on_tool_end 没触发」的工具结果。

        只有根图（parent_run_id is None）才代表用户可见的一次 run 结束；
        内部节点也会触发 on_chain_end，那些不是收尾，直接跳过（照 deer-flow
        journal.py:488）。
        """
        if parent_run_id is not None:
            return
        self._reconcile_final_tool_messages(outputs)
        self._flush_sync()

    # ------------------------------------------------------------------
    # 兜底补写：on_llm_end 记名单 → on_chain_end 用名单把漏写的捞回来
    # ------------------------------------------------------------------

    @staticmethod
    def _message_identity(message: BaseMessage) -> str | None:
        """给一条消息一个稳定身份：工具结果用 tool_call_id，普通消息用 message id。

        返回 None 表示两者都没有（极小概率，无法去重）。
        """
        tool_call_id = getattr(message, "tool_call_id", None)
        if isinstance(tool_call_id, str) and tool_call_id:
            return f"tool:{tool_call_id}"
        message_id = getattr(message, "id", None)
        if isinstance(message_id, str) and message_id:
            return f"message:{message_id}"
        return None

    @staticmethod
    def _tool_call_value(tool_call: Any, key: str) -> Any:
        """tool_call 可能是 dict 也可能是对象，统一取值。"""
        if isinstance(tool_call, Mapping):
            return tool_call.get(key)
        return getattr(tool_call, key, None)

    @staticmethod
    def _is_ai_message(message: Any) -> bool:
        """判断是否 AI 消息（AIMessage 类型，或 type 字段是 "ai"）。"""
        return isinstance(message, AIMessage) or getattr(message, "type", None) == "ai"

    def _has_current_run_tool_call(self, message: Any) -> bool:
        """这条 AI 消息里，有没有某个 tool_call 属于本轮发起的？"""
        if not self._is_ai_message(message):
            return False
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_call_id = self._tool_call_value(tool_call, "id")
            if isinstance(tool_call_id, str) and tool_call_id in self._current_run_tool_call_names:
                return True
        return False

    def _remember_current_run_tool_calls(self, message: Any) -> None:
        """把本轮 AI 消息里的 tool_call_id 记进名单。

        照 deer-flow 的 _remember_current_run_tool_calls，但它只记 caller ==
        "lead_agent" 的调用（避开 subagent）。我们最小版只有 lead_agent 一个
        代理、没有 subagent，所以省掉 caller 判断，全部记下。
        """
        if not self._is_ai_message(message):
            return
        tool_calls = getattr(message, "tool_calls", None) or []
        if not isinstance(tool_calls, list):
            return
        for tool_call in tool_calls:
            tool_call_id = self._tool_call_value(tool_call, "id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue
            name = self._tool_call_value(tool_call, "name")
            self._current_run_tool_call_names[tool_call_id] = str(name or "")

    def _persist_tool_result_message(self, message: BaseMessage) -> None:
        """写一条工具结果，并记下它的身份，防止以后再重复写。"""
        self._put(
            event_type=LLM_TOOL_RESULT_EVENT.event_type,
            category=LLM_TOOL_RESULT_EVENT.category,
            content=message.model_dump(),
        )
        identity = self._message_identity(message)
        if identity:
            self._persisted_tool_message_identities.add(identity)

    def _final_output_messages(self, outputs: Any) -> list[Any]:
        """从根图收尾的 outputs 里取 messages 列表。"""
        if isinstance(outputs, Mapping):
            messages = outputs.get("messages", [])
            return messages if isinstance(messages, list) else []
        return []

    def _should_reconcile_tool_message(self, message: ToolMessage) -> bool:
        """这条 ToolMessage 是否需要补写？

        三道关卡（照 deer-flow journal.py:939）：
            ① 用户可见（没被 hide_from_ui 标记）；
            ② 它的 tool_call 属于本轮（在 on_llm_end 记的名单里）—— 把历史里
               上一轮已经落过库的老消息挡在门外；
            ③ 还没写过（identity 不在已写集合里）—— 防重复。
        """
        if message.additional_kwargs.get("hide_from_ui") is True:
            return False
        tool_call_id = getattr(message, "tool_call_id", None)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return False
        if self._current_run_tool_call_names.get(tool_call_id) is None:
            return False
        identity = self._message_identity(message)
        return identity is not None and identity not in self._persisted_tool_message_identities

    def _reconcile_tool_messages(self, messages: Iterable[Any]) -> None:
        """遍历一组消息，把该补写的 ToolMessage 补写掉。"""
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            if self._should_reconcile_tool_message(message):
                self._persist_tool_result_message(message)

    def _current_run_tool_call_suffix(self, messages: Iterable[Any]) -> list[Any] | None:
        """返回「最后一条本轮发起工具调用的 AI 消息」之后的那段消息。

        目的：只扫本轮的新消息，别把整条历史从头到尾扫一遍（照 deer-flow
        journal.py:987）。找到本轮 AI 工具调用边界后，它后面的消息才是本轮
        该补写的范围；没找到边界时，若历史里根本没有 AI 消息，就返回全部。
        """
        batch = list(messages)
        boundary = None
        has_ai_message = False
        for index, message in enumerate(batch):
            if not self._is_ai_message(message):
                continue
            has_ai_message = True
            if self._has_current_run_tool_call(message):
                boundary = index
        if boundary is not None:
            return batch[boundary + 1 :]
        return batch if not has_ai_message else None

    def _reconcile_final_tool_messages(self, outputs: Any) -> None:
        """根图收尾的兜底入口：取本轮新消息，补写漏掉的 ToolMessage。"""
        suffix = self._current_run_tool_call_suffix(self._final_output_messages(outputs))
        if suffix is not None:
            self._reconcile_tool_messages(suffix)

    # ------------------------------------------------------------------
    # 事件构造 + 缓冲
    # ------------------------------------------------------------------

    def _make_event(
        self,
        *,
        event_type: str,
        category: str,
        content: str | dict = "",
        metadata: dict | None = None,
    ) -> dict:
        """构造一条事件记录。字段和 RunEventStore.put_batch 的入参一致。"""
        return {
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "user_id": self.user_id,
            "event_type": event_type,
            "category": category,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _put(
        self,
        *,
        event_type: str,
        category: str,
        content: str | dict = "",
        metadata: dict | None = None,
    ) -> None:
        """把事件塞进 buffer，满了就触发一次同步 flush。"""
        if self._closed:
            return
        self._buffer.append(
            self._make_event(
                event_type=event_type,
                category=category,
                content=content,
                metadata=metadata,
            )
        )
        if len(self._buffer) >= self._flush_threshold:
            self._flush_sync()

    # ------------------------------------------------------------------
    # 写库主链：flush_sync → start_write → write_batch
    # ------------------------------------------------------------------

    def _flush_sync(self) -> None:
        """尽力把 buffer 投给事件循环写库。没有循环就留在 buffer。"""
        if not self._buffer:
            return
        # 已有在途写入 / 正在 flush，就不叠着发，保持写库顺序。
        if self._owned_writes or self._operation_lock.locked():
            return
        batch = self._buffer.copy()
        self._buffer.clear()
        task = self._start_write(batch)
        if task is None:
            # 没有事件循环 → 投不出去，放回 buffer 等 flush 兜底。
            self._buffer = batch + self._buffer

    def _start_write(self, batch: list[dict]) -> asyncio.Task[None] | None:
        """把一批事件投给事件循环写库；没有循环时返回 None。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task: asyncio.Task[None] = loop.create_task(self._write_batch(batch))
        self._owned_writes[task] = batch
        task.add_done_callback(self._reap_settled_write)
        return task

    async def _write_batch(self, batch: list[dict]) -> None:
        """真正写库的那一步（异步）。"""
        store = self._store
        if store is None:
            return
        await store.put_batch(batch)

    def _reap_settled_write(self, task: asyncio.Task[None]) -> None:
        """写库任务结束时回收；失败则放回 buffer 等下次 flush 重试。

        注意：deer-flow 对「结果未知」的失败会封存（quarantine）不重试，
        因为可能已经提交了、只是回执丢了。内存版 store 不会这样，暂用「放回
        重试」，等接 Postgres 再补封存逻辑。
        """
        batch = self._owned_writes.pop(task, None)
        if batch is None:
            return
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._buffer = batch + self._buffer
            logger.warning(
                "RunJournal 写库失败，run=%s（%d 条，%s: %s），已放回 buffer 待重试",
                self.run_id,
                len(batch),
                type(exc).__name__,
                exc,
            )

    async def _drain_pending_writes(self) -> None:
        """等在途写入全部落地，失败的放回 buffer。"""
        if not self._owned_writes:
            return
        pending = list(self._owned_writes.items())
        tasks = [task for task, _batch in pending]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (task, _batch), outcome in zip(pending, results):
            batch = self._owned_writes.pop(task, None)
            if batch is None:
                # done 回调已经处理过了，别重复放回。
                continue
            if isinstance(outcome, BaseException) and not task.cancelled():
                self._buffer = batch + self._buffer
                logger.warning(
                    "RunJournal 写库失败，run=%s（%d 条，%s: %s），已放回 buffer 待重试",
                    self.run_id,
                    len(batch),
                    type(outcome).__name__,
                    outcome,
                )

    # ------------------------------------------------------------------
    # 对外方法（run_agent 的 finally 里调用）
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """强制写掉剩余 buffer。run_agent 的 finally 里调用。"""
        if self._closed:
            return
        async with self._operation_lock:
            if self._closed:
                return
            # 先等在途写入落地。
            await self._drain_pending_writes()
            # 再分批发剩余 buffer。
            while self._buffer:
                batch = self._buffer[: self._flush_threshold]
                del self._buffer[: self._flush_threshold]
                if self._store is None:
                    self._buffer = batch + self._buffer
                    break
                task = self._start_write(batch)
                if task is None:
                    # 没有事件循环（异常情况），留在 buffer。
                    self._buffer = batch + self._buffer
                    break
                await self._drain_pending_writes()

    async def close(self, *, flush: bool = True) -> None:
        """关掉 journal：先 flush（可选），再标记关闭。"""
        if self._closed:
            return
        if flush:
            await self.flush()
        self._closed = True
        # 断开 store 引用，防止之后再被写。
        self._store = None
