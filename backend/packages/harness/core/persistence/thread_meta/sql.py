"""SQLAlchemy 版会话元信息存储 —— 对应 deer-flow 的
deerflow/persistence/thread_meta/sql.py 里的 ThreadMetaRepository（最小版）。

和 memory.py 的唯一区别是「存哪」：
    MemoryThreadMetaStore → 进程内存的 dict（重启就丢）
    DbThreadMetaStore     → threads_meta 表（SQLAlchemy + asyncpg，重启还在）

接口、返回值形状都和 base.py / memory.py 完全一致，所以调用方换一下注入的
store 就行，不用改一行代码。

deer-flow 的 ThreadMetaRepository 有 407 行（归属校验、项目空间、置顶排序、
SQLite 的 BEGIN IMMEDIATE、跨进程锁……），最小版没有 auth / 项目空间，
只留会话列表需要的 CRUD。

关键点：
1. create 是「查一次，没有才 INSERT」的幂等写法，不是数据库 upsert。
   因为最小版单进程、调用方会 ensure，重复创建的概率极低；真撞主键时
   让异常冒出去，也比悄悄覆盖别人的标题好。
2. update_metadata 是 read-modify-write，必须防并发丢更新：
   postgres 用 SELECT ... FOR UPDATE 锁住这一行再合并。
3. touch=False 时用 flag_modified 把 updated_at 的当前值显式写进 SET，
   这样 model 的 onupdate 不会把它自动刷新（deer-flow 同款技巧）。
4. updated_at 由 model 的 onupdate 自动刷新；只有 touch=False 才需要干预。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from core.persistence.models.thread_meta import DEFAULT_THREAD_STATUS, ThreadMetaRow
from core.persistence.thread_meta.base import ThreadMetaStore



def _to_iso(value) -> str | None:
    """把数据库读回的时间（datetime）转成 ISO 字符串。

    naive 值统一当 UTC，保证返回格式和 memory.py 一致。
    （和 core/runtime/runs/store/db.py 里的 _to_iso 同一个作用。）
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value.isoformat()
    return value


class DbThreadMetaStore(ThreadMetaStore):
    """用 threads_meta 表存会话元信息的 ThreadMetaStore。

    依赖一个 async_sessionmaker（由 core.persistence.engine 在启动时建好）。
    构造只存引用，不开 engine —— engine 的开/关由 lifespan 统一管。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: ThreadMetaRow) -> dict:
        """把 ORM 行转成 base.py 约定的记录 dict。

        转换：
            metadata_json → metadata（字段改名，对外不暴露数据库列名）
            created_at / updated_at → ISO 字符串
        """
        d = row.to_dict()
        d["metadata"] = dict(d.pop("metadata_json", {}) or {})
        d["created_at"] = _to_iso(d.get("created_at"))
        d["updated_at"] = _to_iso(d.get("updated_at"))
        return d

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    async def create(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(ThreadMetaRow, thread_id)
                if row is None:
                    row = ThreadMetaRow(
                        thread_id=thread_id,
                        user_id=user_id,
                        display_name=display_name,
                        status=DEFAULT_THREAD_STATUS,
                        metadata_json=dict(metadata or {}),
                    )
                    session.add(row)
                elif row.user_id is None and user_id is not None:
                    # 认领：老行没有归属、这次带了 user_id，补上。
                    # 已有别的 user_id 时不动（幂等的 create 不抢别人的行）。
                    row.user_id = user_id
            # 事务提交后再转 dict：此时 created_at / updated_at 的默认值已经
            # 由数据库侧（INSERT）填好，能读到真实时间。
            return self._row_to_dict(row)

    async def update_display_name(self, thread_id: str, display_name: str) -> bool:
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(ThreadMetaRow, thread_id)
                if row is None:
                    return False
                row.display_name = display_name
                # updated_at 由 model 的 onupdate 自动刷新
                return True

    async def update_status(self, thread_id: str, status: str) -> bool:
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(ThreadMetaRow, thread_id)
                if row is None:
                    return False
                row.status = status
                return True

    async def update_metadata(
        self,
        thread_id: str,
        metadata: dict,
        *,
        touch: bool = True,
    ) -> bool:
        async with self._sf() as session:
            async with session.begin():
                # SELECT ... FOR UPDATE：锁住这一行，防止两个请求同时
                # 读-改-写时互相覆盖（丢更新）。
                result = await session.execute(
                    select(ThreadMetaRow)
                    .where(ThreadMetaRow.thread_id == thread_id)
                    .with_for_update()
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return False

                merged = dict(row.metadata_json or {})
                merged.update(metadata)
                # 必须赋一个新的 dict：SQLAlchemy 默认不追踪 JSON 列的内部变更，
                # 原地 merged.update(...) 不会把行标脏，UPDATE 不会发出去。
                row.metadata_json = merged

                if not touch:
                    # updated_at 有 onupdate，任何 UPDATE 都会刷新它。
                    # 这里把当前值显式标脏，SQLAlchemy 会把它写进 SET，
                    # onupdate 就不再覆盖 —— 保住会话原来的活跃时间。
                    flag_modified(row, "updated_at")
                return True

    async def delete(self, thread_id: str) -> bool:
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(ThreadMetaRow, thread_id)
                if row is None:
                    return False
                await session.delete(row)
                return True

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    async def get(self, thread_id: str) -> dict | None:
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            return self._row_to_dict(row) if row is not None else None

    async def search(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        stmt = select(ThreadMetaRow)
        if user_id is not None:
            stmt = stmt.where(ThreadMetaRow.user_id == user_id)
        if status is not None:
            stmt = stmt.where(ThreadMetaRow.status == status)
        stmt = (
            stmt.order_by(ThreadMetaRow.updated_at.desc(), ThreadMetaRow.thread_id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(row) for row in result.scalars()]
