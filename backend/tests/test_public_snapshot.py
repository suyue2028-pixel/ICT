"""Fictional fixtures ONLY in a dedicated PostgreSQL validation database."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from backend.core.config import get_settings
from backend.db.session import get_engine, get_db
from backend.main import app
from backend.models import (AssociationEvidence, DataSource, Document, DocumentRevision, Evidence,
    KnowledgeEvent, SnapshotImport, SnapshotMember, SnapshotSource, TaskRun)
from backend.schemas.contracts import RawRecord
from backend.schemas.public_snapshot import (AssociationPacket, DocumentPacket, EvidencePacket,
    RevisionPacket, SourcePacket, VulnerabilityPacket)
from backend.services.pipeline import payload_hash, ingest_batch
from backend.collectors.base import CollectionBatch
from backend.services.traceability import association_candidates
from backend.services.public_snapshot import (SnapshotError, import_snapshot, plan_import, read_bundle,
    scan, source_key, verify_import, write_bundle)


@pytest.fixture
def db():
    assert get_settings().postgres_db.startswith(("verify_", "ci_")), "snapshot tests require an isolated database"
    with get_engine().connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


@pytest.fixture
def bundle(tmp_path):
    when = datetime(2025, 1, 2, tzinfo=timezone.utc)
    sid, did, vid = uuid4(), uuid4(), uuid4()
    source = SourcePacket(id=sid, key=f"public:osv:{sid}", name="Fictional snapshot TEST " + str(sid)[:8],
                          category="vulnerability", adapter="osv", config={})
    record = RawRecord(external_id="FICTIONAL-TEST-NOT-REAL", title="[Fictional test] AI library",
        source_url="https://osv.dev/vulnerability/FICTIONAL-TEST-NOT-REAL", document_type="vulnerability",
        content="Fictional AI library test: unsafe deserialization; this is not real intelligence.",
        published_at=when, source_updated_at=when, products=["torch"], is_demo=False,
        vulnerability_id="CVE-2099-999999", aliases=["CVE-2099-999999", "GHSA-AAAA-BBBB-CCCC"],
        source_fields={"cwe": {"ids": ["CWE-502"]}}, content_completeness="full_text")
    payload = record.model_dump(mode="json")
    digest = payload_hash(payload)
    revision = RevisionPacket(content_hash=digest, payload=payload, created_at=when, updated_at=when)
    evidence = EvidencePacket(content_hash=digest, field="cwe", value=record.source_fields["cwe"], state="known",
        excerpt=json.dumps(record.source_fields["cwe"], ensure_ascii=False), source_url=str(record.source_url),
        locator="source_fields.cwe", method="source_reported", created_at=when, updated_at=when)
    associations = [AssociationPacket(content_hash=digest, **a, created_at=when)
                    for a in association_candidates(payload, str(record.source_url))]
    doc = DocumentPacket(id=did, source_key=source.key, external_id=record.external_id, current_hash=digest,
        created_at=when, updated_at=when, first_collected_at=when, last_checked_at=when,
        content_changed_at=None, revisions=[revision], evidence=[evidence], associations=associations,
        vulnerability_ids=[vid])
    vuln = VulnerabilityPacket(id=vid, identifier="CVE-2099-999999", title=record.title,
                              severity="unknown", aliases=record.aliases)
    return write_bundle(tmp_path / "packet", [source], [doc], [vuln], snapshot_id="fictional-test-snapshot")


def total(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_roundtrip_idempotency_provenance_api_and_pending_events(db, bundle):
    first = import_snapshot(db, bundle)
    assert first["new"] == 1 and first["events_created"] == 1 and first["verification"]["complete"]
    source = db.get(DataSource, db.get(SnapshotSource, bundle.sources[0].key).source_id)
    assert not source.enabled and source.cursor == {} and source.last_success_at is None
    doc = db.scalar(select(Document).where(Document.source_id == source.id))
    assert doc.first_collected_at == bundle.documents[0].first_collected_at
    assert doc.created_at == bundle.documents[0].created_at and doc.indexed_at is None
    batch = db.scalar(select(SnapshotImport).where(SnapshotImport.snapshot_id == bundle.manifest.snapshot_id))
    assert batch.imported_at > doc.first_collected_at
    event_row = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id))
    assert event_row.status == "pending" and event_row.detail["reason"] == "public_snapshot_import"
    models = (Document, DocumentRevision, Evidence, AssociationEvidence, KnowledgeEvent, SnapshotMember)
    before = [total(db, model) for model in models]
    second = import_snapshot(db, bundle)
    assert second["events_created"] == 0 and second["new"] == 0 and second["skipped"] == 1
    assert [total(db, model) for model in models] == before
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            result = client.get(f"/api/v1/documents/{doc.id}")
            assert result.status_code == 200 and result.json()["content_hash"] == doc.content_hash
            proof = client.get(f"/api/v1/documents/{doc.id}/versions/{doc.content_hash}").json()
            assert proof["revision"] == bundle.documents[0].revisions[0].payload
            assert proof["associations"][0]["active_link"]
            enriched = client.get(f"/api/v1/documents/{doc.id}/enrichment")
            assert enriched.status_code == 200
            assert "CWE-502" in json.dumps(enriched.json())
    finally:
        app.dependency_overrides.clear()


def test_dry_run_emits_no_write_sql(db, bundle):
    statements = []
    connection = db.connection()
    def capture(conn, cursor, statement, params, context, executemany):
        statements.append(statement.split()[0].upper())
    event.listen(connection, "before_cursor_execute", capture)
    try:
        result = plan_import(db, bundle)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert result["new"] == 1
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}


def test_old_and_ambiguous_snapshots_do_not_replace_new_current(db, bundle, tmp_path):
    import_snapshot(db, bundle)
    source = db.get(DataSource, db.get(SnapshotSource, bundle.sources[0].key).source_id)
    doc = db.scalar(select(Document).where(Document.source_id == source.id))
    newer = RawRecord.model_validate(doc.raw_payload).model_copy(update={
        "content": doc.content + " A newer FIXED version.",
        "source_updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
    task = TaskRun(source_id=source.id, status="running", attempts=1)
    db.add(task); db.flush()
    ingest_batch(db, source, CollectionBatch([newer], {}), task)
    task.status = "succeeded"; db.flush()
    new_hash = doc.content_hash
    event_count = total(db, KnowledgeEvent)
    old = write_bundle(tmp_path / "older", bundle.sources, bundle.documents, bundle.vulnerabilities,
                       snapshot_id="fictional-old-new-batch")
    assert import_snapshot(db, old)["historical"] == 1
    assert doc.content_hash == new_hash and total(db, KnowledgeEvent) == event_count
    doc.source_updated_at = None; db.flush()
    ambiguous = write_bundle(tmp_path / "ambiguous", bundle.sources, bundle.documents, bundle.vulnerabilities,
                             snapshot_id="fictional-ambiguous")
    assert import_snapshot(db, ambiguous)["conflict"] == 1
    assert doc.content_hash == new_hash and total(db, KnowledgeEvent) == event_count


def test_newer_snapshot_promotes_once(db, bundle, tmp_path):
    import_snapshot(db, bundle)
    packet = bundle.documents[0].model_copy(deep=True)
    payload = dict(packet.revisions[0].payload)
    payload["content"] += " New source revision."
    payload["source_updated_at"] = "2026-01-01T00:00:00Z"
    digest = payload_hash(payload)
    packet.revisions.append(RevisionPacket(content_hash=digest, payload=payload,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    packet.current_hash = digest
    newer = write_bundle(tmp_path / "newer", bundle.sources, [packet], bundle.vulnerabilities,
                         snapshot_id="fictional-newer")
    result = import_snapshot(db, newer)
    assert result["updated"] == 1 and result["events_created"] == 1
    assert import_snapshot(db, newer)["events_created"] == 0


def test_uuid_hint_collision_never_overwrites_unrelated_document(db, bundle):
    source = DataSource(name="Unrelated test " + str(uuid4()), adapter="offline", category="offline", config={})
    db.add(source); db.flush()
    other = Document(id=bundle.documents[0].id, source_id=source.id, external_id="unrelated",
        title="Fictional unrelated", source_url="https://example.invalid", document_type="article",
        content="Unrelated fixture", content_hash="a" * 64, raw_payload={}, is_demo=True, metadata_json={})
    db.add(other); db.flush()
    result = import_snapshot(db, bundle)
    assert result["uuid_remapped"] == 1 and other.content == "Unrelated fixture"
    member = db.scalar(select(SnapshotMember).where(SnapshotMember.remote_document_id == str(other.id)))
    assert member.document_id != other.id


def rewrite_file(directory, kind, transform):
    path = directory / f"{kind}-0001.jsonl"
    data = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    transform(data)
    content = ("\n".join(json.dumps(row, ensure_ascii=False) for row in data) + "\n").encode()
    path.write_bytes(content)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for file in manifest["files"]:
        if file["kind"] == kind:
            file["bytes"], file["sha256"] = len(content), hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("case", ["checksum", "missing_reference", "demo", "hash", "secret", "format", "locator", "unknown_raw", "traversal", "duplicate"])
def test_untrusted_packets_rejected_before_import(bundle, tmp_path, case):
    directory = tmp_path / "packet"
    if case == "checksum":
        (directory / "documents-0001.jsonl").write_bytes(b"tampered\n")
    elif case == "format":
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8")); manifest["format_version"] = 999
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif case == "traversal":
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8")); manifest["files"][0]["path"] = "../private.jsonl"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        def change(rows):
            d = rows[0]
            if case == "missing_reference": d["source_key"] = "missing-source"
            if case == "demo": d["revisions"][0]["payload"]["is_demo"] = True
            if case == "hash": d["revisions"][0]["payload"]["content"] += " corrupt"
            if case == "secret": d["revisions"][0]["payload"]["content"] += " sk-" + "TESTONLY" * 5
            if case == "locator": d["associations"][0]["locator"] = "/content"
            if case == "unknown_raw": d["revisions"][0]["payload"]["raw"]["unapproved"] = "value"
            if case == "duplicate": d["revisions"].append(d["revisions"][0])
        rewrite_file(directory, "documents", change)
    with pytest.raises(SnapshotError):
        read_bundle(directory)


def test_snapshot_id_cannot_be_reused_for_changed_package(db, bundle, tmp_path):
    import_snapshot(db, bundle)
    changed = write_bundle(tmp_path / "different", bundle.sources, bundle.documents, bundle.vulnerabilities,
                           snapshot_id=bundle.manifest.snapshot_id)
    with pytest.raises(SnapshotError, match="snapshot_id_content_conflict"):
        plan_import(db, changed)


def test_missing_migration_refused(db, bundle, monkeypatch):
    bundle.manifest.required_migration = "future_unknown_migration"
    with pytest.raises(SnapshotError, match="database_migration_incompatible"):
        plan_import(db, bundle)


def test_secret_scanner_does_not_expose_values():
    value = "fictional-sensitive-configuration-value"
    with pytest.raises(SnapshotError) as error:
        scan({"content": value}, [value], record="record-id-only")
    assert value not in str(error.value) and error.value.record == "record-id-only"
    with pytest.raises(SnapshotError):
        scan({"nested": {"api_key": "fictional"}})


def test_late_failure_rolls_back_import_transaction(db, bundle, tmp_path):
    import_snapshot(db, bundle)
    local = db.scalar(select(Document).where(Document.external_id == bundle.documents[0].external_id))
    evidence = db.scalar(select(Evidence).where(Evidence.document_id == local.id))
    evidence.state = "unknown"
    db.flush()
    second = write_bundle(tmp_path / "transaction-failure", bundle.sources, bundle.documents,
                          bundle.vulnerabilities, snapshot_id="fictional-failed-transaction")
    before = total(db, SnapshotImport)
    with pytest.raises(SnapshotError, match="immutable_evidence_conflict"):
        with db.begin_nested():
            import_snapshot(db, second)
    assert total(db, SnapshotImport) == before
    assert not db.scalar(select(SnapshotImport.id).where(SnapshotImport.snapshot_id == second.manifest.snapshot_id))


def test_second_snapshot_of_identical_data_creates_no_index_side_effects(db, bundle, tmp_path):
    import_snapshot(db, bundle)
    second = write_bundle(tmp_path / "same-data-new-id", bundle.sources, bundle.documents,
                          bundle.vulnerabilities, snapshot_id="fictional-same-data-new-id")
    before = total(db, KnowledgeEvent)
    result = import_snapshot(db, second)
    assert result["reused"] == 1 and result["new_revisions"] == 0
    assert result["events_created"] == 0 and total(db, KnowledgeEvent) == before


def test_published_snapshot_manifests_if_present():
    # Makes checksum/reference/schema validation part of ordinary CI; no DB writes.
    root = Path(__file__).resolve().parents[1] / "datasets/public-intelligence"
    for manifest in root.glob("*/manifest.json"):
        assert read_bundle(manifest.parent).manifest.counts["documents"] > 0


def test_unjustified_vulnerability_alias_is_rejected(bundle, tmp_path):
    def change(rows):
        rows[0]["aliases"].append("CVE-2099-111111")
    rewrite_file(tmp_path / "packet", "vulnerabilities", change)
    with pytest.raises(SnapshotError, match="vulnerability_alias_or_reference_invalid"):
        read_bundle(tmp_path / "packet")
