from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from typing import Literal
from pydantic import SecretStr, Field, field_validator
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
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str | None = None
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    index_provider: Literal["internal", "external"] = "internal"
    qa_provider: Literal["auto", "deepseek", "external"] = "auto"
    agent_service_url: str | None = None
    agent_service_token: SecretStr | None = None
    agent_tool_token: SecretStr | None = None
    agent_write_token: SecretStr | None = None
    agent_timeout_seconds: int = Field(default=30, ge=1, le=120)
    user_queries_per_minute: int = Field(default=10, ge=1, le=100)
    user_queries_per_day: int = Field(default=100, ge=1, le=10000)
    nvd_api_key: SecretStr | None = None
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
