"""Original-version proof, separate from unmeasured semantic claim support."""
import json
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import (AssociationEvidence, Document, DocumentRevision,
    KnowledgeEvent, VulnerabilityAlias, VulnerabilityDocument)


def locate_quote(payload, excerpt, locator):
    """Offsets are Unicode code points in content or json.dumps(ensure_ascii=False)."""
    texts = {"content": payload.get("content", ""),
             "revision_json": json.dumps(payload, ensure_ascii=False)}
    match = re.fullmatch(r"(content|revision_json):chars:(\d+):(\d+)", locator)
    if match:
        kind, start, end = match[1], int(match[2]), int(match[3])
        value = texts[kind]
        if end <= len(value) and start < end and value[start:end] == excerpt:
            return {"representation": kind, "start": start, "end": end,
                    "offset_unit": "unicode_code_points", "locator_verified": True}
        raise HTTPException(422, "agent_citation_locator_invalid")
    if locator in texts:  # Field-only locator supported by the original v1 contract.
        start = texts[locator].find(excerpt)
        if start >= 0:
            return {"representation": locator, "start": start, "end": start + len(excerpt),
                    "offset_unit": "unicode_code_points", "locator_verified": True}
    raise HTTPException(422, "agent_citation_locator_invalid")


def association_candidates(payload, source_url):
    """Use the SAME conservative rules as ingestion; never merge article co-mentions."""
    from backend.schemas.contracts import RawRecord
    from backend.services.pipeline import _aliases
    record = RawRecord.model_validate(payload)
    aliases = _aliases(record, type("DocumentURL", (), {"source_url": source_url})())
    groups = [aliases] if record.document_type == "vulnerability" else [[value] for value in aliases]
    items = []
    for group in groups:
        for identifier in group:
            if identifier.startswith("LOCAL-"):
                body = " ".join(record.content.casefold().split())
                exact = bool(any(p.strip() for p in record.products) and len(body) >= 80)
                locator, excerpt = ("/content", record.content) if exact else ("/source_url", source_url)
                method = "exact_product_body_fallback" if exact else "exact_url_fallback"
            else:
                method = "source_declared_alias" if len(group) > 1 else "explicit_identifier"
                locator, excerpt = "/vulnerability_id", record.vulnerability_id
                if not excerpt or excerpt.upper() != identifier:
                    position = next(i for i, v in enumerate(record.aliases) if v.upper() == identifier)
                    locator, excerpt = f"/aliases/{position}", record.aliases[position]
            items.append({"identifier": identifier, "method": method, "locator": locator,
                          "excerpt": excerpt, "group_identifiers": group})
    return items


def save_associations(db, document, payload):
    existing = set(db.scalars(select(AssociationEvidence.identifier).where(
        AssociationEvidence.document_id == document.id, AssociationEvidence.content_hash == document.content_hash)))
    for item in association_candidates(payload, document.source_url):
        if item["identifier"] not in existing:
            db.add(AssociationEvidence(document_id=document.id, content_hash=document.content_hash, **item))


def version_associations(db, document, revision):
    saved = list(db.scalars(select(AssociationEvidence).where(
        AssociationEvidence.document_id == document.id, AssociationEvidence.content_hash == revision.content_hash)))
    if saved:
        items = [{name: getattr(row, name) for name in
                  ("identifier", "method", "locator", "excerpt", "group_identifiers")} |
                 {"origin": "recorded_at_ingestion", "recorded_at": row.created_at} for row in saved]
    else:
        items = [item | {"origin": "reconstructed_from_revision", "recorded_at": None} for item in
                 association_candidates(revision.payload, revision.payload.get("source_url", document.source_url))]
    aliases = {row.value: row.vulnerability_id for row in db.scalars(select(VulnerabilityAlias).where(
        VulnerabilityAlias.value.in_([item["identifier"] for item in items]),
        VulnerabilityAlias.is_demo == document.is_demo))}
    links = set(db.scalars(select(VulnerabilityDocument.vulnerability_id).where(
        VulnerabilityDocument.document_id == document.id)))
    active = document.deleted_at is None and revision.content_hash == document.content_hash
    for item in items:
        target = aliases.get(item["identifier"])
        item.update({"vulnerability_id": target,
                     "active_link": active and target is not None and target in links})
    return items


def original_version(db, document, digest):
    row = db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == document.id,
        DocumentRevision.content_hash == digest))
    if row is None:
        raise HTTPException(404, "document_revision_missing")
    return row


def answer_evidence(db, invocation):
    payload = invocation.response_payload or {}
    rows = []
    for number, quote in enumerate(payload.get("evidence", []), 1):
        document = db.get(Document, UUID(quote["document_id"]))
        revision = original_version(db, document, quote["content_hash"])
        try:
            position = locate_quote(revision.payload, quote["content"], quote["locator"])
            valid = quote["source"] == revision.payload.get("source_url")
        except HTTPException:
            position, valid = None, False
        current = document.deleted_at is None and revision.content_hash == document.content_hash
        events = db.scalars(select(KnowledgeEvent).where(KnowledgeEvent.document_id == document.id,
            KnowledgeEvent.content_hash == revision.content_hash).order_by(KnowledgeEvent.created_at)).all()
        rows.append({"number": number, "document_id": document.id, "content_hash": revision.content_hash,
            "title": revision.payload.get("title"), "source_url": revision.payload.get("source_url"),
            "excerpt": quote["content"], "locator": quote["locator"], "position": position,
            "quote_verified": valid, "is_current": current, "deleted_at": document.deleted_at,
            "version_url": f"/api/v1/documents/{document.id}/versions/{revision.content_hash}",
            "associations": version_associations(db, document, revision),
            "index_events_observed_now": [{"event_id": e.id, "operation": e.operation, "status": e.status} for e in events]})
    return {"schema_version": "1", "invocation_id": invocation.id, "session_id": invocation.conversation_id,
        "request_id": invocation.request_id, "status": invocation.status,
        "generation_mode": payload.get("generation_mode") or "unknown",
        "llm_http_calls": payload.get("llm_http_calls"), "answer": payload.get("answer"),
        "retrieval_mode": payload.get("retrieval_mode"), "cve_ids": payload.get("cve_ids", []),
        "evidence": rows, "all_quotes_verified": bool(rows) and all(row["quote_verified"] for row in rows),
        "all_versions_current": bool(rows) and all(row["is_current"] for row in rows),
        "claim_support_status": "not_independently_reviewed",
        "limits": "Quote integrity and association provenance are checked; semantic support of every claim requires review. Index status is observed now, not reconstructed as of the answer."}
