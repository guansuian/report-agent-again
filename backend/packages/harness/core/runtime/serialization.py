"""把 LangChain 对象序列化成可 JSON 化的 dict —— 对应 deer-flow 的
deerflow/runtime/serialization.py（最小版）。

作用：run_agent 把 agent.astream 吐出来的 chunk 变成能塞进 StreamBridge、
最终发成 SSE 的数据。只有 JSON 能表示的类型才能过这条线。

最小版只做一件事：递归地 model_dump() / dict() / str() 兜底，并把非有限
浮点（NaN / inf）归一成 None——它们不是合法 JSON，会打爆 SSE / JSONResponse。
"""

from __future__ import annotations

import math
from typing import Any


def serialize_lc_object(obj: Any) -> Any:
    """递归地把一个 LangChain 对象转成 JSON 可序列化的值。"""
    if obj is None:
        return None
    if isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        # NaN / inf / -inf 不是合法 JSON（RFC 8259），归一成 None。
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: serialize_lc_object(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_lc_object(item) for item in obj]
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return serialize_lc_object(obj.model_dump())
        except Exception:
            pass
    # Pydantic v1 / 旧对象
    if hasattr(obj, "dict"):
        try:
            return serialize_lc_object(obj.dict())
        except Exception:
            pass
    # 兜底
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def serialize_messages_tuple(obj: Any) -> Any:
    """把 messages 模式的 (chunk, metadata) 二元组序列化。

    LangGraph 在 stream_mode="messages" 下，每吐一小段就给一个二元组：

        chunk    —— 这一小段内容（AIMessageChunk，可能只是一个字）
        metadata —— 这段内容来自哪个节点等信息（dict）

    返回 [chunk, metadata] 的列表，顺序固定：前端按下标 0 / 1 取。
    """
    if isinstance(obj, tuple) and len(obj) == 2:
        chunk, metadata = obj
        return [
            serialize_lc_object(chunk),
            serialize_lc_object(metadata) if isinstance(metadata, dict) else {},
        ]
    return serialize_lc_object(obj)


def serialize(obj: Any, *, mode: str = "") -> Any:
    """按 mode 序列化一个对象。

    values   —— 全量状态 dict，剥掉 LangGraph 内部键
    messages —— (chunk, metadata) 二元组，转成 [chunk, metadata]
    其他     —— 递归兜底
    """
    if mode == "messages":
        return serialize_messages_tuple(obj)
    if mode == "values":
        if isinstance(obj, dict):
            return {
                k: serialize_lc_object(v)
                for k, v in obj.items()
                if not k.startswith("__")
            }
        return serialize_lc_object(obj)
    return serialize_lc_object(obj)
