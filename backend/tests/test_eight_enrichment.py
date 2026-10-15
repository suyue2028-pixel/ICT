"""Fictional evidence-only enrichment and real PostgreSQL/outbox behavior."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_agent_upgrade import db, client, evidence_document
from backend.core.config import get_settings
from backend.models import Document, DocumentRevision, Evidence, TaskRun, Outbox, Vulnerability, VulnerabilityDocument
from backend.schemas.contracts import RawRecord
from backend.collectors.base import CollectionBatch
from backend.services.pipeline import ingest_batch
from backend.services.eight_enrichment import DIMENSIONS, METHOD, store_dimensions, create_enrichment_task, summary_dimensions
from backend.services.traceability import locate_quote
from backend.services.enrichment_quality import monitoring, prediction_hash
from backend.services.cvss_base import calculate_v3_base
from backend.services.jobs import utc_now, create_task, maintain_jobs


def admin():
    return {"X-API-Key": get_settings().admin_api_key.get_secret_value()}


def test_eight_results_keep_unknown_and_never_claim_nonexistent_exploits(db):
    _, doc = evidence_document(db)
    digest, raw = doc.content_hash, deepcopy(doc.raw_payload)
    result = store_dimensions(db, doc)
    rows = list(db.scalars(select(Evidence).where(Evidence.document_id == doc.id, Evidence.method == METHOD)))
    assert set(result) == set(DIMENSIONS) and len(rows) == 8
    assert result["analysis"]["state"] == "known"
    assert result["exploitation"]["state"] == result["poc"]["state"] == "not_found"
    for row in rows:
        for c in row.value["evidence"]:
            quoted_doc = db.get(Document, __import__("uuid").UUID(c["document_id"]))
            assert locate_quote(quoted_doc.raw_payload, c["excerpt"], c["locator"])["locator_verified"]
    store_dimensions(db, doc)
    assert db.scalar(select(func.count()).select_from(Evidence).where(Evidence.document_id == doc.id, Evidence.method == METHOD)) == 8
    assert doc.content_hash == digest and doc.raw_payload == raw
    checks = monitoring(db, is_demo=True, source_id=doc.source_id, document_type="all", prediction_method="external")
    assert checks["automatic_checks"]["error_count"] == 0


def test_policy_has_eight_results_and_vulnerability_only_dimensions_are_not_applicable(db):
    source, _ = evidence_document(db)
    task = TaskRun(source_id=source.id, attempts=1, status="running")
    db.add(task); db.flush()
    record = RawRecord(external_id="fictional-policy", title="Fictional AI governance policy", document_type="policy",
        source_url="https://example.invalid/policy", content="This AI security policy protects personal data and governs models.",
        is_demo=True, content_completeness="full_text")
    ingest_batch(db, source, CollectionBatch([record], {}), task)
    task.status = "succeeded"; db.flush()
    doc = db.scalar(select(Document).where(Document.external_id == record.external_id))
    result = store_dimensions(db, doc)
    assert len(result) == 8
    rows = list(db.scalars(select(Evidence).where(Evidence.document_id == doc.id)))
    summary = summary_dimensions(rows)
    assert summary["cvss"]["state"] == summary["affected"]["state"] == "not_applicable"
    assert summary["analysis"]["state"] == "known"


def test_vulnerability_json_rows_allow_exact_identifier_peer_lookup(db):
    _, doc = evidence_document(db)
    doc.document_type = "vulnerability"
    db.flush()
    result = store_dimensions(db, doc)
    assert len(result) == 8
    assert result["affected"]["applicability"] == "applicable"
    assert result["analysis"]["state"] == "known"


def test_multi_cve_bulletin_does_not_transfer_another_vulnerabilitys_fields(db):
    source, doc = evidence_document(db)
    doc.document_type = "vulnerability"
    task = TaskRun(source_id=source.id, attempts=1, status="running")
    db.add(task); db.flush()
    record = RawRecord(external_id="fictional-multi-cve", title="Fictional two-CVE bulletin", document_type="advisory",
        source_url="https://example.invalid/multi-cve", content="Fictional AI model bulletin lists two separate vulnerabilities.",
        is_demo=True, aliases=["CVE-2099-98765", "CVE-2099-98766"],
        source_fields={"cwe": {"ids": ["CWE-999"]}})
    ingest_batch(db, source, CollectionBatch([record], {}), task)
    task.status = "succeeded"; db.flush()
    store_dimensions(db, doc)
    row = db.scalar(select(Evidence).where(Evidence.document_id == doc.id, Evidence.method == METHOD, Evidence.field == "cwe"))
    assert "CWE-999" not in str(row.value["value"])


@pytest.mark.parametrize("content,found", [
    ("A fictional vulnerability. The exploit has been disclosed to the public and may be used.", True),
    ("No public exploit has been disclosed.", False),
    ("It is false that The exploit has been disclosed to the public and may be used.", False)])
def test_public_exploit_narrative_is_not_claimed_as_active_exploitation(db, content, found):
    _, doc = evidence_document(db)
    doc.document_type, doc.source_url = "vulnerability", "https://nvd.nist.gov/vuln/detail/CVE-2099-98765"
    doc.content = content
    store_dimensions(db, doc)
    row = db.scalar(select(Evidence).where(Evidence.document_id == doc.id, Evidence.method == METHOD, Evidence.field == "exploitation"))
    assert (row.state == "known") is found
    if found:
        assert row.value["value"]["status"] == "public_exploit_disclosed_not_confirmed_active"
        assert row.value["evidence"][0]["excerpt"] in content


def test_explicit_osv_fix_and_cvss_calculation_keep_evidence_and_original_unknown_score(db):
    source, doc = evidence_document(db)
    record = RawRecord.model_validate(doc.raw_payload)
    record.source_fields = {"affected": {"items": [{"package": {"name": "fictional", "ecosystem": "PyPI"},
        "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "2.0"}, {"limit": "3.0"}]}]}]},
        "cvss": {"vectors": [{"version": "3.1", "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "score": None}]}}
    task = TaskRun(source_id=source.id, attempts=1, status="running")
    db.add(task); db.flush(); ingest_batch(db, source, CollectionBatch([record], {}), task)
    task.status = "succeeded"; db.flush()
    store_dimensions(db, doc)
    rows = {r.field: r for r in db.scalars(select(Evidence).where(Evidence.document_id == doc.id,
        Evidence.content_hash == doc.content_hash, Evidence.method == METHOD))}
    assert "2.0" in str(rows["remediation"].value["value"]) and "3.0" not in str(rows["remediation"].value["value"])
    assert rows["cvss"].value["value"][0]["score"] == 9.8
    assert rows["cvss"].value["value"][0]["derivation"] == "cvss_v3_base_formula"
    native = db.scalar(select(Evidence).where(Evidence.document_id == doc.id,
        Evidence.content_hash == doc.content_hash, Evidence.field == "cvss", Evidence.method == "source_reported"))
    assert native.value["vectors"][0]["score"] is None


def test_topic_paper_candidates_need_review_and_demo_isolation(db):
    source, doc = evidence_document(db)
    task = TaskRun(source_id=source.id, attempts=1, status="running")
    db.add(task); db.flush()
    record = RawRecord(external_id="fictional-paper", title="Synthetic deserialization study", document_type="paper",
        source_url="https://example.invalid/paper", content="LLMs suffer from unsafe deserialization of untrusted model weights.",
        is_demo=True, content_completeness="summary")
    ingest_batch(db, source, CollectionBatch([record], {}), task); task.status = "succeeded"; db.flush()
    result = store_dimensions(db, doc)
    assert result["papers"]["state"] == "pending_review"
    paper = db.scalar(select(Document).where(Document.external_id == record.external_id))
    paper.is_demo = False; db.flush()
    result = store_dimensions(db, doc)
    assert result["papers"]["state"] == "not_found"


def test_document_jobs_do_not_block_collection_and_outbox_routes_to_correct_worker(db):
    source, doc = evidence_document(db)
    enrichment, created = create_enrichment_task(db, doc)
    assert created and create_enrichment_task(db, doc)[0].id == enrichment.id
    collection = create_task(db, source)
    assert collection.kind == "offline_demo" and collection.id != enrichment.id
    db.flush()
    sent = []
    class Celery:
        def send_task(self, name, **kw):
            sent.append(name)
    maintain_jobs(db, Celery())
    assert "backend.enrich" in sent and "backend.collect" in sent


def test_worker_duplicate_delivery_and_version_changes_cannot_write_stale_enrichment(db, monkeypatch):
    _, doc = evidence_document(db)
    task, _ = create_enrichment_task(db, doc); db.flush()
    from backend.tasks.jobs import enrich
    monkeypatch.setattr("backend.tasks.jobs.get_session_factory", lambda: sessionmaker(bind=db.connection(),
        expire_on_commit=False, join_transaction_mode="create_savepoint"))
    assert enrich(str(task.id))["processed_dimensions"] == 8
    assert enrich(str(task.id))["ignored"]
    db.expire_all()
    assert db.get(TaskRun, task.id).status == "succeeded"
    task2, _ = create_enrichment_task(db, doc, force=True); db.flush()
    doc.content_hash = "f" * 64; db.flush()
    assert enrich(str(task2.id))["superseded"]
    db.expire_all()
    assert db.get(TaskRun, task2.id).error_code == "enrichment_document_changed"


def test_enrichment_api_authentication_and_complete_summary(db, client):
    _, doc = evidence_document(db)
    assert client.post(f"/api/v1/documents/{doc.id}/enrich").status_code == 401
    response = client.post(f"/api/v1/documents/{doc.id}/enrich", headers=admin())
    assert response.status_code == 202 and response.json()["task_id"]
    store_dimensions(db, doc)
    response = client.get(f"/api/v1/documents/{doc.id}/enrichment")
    assert response.status_code == 200 and len(response.json()["summary"]) == 8
    assert all(row["state"] != "unprocessed" for row in response.json()["summary"].values())


def test_recheck_timestamp_does_not_invalidate_unchanged_quality_predictions(db):
    _, doc = evidence_document(db)
    store_dimensions(db, doc)
    rows = list(db.scalars(select(Evidence).where(Evidence.document_id == doc.id, Evidence.method == METHOD)))
    before = prediction_hash(rows)
    store_dimensions(db, doc)
    assert prediction_hash(rows) == before


def test_eight_dimension_progress_counts_and_failed_jobs_are_not_automatically_recreated(db, client):
    _, doc = evidence_document(db)
    before = client.get("/api/v1/ops/enrichment?is_demo=true", headers=admin())
    assert before.status_code == 200 and len(before.json()["dimensions"]) == 8
    assert client.get("/api/v1/ops/enrichment").status_code == 401
    store_dimensions(db, doc)
    after = client.get("/api/v1/ops/enrichment?is_demo=true", headers=admin()).json()
    assert after["all_eight_processed_documents"] == before.json()["all_eight_processed_documents"] + 1
    task, _ = create_enrichment_task(db, doc, force=True)
    task.status, task.attempts, task.finished_at = "failed", 3, utc_now()
    db.flush()
    assert create_enrichment_task(db, doc)[0].id == task.id
    retry, created = create_enrichment_task(db, doc, force=True)
    assert created and retry.id != task.id


@pytest.mark.parametrize("vector,score", [("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H", 7.5),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0), ("CVSS:4.0/AV:N", None)])
def test_cvss_formula_and_unsupported_version(vector, score):
    assert calculate_v3_base(vector) == score
