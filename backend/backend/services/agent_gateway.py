"""Explicit external Agent protocol. No mock answers or implicit provider fallback."""
import json
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from backend.models import Document, DocumentRevision, DataSource, VulnerabilityAlias, VulnerabilityDocument
from backend.schemas.agent import AgentAnswer
from backend.schemas.contracts import Citation


def endpoint(settings):
    url = settings.agent_service_url
    if not url:
        raise HTTPException(503, "agent_service_not_configured")
    parsed = urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname
            or parsed.scheme not in {"http", "https"}
            or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"})):
        raise HTTPException(503, "agent_service_url_invalid")
    return url.rstrip("/")


def call_agent(settings, payload):
    headers = {"X-Request-ID": payload["request_id"]}
    if settings.agent_service_token:
        headers["Authorization"] = "Bearer " + settings.agent_service_token.get_secret_value()
    try:
        with httpx.Client(timeout=settings.agent_timeout_seconds, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", endpoint(settings) + "/v1/query", headers=headers, json=payload) as response:
                if response.status_code == 429:
                    raise HTTPException(503, "agent_rate_limited")
                if response.status_code == 503:
                    # Forward only public failure codes owned by this protocol.
                    # Never expose arbitrary provider or upstream response text.
                    raw_error = b""
                    for part in response.iter_bytes(chunk_size=1024):
                        raw_error += part
                        if len(raw_error) > 4096:
                            break
                    try:
                        error = json.loads(raw_error) if len(raw_error) <= 4096 else None
                    except (ValueError, UnicodeDecodeError):
                        error = None
                    detail = error.get("detail") if isinstance(error, dict) else None
                    if isinstance(detail, str) and detail in {
                            "model_relevance_failed", "model_verification_failed", "model_generation_failed",
                            "retrieval_failed", "backend_index_unavailable", "backend_index_embedding_signature_mismatch"}:
                        raise HTTPException(503, detail)
                response.raise_for_status()
                parts, size = [], 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > 500000:
                        raise HTTPException(502, "agent_response_too_large")
                    parts.append(part)
                answer = AgentAnswer.model_validate_json(b"".join(parts))
                for name, allowed in (("X-RAG-Answer-Mode", {"live", "extractive", "mock"}),
                                      ("X-RAG-Embedding-Mode", {"live", "mock"})):
                    value = response.headers.get(name)
                    if value in allowed:
                        answer._rag_headers[name] = value
                count = response.headers.get("X-RAG-LLM-Calls", "")
                if count in {"0", "1", "2", "3"}:
                    answer._rag_headers["X-RAG-LLM-Calls"] = count
                return answer
    except httpx.TimeoutException:
        raise HTTPException(504, "agent_timeout") from None
    except httpx.HTTPError:
        raise HTTPException(502, "agent_unavailable") from None
    except ValueError:
        raise HTTPException(502, "agent_invalid_response") from None


def check_health(settings):
    headers = {"Authorization": "Bearer " + settings.agent_service_token.get_secret_value()} if settings.agent_service_token else {}
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", endpoint(settings) + "/v1/health", headers=headers) as response:
                response.raise_for_status()
                raw = b""
                for part in response.iter_bytes():
                    raw += part
                    if len(raw) > 16000:
                        raise ValueError("health response too large")
                result = json.loads(raw)
        if not isinstance(result, dict) or result.get("schema_version") != "1" or result.get("status") != "ok":
            raise ValueError("unsupported health/version")
        return {"schema_version": "1", "status": "ok", "configured": True}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "agent_health_or_version_unavailable") from None


def safe_trace(answer, settings):
    import re
    secrets = []
    for name in ("admin_api_key", "deepseek_api_key", "embedding_api_key", "postgres_password", "redis_password",
                 "ai_service_token", "agent_service_token", "agent_tool_token", "agent_write_token"):
        value = getattr(settings, name, None)
        if value and value.get_secret_value():
            secrets.append(value.get_secret_value())
    records = []
    for item in answer.tool_trace:
        row = item.model_dump(mode="json")
        for key in ("input_summary", "error_code"):
            value = row[key]
            if value:
                for secret in secrets:
                    value = value.replace(secret, "[redacted]")
                row[key] = re.sub(r"(?i)(bearer\s+|(?:api[_-]?key|token|password)\s*[:=]\s*)\S+", r"\1[redacted]", value)
        records.append(row)
    return records


def validate_answer(db, answer, is_demo, *, query=None):
    from backend.api.v1.ai import _verify_citation
    from backend.services.agent_conversation import conversation_kind, safe_conversation_answer
    if answer.response_kind == "conversation":
        if (not query or not conversation_kind(query) or answer.status != "completed"
                or answer.retrieval_mode != "conversation" or answer.sources or answer.evidence
                or answer.cve_ids or answer.risk_level != "UNKNOWN" or answer.tool_calls
                or answer.tool_trace or answer.coverage.answered_topics or answer.coverage.missing_topics
                or not safe_conversation_answer(answer.answer)):
            raise HTTPException(502, "agent_invalid_conversation_response")
        return
    if answer.retrieval_mode == "conversation":
        raise HTTPException(502, "agent_response_kind_mismatch")
    if answer.status == "insufficient_evidence" and answer.coverage.answered_topics:
        raise HTTPException(502, "agent_invalid_answer_coverage")
    if answer.status in {"completed", "partial"} and (not answer.sources or not answer.evidence):
        raise HTTPException(502, "agent_missing_evidence")
    citations = {}
    for source in answer.sources:
        document = db.scalar(select(Document).where(Document.id == source.document_id)
            .with_for_update().execution_options(populate_existing=True))
        revision = db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == source.document_id,
            DocumentRevision.content_hash == source.content_hash))
        if (document is None or document.deleted_at is not None or revision is None or document.is_demo != is_demo
                or document.content_hash != source.content_hash or str(source.url) != revision.payload.get("source_url")
                or source.title != revision.payload.get("title")):
            raise HTTPException(502, "agent_source_scope_or_version_invalid")
        actual = db.get(DataSource, document.source_id)
        if actual is None or source.source_type != actual.category or source.source_name != actual.name:
            raise HTTPException(502, "agent_source_metadata_invalid")
        citations[(source.document_id, source.content_hash)] = source
    for evidence in answer.evidence:
        source = citations.get((evidence.document_id, evidence.content_hash))
        if source is None or str(source.url) != str(evidence.source):
            raise HTTPException(502, "agent_evidence_source_mismatch")
        try:
            _verify_citation(db, Citation(document_id=evidence.document_id, content_hash=evidence.content_hash,
                                         source_url=evidence.source, excerpt=evidence.content))
            from backend.services.traceability import locate_quote
            revision = db.scalar(select(DocumentRevision).where(
                DocumentRevision.document_id == evidence.document_id,
                DocumentRevision.content_hash == evidence.content_hash))
            locate_quote(revision.payload, evidence.content, evidence.locator)
        except HTTPException:
            raise HTTPException(502, "agent_citation_invalid") from None
    for cve in answer.cve_ids:
        linked = db.scalar(select(VulnerabilityAlias.id).join(VulnerabilityDocument,
            VulnerabilityDocument.vulnerability_id == VulnerabilityAlias.vulnerability_id).where(
                VulnerabilityAlias.value == cve, VulnerabilityAlias.is_demo == is_demo,
                VulnerabilityDocument.document_id.in_([s.document_id for s in answer.sources])).limit(1))
        if linked is None:
            raise HTTPException(502, "agent_cve_not_in_sources")


def verify_external_index(settings, event, is_demo, index_id):
    if not isinstance(index_id, str) or not 1 <= len(index_id) <= 200:
        raise HTTPException(422, "index_id_required")
    headers = {}
    if settings.agent_service_token:
        headers["Authorization"] = "Bearer " + settings.agent_service_token.get_secret_value()
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", endpoint(settings) + "/v1/indexes/verify", headers=headers,
                json={"schema_version": "1", "document_id": str(event.document_id), "content_hash": event.content_hash,
                      "index_id": index_id, "is_demo": is_demo, "operation": event.operation}) as response:
                response.raise_for_status()
                parts, size = [], 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > 16000:
                        raise ValueError("index proof too large")
                    parts.append(part)
                result = json.loads(b"".join(parts))
        if not isinstance(result, dict):
            raise ValueError("invalid proof format")
        if (result.get("schema_version") != "1" or result.get("queryable") is not (event.operation != "delete")
                or result.get("document_id") != str(event.document_id) or result.get("content_hash") != event.content_hash
                or result.get("is_demo") is not is_demo or result.get("index_id") != index_id):
            raise ValueError("index proof mismatch")
        if event.operation == "delete" and result.get("removed") is not True:
            raise ValueError("delete not confirmed")
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(422, "external_index_not_queryable") from None
