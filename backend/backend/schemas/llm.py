"""Internal platform model contract. Clients cannot select credentials or endpoints."""
import json
from typing import Any, Literal
from pydantic import Field, field_validator, model_validator
from backend.schemas.agent import StrictModel


Purpose = Literal["classify", "general_explain", "plan", "rewrite", "assess", "synthesize", "verify", "converse", "legacy_answer"]


class ClassificationEntities(StrictModel):
    cve_ids: list[str] = Field(max_length=8)
    products: list[str] = Field(max_length=8)

    @field_validator("cve_ids")
    @classmethod
    def valid_cves(cls, values):
        import re
        if any(not re.fullmatch(r"CVE-\d{4}-\d{4,}", value) for value in values):
            raise ValueError("invalid classification identifier")
        return values

    @field_validator("products")
    @classmethod
    def valid_products(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("invalid classification entity")
        return values


class ClassificationResult(StrictModel):
    intent: Literal["general", "security", "clarification"]
    entities: ClassificationEntities
    information_needs: list[str] = Field(max_length=8)
    freshness_required: bool = Field(strict=True)
    conversation_answer: str | None = Field(max_length=4000)

    @field_validator("information_needs")
    @classmethod
    def valid_needs(cls, values):
        if any(not value.strip() or len(value) > 120 for value in values):
            raise ValueError("invalid information need")
        return values

    @model_validator(mode="after")
    def direct_answer_is_general(self):
        if (self.intent != "general" and self.conversation_answer is not None
                or self.intent == "general" and self.entities.cve_ids
                or self.intent == "general" and self.conversation_answer is None
                or self.conversation_answer is not None and not self.conversation_answer.strip()):
            raise ValueError("invalid classification direct answer")
        return self


class GeneralExplanationResult(StrictModel):
    answer: str = Field(min_length=1, max_length=4000)

    @field_validator("answer")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("empty general explanation")
        return value


class CompletionRequest(StrictModel):
    purpose: Purpose
    payload: dict[str, Any]
    max_attempts: int = Field(default=1, ge=1, le=2, strict=True)
    timeout_seconds: float = Field(default=30, ge=1, le=120)
    max_answer_tokens: int = Field(default=1200, ge=1, le=8192, strict=True)
    required_capabilities: list[Literal["json_object", "server_tool_workflow", "native_tool_calls"]] = Field(
        default_factory=lambda: ["json_object", "server_tool_workflow"], max_length=3)

    @field_validator("payload")
    @classmethod
    def bounded_payload(cls, value):
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 250000:
            raise ValueError("model payload exceeds limit")
        return value


class TokenUsage(StrictModel):
    prompt_tokens: int | None = Field(default=None, ge=0, le=10000000, strict=True)
    completion_tokens: int | None = Field(default=None, ge=0, le=10000000, strict=True)
    total_tokens: int | None = Field(default=None, ge=0, le=20000000, strict=True)


class CompletionResponse(StrictModel):
    result: dict[str, Any]
    attempts: int = Field(ge=1, le=2)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider: Literal["deepseek", "qwen"]
    model: str = Field(min_length=1, max_length=120)
