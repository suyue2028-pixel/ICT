from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import String, cast, or_, select

from backend.api.dependencies import Database, PageNumber, PageSize, paginate, require_record
from backend.models import Document, Evidence, Vulnerability, VulnerabilityAlias, VulnerabilityDocument
from backend.schemas.contracts import DocumentRead, Page, VulnerabilityRead

router = APIRouter(prefix="/intelligence", tags=["情报查询"])


@router.get("", response_model=Page[VulnerabilityRead])
def list_intelligence(db: Database, page: PageNumber = 1, page_size: PageSize = 20,
                      is_demo: bool = False, keyword: str | None = Query(default=None, max_length=200),
                      severity: str | None = None, ai_relevant: bool | None = True,
                      product: str | None = Query(default=None, max_length=200),
                      source_id: UUID | None = None, published_from: datetime | None = None,
                      published_to: datetime | None = None):
    if any(value and value.tzinfo is None for value in (published_from, published_to)):
        raise HTTPException(422, "timezone_required")
    current = select(VulnerabilityDocument.vulnerability_id).join(
        Document, Document.id == VulnerabilityDocument.document_id).where(
        Document.deleted_at.is_(None), Document.is_demo == is_demo)
    query = select(Vulnerability).where(Vulnerability.is_demo == is_demo, Vulnerability.id.in_(current))
    if ai_relevant is not None:
        query = query.where(Vulnerability.ai_relevant == ai_relevant)
    if keyword:
        query = query.where(or_(Vulnerability.title.icontains(keyword, autoescape=True),
                               Vulnerability.identifier.icontains(keyword, autoescape=True)))
    if severity:
        query = query.where(Vulnerability.severity == severity)
    if any(value is not None for value in (product, source_id, published_from, published_to)):
        matches = select(VulnerabilityDocument.vulnerability_id).join(
            Document, Document.id == VulnerabilityDocument.document_id).where(Document.deleted_at.is_(None))
        if product:
            matches = matches.where(or_(Document.content.icontains(product, autoescape=True),
                cast(Document.raw_payload, String).icontains(product, autoescape=True)))
        if source_id:
            matches = matches.where(Document.source_id == source_id)
        if published_from:
            matches = matches.where(Document.published_at >= published_from)
        if published_to:
            matches = matches.where(Document.published_at <= published_to)
        query = query.where(Vulnerability.id.in_(matches))
    return paginate(db, query.order_by(Vulnerability.created_at.desc()), VulnerabilityRead, page, page_size)


@router.get("/{vulnerability_id}")
def intelligence_detail(vulnerability_id: UUID, db: Database):
    vulnerability = require_record(db, Vulnerability, vulnerability_id)
    documents = db.scalars(select(Document).join(VulnerabilityDocument,
        Document.id == VulnerabilityDocument.document_id).where(
        VulnerabilityDocument.vulnerability_id == vulnerability_id, Document.deleted_at.is_(None))).all()
    evidence = db.scalars(select(Evidence).where(Evidence.document_id.in_([doc.id for doc in documents]))).all()
    aliases = db.scalars(select(VulnerabilityAlias.value).where(
        VulnerabilityAlias.vulnerability_id == vulnerability_id)).all()
    hashes = {doc.id: doc.content_hash for doc in documents}
    return {**VulnerabilityRead.model_validate(vulnerability).model_dump(mode="json"),
            "aliases": aliases,
            "documents": [DocumentRead.model_validate(doc) for doc in documents],
            "evidence": [{"document_id": row.document_id, "content_hash": row.content_hash,
                          "is_current": row.content_hash == hashes.get(row.document_id),
                          "field": row.field, "value": row.value, "state": row.state,
                          "excerpt": row.excerpt, "source_url": row.source_url,
                          "locator": row.locator, "method": row.method} for row in evidence]}
