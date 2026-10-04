"""Authenticated backend V1 adapter over an explicitly mapped RAG index."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import ValidationError

from ..schemas import Message, QueryRequest, QueryResponse
from .contracts import (
    AgentAnswer, AgentEvidence, AgentSource, BackendQuery, IndexProof,
    IndexVerification, ToolTrace,
)

_CVE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)
_RISK_ORDER = ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")


def _locator(original: str, revision_text: str, excerpt: str) -> str:
    """Locate the validated excerpt in the backend's original representation."""
    start = original.find(excerpt)
    if start >= 0:
        return f"content:chars:{start}:{start + len(excerpt)}"
    start = revision_text.find(excerpt)
    if start < 0:
        raise ValueError("excerpt_not_in_backend_revision")
    return f"revision_json:chars:{start}:{start + len(excerpt)}"


def _source(metadata: dict[str, Any]) -> AgentSource:
    return AgentSource(
        document_id=metadata["document_id"], content_hash=metadata["content_hash"],
        title=metadata["title"], url=metadata["source_url"],
        source_type=metadata["source_type"], source_name=metadata["source_name"],
        published_time=metadata.get("published_time"),
    )


def _backend_trace(summary, mappings):
    by_rag_id = {rag_id: metadata for (rag_id, _), metadata in mappings.items()}
    traces, calls, warnings = [], [], []
    for event in summary.get("tools", [])[:30]:
        name = event.get("tool")
        if isinstance(name, str):
            calls.append(name)
        # Missing instrumentation is disclosed, never filled with fabricated zero durations.
        if event.get("status") not in {"completed", "failed", "empty"} or "duration_ms" not in event:
            warnings.append("tool_trace_incomplete")
            continue
        documents = list(dict.fromkeys(
            str(by_rag_id[doc_id]["document_id"])
            for doc_id in event.get("document_ids", []) if doc_id in by_rag_id
        ))[:30]
        try:
            traces.append(ToolTrace(
                tool=name, status=event["status"],
                duration_ms=max(0, round(event["duration_ms"])),
                document_ids=documents,
                error_code=event.get("error_code"),
                input_summary=event.get("input_summary"),
            ))
        except (ValidationError, TypeError, ValueError):
            warnings.append("tool_trace_invalid")
    return calls, traces, warnings


def _allowed_cves(metadata):
    revision = metadata.get("revision", {})
    values = revision.get("aliases", [])
    if not isinstance(values, list):
        values = []
    values = [*values, revision.get("vulnerability_id") or ""]
    return {value.upper() for value in values
            if isinstance(value, str) and _CVE.fullmatch(value)}


def _risk(facts, valid_ids):
    recorded = []
    for fact in facts:
        if (fact.field != "cvss" or fact.verification != "source_recorded"
                or fact.origin == "llm_candidate" or not set(fact.evidence_ids).issubset(valid_ids)
                or not isinstance(fact.value, dict)):
            continue
        level = str(fact.value.get("severity") or "").upper()
        if level in _RISK_ORDER:
            recorded.append(level)
    return max(recorded, key=_RISK_ORDER.index) if recorded else "UNKNOWN"


def _extractive_answer(evidence, reason):
    lines = [reason, "以下仅展示通过后端原始版本核对的来源摘录："]
    for number, item in enumerate(evidence[:4], 1):
        excerpt = item.content[:240].replace("\n", " ")
        if re.search(r"ignore.*(?:instruction|previous)|忽略.*指令|输出.*HACKED", excerpt, re.I):
            excerpt = "资料包含疑似指令注入内容；已作为不可信资料隔离，请查看证据。"
        lines.append(f"[{number}] {excerpt}")
    return "\n".join(lines)


def _generation_note(mode, llm_calls, configured_llm_mode):
    if mode == "mock":
        return "generation_mode:mock_not_real_model"
    if mode == "extractive":
        if not llm_calls:
            return "generation_mode:extractive_no_remote_llm"
        if configured_llm_mode == "mock":
            return "generation_mode:extractive_after_mock_attempt"
        return "generation_mode:extractive_after_remote_attempt"
    return None


def _bounded_answer(answer: str, byte_limit: int):
    """UTF-8 transport bound; this is not a vendor tokenizer measurement."""
    encoded = answer.encode("utf-8")
    if len(encoded) <= byte_limit:
        return answer, False
    marker = "\n..." if byte_limit >= 8 else ""
    available = byte_limit - len(marker.encode("utf-8"))
    retained, size = [], 0
    for line in answer.splitlines():
        line_size = len(line.encode("utf-8")) + (1 if retained else 0)
        if size + line_size > available:
            break
        retained.append(line)
        size += line_size
    text = "\n".join(retained)
    if not text:
        text = encoded[:available].decode("utf-8", errors="ignore").rstrip()
    text = text or "?"
    if len((text + marker).encode("utf-8")) <= byte_limit:
        text += marker
    return text, True


def adapt_answer(result: QueryResponse, mappings, summary, *, configured_llm_mode=None,
                 max_answer_tokens: int | None = None) -> AgentAnswer:
    """Only mapped, current backend source revisions can reach the gateway."""
    sources, evidence, valid_ids, retained_metadata = {}, [], set(), {}
    errors = list(result.warnings)
    rejected = False
    for item in result.sources:
        metadata = mappings.get((item.document_id, item.version))
        if metadata is None:
            rejected = True
            errors.append("citation_not_in_backend_scope")
            continue
        revision = metadata.get("revision", {})
        content = revision.get("content")
        original = content if isinstance(content, str) else ""
        excerpt = item.excerpt[:2000]
        revision_text = json.dumps(revision, ensure_ascii=False)
        if (not excerpt or item.url != metadata.get("source_url")
                or (excerpt not in original and excerpt not in revision_text)):
            rejected = True
            errors.append("citation_excerpt_not_in_backend_revision")
            continue
        try:
            source = _source(metadata)
            converted = AgentEvidence(
                document_id=source.document_id, content_hash=source.content_hash,
                content=excerpt, source=source.url, locator=_locator(original, revision_text, excerpt),
                score=None, score_type="not_scored",
            )
        except (KeyError, TypeError, ValidationError):
            rejected = True
            errors.append("backend_source_metadata_invalid")
            continue
        source_key = (str(source.document_id), source.content_hash)
        if (source_key not in sources and len(sources) >= 30) or len(evidence) >= 50:
            rejected = True
            errors.append("transport_evidence_limit")
            continue
        sources[source_key] = source
        retained_metadata[source_key] = metadata
        evidence.append(converted)
        valid_ids.add(item.evidence_id)

    calls, traces, trace_errors = _backend_trace(summary, mappings)
    errors.extend(trace_errors)
    generation_note = _generation_note(result.mode, result.metrics.llm_calls, configured_llm_mode)
    if generation_note:
        errors.insert(0, generation_note)
    if result.metrics.embedding_mode == "mock":
        errors.append("embedding_mode:mock_not_real_semantic_retrieval")
    if result.missing_fields:
        # Internal document IDs are not part of the backend's public identity contract.
        errors.append("source_fields_missing")
    if result.conflicts:
        errors.append("source_assertions_conflict")

    if not evidence:
        status = "insufficient_evidence"
        answer = ("后端当前文档范围内没有可核验的证据，无法给出有来源支持的回答。"
                  if rejected else result.answer)
        if rejected:
            errors.insert(0, "answer_rebuilt_after_citation_validation")
            errors.insert(0, _generation_note("extractive", result.metrics.llm_calls, configured_llm_mode))
        cves, risk = [], "UNKNOWN"
    else:
        status = "completed" if result.status == "ok" else "partial"
        claims_invalid = any(not set(claim.evidence_ids).issubset(valid_ids) for claim in result.claims)
        facts_invalid = any(not set(fact.evidence_ids).issubset(valid_ids) for fact in result.facts)
        if rejected or claims_invalid or facts_invalid:
            status = "partial"
            errors.insert(0, "answer_rebuilt_after_citation_validation")
            rebuilt_note = _generation_note("extractive", result.metrics.llm_calls, configured_llm_mode)
            if rebuilt_note not in errors:
                errors.insert(0, rebuilt_note)
            answer = _extractive_answer(evidence, "部分引用未通过原始版本核对，相关结论已撤下。")
        else:
            references = {item.evidence_id: number for number, item in enumerate(result.sources, 1)}

            def replace_reference(match):
                identifiers = match.group(1).split(",")
                if all(identifier in references for identifier in identifiers):
                    return "[" + ",".join(str(references[identifier]) for identifier in identifiers) + "]"
                return match.group(0)

            answer = re.sub(r"\[([^\]\n]+)\]", replace_reference, result.answer)
        risk = _risk(result.facts, valid_ids)
    if len(answer) > 12000:
        answer = answer[:11940] + "\n回答超过传输长度限制，已截断；请缩小查询范围。"
        errors.append("transport_answer_truncated")
        if evidence:
            status = "partial"
    if max_answer_tokens is not None:
        answer, truncated = _bounded_answer(answer, max_answer_tokens)
        if truncated:
            errors.insert(0, "answer_budget_truncated")
            if evidence:
                status = "partial"
    allowed = set().union(*(_allowed_cves(metadata) for metadata in retained_metadata.values()))
    cves = sorted({value.upper() for value in _CVE.findall(answer)} & allowed)[:100]
    mode = "structured" if "exact_lookup" in calls else "hybrid" if "hybrid_search" in calls else "keyword"
    return AgentAnswer(
        status=status, answer=answer or "没有满足要求的来源证据。", sources=list(sources.values()),
        cve_ids=cves, risk_level=risk, evidence=evidence, tool_calls=calls,
        tool_trace=traces, partial_errors=list(dict.fromkeys(errors))[:20], retrieval_mode=mode,
    )


def create_app(service, index, service_token: str | None) -> FastAPI:
    """Inject service/index for local validation; all V1 routes require a token."""
    app = FastAPI(title="ICT Agent backend V1 adapter", version="1")
    app.state.service, app.state.index = service, index
    expected = (service_token or "").encode("utf-8")

    @app.middleware("http")
    async def response_metadata(request: Request, call_next):
        response = await call_next(request)
        request_id = request.headers.get("X-Request-ID", "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
            request_id = str(uuid4())
        response.headers.setdefault("X-Request-ID", request_id)
        response.headers.setdefault("X-RAG-Answer-Mode", service.settings.llm_mode)
        response.headers.setdefault("X-RAG-Embedding-Mode", service.embedding.mode)
        return response

    async def authenticate(authorization: str | None = Header(default=None)):
        if not expected:
            raise HTTPException(503, "agent_service_token_not_configured")
        scheme, _, token = (authorization or "").partition(" ")
        if (scheme.casefold() != "bearer" or not token
                or not secrets.compare_digest(token.encode("utf-8"), expected)):
            raise HTTPException(401, "invalid_agent_service_token", headers={"WWW-Authenticate": "Bearer"})

    authorized = [Depends(authenticate)]

    @app.get("/v1/health", dependencies=authorized)
    async def health(response: Response):
        response.headers["X-RAG-Answer-Mode"] = service.settings.llm_mode
        response.headers["X-RAG-Embedding-Mode"] = service.embedding.mode
        try:
            snapshot = await asyncio.to_thread(service.store.snapshot)
            if snapshot.signature and snapshot.signature != service.embedding.signature():
                raise HTTPException(503, "backend_index_embedding_signature_mismatch")
            scopes = await asyncio.to_thread(lambda: (index.mappings(False), index.mappings(True)))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "backend_index_unavailable") from None
        return {"schema_version": "1", "status": "ok", "llm_mode": service.settings.llm_mode,
                "embedding_provider": service.settings.embedding_provider,
                "embedding_mode": service.embedding.mode,
                "backend_documents": {"real": len(scopes[0]), "demo": len(scopes[1])}}

    @app.post("/v1/query", response_model=AgentAnswer, dependencies=authorized)
    async def query(body: BackendQuery, response: Response):
        response.headers["X-Request-ID"] = body.request_id
        response.headers["X-RAG-Answer-Mode"] = service.settings.llm_mode
        response.headers["X-RAG-Embedding-Mode"] = service.embedding.mode
        try:
            mappings = await asyncio.to_thread(index.mappings, body.is_demo)
        except Exception:
            raise HTTPException(503, "backend_index_unavailable") from None
        request = QueryRequest(question=body.query, history=[
            Message(role=item.role, content=item.content) for item in body.history[-6:]
        ])
        try:
            result = await service.query(
                request, document_ids={rag_id for rag_id, _ in mappings},
                document_versions={rag_id: version for rag_id, version in mappings},
                max_tool_calls=body.limits.max_tool_calls,
                max_answer_tokens=body.limits.max_answer_tokens,
                timeout=min(service.settings.request_timeout, 25.0),
            )
        except TimeoutError:
            raise HTTPException(504, "agent_query_timeout") from None
        result_headers = {"X-Request-ID": body.request_id, "X-RAG-Answer-Mode": result.mode,
                          "X-RAG-Embedding-Mode": result.metrics.embedding_mode,
                          "X-RAG-LLM-Calls": str(result.metrics.llm_calls)}
        if result.status == "error":
            if "TimeoutError" in result.warnings:
                raise HTTPException(504, "agent_query_timeout", headers=result_headers)
            if "BudgetExceeded" in result.warnings:
                raise HTTPException(429, "agent_call_budget_exceeded", headers=result_headers)
            raise HTTPException(503, "agent_query_failed", headers=result_headers)
        response.headers.update(result_headers)
        try:
            summary = service.get_run_summary(result.trace_id)
        except KeyError:
            summary = {"tools": []}
            result.warnings.append("tool_trace_unavailable")
        answer = adapt_answer(result, mappings, summary, configured_llm_mode=service.settings.llm_mode,
                              max_answer_tokens=body.limits.max_answer_tokens)
        if "answer_rebuilt_after_citation_validation" in answer.partial_errors:
            response.headers["X-RAG-Answer-Mode"] = "extractive"
        return answer

    @app.post("/v1/indexes/verify", response_model=IndexProof, dependencies=authorized)
    async def verify(body: IndexVerification):
        try:
            proof = await asyncio.to_thread(index.verify, body.model_dump(mode="json"))
            return IndexProof.model_validate(proof)
        except KeyError:
            raise HTTPException(404, "backend_index_not_found") from None
        except (ValueError, ValidationError):
            raise HTTPException(422, "backend_index_verification_failed") from None

    return app
