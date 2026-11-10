"""run_events 表 + seq 水印表的 ORM model —— 对应 deer-flow 的
persistence/models/run_event.py（表结构完全对齐）。

两张表：
    run_events           —— 事件本身（消息 / 工具结果等），读历史靠它
    run_event_thread_seq —— 每线程的 seq 高水位（删了事件也不回退 seq）

列名、类型、约束、索引完全照抄 deer-flow：
    run_events: id, thread_id, run_id, user_id, event_type, category,
                content(Text), content_sha256, event_metadata(JSON), seq, created_at
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from core.persistence.base import Base

# 对应 deer-flow constants.py 的 RUN_EVENT_TYPE_MAX_LENGTH / RUN_EVENT_CATEGORY_MAX_LENGTH
RUN_EVENT_TYPE_MAX_LENGTH = 32
RUN_EVENT_CATEGORY_MAX_LENGTH = 16


class RunEventRow(Base):
    """一条 run 事件（消息、工具结果等），读历史 / 前端展示都靠它。"""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 事件归属哪个用户。最小版还没接 auth 中间件，恒为 None（表结构先对齐）。
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(RUN_EVENT_TYPE_MAX_LENGTH), nullable=False)
    category: Mapped[str] = mapped_column(String(RUN_EVENT_CATEGORY_MAX_LENGTH), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    # 持久化内容的 sha256 摘要（去重 / 幂等用）。最小版先存着，暂不用于去重。
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    seq: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_events_thread_seq"),
        Index("ix_events_thread_cat_seq", "thread_id", "category", "seq"),
        Index("ix_events_run", "thread_id", "run_id", "seq"),
    )


class RunEventThreadSeqRow(Base):
    """每线程的 seq 高水位，保证删了事件 seq 也不回退。"""

    __tablename__ = "run_event_thread_seq"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
