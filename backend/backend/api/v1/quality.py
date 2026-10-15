"""Admin-only quality observation and immutable version-bound human review."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from backend.api.dependencies import Database, require_record
from backend.core.security import require_admin
from backend.models import EnrichmentQualityBatch
from backend.schemas.quality import QualityBatchRequest, QualityReviewRequest, PredictionMethod
from backend.services.enrichment_quality import (DIMENSIONS, monitoring, create_batch,
    save_review, batch_report, current_case_flags, LIMITATIONS, DEFAULT_SCAN_BATCH_SIZE)

router = APIRouter(prefix="/ops/enrichment-quality", tags=["富化质量审核"],
                   dependencies=[Depends(require_admin)])


@router.get("")
def quality_summary(db: Database, is_demo: bool = False, ai_relevant: bool = True,
                    source_id: UUID | None = None,
                    document_type: Literal["vulnerability", "advisory", "all"] = "vulnerability",
                    prediction_method: PredictionMethod = "source_reported",
                    batch_size: int = Query(DEFAULT_SCAN_BATCH_SIZE, ge=1, le=500)):
    """分批扫描全部符合条件的当前文档；覆盖率、结构/出处问题不等于人工准确率。"""
    result = monitoring(db, is_demo, ai_relevant, source_id, document_type, prediction_method,
                        batch_size=batch_size, include_issues=False)
    result.pop("issues")
    selection = EnrichmentQualityBatch.selection
    latest = db.scalar(select(EnrichmentQualityBatch).where(EnrichmentQualityBatch.is_demo == is_demo,
        selection["ai_relevant"].as_boolean() == ai_relevant,
        selection["source_id"].as_string() == (str(source_id) if source_id else None),
        selection["document_type"].as_string() == document_type,
        selection["prediction_method"].as_string() == prediction_method)
        .order_by(EnrichmentQualityBatch.created_at.desc(), EnrichmentQualityBatch.id.desc()).limit(1))
    result["reviewed_evaluation"] = batch_report(db, latest, current_only=True) if latest else {
        "status": "awaiting_independent_review", "overall": {"precision": None, "recall": None,
        "exact_field_accuracy": None}, "independently_reviewed_documents": 0}
    return result


@router.get("/issues")
def quality_issues(db: Database, is_demo: bool = False, ai_relevant: bool = True,
                   source_id: UUID | None = None,
                   document_type: Literal["vulnerability", "advisory", "all"] = "vulnerability",
                   prediction_method: PredictionMethod = "source_reported",
                   severity: Literal["error", "warning"] | None = None,
                   code: str | None = Query(default=None, max_length=100),
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   batch_size: int = Query(DEFAULT_SCAN_BATCH_SIZE, ge=1, le=500)):
    """扫描完整范围后按错误码/级别分页，内存仅保留本页问题。"""
    result = monitoring(db, is_demo, ai_relevant, source_id, document_type, prediction_method,
        batch_size=batch_size, issue_severity=severity, issue_code=code,
        issue_offset=(page - 1) * page_size, issue_limit=page_size)
    return {"items": result["issues"], "page": page,
        "page_size": page_size, "total": result["issues_total"], "scanned_documents": result["scanned_documents"],
        "total_documents": result["total_documents"], "scan_batch_size": result["scan_batch_size"],
        "scan_version": result["scan_version"], "scan_consistency": result["scan_consistency"],
        "scan_complete": result["scan_complete"], "scope": result["scope"], "limitations": LIMITATIONS}


@router.post("/batches", status_code=201)
def new_batch(body: QualityBatchRequest, db: Database):
    """分来源确定性抽样，冻结原文、版本和预测；不填入任何人工金标。"""
    row = create_batch(db, body)
    db.commit()
    return {"batch_id": row.id, "sample_size": row.sample_size, "fingerprint": row.fingerprint,
            "selection": row.selection, "status": "awaiting_independent_review"}


@router.get("/batches")
def list_batches(db: Database, is_demo: bool = False, page: int = Query(1, ge=1),
                 page_size: int = Query(20, ge=1, le=100)):
    query = select(EnrichmentQualityBatch).where(EnrichmentQualityBatch.is_demo == is_demo)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(EnrichmentQualityBatch.created_at.desc(), EnrichmentQualityBatch.id.desc())
        .offset((page - 1) * page_size).limit(page_size))
    return {"items": [{"batch_id": row.id, "created_at": row.created_at,
        "sample_size": row.sample_size, "fingerprint": row.fingerprint, "selection": row.selection} for row in rows],
        "page": page, "page_size": page_size, "total": total}


@router.get("/batches/{batch_id}")
def export_batch(batch_id: UUID, db: Database):
    """私有审核材料含完整已保存原文；仅管理员导出，不进入公开情报接口。"""
    row = require_record(db, EnrichmentQualityBatch, batch_id)
    flags = current_case_flags(db, row)
    cases = [{**case, "is_current": flags[case["document_id"]],
        "review_template": {"document_id": case["document_id"], "content_hash": case["content_hash"],
            "prediction_hash": case["prediction_hash"], "reviewer": None, "annotation_origin": None,
            "independent": None, "field_reviews": {field: {"expected": None, "basis": ""} for field in DIMENSIONS}}}
        for case in row.cases]
    return {"schema_version": "quality-review-1", "batch_id": row.id, "fingerprint": row.fingerprint,
        "is_demo": row.is_demo, "selection": row.selection, "cases": cases, "limitations": LIMITATIONS}


@router.post("/batches/{batch_id}/reviews")
def review_case(batch_id: UUID, body: QualityReviewRequest, db: Database):
    """人工标签追加保存；相同请求幂等，冲突/版本变化409，不覆盖旧标签。"""
    batch = require_record(db, EnrichmentQualityBatch, batch_id)
    row, created = save_review(db, batch, body)
    db.commit()
    return {"review_id": row.id, "created": created, "reviewed_at": row.reviewed_at,
        "annotation_origin": row.annotation_origin,
        "eligible_annotation": row.annotation_origin == "human" and row.independent,
        "is_demo": batch.is_demo, "competition_threshold_verified": False}


@router.get("/batches/{batch_id}/report")
def evaluate_batch(batch_id: UUID, db: Database, current_only: bool = True):
    """逐维度TP/FP/FN、precision/recall、审核覆盖率；缺标签与零分母返回null。"""
    return batch_report(db, require_record(db, EnrichmentQualityBatch, batch_id), current_only)
