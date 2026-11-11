"""SQLAlchemy 版 run 存储 —— 对应 deer-flow 的
deerflow/persistence/run/sql.py 里的 RunRepository（最小版）。

和 memory.py 的唯一区别是「存哪」：
    MemoryRunStore → 进程内存的 dict（重启就丢）
    DbRunStore     → runs 表（SQLAlchemy + asyncpg，重启还在）

接口、返回值形状都和 base.py / memory.py 完全一致，所以 RunManager 换一下
注入的 store 就行，不用改一行代码。

deer-flow 的 RunRepository 有 1151 行（幂等键、租约、change_seq、证据封存…），
最小版保留 run 的创建、查询、状态和用量更新，以及启动清账。

关键点：
1. put 是 upsert，不是纯 INSERT：
       session.get 先查，没有才 add，有就逐字段 setattr。
   为什么：上层失败重试会重复 put，纯 INSERT 会撞主键报错。
   对应 deer-flow sql.py:194-200 的写法。
2. 覆盖写时不动 created_at —— 创建时间只有第一次算数。
3. 状态更新时 updated_at 由 model 的 onupdate 自动刷新；用量保存时显式更新，
   保证重复保存相同合计也会记录保存时间。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.persistence.models.run import RunRow
from core.runtime.runs.schemas import RunStatus
from core.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)


def _to_iso(value) -> str | None:
    """把数据库读回的时间（datetime）转成 ISO 字符串。

    naive 值统一当 UTC，保证返回格式和 memory.py 一致。
    （和 core/runtime/events/store/db.py 里的 _coerce_iso 同一个作用，
    那边是模块私有函数，跨模块 import 私有名不好，所以这里重写一份小的。）
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value.isoformat()
    return value


class DbRunStore(RunStore):
    """用 runs 表存 run 记录的 RunStore。

    依赖一个 async_sessionmaker（由 core.persistence.engine 在启动时建好）。
    构造只存引用，不开 engine —— engine 的开/关由 lifespan 统一管。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: RunRow) -> dict:
        """把 ORM 行转成 base.py 约定的记录 dict。

        转换：
            metadata_json → metadata（字段改名，对外不暴露数据库列名）
            created_at / updated_at → ISO 字符串
        """
        d = row.to_dict()
        d["metadata"] = d.pop("metadata_json", {}) or {}
        d["created_at"] = _to_iso(d.get("created_at"))
        d["updated_at"] = _to_iso(d.get("updated_at"))
        return d

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

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
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    # 第一次写 → INSERT
                    row = RunRow(
                        run_id=run_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        status=str(status),
                        error=error,
                        metadata_json=dict(metadata or {}),
                    )
                    session.add(row)
                else:
                    # 重试 / 覆盖 → UPDATE。注意不动 created_at
                    row.thread_id = thread_id
                    # user_id 为 None 时保留旧的，不把已有归属冲掉
                    if user_id is not None:
                        row.user_id = user_id
                    row.status = str(status)
                    row.error = error
                    row.metadata_json = dict(metadata or {})
            # 事务提交后再转 dict：此时 created_at / updated_at 的默认值已经由
            # 数据库侧（INSERT/UPDATE）填好，能读到真实时间
            return self._row_to_dict(row)

    async def update_status(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        error: str | None = None,
    ) -> None:
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    # 静默跳过，不抛异常 —— 和 memory.py 行为一致
                    logger.warning("update_status: run %s 不存在，跳过", run_id)
                    return
                row.status = str(status)
                if error is not None:
                    row.error = error
                # updated_at 由 model 的 onupdate 自动刷新

    async def update_run_completion(
        self,
        run_id: str,
        *,
        total_input_tokens: int,
        total_output_tokens: int,
        total_tokens: int,
        llm_call_count: int,
    ) -> None:
        """按 run_id 覆盖保存用量合计，不修改状态和错误信息。"""
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RunRow, run_id)
                if row is None:
                    logger.warning("update_run_completion: run %s 不存在，跳过", run_id)
                    return

                # Journal 已经累计好了，这里直接赋值，不再相加。
                row.total_input_tokens = total_input_tokens
                row.total_output_tokens = total_output_tokens
                row.total_tokens = total_tokens
                row.llm_call_count = llm_call_count
                # 即使用量与上次相同，也记录这次保存的时间。
                row.updated_at = datetime.now(UTC)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    async def get(self, run_id: str) -> dict | None:
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            return self._row_to_dict(row) if row is not None else None

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        async with self._sf() as session:
            stmt = select(RunRow).where(RunRow.thread_id == thread_id)
            # user_id 为 None = 不筛选（单用户模式）
            if user_id is not None:
                stmt = stmt.where(RunRow.user_id == user_id)
            result = await session.execute(
                stmt.order_by(RunRow.created_at.desc()).limit(limit)
            )
            return [self._row_to_dict(row) for row in result.scalars()]

    async def aggregate_tokens_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, int]:
        """由数据库汇总会话下已结束 run 的用量，不加载完整记录、不分页。"""
        # SUM 在没有记录时返回 NULL，coalesce(..., 0) 将它变成 0。
        stmt = select(
            func.coalesce(func.sum(RunRow.total_input_tokens), 0).label("total_input_tokens"),
            func.coalesce(func.sum(RunRow.total_output_tokens), 0).label("total_output_tokens"),
            func.coalesce(func.sum(RunRow.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(RunRow.llm_call_count), 0).label("llm_call_count"),
            func.count(RunRow.run_id).label("total_runs"),
        ).where(
            RunRow.thread_id == thread_id,
            RunRow.status.in_(("success", "error", "timeout", "interrupted")),
        )
        if user_id is not None:
            stmt = stmt.where(RunRow.user_id == user_id)

        async with self._sf() as session:
            result = await session.execute(stmt)
            # 五项合计只有一行；按 label 定义的字段名转成字典返回。
            return dict(result.mappings().one())

    # ------------------------------------------------------------------
    # 启动清账
    # ------------------------------------------------------------------

    async def fail_inflight(self, error: str) -> int:
        """一条 UPDATE 把上次进程留下的 pending / running 全标成 error。

        注意：这里用 bulk update，不会触发 model 的 onupdate，
        所以 updated_at 要自己写。
        """
        async with self._sf() as session:
            async with session.begin():
                result = await session.execute(
                    update(RunRow)
                    .where(RunRow.status.in_(["pending", "running"]))
                    .values(
                        status=str(RunStatus.error),
                        error=error,
                        updated_at=datetime.now(UTC),
                    )
                )
                return result.rowcount or 0
