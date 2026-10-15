"""Synthetic catalogue/time boundaries and rollback-only PostgreSQL fixtures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_agent_upgrade import db, client, token
from backend.collectors.extended import ExtendedCollector, ExtendedConfig
from backend.collectors.discovery import DiscoveryCollector, dated_metadata, NIST_DRAFTS, GOVUK_SEARCH
from backend.collectors.live import SourceConfig
from backend.collectors.base import CollectionBatch
from backend.core.config import get_settings
from backend.models import DataSource, Document, TaskRun
from backend.services.coverage import coverage_snapshot, delivery_metrics, latency
from backend.services.jobs import utc_now
from backend.services.pipeline import ingest_batch, relevance
from backend.schemas.contracts import RawRecord

NOW = datetime(2099, 2, 10, 12, tzinfo=timezone.utc)


def catalogue_row(index):
    return {"title": f"NVIDIA TensorRT-LLM security {index}", "bulletin id": f"fictional-{index}",
        "cve identifier(s)": f"CVE-2099-{10000 + index}", "severity": "High",
        "publish date": "01 Feb 2099", "last updated": "02 Feb 2099"}


def test_nvidia_legacy_etag_cannot_hide_tail_and_full_cycle_allows_304():
    class Transport:
        def request(self, *args, **kwargs):
            if kwargs["headers"].get("If-None-Match"):
                return 304, {}, b""
            return 200, {"etag": "fictional-etag"}, json.dumps({"data": [catalogue_row(i) for i in range(25)]}).encode()
    collector, cursor, seen = ExtendedCollector("nvidia", {}), {"etag": "old-first-ten"}, set()
    for _ in range(3):
        batch = collector._nvidia(cursor, Transport(), None)
        assert "etag" not in batch.next_cursor or not batch.more
        seen.update(record.external_id for record in batch.records)
        cursor = batch.next_cursor
    assert len(seen) == 25 and not batch.more and batch.coverage["cycle_complete"]
    assert not collector._nvidia(cursor, Transport(), None).records


def test_nvidia_changed_catalogue_restarts_at_newest_without_using_old_offset():
    class Transport:
        def __init__(self, count): self.count = count
        def request(self, *args, **kwargs):
            return 200, {}, json.dumps({"data": [catalogue_row(i) for i in range(self.count)]}).encode()
    collector = ExtendedCollector("nvidia", {"max_items": 2})
    first = collector._nvidia({}, Transport(5), None)
    second = collector._nvidia(first.next_cursor, Transport(6), None)
    assert second.coverage["offset"] == 0 and second.coverage["eligible_count"] == 6


def test_nvd_recent_request_precedes_large_historical_backfill_and_input_cursor_immutable(monkeypatch):
    monkeypatch.setattr("backend.collectors.extended.utc_now", lambda: NOW)
    calls = []
    class Transport:
        def request(self, *args, **kwargs):
            calls.append(kwargs["params"])
            return 200, {}, b'{"vulnerabilities":[],"totalResults":0}'
    cursor = {"window_start": (NOW - timedelta(days=200)).isoformat()}
    original = deepcopy(cursor)
    result = ExtendedCollector("nvd", {"keywords": ["ollama"]})._nvd(cursor, Transport(), SimpleNamespace(nvd_api_key=None))
    assert len(calls) == 2 and datetime.fromisoformat(calls[0]["lastModStartDate"]) == NOW - timedelta(hours=48)
    assert datetime.fromisoformat(calls[1]["lastModEndDate"]) - datetime.fromisoformat(calls[1]["lastModStartDate"]) == timedelta(days=120)
    assert result.more and result.next_cursor["backfill"] and cursor == original


def test_nvd_failure_never_mutates_cursor_or_returns_partial_commit(monkeypatch):
    monkeypatch.setattr("backend.collectors.extended.utc_now", lambda: NOW)
    class Transport:
        calls = 0
        def request(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2: raise TimeoutError("synthetic history failure")
            return 200, {}, b'{"vulnerabilities":[],"totalResults":0}'
    cursor = {"window_start": (NOW - timedelta(days=10)).isoformat()}
    saved = deepcopy(cursor)
    with pytest.raises(TimeoutError):
        ExtendedCollector("nvd", {})._nvd(cursor, Transport(), SimpleNamespace(nvd_api_key=None))
    assert cursor == saved


def test_nvd_last_keyword_does_not_trigger_busy_poll_for_a_twelve_minute_cycle(monkeypatch):
    monkeypatch.setattr("backend.collectors.extended.utc_now", lambda: NOW)
    class Transport:
        def request(self, *args, **kwargs): return 200, {}, b'{"vulnerabilities":[],"totalResults":0}'
    collector = ExtendedCollector("nvd", {"keywords": ["ollama", "gradio"]})
    result = collector._nvd_window({"keyword_index": 1, "window_end": (NOW - timedelta(minutes=12)).isoformat(),
        "window_start": (NOW - timedelta(days=1)).isoformat()}, Transport(), SimpleNamespace(nvd_api_key=None))
    assert not result.more


@pytest.mark.parametrize("value", ["2099-02-01", "2099-02-01T00:00:00", "2099-02-01T13:20:00"])
def test_no_fabricated_timestamp_for_unspecified_timezone(value):
    actual, metadata = dated_metadata(value)
    assert actual is None and metadata["date_precision"] == 3


def test_nist_discovers_only_ai_security_drafts_and_preserves_draft_status():
    class Transport:
        def request(self, method, url, **kwargs):
            assert url == NIST_DRAFTS
            entries = [dict(link="https://csrc.nist.gov/pubs/sp/fictional/ipd", title="Fictional AI safety framework",
                summary="Draft security guidance for generative AI systems.", published="2099-02-01T00:00:00"),
                dict(link="https://csrc.nist.gov/pubs/sp/other/ipd", title="RSA cryptography", summary="Key management")]
            return 200, {}, json.dumps({"entries": entries}).encode()
    batch = DiscoveryCollector("nist_drafts", {})._nist_drafts({}, Transport(), None)
    assert len(batch.records) == 1 and batch.coverage["eligible_count"] == 1
    record = batch.records[0]
    assert record.metadata_json["legal_status"] == "draft_open_for_comment"
    assert record.published_at is None and record.content_completeness == "summary" and relevance(record)["relevant"]


def test_policy_discovery_uses_content_timestamp_not_search_ranking_and_rejects_external_links(monkeypatch):
    monkeypatch.setattr("backend.collectors.discovery.utc_now", lambda: NOW)
    class Transport:
        allowed_urls = set()
        def request(self, method, url, **kwargs):
            if url == GOVUK_SEARCH:
                return 200, {}, json.dumps({"start": 0, "total": 2, "results": [
                    {"link": "/government/publications/fictional-ai-security", "public_timestamp": NOW.isoformat()},
                    {"link": "http://127.0.0.1/private"}]}).encode()
            assert url == "https://www.gov.uk/api/content/government/publications/fictional-ai-security"
            return 200, {}, json.dumps({"base_path": "/government/publications/fictional-ai-security",
                "title": "Fictional AI security guidance", "first_published_at": "2098-01-01T10:00:00Z",
                "public_updated_at": "2099-02-09T10:00:00Z", "document_type": "guidance",
                "details": {"body": "Generative AI security and prompt injection risks."}}).encode()
    batch = DiscoveryCollector("govuk_search", {})._policy_window({"from": "2099-02-08T12:00:00Z"}, Transport(), None)
    assert len(batch.records) == 1 and batch.records[0].published_at.year == 2098
    assert batch.records[0].source_updated_at.year == 2099 and batch.coverage["results_examined"] == 2


def test_policy_empty_page_cannot_advance_cursor(monkeypatch):
    monkeypatch.setattr("backend.collectors.discovery.utc_now", lambda: NOW)
    class Transport:
        def request(self, *args, **kwargs): return 200, {}, b'{"start":0,"total":3,"results":[]}'
    cursor = {"from": NOW.isoformat()}
    with pytest.raises(ValueError, match="empty_page"):
        DiscoveryCollector("govuk_search", {})._policy_window(cursor, Transport(), None)
    assert cursor == {"from": NOW.isoformat()}


def test_delivery_separates_backfill_imprecise_future_and_four_hour_boundary():
    def doc(hours, first=0, metadata=None):
        return dict(published_at=NOW - timedelta(hours=hours), first_collected_at=NOW - timedelta(hours=first),
            metadata_json=metadata or {}, content_changed_at=None, source_updated_at=None)
    result = delivery_metrics([doc(4), doc(5), doc(100), doc(1, metadata={"date_precision": 3}), doc(-1)], NOW, 48)
    measured = result["recent_publication_to_first_collection"]
    assert measured["count"] == 2 and measured["within_target"] == 1 and measured["over_target"] == 1
    assert measured["target_seconds"] == 14400
    assert result["historical_first_seen_in_window"]["count"] == 1
    assert result["imprecise_publication_time"] == 1 and result["negative_publication_delay"] == 1
    assert latency([])["observed_within_target_ratio"] is None


def test_four_hour_target_shared_for_publications_updates_and_coverage_api(db, client):
    from backend.services.freshness import DELIVERY_TARGET_SECONDS, freshness_snapshot
    result = latency([14399, 14400, 14401, 21600])
    assert result["target_seconds"] == DELIVERY_TARGET_SECONDS == 14400
    assert result["within_target"] == result["over_target"] == 2
    docs = [dict(published_at=NOW - timedelta(hours=3), first_collected_at=NOW,
        metadata_json={}, source_updated_at=NOW - timedelta(hours=5), content_changed_at=NOW)]
    measured = delivery_metrics(docs, NOW, 48)
    assert measured["updated_to_changed_in_window"]["target_seconds"] == 14400
    assert measured["updated_to_changed_in_window"]["over_target"] == 1
    fresh = freshness_snapshot(db, utc_now())
    response = client.get("/api/v1/ops/coverage", headers={"X-API-Key": get_settings().admin_api_key.get_secret_value()})
    assert response.status_code == 200
    coverage = response.json()
    targets = [coverage["delivery"][key]["target_seconds"] for key in
        ("recent_publication_to_first_collection", "historical_first_seen_in_window", "updated_to_changed_in_window")]
    assert targets == [fresh["schedule_policy"]["target_seconds"]] * 3 == [14400] * 3


def test_admin_coverage_counts_saved_catalogue_not_independent_recall_and_excludes_demo(db, client):
    source = DataSource(name="fictional-coverage-" + str(uuid4()), adapter="nist_drafts", category="standard", config={})
    db.add(source); db.flush()
    task = TaskRun(source_id=source.id, status="running", attempts=1)
    db.add(task); db.flush()
    record = RawRecord(external_id="fictional-draft", title="Fictional AI security", content="AI prompt injection safety test",
        source_url="https://example.invalid/fictional", document_type="standard", is_demo=False)
    ingest_batch(db, source, CollectionBatch([record], {}), task)
    task.status, task.finished_at = "succeeded", utc_now()
    task.result = {"coverage": {"mode": "current_draft_catalogue", "eligible_ids": ["fictional-draft", "fictional-missing"]}}
    db.flush()
    assert client.get("/api/v1/ops/coverage").status_code == 401
    assert client.get("/api/v1/ops/coverage", headers=token(db)).status_code == 401
    response = client.get("/api/v1/ops/coverage", headers={"X-API-Key": get_settings().admin_api_key.get_secret_value()})
    assert response.status_code == 200
    result = response.json(); row = next(row for row in result["sources"] if row["source_id"] == str(source.id))
    assert row["catalogue_capture"]["observed_catalogue_capture_ratio"] == .5
    assert result["independent_vulnerability_recall"] is None and not result["continuous_sla_verified"]
    doc = db.scalar(select(Document).where(Document.source_id == source.id))
    doc.is_demo = True; db.flush()
    result = coverage_snapshot(db, utc_now())
    assert next(row for row in result["sources"] if row["source_id"] == source.id)["documents"] == 0


def test_default_inventory_expansion_preserves_explicit_user_config():
    assert len(SourceConfig.validate_for("osv", {}).packages) == 10
    assert len(ExtendedConfig().keywords) == 12
    assert ExtendedConfig(keywords=["custom-product"]).keywords == ["custom-product"]
    assert SourceConfig.validate_for("osv", {"packages": ["custom-package"]}).packages == ["custom-package"]
