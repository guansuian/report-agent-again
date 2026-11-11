"""runs 表的 ORM model —— 对应 deer-flow 的
deerflow/persistence/run/model.py（最小版）。

一张表：
    runs —— 一次 run 的记录（谁在跑、跑成没、报了什么错、用了多少 Token）

deer-flow 的 RunRow 有 40+ 列（幂等键、租约、token 统计、证据封存……），
最小版保留基础信息、用量合计和时间戳：

    run_id        主键，一次 run 的唯一标识
    thread_id     属于哪个会话（按它查「这个会话跑过几次」）
    user_id       归属用户（可空；从创建 run 的请求透传，用于按人过滤）
    status        生命周期状态，取值见 RunStatus（pending/running/success/...）
    error         出错时的描述，成功时为 NULL
    metadata_json 前端需要的附加信息（JSON，随便塞）
    total_input_tokens  本次 run 的累计输入 Token
    total_output_tokens 本次 run 的累计输出 Token
    total_tokens        本次 run 的累计总 Token
    llm_call_count      本次 run 中取得有效用量的模型调用次数
    created_at    创建时间
    updated_at    最后修改时间（改状态时自动刷新）

注意两个坑：
1. 列名不能叫 metadata —— SQLAlchemy 的 Declarative 基类自己占用了
   `Model.metadata`（存表结构信息），再定义一个同名属性会直接报错。
   所以叫 metadata_json（deer-flow 也是这个名字）。
2. 不能有 task 列 —— RunRecord.task 是 asyncio.Task，纯进程内句柄，
   没法序列化进数据库，重启后也没意义。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.persistence.base import Base

# 状态字符串最长的是 "interrupted"（11 个字符），16 够用；
# 对应 deer-flow 的 RUN_STATUS_MAX_LENGTH（那边给到 20，留了余量）。
RUN_STATUS_MAX_LENGTH = 16


class RunRow(Base):
    """一次 run 的持久化记录，runs 表的一行。"""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 按会话查 run 列表时会用到，加索引（deer-flow 也建了这个索引）。
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 归属用户。None = 未归属；查询时 None 表示不筛选（单用户模式）。
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(RUN_STATUS_MAX_LENGTH), nullable=False, default="pending"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 前端附加信息，如 {"assistant_id": "..."}。默认空对象，读的时候不用判 None。
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # 本次 run 的用量合计，初始为 0；用于保存 Journal 累计出的结果。
    total_input_tokens: Mapped[int] = mapped_column(default=0)
    total_output_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    llm_call_count: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # onupdate：每次 UPDATE 时自动刷新，不用业务代码手动改。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
