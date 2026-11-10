"""run 相关的枚举 / 数据结构 —— 对应 deer-flow 的
deerflow/runtime/runs/schemas.py（最小版）。

只放最基础的生命周期状态。以后要加 disconnect 模式、operation_kind 等，
在这里继续补即可。
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """一次 run 的生命周期状态。"""

    pending = "pending"            # 已创建，排队中，还没开始执行
    running = "running"            # 正在执行（模型调用 / 工具调用中）
    success = "success"            # 正常跑完，产出最终回答
    error = "error"                # 执行中出错（异常 / 工具失败导致中止）
    timeout = "timeout"            # 超过时间上限，被强制中止
    interrupted = "interrupted"    # 被外部打断（用户取消 / 断开连接）
