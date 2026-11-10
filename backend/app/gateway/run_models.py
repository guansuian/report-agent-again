"""run 边界的请求模型 —— 对应 deer-flow 的 backend/app/gateway/run_models.py（最小版）。

deer-flow 的 RunCreateRequest 有 20 个字段，一大半是「兼容 LangGraph SDK 客户端」或
「占位拒绝不支持的选项」（webhook / on_completion / after_seconds ...），跟我们要跑通
的核心无关。这里只留最小流程真正用到的字段。

为什么单独一个文件：RunCreateRequest 被多个入口共用（HTTP 的 stream / wait / join
路由，以及以后可能的内部启动路径）。边界上先把 JSON 校验成强类型，后面 start_run /
run_agent 才能安全地用 body.input / body.config，不怕类型错。

依赖说明：stream_mode 的合法值来自 core.runtime.stream_modes，边界上就校验，
拼错的名字直接 422，不拖到后台任务里才炸。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.runtime.stream_modes import UnsupportedStreamModeError, normalize_stream_modes


class RunCreateRequest(BaseModel):
    """创建一次 run 的请求体。

    前端发来的 JSON 先被它校验成强类型对象，再传给 start_run。
    """

    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] | None = None          # 图输入，例如 {"messages": [...]}
    config: dict[str, Any] | None = None         # RunnableConfig 覆盖（configurable / context）
    context: dict[str, Any] | None = None        # 运行时覆盖（model_name 等）
    stream_mode: list[str] | str | None = None   # 前端要的流模式：values / messages-tuple
    # 归属用户。还没接登录，先由前端透传；写入 runs / run_events / threads_meta，
    # 只作标签和列表过滤，不是权限校验（请求体里的值可伪造）。
    user_id: str | None = Field(default=None, max_length=64)

    @field_validator("user_id")
    @classmethod
    def _user_id_not_blank(cls, value: str | None) -> str | None:
        """传了 user_id 就必须是非空字符串；前后空格去掉再存。"""
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("user_id 不能是空字符串")
        return value

    @field_validator("stream_mode", mode="before")
    @classmethod
    def reject_unsupported_stream_modes(cls, value: Any) -> Any:
        """不认识的名字（比如把 messages-tuple 拼成 message）在边界上挡掉。"""
        if value is None:
            return value
        if not isinstance(value, str) and (
            not isinstance(value, list) or not all(isinstance(m, str) for m in value)
        ):
            return value  # 形状不对交给 pydantic 自己报类型错误
        try:
            normalize_stream_modes(value)
        except UnsupportedStreamModeError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("config")
    @classmethod
    def reject_non_object_config_sections(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """config.configurable 和 config.context 必须是 dict（或没传）。

        run_agent 里会 config.get("configurable")，如果不是 dict 会炸；
        在边界上就挡掉，比跑到一半才崩好。
        """
        if value is None:
            return value
        for section in ("configurable", "context"):
            if value.get(section) is not None and not isinstance(value[section], dict):
                raise ValueError(f"config.{section} must be an object or null")
        return value
