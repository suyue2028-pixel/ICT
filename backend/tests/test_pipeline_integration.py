"""Transactional PostgreSQL tests; all test rows are rolled back."""
from uuid import uuid4
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from backend.collectors.base import CollectionBatch
from backend.collectors.live import LiveCollector, Transport
from backend.db.session import get_engine
from backend.models import (
    DataSource, Document, DocumentRevision, KnowledgeEvent, Outbox, TaskRun,
    Vulnerability, VulnerabilityAlias, VulnerabilityDocument,
)
from backend.schemas.contracts import EnrichmentWrite, QuestionRequest, RawRecord
from backend.api.v1.ai import ask_external, write_enrichment
from backend.api.v1.documents import document_enrichment
from backend.services.pipeline import ingest_batch
from backend.services.jobs import create_task, maintain_jobs, utc_now


@pytest.fixture
def db():
    with get_engine().connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


def source(db, adapter="offline"):
    row = DataSource(name=f"test-{uuid4()}", category="test", adapter=adapter,
                     cursor={"sentinel": "old"}, config={})
    db.add(row)
    db.flush()
    return row


def ingest(db, src, records, cursor=None):
    task = TaskRun(source_id=src.id, status="running", attempts=1)
    db.add(task)
    db.flush()
    result = ingest_batch(db, src, CollectionBatch(records, cursor or {"done": True}), task)
    task.status = "succeeded"
    db.flush()
    return result


def record(external_id, doc_type="vulnerability", aliases=None, content="AI tool security issue", demo=True):
    return RawRecord(external_id=external_id, title=f"{external_id} title",
                     source_url=f"https://example.org/{external_id}", document_type=doc_type,
                     content=content, aliases=aliases or [], is_demo=demo)


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_repeat_import_preserves_versions_and_cursor(db):
    src = source(db)
    first = record("revision-a", aliases=["CVE-2099-12345"])
    assert ingest(db, src, [first])["created"] == 1
    document = db.scalar(select(Document).where(Document.external_id == "revision-a"))
    first_seen = document.first_collected_at
    assert ingest(db, src, [first])["unchanged"] == 1
    assert count(db, DocumentRevision) >= 1
    changed = first.model_copy(update={"content": "AI tool security issue, revised"})
    assert ingest(db, src, [changed])["updated"] == 1
    assert document.first_collected_at == first_seen
    assert db.scalar(select(func.count()).select_from(DocumentRevision).where(
        DocumentRevision.document_id == document.id)) == 2
    assert db.scalar(select(func.count()).select_from(KnowledgeEvent).where(
        KnowledgeEvent.document_id == document.id)) == 2
    assert src.cursor == {"done": True}


def test_article_multiple_cves_never_merges_distinct_entities(db):
    src = source(db)
    article = record("article-two", "article", ["CVE-2099-30001", "CVE-2099-30002"])
    ingest(db, src, [article])
    ids = db.scalars(select(VulnerabilityAlias.vulnerability_id).where(
        VulnerabilityAlias.value.in_(["CVE-2099-30001", "CVE-2099-30002"]))).all()
    assert len(ids) == 2 and ids[0] != ids[1]
    assert db.scalar(select(func.count()).select_from(VulnerabilityDocument).where(
        VulnerabilityDocument.vulnerability_id.in_(ids))) == 2


def test_explicit_bridge_merges_and_demo_scope_isolated(db):
    demo = source(db)
    live = source(db, "osv")
    ingest(db, demo, [record("a", aliases=["CVE-2099-40001"]),
                      record("b", aliases=["GHSA-AAAA-BBBB-CCCC"])])
    ingest(db, demo, [record("bridge", aliases=["CVE-2099-40001", "GHSA-AAAA-BBBB-CCCC"])])
    alias_ids = db.scalars(select(VulnerabilityAlias.vulnerability_id).where(
        VulnerabilityAlias.is_demo.is_(True), VulnerabilityAlias.value.in_(
            ["CVE-2099-40001", "GHSA-AAAA-BBBB-CCCC"]))).all()
    assert len(alias_ids) == 2 and alias_ids[0] == alias_ids[1]
    ingest(db, live, [record("live", aliases=["CVE-2099-40001"], demo=False)])
    assert db.scalar(select(Vulnerability.id).where(Vulnerability.is_demo.is_(False),
        Vulnerability.identifier == "CVE-2099-40001")) != alias_ids[0]


def test_osv_page_token_is_resumed():
    requests = []

    def handler(request):
        requests.append(request.content)
        return httpx.Response(200, json={"vulns": [], "next_page_token": "next"})

    transport = Transport(client=httpx.Client(transport=httpx.MockTransport(handler)), sleeper=lambda _: None)
    collector = LiveCollector("osv", {"packages": ["torch"], "max_pages": 1})
    first = collector._collect_osv({}, transport)
    assert first.more and first.next_cursor == {"package_index": 0, "page_token": "next"}
    collector._collect_osv(first.next_cursor, transport)
    assert b'"page_token":"next"' in requests[1]
    transport.close()


def test_enrichment_requires_real_revision_and_is_idempotent(db):
    src = source(db)
    original = record("enrichment-test", content="AI tool has CWE-502 unsafe deserialization")
    ingest(db, src, [original])
    document = db.scalar(select(Document).where(Document.source_id == src.id))
    status = document_enrichment(document.id, db)
    assert status["source"]["cwe"]["state"] == "unknown"
    assert status["external"]["cwe"] == [{"state": "unprocessed"}]
    citation = {"document_id": str(document.id), "content_hash": document.content_hash,
                "source_url": document.source_url, "locator": "details", "excerpt": "CWE-502"}
    body = EnrichmentWrite.model_validate({"document_id": str(document.id),
        "content_hash": document.content_hash, "provider": "verified-test",
        "fields": {"cwe": {"state": "known", "value": ["CWE-502"],
                           "method": "model", "evidence": [citation]}}})
    write_enrichment(body, db)
    write_enrichment(body, db)
    assert status["source"]["cwe"]["state"] == "unknown"
    assert document_enrichment(document.id, db)["external"]["cwe"][0]["state"] == "known"
    bad = body.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(HTTPException) as error:
        write_enrichment(bad, db)
    assert error.value.status_code == 409


def test_successful_source_has_manual_rate_limit(db):
    src = source(db, "osv")
    db.add(TaskRun(source_id=src.id, status="succeeded", attempts=1,
                   finished_at=utc_now()))
    db.flush()
    with pytest.raises(HTTPException) as error:
        create_task(db, src, enforce_rate_limit=True)
    assert error.value.status_code == 429


def test_broker_failure_retains_outbox_for_resend(db):
    src = source(db)
    task = create_task(db, src)

    class Unavailable:
        def send_task(self, *args, **kwargs):
            raise ConnectionError("not written to task details")

    assert maintain_jobs(db, Unavailable())["sent"] == 0
    box = db.scalar(select(Outbox).where(Outbox.task_id == task.id))
    assert box.sent_at is None and task.status == "queued"
    box.available_at = utc_now() - timedelta(seconds=1)
    sent = []

    class Available:
        def send_task(self, name, args, retry, ignore_result):
            assert ignore_result is True
            sent.append((name, args, retry))

    assert maintain_jobs(db, Available())["sent"] == 1
    assert sent == [("backend.collect", [str(task.id)], False)]


def test_external_answer_gateway_validates_real_citation(db, monkeypatch):
    src = source(db)
    ingest(db, src, [record("qa-gateway-test", content="AI model has CWE-502 unsafe deserialization")])
    document = db.scalar(select(Document).where(Document.source_id == src.id))
    citation = {"document_id": str(document.id), "content_hash": document.content_hash,
                "source_url": document.source_url, "excerpt": "CWE-502"}
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: SimpleNamespace(
        ai_service_url="https://ai.example.invalid", ai_service_token=None))

    def response_with(citations):
        def fake_post(url, json, headers, timeout):
            assert url == "https://ai.example.invalid/v1/ask"
            assert json["question"] == "CWE-502?" and timeout == 20
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "schema_version": "1", "status": "completed", "answer": "See cited evidence.",
                "citations": citations})
        return fake_post

    monkeypatch.setattr("backend.api.v1.ai.httpx.post", response_with([citation]))
    answer = ask_external(QuestionRequest(question="CWE-502?"), db)
    assert answer.status == "completed" and len(answer.citations) == 1
    monkeypatch.setattr("backend.api.v1.ai.httpx.post", response_with([
        {**citation, "excerpt": "fabricated quotation"}]))
    with pytest.raises(HTTPException) as error:
        ask_external(QuestionRequest(question="CWE-502?"), db)
    assert error.value.status_code == 502


def test_deepseek_answer_uses_verified_source_citation(db, monkeypatch):
    src = source(db)
    ingest(db, src, [record("deepseek-test", content="AI model has CWE-502 unsafe deserialization")])
    document = db.scalar(select(Document).where(Document.source_id == src.id))
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: SimpleNamespace(
        deepseek_api_key=SecretStr("test-only-key"), deepseek_model="deepseek-flash",
        ai_service_url=None))
    requests = []

    def fake_post(url, json, headers, timeout):
        requests.append(json)
        assert url == "https://api.deepseek.com/chat/completions"
        assert headers["Authorization"] == "Bearer test-only-key"
        assert json["model"] == "deepseek-flash"
        assert json["thinking"] == {"type": "disabled"}
        assert json["response_format"] == {"type": "json_object"}
        assert timeout == 30
        assert "CWE-502" in json["messages"][1]["content"]
        assert "test-only-key" not in str(json)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content":
                '{"status":"completed","answer":"存在 CWE-502 问题。","citation_indices":[1]}'}}]})

    monkeypatch.setattr("backend.api.v1.ai.httpx.post", fake_post)
    answer = ask_external(QuestionRequest(question="CWE-502?"), db, is_demo=True)
    assert answer.status == "completed"
    assert len(answer.citations) == 1
    assert answer.citations[0].document_id == document.id
    assert answer.citations[0].content_hash == document.content_hash
    assert len(requests) == 1


def test_deepseek_rejects_unverified_selection_and_skips_empty_search(db, monkeypatch):
    src = source(db)
    ingest(db, src, [record("deepseek-invalid", content="AI model has CWE-502 unsafe deserialization")])
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: SimpleNamespace(
        deepseek_api_key=SecretStr("test-only-key"), deepseek_model="deepseek-flash",
        ai_service_url=None))
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content":
                '{"status":"completed","answer":"unsupported","citation_indices":[2]}'}}]})

    monkeypatch.setattr("backend.api.v1.ai.httpx.post", fake_post)
    empty = ask_external(QuestionRequest(question="zzzznevermatched"), db, is_demo=True)
    assert empty.status == "insufficient_evidence" and not calls
    with pytest.raises(HTTPException) as error:
        ask_external(QuestionRequest(question="CWE-502?"), db, is_demo=True)
    assert error.value.status_code == 502
    assert error.value.detail == "deepseek_invalid_response"
