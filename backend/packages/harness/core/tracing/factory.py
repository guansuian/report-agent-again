"""追踪回调工厂 —— 对应真实 deer-flow 的 deerflow/tracing/factory.py:build_tracing_callbacks。

真实实现会按配置构造 Langfuse / LangSmith 的 callback handler。
这里返回空列表占位（相当于 tracing 关闭）。
"""

from __future__ import annotations


def build_tracing_callbacks() -> list:
    """返回要挂到 graph 根节点的 tracing callback 列表。"""
    return []
