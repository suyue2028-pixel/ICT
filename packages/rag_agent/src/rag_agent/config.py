from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Literal
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
            "RAG_TIMEOUT": "request_timeout",
        }
        return cls(**{v: os.environ[k] for k, v in mapping.items() if k in os.environ})
