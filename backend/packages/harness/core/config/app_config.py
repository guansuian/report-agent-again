"""应用配置 —— 对应真实 deer-flow 的 deerflow/config/app_config.py。

真实 deer-flow 的 AppConfig 有几十个字段（memory、summarization、skills、
subagents、database、authorization、token_budget ...），每个字段都是一个
独立的 dataclass。这里只保留「装配主链路」用得到的最小集合。

用 pydantic 的 BaseModel 做配置模型：get_app_config() 读项目根目录的
config.yaml，先解析 $ENV_VAR 引用，再用 model_validate 做类型校验；
找不到文件时回退到内置默认配置。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from core.config.company_config import CompanyConfig

logger = logging.getLogger(__name__)


# 模块级就加载 .env（python-dotenv 的 load_dotenv），保证任何 get_app_config()
# 调用之前环境变量已就位，之后 _resolve_env_variables 才能解析 config.yaml 里的 $VAR。
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class ModelConfig(BaseModel):
    """一个模型的配置档案。"""

    # yaml 里出现未知字段（比如拼错名）就直接报错，而不是悄悄忽略
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str = "placeholder"
    api_key: str | None = None   # 支持 $ENV_VAR 引用，加载时由 _resolve_env_variables 解析
    base_url: str | None = None  # OpenAI 兼容接口地址，如 https://api.deepseek.com
    supports_vision: bool = False
    supports_thinking: bool = False


class AgentConfig(BaseModel):
    """一个自定义 agent 的配置档案（对应真实 deer-flow 的 SOUL.md + 配置）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    model: str | None = None
    skills: list[str] | None = None
    memory_enabled: bool = True


class AuthorizationConfig(BaseModel):
    """授权配置。

    fail_closed：授权 provider 出错或被拒时，是直接抛错（True）还是降级放行（False）。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    fail_closed: bool = False
    default_role: str = "user"


class DatabaseConfig(BaseModel):
    """数据库配置（对应真实 deer-flow 的 DatabaseConfig，最小版）。

    最小版只留两个字段：
      - backend      —— 用哪个后端（memory / sqlite / postgres）
      - postgres_url —— postgres 的连接串（DSN），库名/账号/密码都在这条串里

    库名、账号、密码都塞在 postgres_url 里，engine 建库时从这条串解析出库名。
    以后同事要换库名，只改 .env 里的 DATABASE_URL，代码不动。
    """

    model_config = ConfigDict(extra="forbid")

    backend: Literal["memory", "sqlite", "postgres"] = "memory"
    postgres_url: str = ""

    @property
    def app_sqlalchemy_url(self) -> str:
        """SQLAlchemy asyncpg 用的 URL（postgresql:// → postgresql+asyncpg://）。

        业务表 ORM 走 SQLAlchemy，异步驱动是 asyncpg，连接串要带
        postgresql+asyncpg:// 前缀。这里从 postgres_url 现换，config.yaml
        和 .env 不用再加新字段。
        """
        url = self.postgres_url
        if url.startswith("postgresql+asyncpg://"):
            return url
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url


class AppConfig(BaseModel):
    """汇总所有配置的根对象。真实 deer-flow 有几十个字段，这里只留主链路用到的。"""

    model_config = ConfigDict(extra="forbid")

    models: list[ModelConfig] = Field(default_factory=list)
    agents: list[AgentConfig] = Field(default_factory=list)
    authorization: AuthorizationConfig = Field(default_factory=AuthorizationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    company: CompanyConfig = Field(default_factory=CompanyConfig)

    # 下面这些布尔开关，在真实实现里分别对应一个独立配置文件的 ``.enabled`` 字段
    memory_enabled: bool = True
    summarization_enabled: bool = True
    token_usage_enabled: bool = True
    loop_detection_enabled: bool = True
    token_budget_enabled: bool = False


    def get_model_config(self, name: str | None) -> ModelConfig | None:
        for m in self.models:
            if m.name == name:
                return m
        return None


# 进程级配置单例（真实 deer-flow 从 config.yaml 加载后缓存到这里）
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    global _app_config
    if _app_config is None:
        _app_config = _load_from_yaml()
    return _app_config


def _resolve_env_variables(config):
    """递归解析配置里的 $ENV_VAR 引用（对应真实 deer-flow 的 AppConfig.resolve_env_variables）。

    config.yaml 里写 ``api_key: $OPENAI_API_KEY``，加载时把 ``$OPENAI_API_KEY``
    替换成环境变量的实际值；环境变量没设置就报错（比拿到 None 更早暴露问题）。
    """
    if isinstance(config, str):
        if config.startswith("$"):
            value = os.getenv(config[1:])
            if value is None:
                raise ValueError(f"环境变量 {config[1:]} 未设置（config.yaml 引用了 ${config[1:]}）")
            return value
        return config
    if isinstance(config, dict):
        return {k: _resolve_env_variables(v) for k, v in config.items()}
    if isinstance(config, list):
        return [_resolve_env_variables(item) for item in config]
    return config


def _load_from_yaml() -> AppConfig:
    """读项目根目录的 config.yaml，解析 $ENV_VAR 后用 pydantic 校验翻译成 AppConfig。

    config.yaml 放在 backend/packages/harness/（pyproject.toml 旁边）。
    找不到文件时回退到内置默认配置。
    """
    yaml_path = Path(__file__).resolve().parents[2] / "config.yaml"
    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("找不到 %s，回退到内置默认配置", yaml_path)
        return _default_config()

    data = _resolve_env_variables(data)
    return AppConfig.model_validate(data)


def _default_config() -> AppConfig:
    """内置示例配置，作为 config.yaml 缺失时的回退。"""
    return AppConfig(
        models=[
            ModelConfig(name="deepseek-chat"),
            ModelConfig(name="gpt-4o", supports_vision=True, supports_thinking=True),
        ],
        agents=[AgentConfig(name="default")],
    )
