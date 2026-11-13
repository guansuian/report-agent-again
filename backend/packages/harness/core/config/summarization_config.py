"""上下文压缩配置，对应 DeerFlow 的 config/summarization_config.py。

第一版只按 Token 数触发，保留最近若干条消息，使用本次 run 的模型生成摘要。
下面的默认数值是配置起点，启用前需要按实际模型容量调整。
"""

from pydantic import BaseModel, ConfigDict, Field


class SummarizationConfig(BaseModel):
    """决定什么时候压缩，以及一次摘要调用允许使用多少 Token。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    trigger_tokens: int = Field(
        default=8000, gt=0,
        description="当前上下文达到多少 Token 时触发压缩，包含已有摘要",
    )
    keep_messages: int = Field(
        default=20, gt=0,
        description="保留最近多少条消息；不是对话轮数，工具配对可能让实际条数增加",
    )
    max_input_tokens: int = Field(
        default=16000, gt=0,
        description="一次摘要请求的输入上限，包含摘要提示词、旧摘要和待总结消息；放不下时不截断原文",
    )
    max_output_tokens: int = Field(
        default=1000, gt=0,
        description="一次摘要调用最多生成多少 Token",
    )
