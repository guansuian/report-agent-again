"""工具产物登记配置，参考 DeerFlow 的 config/tool_artifact_config.py。"""

from pydantic import BaseModel, ConfigDict, Field


class ToolArtifactConfig(BaseModel):
    """控制产物捕获中间件；登记记录保存在 State.tool_artifacts 中。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="是否启用工具产物登记",
    )
    max_entries: int = Field(
        default=100, ge=10, le=1000,
        description="每个会话最多保留多少条产物记录；超出时移除最早登记的记录，不删除产物文件",
    )
