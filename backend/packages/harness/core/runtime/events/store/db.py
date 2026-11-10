"""SQLAlchemy 版事件存储 —— 对应 deer-flow 的
deerflow/runtime/events/store/db.py（最小版）。

和 memory.py 的唯一区别是「存哪」：
    MemoryRunEventStore → 进程内存（重启就丢）
    DbRunEventStore     → run_events 表（SQLAlchemy + asyncpg，重启还在）

其它约定（接口、seq 递增、list_messages 只返回 message）都和
base.py / memory.py 完全一致，所以调用方（RunJournal、前端历史接口）
不用改一行，换一下注入的 store 即可。

关键点（为什么这么做，完全对齐 deer-flow）：
1. seq 分配：不直接用 MAX(seq)+1，而是 max(表内 MAX, 水印表 seq)+1。
   水印表（run_event_thread_seq）记住「这个线程曾经到过几」。删掉某个 run
   的事件后，表内 MAX 会回退但水印不回退，seq 继续递增（否则前端按 seq
   去重会把新消息当旧消息丢掉）。delete_by_thread 删整个线程时才连水印
   一起删，seq 从头开始。
2. 跨进程锁：MAX+1 两步不是原子的，多进程会撞 seq。postgres 用
   pg_advisory_xact_lock(hashtext(thread_id)) 把整段串行化；进程内再配
   每线程一把 asyncio.Lock 挡协程交错。
3. content 序列化：content 可能是 str 或 dict。TEXT 列只收字符串，dict
   就 json.dumps 再存，并在 event_metadata 里打 content_is_json /
   content_is_dict 标记，读回时凭标记还原（标记用完即丢，不返回给调用方）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.persistence.models.run_event import RunEventRow, RunEventThreadSeqRow
from core.runtime.events.store.base import RunEventStore


def _parse_created_at(value: str | None) -> datetime:
    """把 ISO 字符串转成带时区的 datetime（缺省取当前 UTC）。

    写库前调用：timestamptz 列需要一个 datetime；naive 值统一当 UTC。
    """
    timestamp = datetime.fromisoformat(value) if value else datetime.now(UTC)
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)


def _coerce_iso(value) -> str | None:
    """把数据库读回的 created_at（datetime）转成 ISO 字符串。

    naive 值统一当 UTC，保证返回格式和 memory.py 一致。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.isoformat()
    return value


class DbRunEventStore(RunEventStore):
    """用 run_events 表存 run 事件的 RunEventStore。

    依赖一个 async_sessionmaker（由 core.persistence.engine 在启动时建好）。
    构造只存引用，不开 engine —— engine 的开/关由 lifespan 统一管。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        # key = thread_id，value = 该 thread 的 asyncio.Lock
        # 挡同一进程内协程交错；跨进程靠 advisory lock（见 _acquire_thread_mutation_fence）。
        # deer-flow 用 weakref 防锁泄漏，最小版先用普通 dict，删线程时再 pop。
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_write_lock(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())

    # ------------------------------------------------------------------
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    @staticmethod
    def _content_to_db(content, metadata):
        """content 转成能进 TEXT 列的形式，并决定要不要打 JSON 标记。

        返回 (db_content, metadata)：
            content 是 str  → 原样存，metadata 不动
            content 是 dict → json.dumps 存，metadata 打 content_is_json
                              和 content_is_dict 两个标记
        """
        metadata = dict(metadata or {})
        if isinstance(content, str):
            return content, metadata
        db_content = json.dumps(content, default=str, ensure_ascii=False)
        metadata["content_is_json"] = True
        if isinstance(content, dict):
            metadata["content_is_dict"] = True
        return db_content, metadata

    @staticmethod
    def _row_to_dict(row: RunEventRow) -> dict:
        """把 ORM 行转成 memory.py 同款 record dict（base.py 约定的 9 字段）。

        转换：
            event_metadata → metadata（JSON 列的 dict）
            created_at     → ISO 字符串
            id/content_sha256 → 丢掉（内部列，不返回给调用方）
            user_id        → 保留（归属标签，调用方要能读到）
            content        → 有 JSON 标记就还原成 dict，标记用完即丢
        """
        d = row.to_dict()
        d["metadata"] = d.pop("event_metadata", {}) or {}
        d["created_at"] = _coerce_iso(d.get("created_at"))

        d.pop("id", None)
        d.pop("content_sha256", None)

        metadata = d["metadata"]
        is_json = metadata.pop("content_is_json", False)
        is_json = metadata.pop("content_is_dict", False) or is_json
        raw = d.get("content", "")
        if is_json and isinstance(raw, str):
            try:
                d["content"] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                pass  # 序列化内容坏了的兜底：原样当字符串返回
        return d

    # ------------------------------------------------------------------
    # seq 分配（水印表 + 跨进程锁）
    # ------------------------------------------------------------------

    @staticmethod
    async def _acquire_thread_mutation_fence(session: AsyncSession, thread_id: str) -> None:
        """拿跨进程的线程互斥锁（postgres 用 advisory lock）。

        postgres 里 SELECT max(...) FOR UPDATE 锁不住（聚合结果不是可锁行），
        所以改用事务级 advisory lock，按 thread_id 哈希锁住整段写操作。
        进程内的另一半是 _get_write_lock()，两者合起来保证 seq 不撞。
        """
        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
        if dialect_name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(CAST(:thread_id AS text))::bigint)"),
                {"thread_id": thread_id},
            )

    @staticmethod
    async def _max_seq_for_thread(session: AsyncSession, thread_id: str) -> int | None:
        """取该 thread 当前的 seq 高水位（表内 MAX 和水印表取较大者）。

        行内 MAX 是已有数据的真相，水印表保证删了事件后 seq 不回退。
        """
        await DbRunEventStore._acquire_thread_mutation_fence(session, thread_id)

        row_max = await session.scalar(
            select(func.max(RunEventRow.seq)).where(RunEventRow.thread_id == thread_id)
        )
        watermark = await session.scalar(
            select(RunEventThreadSeqRow.seq).where(RunEventThreadSeqRow.thread_id == thread_id)
        )
        values = [v for v in (row_max, watermark) if v is not None]
        return max(values) if values else None

    @staticmethod
    async def _raise_seq_watermark(session: AsyncSession, thread_id: str, seq: int) -> None:
        """把 *seq* 记为线程新的最低分配底线（只升不降）。"""
        if seq <= 0:
            return
        watermark = await session.get(RunEventThreadSeqRow, thread_id)
        if watermark is None:
            session.add(RunEventThreadSeqRow(thread_id=thread_id, seq=seq))
        elif watermark.seq < seq:
            watermark.seq = seq

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
        db_content, metadata = self._content_to_db(content, metadata)
        async with self._get_write_lock(thread_id):
            async with self._sf() as session:
                async with session.begin():
                    max_seq = await self._max_seq_for_thread(session, thread_id)
                    seq = (max_seq or 0) + 1
                    row = RunEventRow(
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id,
                        event_type=event_type,
                        category=category,
                        content=db_content,
                        content_sha256=hashlib.sha256(db_content.encode("utf-8")).hexdigest(),
                        event_metadata=metadata,
                        seq=seq,
                        created_at=_parse_created_at(created_at),
                    )
                    session.add(row)
                    await self._raise_seq_watermark(session, thread_id, seq)
        return self._row_to_dict(row)

    async def put_batch(self, events: list[dict]) -> list[dict]:
        """批量写入（RunJournal flush 缓冲用）。

        deer-flow 强制整批属于同一线程，一次拿锁、一次读 MAX，逐条 seq 递增。
        """
        if not events:
            return []
        thread_ids = {e["thread_id"] for e in events}
        if len(thread_ids) > 1:
            raise ValueError(f"put_batch 要求所有事件属于同一线程，got {thread_ids!r}")
        thread_id = events[0]["thread_id"]

        async with self._get_write_lock(thread_id):
            async with self._sf() as session:
                async with session.begin():
                    max_seq = await self._max_seq_for_thread(session, thread_id)
                    seq = max_seq or 0
                    rows: list[RunEventRow] = []
                    for e in events:
                        seq += 1
                        content = e.get("content", "")
                        category = e.get("category", "trace")
                        metadata = e.get("metadata")
                        db_content, metadata = self._content_to_db(content, metadata)
                        row = RunEventRow(
                            thread_id=e["thread_id"],
                            run_id=e["run_id"],
                            user_id=e.get("user_id"),
                            event_type=e["event_type"],
                            category=category,
                            content=db_content,
                            content_sha256=hashlib.sha256(db_content.encode("utf-8")).hexdigest(),
                            event_metadata=metadata,
                            seq=seq,
                            created_at=_parse_created_at(e.get("created_at")),
                        )
                        session.add(row)
                        rows.append(row)
                    await self._raise_seq_watermark(session, thread_id, seq)
        return [self._row_to_dict(r) for r in rows]

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
        stmt = select(RunEventRow).where(
            RunEventRow.thread_id == thread_id, RunEventRow.category == "message"
        )
        # user_id 为 None = 不筛选（单用户模式）
        if user_id is not None:
            stmt = stmt.where(RunEventRow.user_id == user_id)
        if before_seq is not None:
            stmt = stmt.where(RunEventRow.seq < before_seq)
        if after_seq is not None:
            stmt = stmt.where(RunEventRow.seq > after_seq)

        if after_seq is not None:
            # 向后翻页：seq > after_seq 的前 limit 条（升序）
            stmt = stmt.order_by(RunEventRow.seq.asc()).limit(limit)
            async with self._sf() as session:
                result = await session.execute(stmt)
                return [self._row_to_dict(r) for r in result.scalars()]

        # 向前翻页或默认：seq < before_seq / 最新的 limit 条，降序取再反转回升序
        stmt = stmt.order_by(RunEventRow.seq.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = list(result.scalars())
            return [self._row_to_dict(r) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------

    async def delete_by_thread(self, thread_id: str) -> int:
        async with self._get_write_lock(thread_id):
            async with self._sf() as session:
                async with session.begin():
                    await self._acquire_thread_mutation_fence(session, thread_id)
                    count = await session.scalar(
                        select(func.count())
                        .select_from(RunEventRow)
                        .where(RunEventRow.thread_id == thread_id)
                    ) or 0
                    if count > 0:
                        await session.execute(
                            delete(RunEventRow).where(RunEventRow.thread_id == thread_id)
                        )
                    # 线程一个事件都不剩时，才删水印（重置 seq）；否则保留分配底线
                    remaining = (
                        select(RunEventRow.id)
                        .where(RunEventRow.thread_id == thread_id)
                        .exists()
                    )
                    await session.execute(
                        delete(RunEventThreadSeqRow).where(
                            RunEventThreadSeqRow.thread_id == thread_id, ~remaining
                        )
                    )
        self._locks.pop(thread_id, None)
        return count
