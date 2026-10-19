"""Small structured read tools for the Agent. Remote refresh uses /sources/*/collect."""
from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from backend.api.dependencies import Database, PageNumber, PageSize, require_record
from backend.core.security import require_tool
from backend.models import DataSource, Document, DocumentRevision, Evidence, TaskRun, Vulnerability, VulnerabilityAlias, VulnerabilityDocument
from backend.services.jobs import utc_now
from backend.services.freshness import source_stale
from backend.schemas.contracts import Page
from backend.schemas.tools import ToolEnvelope, ToolCveSummary, ToolCveDetail, ToolDocumentPreview, ToolDocumentVersion

router = APIRouter(prefix="/tools", tags=["Agent 数据工具"], dependencies=[Depends(require_tool)])


def envelope(request, data, status="completed"):
    return {"schema_version": "1", "request_id": request.state.request_id, "status": status, "data": data}


def document_json(db, doc):
    source = db.get(DataSource, doc.source_id)
    return {"document_id": doc.id, "content_hash": doc.content_hash, "raw_id": doc.external_id,
        "title": doc.title, "source_url": doc.source_url, "source_name": source.name,
        "source_type": source.category, "adapter": source.adapter, "document_type": doc.document_type,
        "published_time": doc.published_at, "source_updated_at": doc.source_updated_at,
        "first_collected_at": doc.first_collected_at, "ai_relevant": doc.ai_relevant,
        "last_checked_at": doc.last_checked_at, "content_changed_at": doc.content_changed_at,
        "indexed_at": doc.indexed_at,
        "metadata": doc.metadata_json, "content_completeness": doc.content_completeness,
        "source_reported_severity": doc.raw_payload.get("severity"),
        "is_demo": doc.is_demo}


@router.get("/cves", response_model=ToolEnvelope[Page[ToolCveSummary]])
def search_cve(request: Request, db: Database, query: str = Query(min_length=1, max_length=200),
               is_demo: bool = False, page: PageNumber = 1, page_size: PageSize = 20):
    alias_query = select(VulnerabilityAlias.vulnerability_id).where(
        VulnerabilityAlias.value.icontains(query, autoescape=True), VulnerabilityAlias.is_demo == is_demo)
    current = select(VulnerabilityDocument.vulnerability_id).join(Document,
        Document.id == VulnerabilityDocument.document_id).where(Document.is_demo == is_demo,
        Document.deleted_at.is_(None))
    statement = select(Vulnerability).where(Vulnerability.is_demo == is_demo, or_(
        Vulnerability.id.in_(alias_query), Vulnerability.title.icontains(query, autoescape=True)),
        Vulnerability.id.in_(current))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(statement.order_by(Vulnerability.identifier).offset((page - 1) * page_size).limit(page_size)).all()
    return envelope(request, {"items": [{"id": v.id, "cve_id": v.identifier if v.identifier.startswith("CVE-") else None,
        "identifier": v.identifier, "title": v.title, "severity": v.severity, "ai_relevant": v.ai_relevant} for v in rows],
        "total": total, "page": page, "page_size": page_size}, "completed" if rows else "empty")


@router.get("/cves/{cve_id}", response_model=ToolEnvelope[ToolCveDetail])
def get_cve_detail(cve_id: str, request: Request, db: Database, is_demo: bool = False):
    import re
    cve_id = cve_id.upper()
    if not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve_id):
        raise HTTPException(422, "invalid_cve_id")
    vulnerability = db.scalar(select(Vulnerability).join(VulnerabilityAlias,
        VulnerabilityAlias.vulnerability_id == Vulnerability.id).where(
            VulnerabilityAlias.value == cve_id, Vulnerability.is_demo == is_demo))
    if vulnerability is None:
        raise HTTPException(404, "cve_not_found_in_local_store")
    docs = db.scalars(select(Document).join(VulnerabilityDocument,
        VulnerabilityDocument.document_id == Document.id).where(
            VulnerabilityDocument.vulnerability_id == vulnerability.id, Document.is_demo == is_demo,
            Document.deleted_at.is_(None))).all()
    if not docs:
        raise HTTPException(404, "cve_has_no_current_document")
    evidence = []
    for doc in docs:
        if doc.deleted_at is not None:
            continue
        rows = db.scalars(select(Evidence).where(Evidence.document_id == doc.id,
            Evidence.content_hash == doc.content_hash)).all()
        evidence.extend({"field": r.field, "state": r.state, "value": r.value, "method": r.method,
                         "document_id": doc.id, "content_hash": r.content_hash,
                         "source_url": r.source_url, "locator": r.locator, "excerpt": r.excerpt} for r in rows)
    return envelope(request, {"cve_id": cve_id, "identifier": vulnerability.identifier,
        "description": docs[0].content if docs else None, "severity": vulnerability.severity,
        "severity_resolution": "maximum_source_reported_over_history; see current evidence for conflicts",
        "published_time": min((d.published_at for d in docs if d.published_at), default=None),
        "cvss": [e for e in evidence if e["field"] == "cvss"],
        "aliases": list(db.scalars(select(VulnerabilityAlias.value).where(
            VulnerabilityAlias.vulnerability_id == vulnerability.id))),
        "references": [document_json(db, d) for d in docs if d.deleted_at is None], "evidence": evidence})


def search_documents(db, request, keyword, is_demo, category, adapter, page, page_size, doc_type=None):
    statement = select(Document).join(DataSource).where(Document.is_demo == is_demo, Document.deleted_at.is_(None))
    if category:
        statement = statement.where(DataSource.category == category)
    if adapter:
        statement = statement.where(DataSource.adapter == adapter)
    if doc_type:
        statement = statement.where(Document.document_type == doc_type)
    if keyword:
        statement = statement.where(or_(Document.title.icontains(keyword, autoescape=True),
            Document.content.icontains(keyword, autoescape=True), Document.external_id.icontains(keyword, autoescape=True)))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(statement.order_by(Document.first_collected_at.desc(), Document.id)
        .offset((page - 1) * page_size).limit(page_size)).all()
    checked = dict(db.execute(select(TaskRun.source_id, func.max(TaskRun.finished_at)).where(
        TaskRun.source_id.in_({d.source_id for d in rows}), TaskRun.kind == "collect", TaskRun.status == "succeeded")
        .group_by(TaskRun.source_id)).all())
    return envelope(request, {"items": [{**document_json(db, d), "excerpt": d.content[:2000],
        "cache": {"last_checked_at": checked.get(d.source_id), "version_updated_at": d.content_changed_at or d.first_collected_at,
                  "stale": source_stale(db.get(DataSource, d.source_id), utc_now(), checked.get(d.source_id))}} for d in rows],
        "total": total, "page": page, "page_size": page_size}, "completed" if rows else "empty")


@router.get("/nvd", response_model=ToolEnvelope[Page[ToolDocumentPreview]])
def search_nvd(request: Request, db: Database, query: str = Query(min_length=1, max_length=200),
               is_demo: bool = False, page: PageNumber = 1, page_size: PageSize = 20):
    return search_documents(db, request, query, is_demo, None, "nvd", page, page_size)


@router.get("/vendor-advisories", response_model=ToolEnvelope[Page[ToolDocumentPreview]])
def search_vendor_advisory(request: Request, db: Database, query: str = Query(min_length=1, max_length=200),
               is_demo: bool = False, page: PageNumber = 1, page_size: PageSize = 20):
    return search_documents(db, request, query, is_demo, "vendor", None, page, page_size)


@router.get("/documents", response_model=ToolEnvelope[Page[ToolDocumentPreview]])
def search_source_documents(request: Request, db: Database, query: str | None = Query(default=None, max_length=200),
               is_demo: bool = False, document_type: str | None = None,
               page: PageNumber = 1, page_size: PageSize = 20):
    return search_documents(db, request, query, is_demo, None, None, page, page_size, document_type)


@router.get("/documents/{document_id}", response_model=ToolEnvelope[ToolDocumentVersion])
def read_document(document_id: UUID, request: Request, db: Database, is_demo: bool = False,
                  content_hash: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$")):
    doc = require_record(db, Document, document_id)
    if doc.is_demo != is_demo or doc.deleted_at is not None:
        raise HTTPException(404, "document_scope_mismatch")
    digest = content_hash or doc.content_hash
    revision = db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == doc.id,
        DocumentRevision.content_hash == digest))
    if revision is None:
        raise HTTPException(404, "document_revision_missing")
    version = document_json(db, doc)
    for key, payload_key in {"title": "title", "source_url": "source_url", "document_type": "document_type",
                            "published_time": "published_at", "source_updated_at": "source_updated_at",
                            "metadata": "metadata_json", "content_completeness": "content_completeness",
                            "source_reported_severity": "severity"}.items():
        version[key] = revision.payload.get(payload_key)
    if digest != doc.content_hash:
        # Current-version observations cannot be relabelled as historical proof.
        for name in ("last_checked_at", "content_changed_at", "indexed_at"):
            version[name] = None
    return envelope(request, {**version, "content_hash": digest,
        "is_current": digest == doc.content_hash, "revision": revision.payload,
        "content": revision.payload.get("content"), "version_created_at": revision.created_at})
