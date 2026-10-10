"""Authenticated Agent gateway with backend-owned sessions, budgets and audit."""
import hashlib
from datetime import timedelta
from time import perf_counter
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import case, func, select
from backend.api.dependencies import Database, PageNumber, PageSize, require_record
from backend.core.config import get_settings
from backend.core.security import User, require_admin
from backend.models import AgentInvocation, Conversation, ConversationMessage
from backend.schemas.agent import AgentQuery, AgentQueryResponse, ToolTrace
from backend.schemas.traceability import AgentInvocationEvidence
from backend.services import agent_gateway
from backend.services.jobs import utc_now

router = APIRouter(prefix="/agent", tags=["Agent 联调与用户会话"])


def own_session(db, session_id, owner):
    row = require_record(db, Conversation, session_id)
    if row.owner_id != owner:
        raise HTTPException(403, "session_access_denied")
    return row


@router.post("/query", response_model=AgentQueryResponse)
def query_agent(body: AgentQuery, request: Request, db: Database, owner: User,
                idempotency_key: str | None = Header(default=None, max_length=128), response: Response = None):
    settings = get_settings()
    agent_gateway.endpoint(settings)
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    # Serialize budget and idempotency allocation across API workers, not the network call.
    from sqlalchemy import text
    lock_key = int.from_bytes(hashlib.sha256(owner.encode()).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    if idempotency_key:
        previous = db.scalar(select(AgentInvocation).where(AgentInvocation.owner_id == owner,
            AgentInvocation.idempotency_key == idempotency_key))
        if previous:
            if previous.request_hash != digest:
                raise HTTPException(409, "idempotency_key_conflict")
            if previous.response_payload:
                if response is not None:
                    saved = previous.response_payload
                    if saved.get("generation_mode") in {"live", "extractive", "mock"}:
                        response.headers["X-RAG-Answer-Mode"] = saved["generation_mode"]
                    if type(saved.get("llm_http_calls")) is int and 0 <= saved["llm_http_calls"] <= 3:
                        response.headers["X-RAG-LLM-Calls"] = str(saved["llm_http_calls"])
                return AgentQueryResponse.model_validate(previous.response_payload)
            raise HTTPException(409, "agent_request_running" if previous.status == "running" else "agent_request_failed_use_new_key")
    now = utc_now()
    for period, limit in ((timedelta(minutes=1), settings.user_queries_per_minute),
                          (timedelta(days=1), settings.user_queries_per_day)):
        count = db.scalar(select(func.count()).select_from(AgentInvocation).where(
            AgentInvocation.owner_id == owner, AgentInvocation.created_at >= now - period))
        if count >= limit:
            raise HTTPException(429, "user_query_budget_exceeded")
    if body.session_id:
        conversation = own_session(db, body.session_id, owner)
        db.execute(select(Conversation.id).where(Conversation.id == conversation.id).with_for_update())
        if conversation.is_demo != body.is_demo or conversation.provider != "external":
            raise HTTPException(409, "session_provider_or_demo_mismatch")
    else:
        conversation = Conversation(owner_id=owner, provider="external", is_demo=body.is_demo, turn_count=0)
        db.add(conversation)
        db.flush()
    if conversation.turn_count >= 100:
        raise HTTPException(409, "conversation_turn_limit_reached")
    active = db.scalar(select(AgentInvocation).where(AgentInvocation.conversation_id == conversation.id,
        AgentInvocation.status == "running").with_for_update())
    if active:
        if active.created_at >= now - timedelta(seconds=settings.agent_timeout_seconds + 60):
            raise HTTPException(409, "session_query_running")
        active.status, active.error_code, active.finished_at = "failed", "agent_lease_expired", now
        db.flush()
    history = list(reversed(db.scalars(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id).order_by(
            ConversationMessage.created_at.desc(), case((ConversationMessage.role == "assistant", 1), else_=0).desc(), ConversationMessage.id.desc()).limit(8)).all()))
    invocation = AgentInvocation(owner_id=owner, conversation_id=conversation.id,
        request_id=request.state.request_id, idempotency_key=idempotency_key,
        request_hash=digest, status="running", tool_trace=[])
    db.add(invocation)
    db.flush()
    payload = {"schema_version": "1", "query": body.query, "session_id": str(conversation.id),
        "is_demo": body.is_demo, "request_id": request.state.request_id, "invocation_id": str(invocation.id),
        "history": [{"role": row.role, "content": row.content[:2000], "citations": row.citations} for row in history],
        "limits": {"max_tool_calls": 10, "max_answer_tokens": 1200}, "state_owner": "backend"}
    invocation_id, conversation_id = invocation.id, conversation.id
    db.commit()
    started = perf_counter()
    try:
        answer = agent_gateway.call_agent(settings, payload)
        if response is not None:
            response.headers.update(answer._rag_headers)
        agent_gateway.validate_answer(db, answer, body.is_demo, query=body.query)
        call_count = answer._rag_headers.get("X-RAG-LLM-Calls")
        response = AgentQueryResponse(**answer.model_dump(), session_id=conversation_id,
            invocation_id=invocation_id, request_id=request.state.request_id,
            generation_mode=answer._rag_headers.get("X-RAG-Answer-Mode"),
            llm_http_calls=int(call_count) if call_count in {"0", "1", "2", "3"} else None)
        # Short final transaction: metadata may have changed while Agent was working.
        conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
        invocation = db.scalar(select(AgentInvocation).where(AgentInvocation.id == invocation_id).with_for_update())
        if invocation.status != "running":
            raise HTTPException(409, "agent_invocation_expired")
        db.add(ConversationMessage(conversation_id=conversation_id, role="user", content=body.query, citations=[]))
        db.add(ConversationMessage(conversation_id=conversation_id, role="assistant", content=answer.answer,
            citations=[item.model_dump(mode="json") for item in answer.evidence], retrieval_mode=answer.retrieval_mode))
        conversation.turn_count += 1
        conversation.updated_at = utc_now()
        invocation.status, invocation.response_payload = answer.status, response.model_dump(mode="json")
        invocation.tool_trace = agent_gateway.safe_trace(answer, settings)
        response.tool_trace = [ToolTrace.model_validate(item) for item in invocation.tool_trace]
        invocation.response_payload = response.model_dump(mode="json")
        invocation.duration_ms, invocation.finished_at = int((perf_counter() - started) * 1000), utc_now()
        db.commit()
        return response
    except HTTPException as error:
        db.rollback()
        invocation = db.get(AgentInvocation, invocation_id)
        invocation.status, invocation.error_code = "failed", str(error.detail)
        invocation.duration_ms, invocation.finished_at = int((perf_counter() - started) * 1000), utc_now()
        db.commit()
        raise


@router.get("/sessions")
def sessions(db: Database, owner: User, page: PageNumber = 1, page_size: PageSize = 20):
    query = select(Conversation).where(Conversation.owner_id == owner)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Conversation.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"session_id": row.id, "is_demo": row.is_demo, "turn_count": row.turn_count,
                        "provider": row.provider, "updated_at": row.updated_at} for row in rows],
            "total": total, "page": page, "page_size": page_size}


@router.get("/sessions/{session_id}")
def session_detail(session_id: UUID, db: Database, owner: User):
    row = own_session(db, session_id, owner)
    messages = db.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == row.id)
        .order_by(ConversationMessage.created_at, case((ConversationMessage.role == "user", 0), else_=1), ConversationMessage.id)).all()
    return {"session_id": row.id, "is_demo": row.is_demo, "turn_count": row.turn_count,
            "messages": [{"id": m.id, "created_at": m.created_at, "role": m.role, "content": m.content, "citations": m.citations,
                          "retrieval_mode": m.retrieval_mode} for m in messages]}


@router.get("/invocations/{invocation_id}")
def invocation_detail(invocation_id: UUID, db: Database, owner: User):
    row = require_record(db, AgentInvocation, invocation_id)
    if row.owner_id != owner:
        raise HTTPException(403, "invocation_access_denied")
    return {"id": row.id, "session_id": row.conversation_id, "status": row.status,
            "request_id": row.request_id, "duration_ms": row.duration_ms,
            "error_code": row.error_code, "tool_trace": row.tool_trace,
            "generation_mode": (row.response_payload or {}).get("generation_mode"),
            "llm_http_calls": (row.response_payload or {}).get("llm_http_calls"),
            "trace_origin": "agent_reported_and_schema_validated", "finished_at": row.finished_at}


@router.get("/health", dependencies=[Depends(require_admin)])
def agent_health():
    return agent_gateway.check_health(get_settings())


@router.get("/invocations/{invocation_id}/evidence", response_model=AgentInvocationEvidence)
def invocation_evidence(invocation_id: UUID, db: Database, owner: User):
    """Resolve this user's saved answer to immutable source versions and link proof."""
    row = require_record(db, AgentInvocation, invocation_id)
    if row.owner_id != owner:
        raise HTTPException(403, "invocation_access_denied")
    from backend.services.traceability import answer_evidence
    return answer_evidence(db, row)
