from __future__ import annotations
import os
import re
import stat
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, model_validator
from .schemas import Model


class Settings(Model):
    db_path: Path = Path("data/ict.db")
    embedding_provider: Literal["e5", "mock"] = "e5"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    embedding_device: Literal["cpu", "mps"] = "cpu"
    chunk_tokens: int = Field(default=300, ge=32, le=480)
    overlap_tokens: int = Field(default=40, ge=0)
    retrieval_rank_pool: int = Field(default=32, ge=8, le=100)
    retrieval_vector_threshold: float = Field(default=0.78, ge=0, le=1)
    llm_mode: Literal["extractive", "live", "mock"] = "extractive"
    # Platform mode keeps supplier credentials in the business backend. Direct
    # access is an explicit compatibility/development option.
    llm_transport: Literal["backend", "direct"] = "backend"
    llm_gateway_url: str = "http://127.0.0.1:8010"
    llm_service_token: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_key: SecretStr = SecretStr("")
    request_timeout: float = Field(default=75, gt=0, le=300)
    llm_timeout: float = Field(default=60, gt=0)
    max_llm_calls: int = Field(default=6, ge=0, le=8)
    allow_unverified_concepts: bool = True
    max_tool_calls: int = Field(default=10, ge=1, le=10)
    max_retries: int = Field(default=1, ge=0, le=1)
    max_retrieval_rounds: int = Field(default=3, ge=1, le=3)
    external_tools_enabled: bool = False

    @model_validator(mode="after")
    def valid(self):
        parsed = urlsplit(self.llm_base_url)
        try:
            parsed.port
        except ValueError:
            raise ValueError("invalid LLM base URL port") from None
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("LLM base URL must use HTTPS or loopback HTTP without embedded credentials")
        gateway = urlsplit(self.llm_gateway_url)
        try:
            gateway.port
        except ValueError:
            raise ValueError("invalid LLM gateway URL port") from None
        if (not gateway.hostname or gateway.username or gateway.password or gateway.query or gateway.fragment
                or gateway.scheme not in {"http", "https"}
                or (gateway.scheme == "http" and gateway.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("LLM gateway URL must use HTTPS or loopback HTTP without embedded credentials")
        if self.overlap_tokens >= self.chunk_tokens:
            raise ValueError("overlap must be below chunk size")
        if self.embedding_provider == "e5" and not re.fullmatch(r"[a-f0-9]{40}", self.embedding_revision):
            raise ValueError("E5 revision must be an immutable 40-character model commit")
        if self.llm_mode == "live":
            if self.llm_transport == "direct" and (not self.llm_key.get_secret_value() or not self.llm_model):
                raise ValueError("direct live mode requires RAG_LLM_KEY and RAG_LLM_MODEL")
            if self.llm_transport == "backend" and not self.llm_service_token.get_secret_value():
                raise ValueError("platform live mode requires RAG_BACKEND_TOOL_TOKEN or RAG_LLM_SERVICE_TOKEN")
            if self.llm_transport == "backend" and self.llm_key.get_secret_value():
                raise ValueError("platform live mode must not receive a supplier API key")
        return self

    @classmethod
    def from_env(cls):
        mapping = {
            "RAG_DB_PATH": "db_path", "RAG_EMBEDDING_PROVIDER": "embedding_provider",
            "RAG_EMBEDDING_MODEL": "embedding_model", "RAG_EMBEDDING_REVISION": "embedding_revision",
            "RAG_EMBEDDING_DEVICE": "embedding_device", "RAG_LLM_MODE": "llm_mode",
            "RAG_LLM_TRANSPORT": "llm_transport", "RAG_LLM_GATEWAY_URL": "llm_gateway_url",
            "RAG_LLM_SERVICE_TOKEN": "llm_service_token",
            "RAG_RETRIEVAL_RANK_POOL": "retrieval_rank_pool",
            "RAG_RETRIEVAL_VECTOR_THRESHOLD": "retrieval_vector_threshold",
            "RAG_LLM_BASE_URL": "llm_base_url", "RAG_LLM_MODEL": "llm_model", "RAG_LLM_KEY": "llm_key",
            "RAG_TIMEOUT": "request_timeout", "RAG_LLM_TIMEOUT": "llm_timeout",
            "RAG_MAX_LLM_CALLS": "max_llm_calls", "RAG_MAX_TOOL_CALLS": "max_tool_calls",
            "RAG_MAX_RETRIEVAL_ROUNDS": "max_retrieval_rounds",
            "RAG_EXTERNAL_TOOLS_ENABLED": "external_tools_enabled",
            "RAG_ALLOW_UNVERIFIED_CONCEPTS": "allow_unverified_concepts",
        }
        values = {v: os.environ[k] for k, v in mapping.items() if k in os.environ}
        if "llm_gateway_url" not in values and os.environ.get("RAG_BACKEND_URL"):
            values["llm_gateway_url"] = os.environ["RAG_BACKEND_URL"]
        if not values.get("llm_service_token") and os.environ.get("RAG_BACKEND_TOOL_TOKEN"):
            values["llm_service_token"] = os.environ["RAG_BACKEND_TOOL_TOKEN"]
        key_file = os.environ.get("RAG_LLM_KEY_FILE")
        if values.get("llm_transport", "backend") == "backend":
            if key_file or values.get("llm_key"):
                raise ValueError("supplier keys belong to the backend; use explicit RAG_LLM_TRANSPORT=direct for legacy mode")
            key_file = None
        if key_file:
            if values.get("llm_key"):
                raise ValueError("configure RAG_LLM_KEY or RAG_LLM_KEY_FILE, not both")
            try:
                path = Path(key_file).expanduser()
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                        raise ValueError("key file must be a small regular file")
                    raw = handle.read(16385)
                    if len(raw) > 16384:
                        raise ValueError("key file is too large")
                    key = raw.decode("utf-8").strip()
            except (OSError, UnicodeError, ValueError):
                raise ValueError("unable to read LLM key file") from None
            if not key or any(c.isspace() for c in key) or "\x00" in key:
                raise ValueError("LLM key file must contain a single nonempty key")
            values["llm_key"] = key
        if "llm_key" in values:
            values["llm_key"] = SecretStr(values["llm_key"])
        return cls(**values)
