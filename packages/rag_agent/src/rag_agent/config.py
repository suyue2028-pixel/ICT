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
    llm_mode: Literal["extractive", "live", "mock"] = "extractive"
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_key: SecretStr = SecretStr("")
    request_timeout: float = Field(default=30, gt=0, le=300)
    llm_timeout: float = Field(default=20, gt=0)
    max_llm_calls: int = Field(default=3, ge=0, le=3)
    max_tool_calls: int = Field(default=8, ge=1, le=8)
    max_retries: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def valid(self):
        parsed = urlsplit(self.llm_base_url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("LLM base URL must use HTTPS or loopback HTTP without embedded credentials")
        if self.overlap_tokens >= self.chunk_tokens:
            raise ValueError("overlap must be below chunk size")
        if self.embedding_provider == "e5" and not re.fullmatch(r"[a-f0-9]{40}", self.embedding_revision):
            raise ValueError("E5 revision must be an immutable 40-character model commit")
        if self.llm_mode == "live" and (not self.llm_key.get_secret_value() or not self.llm_model):
            raise ValueError("live mode requires RAG_LLM_KEY and RAG_LLM_MODEL")
        return self

    @classmethod
    def from_env(cls):
        mapping = {
            "RAG_DB_PATH": "db_path", "RAG_EMBEDDING_PROVIDER": "embedding_provider",
            "RAG_EMBEDDING_MODEL": "embedding_model", "RAG_EMBEDDING_REVISION": "embedding_revision",
            "RAG_EMBEDDING_DEVICE": "embedding_device", "RAG_LLM_MODE": "llm_mode",
            "RAG_LLM_BASE_URL": "llm_base_url", "RAG_LLM_MODEL": "llm_model", "RAG_LLM_KEY": "llm_key",
            "RAG_TIMEOUT": "request_timeout", "RAG_LLM_TIMEOUT": "llm_timeout",
        }
        values = {v: os.environ[k] for k, v in mapping.items() if k in os.environ}
        key_file = os.environ.get("RAG_LLM_KEY_FILE")
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
