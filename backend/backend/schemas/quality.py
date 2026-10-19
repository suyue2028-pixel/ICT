from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

Dimension = Literal["affected", "cwe", "cvss", "remediation", "analysis", "poc"]
PredictionMethod = Literal["source_reported", "external", "all"]


class QualityBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_demo: bool = Field(default=False, strict=True)
    ai_relevant: bool = Field(default=True, strict=True)
    source_id: UUID | None = None
    document_type: Literal["vulnerability", "advisory", "all"] = "vulnerability"
    prediction_method: PredictionMethod = "source_reported"
    per_source: int = Field(default=12, ge=1, le=30, strict=True)
    max_cases: int = Field(default=100, ge=1, le=100, strict=True)
    seed: str = Field(default="quality-v1", min_length=1, max_length=64)


class FieldReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected: list[JsonValue] = Field(max_length=500)
    basis: str = Field(min_length=10, max_length=2000)

    @field_validator("basis")
    @classmethod
    def meaningful_basis(cls, value):
        if len(value.strip()) < 10:
            raise ValueError("Record the original evidence and locator used to review this field")
        return value.strip()


class QualityReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    prediction_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=100)
    annotation_origin: Literal["human", "ai_assisted", "synthetic"]
    independent: bool = Field(strict=True)
    field_reviews: dict[Dimension, FieldReview] = Field(min_length=1, max_length=6)

    @field_validator("reviewer")
    @classmethod
    def nonblank_reviewer(cls, value):
        if not value.strip():
            raise ValueError("Reviewer is required")
        return value.strip()
