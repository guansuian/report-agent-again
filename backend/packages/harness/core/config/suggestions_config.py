"""推荐问题配置，参考 DeerFlow 的 config/suggestions_config.py。"""

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_SUGGESTIONS = 3
MAX_SUGGESTIONS_LIMIT = 5


class SuggestionsConfig(BaseModel):
    """控制回复结束后是否生成推荐问题，以及最多生成多少个。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="是否在 AI 回复结束后生成推荐问题",
    )
    max_suggestions: int = Field(
        default=DEFAULT_MAX_SUGGESTIONS,
        ge=1,
        le=MAX_SUGGESTIONS_LIMIT,
        description="每次最多生成多少个推荐问题，默认 3 个，允许 1 至 5 个",
    )
