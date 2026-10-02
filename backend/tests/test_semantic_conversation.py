"""Transactional, explicitly synthetic embedding and conversation tests."""

import json as jsonlib
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.api.v1.ai import acknowledge_event, ask_external, create_conversation, get_conversation, retry_event
from backend.collectors.base import CollectionBatch
from backend.db.session import get_engine
from backend.models import DataSource, Document, DocumentRevision, EmbeddingChunk, KnowledgeEvent, TaskRun
from backend.schemas.contracts import ConversationCreate, KnowledgeAck, QuestionRequest, RawRecord
from backend.services.jobs import maintain_index_events, utc_now
from backend.services.pipeline import ingest_batch
from backend.services.semantic import EmbeddingError, embed_texts, search, split_text, store_chunks
from backend.tasks.jobs import index_event


@pytest.fixture
def db():
    with get_engine().connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


def settings():
    return SimpleNamespace(embedding_configured=True, embedding_model="synthetic-test-model",
                           embedding_dimensions=3, deepseek_api_key=SecretStr("test-only-key"),
                           deepseek_model="deepseek-flash", ai_service_url=None)


def make_document(db, is_demo=True):
    source = DataSource(name=f"semantic-test-{uuid4()}", category="test", adapter="offline",
                        config={}, cursor={})
    db.add(source)
    db.flush()
    body = "AI model has CWE-502 unsafe deserialization and a documented fix."
    document = Document(source_id=source.id, external_id=str(uuid4()), title="Synthetic AI test",
                        source_url="https://example.invalid/semantic-test", document_type="article",
                        content=body, content_hash="a" * 64, raw_payload={},
                        is_demo=is_demo, ai_relevant=True, relevance={})
    db.add(document)
    db.flush()
    db.add(DocumentRevision(document_id=document.id, content_hash=document.content_hash,
                            payload={"source_url": document.source_url, "content": body}))
    event = KnowledgeEvent(document_id=document.id, content_hash=document.content_hash,
                           operation="add", status="pending", detail={})
    db.add(event)
    db.flush()
    return document, event


def test_exact_semantic_search_is_versioned_and_demo_isolated(db, monkeypatch):
    document, _ = make_document(db, is_demo=True)
    chunks = split_text(document.content)
    assert len(chunks) == 1 and chunks[0].excerpt == document.content
    store_chunks(db, document, chunks, [[1.0, 0.0, 0.0]], settings())
    monkeypatch.setattr("backend.services.semantic.embed_texts", lambda texts, config: [[1.0, 0.0, 0.0]])
    hits = search(db, "CWE-502", settings(), is_demo=True)
    assert len(hits) == 1 and hits[0].document_id == document.id
    assert not search(db, "CWE-502", settings(), is_demo=False)
    document.content_hash = "b" * 64
    db.flush()
    assert not search(db, "CWE-502", settings(), is_demo=True)


def test_cloud_embedding_contract_validates_real_vector_shape(monkeypatch):
    config = SimpleNamespace(embedding_configured=True,
        embedding_base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        embedding_api_key=SecretStr("test-only-key"), embedding_model="text-embedding-v4",
        embedding_dimensions=1024)
    vector = [1.0] + [0.0] * 1023

    def fake_post(url, headers, json, timeout):
        assert url.endswith("/compatible-mode/v1/embeddings")
        assert headers["Authorization"] == "Bearer test-only-key"
        assert json == {"model": "text-embedding-v4", "input": ["synthetic text"],
                        "dimensions": 1024, "encoding_format": "float"}
        assert timeout == 30
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "data": [{"index": 0, "embedding": vector}]})

    monkeypatch.setattr("backend.services.semantic.httpx.post", fake_post)
    assert embed_texts(["synthetic text"], config) == [vector]
    vector.pop()
    with pytest.raises(EmbeddingError) as error:
        embed_texts(["synthetic text"], config)
    assert error.value.code == "embedding_invalid_response"


def test_index_ack_requires_a_queryable_vector(db, monkeypatch):
    document, event = make_document(db)
    monkeypatch.setattr("backend.api.v1.ai.get_settings", settings)
    acknowledgement = KnowledgeAck(status="indexed", detail={"index_id": "synthetic"})
    with pytest.raises(HTTPException) as error:
        acknowledge_event(event.id, acknowledgement, db)
    assert error.value.status_code == 422
    chunks = split_text(document.content)
    store_chunks(db, document, chunks, [[1.0, 0.0, 0.0]], settings())
    assert acknowledge_event(event.id, acknowledgement, db)["status"] == "indexed"


def test_index_worker_is_idempotent_and_old_version_is_superseded(db, monkeypatch):
    document, event = make_document(db)
    event.status = "queued"
    db.commit()
    factory = sessionmaker(db.connection(), expire_on_commit=False,
                           join_transaction_mode="create_savepoint")
    monkeypatch.setattr("backend.tasks.jobs.get_session_factory", lambda: factory)
    monkeypatch.setattr("backend.tasks.jobs.get_settings", settings)
    monkeypatch.setattr("backend.tasks.jobs.embed_texts",
                        lambda texts, config: [[1.0, 0.0, 0.0] for _ in texts])
    assert index_event(str(event.id)) == {"indexed": 1}
    assert index_event(str(event.id)) == {"ignored": True}
    db.refresh(event)
    assert event.status == "indexed" and event.detail["chunks"] == 1
    assert len(db.scalars(select(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id)).all()) == 1

    document.content_hash = "b" * 64
    stale = KnowledgeEvent(document_id=document.id, content_hash="c" * 64,
                           operation="update", status="queued", detail={})
    db.add(stale)
    db.commit()
    assert index_event(str(stale.id))["reason"] == "superseded"
    db.refresh(stale)
    assert stale.status == "superseded"


def test_dispatch_failure_stays_retryable_and_manual_retry_resets_attempts(db, monkeypatch):
    _, event = make_document(db)
    event.created_at = utc_now() - timedelta(days=3650)
    db.flush()
    monkeypatch.setattr("backend.services.jobs.get_settings", settings)
    monkeypatch.setattr("backend.services.jobs.embedding_endpoint", lambda config: "synthetic-test")

    class Unavailable:
        def send_task(self, *args, **kwargs):
            raise ConnectionError("synthetic test broker failure")

    assert maintain_index_events(db, Unavailable())["sent"] == 0
    assert event.status == "pending" and event.available_at is not None
    event.available_at = utc_now() - timedelta(seconds=1)
    sent = []

    class Available:
        def send_task(self, name, args, retry):
            sent.append((name, args, retry))

    assert maintain_index_events(db, Available())["sent"] >= 1
    assert ("backend.index_event", [str(event.id)], False) in sent
    event.status, event.attempts = "failed", 3
    retry_event(event.id, db)
    assert event.status == "pending" and event.attempts == 0


def test_index_failure_is_recorded_without_fake_vectors(db, monkeypatch):
    document, event = make_document(db)
    event.status = "queued"
    db.commit()
    factory = sessionmaker(db.connection(), expire_on_commit=False,
                           join_transaction_mode="create_savepoint")
    monkeypatch.setattr("backend.tasks.jobs.get_session_factory", lambda: factory)
    monkeypatch.setattr("backend.tasks.jobs.get_settings", settings)

    def unavailable(*args):
        raise EmbeddingError("embedding_network_error")

    monkeypatch.setattr("backend.tasks.jobs.embed_texts", unavailable)
    assert index_event(str(event.id))["error_code"] == "embedding_network_error"
    db.refresh(event)
    assert event.status == "pending" and event.attempts == 1
    assert not db.scalars(select(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id)).all()


def test_two_turn_deepseek_conversation_preserves_citations_and_scope(db, monkeypatch):
    _, _ = make_document(db)
    conversation = create_conversation(ConversationCreate(is_demo=True), db)
    conversation_id = conversation["id"]
    monkeypatch.setattr("backend.api.v1.ai.get_settings", settings)
    seen_messages = []

    def fake_post(url, json, headers, timeout):
        seen_messages.append(json["messages"])
        content = {"status": "completed", "answer": "资料指出存在 CWE-502 问题。",
                   "citation_indices": [1]}
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content":
                jsonlib.dumps(content, ensure_ascii=False)}}]})

    monkeypatch.setattr("backend.api.v1.ai.httpx.post", fake_post)
    first = ask_external(QuestionRequest(question="CWE-502 是什么？",
        conversation_id=conversation_id), db, is_demo=True)
    second = ask_external(QuestionRequest(question="它有哪些原文证据？",
        conversation_id=conversation_id), db, is_demo=True)
    assert first.conversation_id == second.conversation_id == conversation_id
    assert first.retrieval_mode == second.retrieval_mode == "keyword_fallback"
    assert len(first.citations) == len(second.citations) == 1
    assert len(seen_messages) == 2
    assert any(item["role"] == "assistant" for item in seen_messages[1][1:-1])
    detail = get_conversation(conversation_id, db)
    assert detail["turn_count"] == 2 and len(detail["messages"]) == 4
    with pytest.raises(HTTPException) as error:
        ask_external(QuestionRequest(question="CWE-502", conversation_id=conversation_id), db,
                     is_demo=False)
    assert error.value.status_code == 409


def test_offline_ingest_index_semantic_answer_followup_and_revision(db, monkeypatch):
    source = DataSource(name=f"offline-semantic-{uuid4()}", category="test",
                        adapter="offline", config={}, cursor={})
    db.add(source)
    db.flush()
    record = RawRecord(external_id="synthetic-semantic", title="Synthetic AI security note",
        source_url="https://example.invalid/offline-semantic", document_type="article",
        content="AI toolkit has CWE-502 unsafe deserialization. Apply the documented update.",
        is_demo=True)

    def ingest_record(value):
        task = TaskRun(source_id=source.id, status="running", attempts=1)
        db.add(task)
        db.flush()
        result = ingest_batch(db, source, CollectionBatch([value], {"done": True}), task)
        task.status = "succeeded"
        db.commit()
        return result

    assert ingest_record(record)["created"] == 1
    document = db.scalar(select(Document).where(Document.source_id == source.id))
    event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == document.id))
    event.status = "queued"
    db.commit()
    factory = sessionmaker(db.connection(), expire_on_commit=False,
                           join_transaction_mode="create_savepoint")
    monkeypatch.setattr("backend.tasks.jobs.get_session_factory", lambda: factory)
    monkeypatch.setattr("backend.tasks.jobs.get_settings", settings)
    monkeypatch.setattr("backend.tasks.jobs.embed_texts",
                        lambda texts, config: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr("backend.services.semantic.embed_texts",
                        lambda texts, config: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr("backend.api.v1.ai.get_settings", settings)
    assert index_event(str(event.id)) == {"indexed": 1}
    assert search(db, "unsafe deserialization", settings(), is_demo=True)

    def fake_post(url, json, headers, timeout):
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content": jsonlib.dumps({
                "status": "completed", "answer": "原文报告 CWE-502 风险。", "citation_indices": [1]
            }, ensure_ascii=False)}}]})

    monkeypatch.setattr("backend.api.v1.ai.httpx.post", fake_post)
    conversation_id = create_conversation(ConversationCreate(is_demo=True), db)["id"]
    for question in ("CWE-502 是什么问题？", "它有什么修复建议？"):
        answer = ask_external(QuestionRequest(question=question, conversation_id=conversation_id),
                              db, is_demo=True)
        assert answer.retrieval_mode == "semantic"
        assert answer.citations[0].document_id == document.id
    assert get_conversation(conversation_id, db)["turn_count"] == 2

    changed = record.model_copy(update={"content": record.content + " Revised version."})
    assert ingest_record(changed)["updated"] == 1
    assert search(db, "CWE-502", settings(), is_demo=True) == []
    new_event = db.scalar(select(KnowledgeEvent).where(
        KnowledgeEvent.document_id == document.id,
        KnowledgeEvent.content_hash == document.content_hash))
    new_event.status = "queued"
    db.commit()
    assert index_event(str(new_event.id)) == {"indexed": 1}
    assert search(db, "CWE-502", settings(), is_demo=True)[0].content_hash == document.content_hash
    assert ingest_record(changed)["unchanged"] == 1
