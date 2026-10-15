"""Fictional labelled data, rollback-only PostgreSQL; no model self-evaluation."""
from copy import deepcopy
from uuid import UUID, uuid4
import pytest
from sqlalchemy import insert, select, update
from test_agent_upgrade import db, client, token
from backend.core.config import get_settings
from backend.models import DataSource, Document, Evidence, TaskRun
from backend.collectors.base import CollectionBatch
from backend.schemas.contracts import RawRecord
from backend.services.pipeline import ingest_batch
from backend.services import enrichment_quality as quality

PREFIX = "/api/v1/ops/enrichment-quality"


@pytest.fixture
def specimen(db):
    source = DataSource(name="quality-fictional-" + str(uuid4()), adapter="offline", category="offline", config={})
    db.add(source)
    db.flush()
    task = TaskRun(source_id=source.id, status="running", attempts=1)
    db.add(task)
    db.flush()
    record = RawRecord(external_id="quality-fictional", title="Fictional Ollama vulnerability",
        source_url="https://example.invalid/quality", document_type="vulnerability",
        content="Fictional Ollama unsafe deserialization CWE-502, fixed in 1.1; synthetic test only.",
        is_demo=True, products=["ollama"], content_completeness="full_text", source_fields={
            "affected": {"items": [{"package": {"name": "fictional", "ecosystem": "PyPI"},
                "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.1"}]}]}]},
            "cwe": {"ids": ["CWE-502"]},
            "cvss": {"vectors": [{"version": "3.1", "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "score": 9.8}]},
            "remediation": {"references": [{"url": "https://example.invalid/fix"}]},
            "analysis": {"references": [{"url": "https://example.invalid/analysis"}]},
            "poc": {"references": [{"url": "https://example.invalid/poc"}]}})
    ingest_batch(db, source, CollectionBatch([record], {}), task)
    task.status = "succeeded"
    db.flush()
    return source, db.scalar(select(Document).where(Document.source_id == source.id))


def admin():
    return {"X-API-Key": get_settings().admin_api_key.get_secret_value()}


def new_batch(client, specimen, **changes):
    source, _ = specimen
    r = client.post(PREFIX + "/batches", headers=admin(), json={"is_demo": True,
        "source_id": str(source.id), "per_source": 1, **changes})
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


def label(client, batch, **changes):
    case = client.get(PREFIX + "/batches/" + batch, headers=admin()).json()["cases"][0]
    return {"document_id": case["document_id"], "content_hash": case["content_hash"],
        "prediction_hash": case["prediction_hash"], "reviewer": "fictional-test-reviewer",
        "annotation_origin": "human", "independent": True,
        "field_reviews": {"cwe": {"expected": ["CWE-502"],
            "basis": "Fictional test source_fields.cwe explicitly reports CWE-502."}}, **changes}


def report(client, batch, **params):
    return client.get(PREFIX + f"/batches/{batch}/report", headers=admin(), params=params).json()


def test_quality_monitor_six_dimensions_and_missing_labels(db, client, specimen):
    source, _ = specimen
    r = client.get(PREFIX, headers=admin(), params={"is_demo": True, "source_id": str(source.id)}).json()
    assert r["total_documents"] == 1 and r["automatic_checks"]["error_count"] == 0
    assert all(r["dimensions"][field]["known_ratio"] == 1 for field in quality.DIMENSIONS)
    assert r["reviewed_evaluation"]["overall"]["precision"] is None
    batch = new_batch(client, specimen)
    result = report(client, batch)
    assert result["status"] == "awaiting_independent_review" and result["overall"]["recall"] is None
    exported = client.get(PREFIX + "/batches/" + batch, headers=admin()).json()
    assert all(item["expected"] is None for item in exported["cases"][0]["review_template"]["field_reviews"].values())


def test_partial_labels_count_errors_omissions_and_reviewed_absence(db, client, specimen):
    batch = new_batch(client, specimen)
    body = label(client, batch, field_reviews={
        "cwe": {"expected": ["CWE-502", "CWE-79"], "basis": "Fictional independent gold includes a deliberately omitted CWE-79."},
        "remediation": {"expected": [], "basis": "Fictional negative gold: the declared fix link is deliberately incorrect."}})
    assert client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body).status_code == 200
    result = report(client, batch)
    assert result["overall"]["tp"] == result["overall"]["fp"] == result["overall"]["fn"] == 1
    assert result["overall"]["precision"] == result["overall"]["recall"] == .5
    assert result["overall"]["reviewed_fields"] == 2 and result["dimensions"]["affected"]["recall"] is None
    assert result["status"] == "partially_reviewed" and not result["competition_threshold_verified"]


@pytest.mark.parametrize("origin,independent", [("ai_assisted", True), ("human", False), ("synthetic", True)])
def test_ai_and_nonindependent_labels_do_not_produce_real_scores(db, client, specimen, origin, independent):
    batch = new_batch(client, specimen)
    body = label(client, batch, annotation_origin=origin, independent=independent)
    assert client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body).status_code == 200
    result = report(client, batch)
    assert result["excluded_ai_or_nonindependent"] == 1 and result["overall"]["precision"] is None


def test_reviews_are_idempotent_and_immutable(db, client, specimen):
    batch = new_batch(client, specimen)
    body = label(client, batch)
    path = PREFIX + f"/batches/{batch}/reviews"
    first = client.post(path, headers=admin(), json=body).json()
    again = client.post(path, headers=admin(), json=body).json()
    assert first["created"] and not again["created"] and first["review_id"] == again["review_id"]
    body["field_reviews"]["cwe"]["expected"] = ["CWE-79"]
    assert client.post(path, headers=admin(), json=body).status_code == 409


@pytest.mark.parametrize("change", ["version", "prediction", "delete"])
def test_changed_versions_or_predictions_do_not_receive_old_scores(db, client, specimen, change):
    _, doc = specimen
    batch = new_batch(client, specimen)
    body = label(client, batch)
    assert client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body).status_code == 200
    if change == "version":
        doc.content_hash = "f" * 64
    elif change == "prediction":
        row = db.scalar(select(Evidence).where(Evidence.document_id == doc.id, Evidence.field == "cwe"))
        row.value = {"ids": ["CWE-79"]}
    else:
        from backend.services.jobs import utc_now
        doc.deleted_at = utc_now()
    db.flush()
    current, historical = report(client, batch), report(client, batch, current_only=False)
    assert current["stale_cases"] == current["excluded_stale_reviews"] == 1
    assert current["overall"]["precision"] is None and historical["overall"]["precision"] == 1
    assert client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body).status_code == 409


def test_real_and_demo_batches_and_metrics_are_isolated(db, client, specimen):
    source, _ = specimen
    params = {"source_id": str(source.id), "is_demo": False}
    assert client.get(PREFIX, headers=admin(), params=params).json()["total_documents"] == 0
    assert client.post(PREFIX + "/batches", headers=admin(), json=params).status_code == 422
    batch = new_batch(client, specimen)
    assert all(row["batch_id"] != batch for row in client.get(PREFIX + "/batches", headers=admin()).json()["items"])


def test_structural_and_source_checks_are_not_accuracy(db, client, specimen):
    source, doc = specimen
    row = db.scalar(select(Evidence).where(Evidence.document_id == doc.id, Evidence.field == "cvss"))
    value = deepcopy(row.value)
    value["vectors"][0]["score"] = 11
    row.value = value
    db.flush()
    r = client.get(PREFIX + "/issues", headers=admin(), params={"is_demo": True, "source_id": str(source.id)}).json()
    assert {"source_value_mismatch", "cvss_score_out_of_range"} <= {issue["code"] for issue in r["items"]}
    summary = client.get(PREFIX, headers=admin(), params={"is_demo": True, "source_id": str(source.id)}).json()
    assert summary["accuracy"] is None and summary["automatic_checks"]["semantic_accuracy"] is None


def test_unknown_prediction_becomes_false_negative_when_gold_is_present(db, client, specimen):
    _, doc = specimen
    row = db.scalar(select(Evidence).where(Evidence.document_id == doc.id, Evidence.field == "cwe"))
    row.state = "unknown"
    db.flush()
    batch = new_batch(client, specimen)
    body = label(client, batch)
    client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body)
    result = report(client, batch)["overall"]
    assert result["fn"] == 1 and result["recall"] == 0 and result["precision"] is None


def test_external_wrapped_values_are_scored_and_method_scope_is_explicit(db, client, specimen):
    _, doc = specimen
    db.add(Evidence(document_id=doc.id, content_hash=doc.content_hash, field="cwe", state="known",
        method="external:fictional", value={"value": {"ids": ["CWE-79"]}, "review_state": "pending_review",
            "evidence": [{"document_id": str(doc.id)}]}, source_url=doc.source_url, locator="content", excerpt="Fictional"))
    db.flush()
    batch = new_batch(client, specimen, prediction_method="all")
    body = label(client, batch)
    client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body)
    assert report(client, batch)["overall"]["precision"] == .5


def test_admin_authentication_and_strict_review_validation(db, client, specimen):
    batch = new_batch(client, specimen)
    for headers in [{}, token(db), {"X-Agent-Token": get_settings().agent_tool_token.get_secret_value()}]:
        assert client.get(PREFIX, headers=headers).status_code == 401
        assert client.get(PREFIX + f"/batches/{batch}", headers=headers).status_code == 401
        assert client.post(PREFIX + "/batches", headers=headers, json={}).status_code == 401
    body = label(client, batch)
    body["field_reviews"]["cwe"]["expected"] = None
    assert client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body).status_code == 422
    body = label(client, batch, prediction_hash="0" * 64)
    assert client.post(PREFIX + f"/batches/{batch}/reviews", headers=admin(), json=body).status_code == 409
    assert client.post(PREFIX + "/batches", headers=admin(), json={"max_cases": True}).status_code == 422


def test_normalized_facts_deduplicate_numbers_and_preserve_version_roles():
    assert quality.canonical({"score": 9}) == quality.canonical({"score": 9.0})
    assert quality.canonical(True) != quality.canonical(1)
    bounds = quality.fact_units("affected", {"cpe_matches": [{"criteria": "cpe:2.3:a:x:y:*:*:*:*:*:*:*:*", "versionEndExcluding": "2"}]})
    assert bounds[0]["versionEndExcluding"] == "2" and "fixed_version" not in bounds[0]
    assert quality.fact_units("cwe", {"ids": ["CWE-79", "CWE-79"]}) == ["CWE-79"]
    assert quality.fact_units("cvss", {"invalid": "not silently discarded"})
    assert not quality.valid_url(123) and not quality.valid_url("https://example.invalid:bad/fix")
    assert not quality.valid_url("https://user:password@example.invalid/")


@pytest.fixture
def large_scope(db):
    source = DataSource(name="fictional-full-scan-" + str(uuid4()), adapter="offline", category="offline", config={})
    db.add(source)
    db.flush()
    # Fixed ordering places the intentionally malformed fact after the former
    # 1000-document cutoff. All rows live only in the rollback-only test DB.
    rows = [{"id": UUID(int=index + 1), "source_id": source.id, "external_id": f"fictional-scan-{index}",
             "title": "Fictional quality scan only", "source_url": "https://example.invalid/full-scan",
             "document_type": "vulnerability", "content": "Fictional test; never real intelligence.",
             "content_hash": "a" * 64, "raw_payload": {"source_fields": {"cwe": {"ids": ["invalid"]}}},
             "ai_relevant": True, "is_demo": True} for index in range(1005)]
    db.execute(insert(Document), rows)
    db.execute(insert(Evidence), [
        {"document_id": rows[-1]["id"], "content_hash": "a" * 64, "field": "cwe", "state": "known",
         "value": {"ids": ["invalid"]}, "method": "source_reported", "source_url": rows[-1]["source_url"],
         "locator": "source_fields.cwe", "excerpt": "invalid"},
        {"document_id": rows[-2]["id"], "content_hash": "b" * 64, "field": "cwe", "state": "known",
         "value": {"ids": ["CWE-79"]}, "method": "source_reported", "source_url": rows[-2]["source_url"],
         "locator": "source_fields.cwe", "excerpt": "old version"}])
    db.flush()
    return source, rows[-1]["id"]


def test_full_scan_covers_tail_missing_evidence_and_all_batch_sizes(db, large_scope):
    source, _ = large_scope
    summaries = [quality.monitoring(db, is_demo=True, source_id=source.id, batch_size=size, include_issues=False)
                 for size in (1, 200, 500)]
    for result in summaries:
        assert result["total_documents"] == result["scanned_documents"] == 1005
        assert result["scan_complete"] and result["scan_limit"] is None
        assert result["scan_consistency"] == "single_statement_snapshot"
        assert result["dimensions"]["cwe"]["known"] == 1
        assert result["dimensions"]["cwe"]["unknown"] == 1004  # Old hash is excluded.
        assert result["automatic_checks"]["issue_codes"] == {"invalid_cwe": 1}
        assert result["automatic_checks"]["documents_with_errors"] == 1
        assert result["issues"] == [] and result["issues_total"] == 1
        assert result["accuracy"] is result["recall"] is None
    assert summaries[0]["dimensions"] == summaries[1]["dimensions"] == summaries[2]["dimensions"]
    bounded = quality.monitoring(db, is_demo=True, source_id=source.id, limit=1000)
    assert bounded["total_documents"] == 1005 and bounded["scanned_documents"] == 1000
    assert not bounded["scan_complete"] and bounded["automatic_checks"]["error_count"] == 0


def test_full_scan_api_filters_and_pages_issues_after_old_cutoff(db, client, large_scope):
    source, tail_id = large_scope
    params = {"is_demo": True, "source_id": str(source.id), "batch_size": 7,
              "severity": "error", "code": "invalid_cwe", "page_size": 1}
    first = client.get(PREFIX + "/issues", headers=admin(), params=params).json()
    assert first["total"] == 1 and first["scan_complete"] and first["scanned_documents"] == 1005
    assert first["items"][0]["document_id"] == str(tail_id)
    second = client.get(PREFIX + "/issues", headers=admin(), params={**params, "page": 2}).json()
    assert second["total"] == 1 and second["items"] == []
    empty = client.get(PREFIX + "/issues", headers=admin(), params={**params, "severity": "warning"}).json()
    assert empty["total"] == 0
    summary = client.get(PREFIX, headers=admin(), params={"is_demo": True, "source_id": str(source.id)}).json()
    assert summary["scan_complete"] and summary["scanned_documents"] == 1005
    real = client.get(PREFIX, headers=admin(), params={"source_id": str(source.id)}).json()
    assert real["total_documents"] == real["scanned_documents"] == 0 and real["scan_complete"]
    assert all(value["known_ratio"] is None for value in real["dimensions"].values())
    for size in (0, 501):
        assert client.get(PREFIX, headers=admin(), params={"batch_size": size}).status_code == 422


def test_stream_keeps_one_snapshot_when_a_document_changes_mid_scan(db, large_scope, monkeypatch):
    source, tail_id = large_scope
    audit = quality.audit_document
    changed = False
    def change_after_scan_starts(doc, rows):
        nonlocal changed
        if not changed:
            changed = True
            db.execute(update(Document).where(Document.id == tail_id).values(content_hash="c" * 64))
            db.execute(update(Evidence).where(Evidence.document_id == tail_id).values(content_hash="c" * 64,
                value={"ids": ["CWE-79"]}))
        return audit(doc, rows)
    monkeypatch.setattr(quality, "audit_document", change_after_scan_starts)
    result = quality.monitoring(db, is_demo=True, source_id=source.id, batch_size=1)
    assert changed and result["scan_complete"] and result["scanned_documents"] == 1005
    assert result["automatic_checks"]["issue_codes"] == {"invalid_cwe": 1}
    assert result["issues"][0]["content_hash"] == "a" * 64
