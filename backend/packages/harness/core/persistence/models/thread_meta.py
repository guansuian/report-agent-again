"""threads_meta 表的 ORM model —— 对应 deer-flow 的
deerflow/persistence/thread_meta/model.py（最小版）。

一张表：
    threads_meta —— 一个会话（thread）的元信息

deer-flow 的 ThreadMetaRow 有 10 列（incarnation、user_id、project_id ...），
最小版没有登录 / 项目空间，只留「会话列表」真正需要的 7 列：

    thread_id     主键，会话 id（和 runs.thread_id / run_events.thread_id 对应）
    user_id       归属用户（可空；还没接鉴权，先当归属标签 + 列表过滤）
    display_name  会话标题（前端会话列表显示；可以为空）
    status        会话状态：idle / running / error / interrupted / timeout
    metadata_json 附加信息（JSON，比如前端标记，后面要加 assistant_id 也塞这里）
    created_at    创建时间
    updated_at    最后修改时间（改标题 / 状态时自动刷新）

两个注意点：
1. 表名是 threads_meta（deer-flow 原样），不是 thread_meta。
   历史上建表脚本用的也是这个复数名，改表名会让已有数据「消失」。
2. 这一行和 LangGraph 的 checkpoint 是两套数据：
   threads_meta 回答「会话列表怎么展示」，checkpoint 回答「模型记得哪些消息」。
   删掉 threads_meta 不会自动删 checkpoint；两边的清理由路由层决定。

为什么 thread_id 不做外键：
    和 runs / run_events 一样，deer-flow 也不用数据库外键，靠应用层保证一致。
    这样删除线程时不用先删子表，迁移/回填也更省事。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from core.persistence.base import Base

# 状态字符串最长的是 "interrupted"（11 个字符），20 留了余量。
# 对应 deer-flow ThreadMetaRow.status 的 String(20)。
THREAD_STATUS_MAX_LENGTH = 20

# 还没跑过任何 run 的会话默认状态。
DEFAULT_THREAD_STATUS = "idle"


class ThreadMetaRow(Base):
    """一个会话的持久化元信息，threads_meta 表的一行。"""

    __tablename__ = "threads_meta"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 归属用户。还没接登录，先由请求体传入；None = 未归属（老数据 / 匿名）。
    # 注意：这是「归属标签 + 列表过滤器」，不是权限边界（请求体里的 id 可伪造）。
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 标题由前端 PATCH /api/threads/{id} 改；还没起名时是 NULL。
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(
        String(THREAD_STATUS_MAX_LENGTH),
        nullable=False,
        default=DEFAULT_THREAD_STATUS,
    )
    # 前端附加信息，如 {"pinned": true}。默认空对象，读的时候不用判 None。
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # onupdate：每次 UPDATE（改状态 / 标题 / metadata）时自动刷新。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
