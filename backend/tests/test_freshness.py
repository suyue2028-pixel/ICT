"""Synthetic timestamp cases; database fixtures roll back and aren't real collection."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from test_agent_upgrade import db, client
from backend.collectors.base import CollectionBatch
from backend.collectors.extended import ExtendedCollector
from backend.collectors.live import LiveCollector
from backend.models import DataSource, Document, DocumentRevision, KnowledgeEvent, TaskRun
from backend.schemas.contracts import RawRecord
from backend.services.freshness import apply_time_filter, effective_interval, recent_counts, record_failure, source_stale
from backend.services.jobs import create_task, maintain_jobs, utc_now
from backend.services.pipeline import ingest_batch

NOW = datetime(2099, 1, 10, tzinfo=timezone.utc)


def row(db, demo=False):
    src = DataSource(name="synthetic-freshness-" + str(uuid4()), adapter="offline" if demo else "osv",
        category="offline" if demo else "vulnerability", config={}, cursor={}, enabled=False)
    db.add(src)
    db.flush()
    return src


def ingest(db, src, record):
    task = TaskRun(source_id=src.id, status="running", attempts=1)
    db.add(task)
    db.flush()
    result = ingest_batch(db, src, CollectionBatch([record], {"saved": True}), task)
    task.status = "succeeded"
    db.flush()
    return result, db.scalar(select(Document).where(Document.source_id == src.id, Document.external_id == record.external_id))


def synthetic(identifier, demo=False, **updates):
    return RawRecord(external_id=identifier, title="Synthetic AI security test", content="LLM prompt injection test only",
        document_type="article", source_url="https://example.invalid/" + identifier,
        is_demo=demo, published_at=NOW - timedelta(hours=1), **updates)


@pytest.mark.parametrize("basis", ["published", "updated", "discovered", "changed"])
def test_recent_window_boundary_and_future_excluded(db, basis):
    src = row(db)
    attr = {"published": "published_at", "updated": "source_updated_at",
            "discovered": "first_collected_at", "changed": "content_changed_at"}[basis]
    ids = []
    for identifier, delta in (("boundary", -48), ("inside", -1), ("old", -49), ("future", 1)):
        _, doc = ingest(db, src, synthetic(identifier))
        setattr(doc, attr, NOW + timedelta(hours=delta))
        ids.append(doc.id)
    db.flush()
    query = select(Document.id).where(Document.source_id == src.id)
    assert set(db.scalars(apply_time_filter(query, basis, 48, now=NOW))) == set(ids[:2])


def test_naive_or_reversed_time_range_rejected():
    with pytest.raises(HTTPException, match="timezone_required"):
        apply_time_filter(select(Document), time_from=datetime(2099, 1, 1))
    with pytest.raises(HTTPException, match="invalid_time_range"):
        apply_time_filter(select(Document), time_from=NOW, time_to=NOW - timedelta(days=1))


def test_old_publication_new_discovery_is_not_fresh_publication_and_demo_excluded(db):
    real = row(db)
    _, doc = ingest(db, real, synthetic("backfill").model_copy(update={"published_at": NOW - timedelta(days=30)}))
    doc.first_collected_at = NOW - timedelta(hours=1)
    demo = row(db, True)
    _, sample = ingest(db, demo, synthetic("demo", True))
    sample.first_collected_at = NOW - timedelta(hours=1)
    db.flush()
    counts = recent_counts(db, NOW)
    assert counts["newly_discovered"] == 1
    assert counts["newly_discovered_and_recently_published"] == 0
    assert counts["recently_published"] == 0


def test_imprecise_publication_not_counted_as_precise_recent_time(db):
    src = row(db)
    _, doc = ingest(db, src, synthetic("date-only", metadata_json={"date_precision": 3}))
    statement = select(Document.id).where(Document.source_id == src.id)
    assert not list(db.scalars(apply_time_filter(statement, "published", 48, now=NOW)))


def test_duplicate_and_html_noise_do_not_create_new_versions_or_change_time(db):
    src = row(db)
    original = synthetic("repeat", raw={"html": "body and ephemeral navigation A"})
    _, doc = ingest(db, src, original)
    digest = doc.content_hash
    doc.indexed_at = NOW
    replay = original.model_copy(update={"raw": {"html": "body and ephemeral navigation B"}})
    result, doc = ingest(db, src, replay)
    assert result["unchanged"] == 1 and result["updated"] == 0
    assert doc.content_hash == digest and doc.content_changed_at is None and doc.indexed_at == NOW
    assert doc.last_checked_at is not None
    assert len(list(db.scalars(select(DocumentRevision).where(DocumentRevision.document_id == doc.id)))) == 1


def test_actual_update_invalidates_index_and_old_source_version_cannot_overwrite(db):
    src = row(db)
    original = synthetic("update", source_updated_at=NOW - timedelta(hours=1))
    _, doc = ingest(db, src, original)
    first_seen = doc.first_collected_at
    doc.indexed_at = NOW
    revised = original.model_copy(update={"content": "LLM prompt injection corrected mitigation", "source_updated_at": NOW})
    result, doc = ingest(db, src, revised)
    assert result["updated"] == 1 and doc.content_changed_at is not None and doc.indexed_at is None
    digest, changed_at = doc.content_hash, doc.content_changed_at
    result, doc = ingest(db, src, original)
    assert result["stale_ignored"] == 1 and doc.content_hash == digest and doc.content_changed_at == changed_at
    assert doc.first_collected_at == first_seen
    assert len(list(db.scalars(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id)))) == 2


def test_failed_batch_does_not_commit_partial_documents_or_advance_cursor(db):
    src = row(db)
    src.cursor = {"sentinel": "preserved"}
    task = TaskRun(source_id=src.id, status="running", attempts=1)
    db.add(task)
    db.flush()
    with pytest.raises(ValueError, match="source_demo_scope_mismatch"):
        with db.begin_nested():
            ingest_batch(db, src, CollectionBatch([synthetic("good"), synthetic("wrong-scope", True)], {"advanced": True}), task)
    db.refresh(src)
    assert src.cursor == {"sentinel": "preserved"}
    assert not list(db.scalars(select(Document.id).where(Document.source_id == src.id)))


def test_source_staleness_uses_its_actual_schedule_and_backoff_is_bounded():
    src = SimpleNamespace(adapter="arxiv", interval_seconds=3600,
        last_success_at=NOW - timedelta(hours=12), consecutive_failures=0, last_error_code=None, next_run_at=None)
    assert effective_interval(src) == 86400 and not source_stale(src, NOW)
    src.last_success_at = NOW - timedelta(hours=26)
    assert source_stale(src, NOW)
    for _ in range(10):
        record_failure(src, "source_network_error", NOW, terminal=True)
    assert src.consecutive_failures == 10 and src.next_run_at == NOW + timedelta(hours=4)
    src.adapter, src.interval_seconds = "nvd", 604800
    assert effective_interval(src) == 14400


def test_four_hour_interval_limits_preserve_faster_sources_and_daily_cache():
    from backend.api.v1.sources import validate_interval
    from backend.services.freshness import DEFAULT_INTERVALS
    for adapter, seconds in DEFAULT_INTERVALS.items():
        validate_interval(adapter, seconds)
        assert seconds <= (86400 if adapter == "arxiv" else 14400)
    assert effective_interval(SimpleNamespace(adapter="nvd", interval_seconds=3600)) == 3600
    validate_interval("nvd", 14400)
    with pytest.raises(HTTPException, match="source_interval_exceeds_4_hours"):
        validate_interval("nvd", 14401)
    with pytest.raises(HTTPException, match="arxiv_daily_cache_interval_required"):
        validate_interval("arxiv", 14400)
    validate_interval("arxiv", 86400)
    assert effective_interval(SimpleNamespace(adapter="arxiv", interval_seconds=172800)) == 86400


def test_four_hour_actual_latency_boundary_unknown_time_and_backfill(db):
    from backend.services.freshness import freshness_snapshot
    src = row(db)
    for identifier, published, collected in (
        ("four-hour-boundary", NOW - timedelta(hours=4), NOW),
        ("four-hour-exceeded", NOW - timedelta(hours=4, seconds=1), NOW),
        ("historical-backfill", NOW - timedelta(days=30), NOW),
        ("unknown-time", None, NOW),
    ):
        _, doc = ingest(db, src, synthetic(identifier).model_copy(update={"published_at": published}))
        doc.first_collected_at = collected
    db.flush()
    report = freshness_snapshot(db, NOW)
    actual = report["publish_to_first_collection_seconds"]
    assert actual["recent_publications"]["target_seconds"] == 14400
    assert actual["recent_publications"]["within_target_count"] == 1
    assert actual["recent_publications"]["over_target_count"] == 1
    assert actual["recent_publications"]["within_target_ratio"] == .5
    assert actual["historical_backfill"]["count"] == 1
    assert actual["missing_time"] == 1
    assert not report["schedule_policy"]["delivery_target_verified"]


def test_four_hour_empty_measurement_does_not_claim_success(db):
    from backend.services.freshness import freshness_snapshot
    report = freshness_snapshot(db, NOW)
    actual = report["publish_to_first_collection_seconds"]["recent_publications"]
    assert actual["count"] == 0 and actual["within_target_ratio"] is None


def test_source_management_rejects_over_four_hours_and_defaults_to_four(db, client):
    from backend.core.config import get_settings
    admin = {"X-API-Key": get_settings().admin_api_key.get_secret_value()}
    payload = {"name": "synthetic-four-hour-" + str(uuid4()), "adapter": "crossref"}
    rejected = client.post("/api/v1/sources", json={**payload, "interval_seconds": 21600}, headers=admin)
    assert rejected.status_code == 422
    added = client.post("/api/v1/sources", json=payload, headers=admin)
    assert added.status_code == 201 and added.json()["interval_seconds"] == 14400
    assert client.patch("/api/v1/sources/" + added.json()["id"],
        json={"interval_seconds": 14401}, headers=admin).status_code == 422


def test_restart_overdue_source_schedules_once_and_outbox_publish_failure_retains_task(db, monkeypatch):
    db.execute(update(DataSource).values(enabled=False))
    src = row(db)
    src.enabled, src.next_run_at = True, utc_now() - timedelta(days=2)
    class Broker:
        def send_task(self, *args, **kwargs):
            raise ConnectionError("synthetic broker unavailable")
    first = maintain_jobs(db, Broker())
    second = maintain_jobs(db, Broker())
    tasks = list(db.scalars(select(TaskRun).where(TaskRun.source_id == src.id)))
    assert first["sent"] == second["sent"] == 0 and len(tasks) == 1
    assert tasks[0].status == "queued" and src.next_run_at > utc_now()


def test_osv_checks_all_configured_packages_before_hourly_wait():
    class Transport:
        def request(self, *args, **kwargs):
            return 200, {}, b'{"vulns": []}'
    collector = LiveCollector("osv", {"packages": ["torch", "transformers"]})
    first = collector._collect_osv({}, Transport())
    assert first.more and first.next_cursor["package_index"] == 1
    second = collector._collect_osv(first.next_cursor, Transport())
    assert not second.more and second.next_cursor["package_index"] == 0


def test_nvd_long_offline_window_is_bounded_and_overlap_preserves_watermark(monkeypatch):
    monkeypatch.setattr("backend.collectors.extended.utc_now", lambda: NOW)
    requests = []
    class Transport:
        def request(self, *args, **kwargs):
            requests.append(kwargs["params"])
            return 200, {}, b'{"vulnerabilities":[],"totalResults":0}'
    collector = ExtendedCollector("nvd", {"keywords": ["ollama"]})
    batch = collector._nvd_window({"window_start": (NOW - timedelta(days=200)).isoformat()}, Transport(), SimpleNamespace(nvd_api_key=None))
    start, end = (datetime.fromisoformat(requests[0][x]) for x in ("lastModStartDate", "lastModEndDate"))
    assert end - start == timedelta(days=120) and batch.more
    assert datetime.fromisoformat(batch.next_cursor["window_start"]) == end - timedelta(hours=24)


def test_crossref_window_stays_fixed_across_pages_and_backfills_without_jumping(monkeypatch):
    monkeypatch.setattr("backend.collectors.extended.utc_now", lambda: NOW)
    requests = []
    class Transport:
        def request(self, *args, **kwargs):
            requests.append(kwargs["params"])
            identifier = "10.test/page" + str(len(requests))
            return 200, {}, json.dumps({"message": {"items": [{"DOI": identifier, "title": ["Synthetic non-AI metadata"]}],
                "next-cursor": "next-" + str(len(requests))}}).encode()
    collector = ExtendedCollector("crossref", {"max_items": 1, "max_pages_per_cycle": 1})
    first = collector._crossref_window({"since": "2098-12-01", "cursor": "legacy-query-cursor"}, Transport(), None)
    second = collector._crossref_window(first.next_cursor, Transport(), None)
    assert requests[0]["cursor"] == "*" and requests[1]["cursor"] == "next-1"
    assert requests[0]["filter"] == requests[1]["filter"]
    assert "+00:00" not in requests[0]["filter"]
    assert first.more and second.more and first.continuation_delay_seconds == 180
    assert second.next_cursor["since"] == "2098-12-01"


def test_crossref_recent_lane_is_not_blocked_by_old_backfill(monkeypatch):
    monkeypatch.setattr("backend.collectors.extended.utc_now", lambda: NOW)
    requests = []
    class Transport:
        def request(self, *args, **kwargs):
            requests.append(kwargs["params"])
            return 200, {}, b'{"message":{"items":[],"next-cursor":"empty"}}'
    batch = ExtendedCollector("crossref", {})._crossref({"since": "2098-12-01"}, Transport(), None)
    assert "from-index-date:2099-01-08" in requests[0]["filter"]
    assert "from-index-date:2098-12-01" in requests[1]["filter"]
    assert batch.next_cursor["backfill"] and batch.more
    assert batch.next_cursor["recent"]["since"].startswith("2099-01-09")


def test_time_filter_http_and_management_auth(client):
    assert client.get("/api/v1/documents?recent_hours=48&time_basis=discovered").status_code == 200
    assert client.get("/api/v1/intelligence?recent_hours=48&time_basis=updated").status_code == 200
    assert client.get("/api/v1/documents?recent_hours=48&time_basis=other").status_code == 422
    assert client.get("/api/v1/documents?time_from=2099-01-01T00:00:00").status_code == 422
    assert client.get("/api/v1/ops/freshness").status_code == 401


def test_old_tool_revision_does_not_inherit_current_index_or_change_time(db, client):
    from backend.core.config import get_settings
    src = row(db)
    original = synthetic("history")
    _, doc = ingest(db, src, original)
    digest = doc.content_hash
    _, doc = ingest(db, src, original.model_copy(update={"content": "LLM prompt injection updated test only"}))
    doc.indexed_at = utc_now()
    db.flush()
    response = client.get(f"/api/v1/tools/documents/{doc.id}?content_hash={digest}",
        headers={"X-API-Key": get_settings().admin_api_key.get_secret_value()})
    assert response.status_code == 200
    version = response.json()["data"]
    assert not version["is_current"] and version["indexed_at"] is None and version["content_changed_at"] is None
