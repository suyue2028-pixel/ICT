"""Summary and evidence-backed relationships for the frontend."""
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.api.dependencies import Database, require_record
from backend.models import DataSource, Document, TaskRun, Vulnerability, VulnerabilityDocument

router = APIRouter(tags=["统计与关系"])


@router.get("/stats")
def stats(db: Database, is_demo: bool = False):
    document_count = db.scalar(select(func.count()).select_from(Document).where(Document.is_demo == is_demo))
    vulnerability_count = db.scalar(select(func.count()).select_from(Vulnerability).where(
        Vulnerability.is_demo == is_demo))
    source_counts = db.execute(select(DataSource.category, func.count(Document.id)).join(
        Document, Document.source_id == DataSource.id).where(Document.is_demo == is_demo)
        .group_by(DataSource.category)).all()
    severity_counts = db.execute(select(Vulnerability.severity, func.count()).where(
        Vulnerability.is_demo == is_demo).group_by(Vulnerability.severity)).all()
    trend = db.execute(select(func.date(Document.first_collected_at), func.count()).where(
        Document.is_demo == is_demo).group_by(func.date(Document.first_collected_at))
        .order_by(func.date(Document.first_collected_at))).all()
    task_kind = "offline_demo" if is_demo else "collect"
    statuses = dict(db.execute(select(TaskRun.status, func.count()).where(
        TaskRun.kind == task_kind).group_by(TaskRun.status)).all())
    terminal = statuses.get("succeeded", 0) + statuses.get("failed", 0)
    delay = db.execute(select(func.count(), func.avg(func.extract(
        "epoch", Document.first_collected_at - Document.published_at))).where(
        Document.is_demo == is_demo, Document.published_at.is_not(None),
        Document.first_collected_at >= Document.published_at)).one()
    missing_date = db.scalar(select(func.count()).select_from(Document).where(
        Document.is_demo == is_demo, Document.published_at.is_(None)))
    return {"is_demo": is_demo, "documents": document_count,
            "vulnerabilities": vulnerability_count, "source_categories": dict(source_counts),
            "category_count": len(source_counts), "risk_distribution": dict(severity_counts),
            "collection_trend": [{"date": str(date), "count": count} for date, count in trend],
            "task_statuses": statuses,
            "task_success_rate": statuses.get("succeeded", 0) / terminal if terminal else None,
            "latency": {"measured_count": delay[0], "mean_seconds": float(delay[1]) if delay[1] else None,
                        "missing_published_at": missing_date,
                        "definition": "first_collected_at minus source published_at; negative values excluded"},
            "measurement_limit": "No labelled accuracy/recall or AI answer score has been measured"}


@router.get("/relations/{vulnerability_id}")
def relations(vulnerability_id: UUID, db: Database):
    vulnerability = require_record(db, Vulnerability, vulnerability_id)
    documents = db.scalars(select(Document).join(VulnerabilityDocument,
        VulnerabilityDocument.document_id == Document.id).where(
        VulnerabilityDocument.vulnerability_id == vulnerability_id)).all()
    return {"nodes": [{"id": vulnerability.id, "type": "vulnerability", "label": vulnerability.identifier},
                      *[{"id": doc.id, "type": doc.document_type, "label": doc.title} for doc in documents]],
            "edges": [{"from": vulnerability.id, "to": doc.id, "type": "documented_by",
                       "evidence": {"document_id": doc.id, "content_hash": doc.content_hash,
                                    "source_url": doc.source_url}} for doc in documents],
            "scope": "Explicit source-to-vulnerability links only; no graph inference"}
