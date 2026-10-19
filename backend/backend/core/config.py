from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from typing import Literal
from pydantic import SecretStr, Field, AliasChoices, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    app_name: str = "后端开发 · AI 安全知识情报系统"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5440
    postgres_user: str = "backend_dev"
    postgres_password: SecretStr
    postgres_db: str = "backend_development"
    redis_host: str = "127.0.0.1"
    redis_port: int = 6380
    redis_password: SecretStr
    admin_api_key: SecretStr
    api_port: int = 8010
    demo_heartbeat_seconds: int = 30
    outbound_proxy: SecretStr | None = None
    ai_service_url: str | None = None
    ai_service_token: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-flash"
    # Platform credentials never enter public API schemas or frontend variables.
    llm_provider: Literal["deepseek", "qwen"] = "deepseek"
    llm_model: str | None = Field(default=None, min_length=1, max_length=120)
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_api_key_file: Path | None = None
    dashscope_api_key: SecretStr | None = Field(default=None,
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "QWEN_API_KEY", "dashscope_api_key"))
    llm_timeout_seconds: float = Field(default=60, ge=1, le=120)
    llm_max_attempts: int = Field(default=2, ge=1, le=2)
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str | None = None
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    index_provider: Literal["internal", "external"] = "internal"
    qa_provider: Literal["auto", "platform", "deepseek", "external"] = "auto"
    agent_service_url: str | None = None
    agent_service_token: SecretStr | None = None
    agent_tool_token: SecretStr | None = None
    agent_write_token: SecretStr | None = None
    agent_timeout_seconds: int = Field(default=90, ge=1, le=120)
    agent_max_answer_tokens: int = Field(default=8192, ge=256, le=8192)
    user_queries_per_minute: int = Field(default=10, ge=1, le=100)
    user_queries_per_day: int = Field(default=100, ge=1, le=10000)
    signup_enabled: bool = True
    account_session_hours: int = Field(default=8, ge=1, le=24)
    nvd_api_key: SecretStr | None = None
    # Search credentials are owned by FastAPI, never the Agent or browser.
    tavily_api_key: SecretStr | None = None
    tavily_api_key_file: Path | None = None
    tavily_timeout_seconds: float = Field(default=5, ge=1, le=5)
    tavily_max_results: int = Field(default=3, ge=1, le=3)
    tavily_cache_seconds: int = Field(default=300, ge=0, le=900)
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://127.0.0.1:5173", "http://localhost:5173",
    ])

    @field_validator("cors_origins")
    @classmethod
    def valid_origins(cls, values):
        from urllib.parse import urlsplit
        result = []
        for value in values:
            parsed = urlsplit(value)
            try:
                parsed.port
            except ValueError:
                raise ValueError("invalid CORS origin") from None
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in {"", "/"}):
                raise ValueError("CORS origins must contain only scheme, hostname and optional port")
            result.append(value.rstrip("/"))
        return list(dict.fromkeys(result))

    @property
    def platform_llm_model(self) -> str:
        return self.llm_model or (self.deepseek_model if self.llm_provider == "deepseek" else "qwen-plus")

    @property
    def platform_llm_base_url(self) -> str:
        return self.llm_base_url or ("https://api.deepseek.com" if self.llm_provider == "deepseek"
                                    else "https://dashscope.aliyuncs.com/compatible-mode/v1")

    @property
    def platform_llm_configured(self) -> bool:
        key = self.llm_api_key or (self.deepseek_api_key if self.llm_provider == "deepseek"
                                  else self.dashscope_api_key)
        if key and key.get_secret_value().strip():
            return True
        if self.llm_api_key_file:
            try:
                stat = self.llm_api_key_file.stat()
                return (self.llm_api_key_file.is_file() and not self.llm_api_key_file.is_symlink()
                        and 0 < stat.st_size <= 4096 and not stat.st_mode & 0o077)
            except OSError:
                return False
        return False

    @field_validator("llm_base_url")
    @classmethod
    def valid_llm_base_url(cls, value):
        if value is None:
            return None
        from urllib.parse import urlsplit
        from ipaddress import ip_address
        parsed = urlsplit(value)
        try:
            parsed.port
        except ValueError:
            raise ValueError("invalid platform model URL") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.hostname in {"localhost", "host.docker.internal"}):
            raise ValueError("platform model URL must be an administrator-configured HTTPS endpoint")
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError("platform model URL must not be a private IP")
        return value.rstrip("/")

    @property
    def embedding_configured(self) -> bool:
        return bool(self.embedding_api_key and self.embedding_api_key.get_secret_value()
                    and self.embedding_base_url)

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg", username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host, port=self.postgres_port, database=self.postgres_db,
        )

    def redis_url(self, database: int = 0) -> str:
        password = quote(self.redis_password.get_secret_value(), safe="")
        return f"redis://:{password}@{self.redis_host}:{self.redis_port}/{database}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
