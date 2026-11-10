"""公司接口配置；这里只放地址和超时，不放用户 Token。"""

import os

from pydantic import BaseModel, ConfigDict, Field


class CompanyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default_factory=lambda: os.getenv(
        "COMPANY_BASE_URL", "https://vsp-dev.sdaivss.com"
    ))
    timeout: float = Field(default=30.0, gt=0)
