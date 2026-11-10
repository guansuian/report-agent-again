"""SQLAlchemy declarative base —— 对应 deer-flow 的 persistence/base.py（最小版）。

所有业务表的 ORM model 都继承这个 Base。它提供通用的 to_dict()（用
SQLAlchemy 的 inspect 自动反射列），这样每个 model 不用自己写序列化。

注意：LangGraph checkpointer 的表（checkpoints 等）不归这个 Base 管，
那是 AsyncPostgresSaver 自己建的，和业务表是两套。
"""

from __future__ import annotations

from functools import cache

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase


@cache
def _column_keys(cls: type) -> tuple[str, ...]:
    """缓存某个 ORM 类的列名（inspect 反射较贵，按类缓存一次）。

    to_dict / __repr__ 每行都会调用，所以用 @cache 避免重复反射；
    列映射在类定义时就固定了，缓存不会过期。
    """
    return tuple(c.key for c in sa_inspect(cls).mapper.column_attrs)


class Base(DeclarativeBase):
    """所有业务表 ORM model 的基类，带 to_dict() + __repr__。"""

    def to_dict(self, *, exclude: set[str] | None = None) -> dict:
        """把 ORM 实例转成普通 dict（{列名: 值}）。

        Args:
            exclude: 要省略的列名集合。
        """
        keys = _column_keys(type(self))
        if exclude:
            return {k: getattr(self, k) for k in keys if k not in exclude}
        return {k: getattr(self, k) for k in keys}

    def __repr__(self) -> str:
        cols = ", ".join(f"{k}={getattr(self, k)!r}" for k in _column_keys(type(self)))
        return f"{type(self).__name__}({cols})"
