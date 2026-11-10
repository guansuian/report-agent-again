"""支持的流模式 —— 对应 deer-flow 的 deerflow/runtime/stream_modes.py（最小版）。

前端用 stream_mode 说「我想看哪些事件」：

    values          每个图节点跑完，推一次完整状态（全量快照）
    messages-tuple  模型每吐一小段就推一次（打字机效果）

注意同一个东西有三个名字，别搞混：

    前端请求 "messages-tuple"
        → LangGraph 里的模式叫 "messages"
        → SSE 事件名也叫 "messages"

中间这层改名在 to_langgraph_stream_modes 里做。
"""

from __future__ import annotations

from typing import Literal, get_args

RunStreamMode = Literal["values", "messages-tuple"]

SUPPORTED_RUN_STREAM_MODES: frozenset[str] = frozenset(get_args(RunStreamMode))

# 没传 stream_mode 时的默认值。
# deer-flow 默认只给 ["values"]，因为它的前端每次都会显式要 messages-tuple；
# 我们默认带上 messages-tuple，这样 curl 不传参数也能看到打字机效果。
DEFAULT_RUN_STREAM_MODES: list[str] = ["values", "messages-tuple"]


class UnsupportedStreamModeError(ValueError):
    """请求了不认识的流模式（比如拼错成 "message"）。"""

    def __init__(self, modes: list[str]) -> None:
        self.modes = tuple(dict.fromkeys(modes))
        super().__init__(f"Unsupported stream mode(s): {', '.join(self.modes)}")


def normalize_stream_modes(raw: list[str] | str | None) -> list[str]:
    """把请求里的 stream_mode 统一成列表，并挡掉不认识的名字。

    输入可能是三种形状：
        None                      → 用默认值
        "values"                  → ["values"]
        ["values", "messages-tuple"] → 原样
    """
    if raw is None:
        return list(DEFAULT_RUN_STREAM_MODES)

    modes = [raw] if isinstance(raw, str) else list(raw)
    if not modes:                      # 传了空列表 [ ]，也当没传
        return list(DEFAULT_RUN_STREAM_MODES)

    unsupported = [
        mode if isinstance(mode, str) else type(mode).__name__
        for mode in modes
        if not isinstance(mode, str) or mode not in SUPPORTED_RUN_STREAM_MODES
    ]
    if unsupported:
        raise UnsupportedStreamModeError(unsupported)
    return list(dict.fromkeys(modes))


def to_langgraph_stream_modes(raw: list[str] | str | None) -> list[str]:
    """把前端名字翻译成 LangGraph 认的名字：messages-tuple → messages。"""
    modes = normalize_stream_modes(raw)
    mapped = ["messages" if mode == "messages-tuple" else mode for mode in modes]
    return list(dict.fromkeys(mapped))
