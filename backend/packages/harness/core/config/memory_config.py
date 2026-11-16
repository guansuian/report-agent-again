"""长期记忆配置，参考 DeerFlow 的 config/memory_config.py。

第一版只使用 mem0 OSS Python 包，不提供多后端切换或记忆工具模式。
backend_config 交给 mem0，包含提炼模型、向量模型、存储和自定义提炼规则。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryConfig(BaseModel):
    """控制跨对话记忆的保存、读取，以及一次注入多少内容。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="是否启用长期记忆的保存和读取")
    top_k: int = Field(default=5, gt=0, description="每次最多读取多少条相关记忆")
    max_injection_chars: int = Field(
        default=6000, gt=0,
        description="一次注入的记忆文字最多多少字符，避免记忆占用过多上下文",
    )
    custom_instructions_file: str = Field(
        default="core/agents/memory/custom_instructions.md", min_length=1,
        description="记忆提炼规则的 Markdown 文件路径，相对 backend/packages/harness 目录",
    )
    backend_config: dict[str, Any] = Field(
        default_factory=dict, repr=False,
        description="mem0 OSS 配置；llm 为提炼模型，embedder 为向量模型，vector_store 为存储；custom_instructions 正文由 Manager 从文件读取后填入",
    )

    @model_validator(mode="after")
    def _require_backend_config(self) -> "MemoryConfig":
        """启用时明确指定三个组件，不让 mem0 自动选择默认供应商。"""
        if not self.enabled:
            return self

        for component_name in ("llm", "embedder", "vector_store"):
            component = self.backend_config.get(component_name)
            if not isinstance(component, dict) or not component.get("provider"):
                raise ValueError(
                    f"启用长期记忆时，必须配置 memory.backend_config.{component_name}.provider"
                )

        return self
