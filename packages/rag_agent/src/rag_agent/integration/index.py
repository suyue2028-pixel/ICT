"""Persistent backend identities and proofs over the real, versioned RAG store.

The backend's UUID/hash/scope are never replaced by locally derived identities.
Original revision JSON and original content remain available to the HTTP adapter
for its independent citation check. No backend acknowledgement is emitted here.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID

import numpy as np

from ..schemas import AIScope, DocumentInput, Identifier


_SCHEMA = """
CREATE TABLE IF NOT EXISTS backend_document_mappings(
 is_demo INTEGER NOT NULL CHECK(is_demo IN (0,1)), document_id TEXT NOT NULL,
 content_hash TEXT NOT NULL, rag_document_id TEXT NOT NULL, rag_version INTEGER NOT NULL,
 index_id TEXT NOT NULL, operation TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
 metadata_json TEXT NOT NULL, PRIMARY KEY(is_demo,document_id,content_hash),
 FOREIGN KEY(rag_document_id,rag_version) REFERENCES document_versions(document_id,version));
CREATE UNIQUE INDEX IF NOT EXISTS backend_one_active_document
 ON backend_document_mappings(is_demo,document_id) WHERE active=1;
CREATE TABLE IF NOT EXISTS backend_index_proofs(
 index_id TEXT PRIMARY KEY, is_demo INTEGER NOT NULL CHECK(is_demo IN (0,1)),
 document_id TEXT NOT NULL, content_hash TEXT NOT NULL, operation TEXT NOT NULL,
 rag_document_id TEXT NOT NULL, rag_version INTEGER NOT NULL,
 FOREIGN KEY(rag_document_id,rag_version) REFERENCES document_versions(document_id,version));
CREATE TABLE IF NOT EXISTS backend_absence_proofs(
 index_id TEXT PRIMARY KEY, is_demo INTEGER NOT NULL CHECK(is_demo IN (0,1)),
 document_id TEXT NOT NULL, content_hash TEXT NOT NULL);
"""
_HASH = re.compile(r"[a-f0-9]{64}")
_IDENTIFIER = re.compile(r"(?:CVE-\d{4}-\d{4,}|GHSA-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}|PYSEC-[A-Z0-9-]+|OSV-[A-Z0-9-]+)", re.I)
_SOURCE_TYPES = {"vulnerability", "vendor", "community", "blog", "academic", "standard", "policy", "offline"}
_TYPES = {"vulnerability": "vulnerability", "advisory": "advisory", "article": "blog",
          "discussion": "blog", "paper": "paper", "standard": "standard",
          "policy": "policy", "regulation": "policy"}
_COMPLETENESS = {"full_text", "summary", "metadata_only", "partial", "unknown"}


def _identity(document_id, content_hash, is_demo):
    try:
        document_id = str(UUID(str(document_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("backend_document_id_invalid") from None
    if not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash):
        raise ValueError("backend_content_hash_invalid")
    if type(is_demo) is not bool:
        raise ValueError("backend_scope_invalid")
    return document_id, content_hash, is_demo


def _time(value, *, required=False):
    if value is None and not required:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("backend_timestamp_invalid") from None


def _index_id(document_id, content_hash, is_demo, operation):
    value = f"{int(is_demo)}:{document_id}:{content_hash}:{operation}"
    return "backend_" + hashlib.sha256(value.encode()).hexdigest()


def _fields(revision):
    """Copy only supported source records; never derive a missing CVSS score."""
    source_fields = revision.get("source_fields", {})
    if not isinstance(source_fields, dict):
        raise ValueError("backend_source_fields_invalid")
    records = []
    for field, value in source_fields.items():
        if field == "cvss" and isinstance(value, dict):
            vectors = value.get("vectors", [])
            if not vectors and "version" in value:
                vectors = [value]
            if not isinstance(vectors, list):
                raise ValueError("backend_cvss_records_invalid")
            for vector in vectors:
                if (isinstance(vector, dict) and vector.get("version") in {"2.0", "3.0", "3.1", "4.0"}
                        and type(vector.get("score")) in {int, float}
                        and np.isfinite(vector["score"]) and 0 <= vector["score"] <= 10):
                    records.append({"field": "cvss", "value": deepcopy(vector)})
        elif field in {"affected", "remediation", "poc", "affected_version", "fixed_version", "mitigation", "poc_reference"}:
            mapped = {"affected": "affected_version", "remediation": "mitigation", "poc": "poc_reference"}.get(field, field)
            if value not in (None, {}, [], ""):
                records.append({"field": mapped, "value": deepcopy(value)})
    return records


def _prepare(data):
    if not isinstance(data, dict) or data.get("is_current") is not True:
        raise ValueError("backend_document_not_current")
    document_id, digest, scope = _identity(data.get("document_id"), data.get("content_hash"), data.get("is_demo"))
    operation = data.get("operation", "add")
    if operation not in {"add", "update"}:
        raise ValueError("backend_upsert_operation_invalid")
    revision = data.get("revision")
    if not isinstance(revision, dict):
        raise ValueError("backend_revision_required")
    try:
        # Match backend.services.pipeline.payload_hash exactly; reject non-JSON/NaN.
        canonical = json.dumps(revision, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        metadata = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        raise ValueError("backend_revision_json_invalid") from None
    if hashlib.sha256(canonical.encode()).hexdigest() != digest:
        raise ValueError("backend_revision_hash_mismatch")
    for outer, inner in (("title", "title"), ("source_url", "source_url"), ("content", "content"),
                         ("raw_id", "external_id"), ("document_type", "document_type"),
                         ("content_completeness", "content_completeness")):
        if outer not in data or inner not in revision or data[outer] != revision[inner]:
            raise ValueError("backend_revision_metadata_mismatch")
    if type(revision.get("is_demo")) is not bool or revision["is_demo"] is not scope:
        raise ValueError("backend_revision_scope_mismatch")
    for outer, inner in (("published_time", "published_at"), ("source_updated_at", "source_updated_at")):
        if _time(data.get(outer)) != _time(revision.get(inner)):
            raise ValueError("backend_revision_metadata_mismatch")
    if "metadata" in data and data["metadata"] != revision.get("metadata_json"):
        raise ValueError("backend_revision_metadata_mismatch")
    if "source_reported_severity" in data and data["source_reported_severity"] != revision.get("severity"):
        raise ValueError("backend_revision_metadata_mismatch")
    if (not isinstance(data.get("source_name"), str) or not data["source_name"].strip()
            or len(data["source_name"]) > 100 or data.get("source_type") not in _SOURCE_TYPES):
        raise ValueError("backend_source_metadata_invalid")
    if data["document_type"] not in _TYPES or data["content_completeness"] not in _COMPLETENESS:
        raise ValueError("backend_document_metadata_invalid")
    content = data["content"]
    if not isinstance(content, str) or not content.strip() or len(content) > 200000:
        raise ValueError("backend_original_content_required")
    collected = _time(data.get("first_collected_at"), required=True)
    _time(data.get("version_created_at"))
    products = revision.get("products", [])
    aliases = revision.get("aliases", [])
    if (not isinstance(products, list) or any(not isinstance(p, str) for p in products)
            or not isinstance(aliases, list) or any(not isinstance(i, str) for i in aliases)):
        raise ValueError("backend_entities_invalid")
    values = [*aliases, revision.get("vulnerability_id") or "", revision.get("external_id") or ""]
    identifiers = [Identifier(namespace=value.split("-", 1)[0], value=value)
                   for value in dict.fromkeys(v.upper() for v in values if isinstance(v, str) and _IDENTIFIER.fullmatch(v))]
    raw_payload = {"backend_revision": deepcopy(revision), "source_fields": _fields(revision),
                   "backend_identity": {"document_id": document_id, "content_hash": digest, "is_demo": scope},
                   "content_completeness": data["content_completeness"]}
    raw = revision.get("raw")
    if isinstance(raw, dict) and isinstance(raw.get("metrics"), dict):
        raw_payload["cve"] = deepcopy(raw)
        # NVD extraction preserves full original CVSS records and CPE conditions.
        raw_payload["source_fields"] = [r for r in raw_payload["source_fields"] if r["field"] not in {"cvss", "affected_version"}]
    relevant = data.get("ai_relevant", True)
    if type(relevant) is not bool:
        raise ValueError("backend_relevance_invalid")
    document = DocumentInput(
        external_id=f"backend:{int(scope)}:{document_id}", source_name=data["source_name"],
        source_kind=data["source_type"], source_url=data["source_url"],
        doc_type=_TYPES[data["document_type"]], title=data["title"], text=content,
        published_at=_time(data.get("published_time")), source_modified_at=_time(data.get("source_updated_at")),
        collected_at=collected, identifiers=identifiers, product_entities=products,
        ai_scope=AIScope(relevant=relevant, reason="backend source-recorded relevance; not independent verification"),
        raw_payload=raw_payload,
    )
    metadata.update(document_id=document_id, content_hash=digest, is_demo=scope, operation=operation,
                    index_id=_index_id(document_id, digest, scope, operation))
    return document, metadata


class BackendIndex:
    def __init__(self, service):
        self.service, self.store = service, service.store
        # Multiple adapters over one service must serialize the full import/map step.
        if not hasattr(service, "_backend_sync_lock"):
            service._backend_sync_lock = asyncio.Lock()
        self._lock = service._backend_sync_lock
        with self.store.connection() as connection:
            connection.executescript(_SCHEMA)

    def _queryable(self, connection, row):
        if self.service.embedding.mode != "live":
            return False
        doc = connection.execute("SELECT d.current_version,v.payload FROM documents d JOIN document_versions v ON v.document_id=d.document_id AND v.version=d.current_version WHERE d.document_id=? AND NOT EXISTS (SELECT 1 FROM document_tombstones t WHERE t.document_id=d.document_id)", (row["rag_document_id"],)).fetchone()
        if doc is None or doc["current_version"] != row["rag_version"]:
            return False
        payload = json.loads(doc["payload"])
        metadata = json.loads(row["metadata_json"])
        if (payload.get("external_id") != f"backend:{row['is_demo']}:{row['document_id']}"
                or payload.get("text") != metadata["content"] or payload.get("title") != metadata["title"]
                or payload.get("source_url") != metadata["source_url"]
                or payload.get("source_name") != metadata["source_name"]):
            return False
        signature = self.service.embedding.signature()
        stored = connection.execute("SELECT value FROM schema_meta WHERE key='embedding_signature'").fetchone()
        if stored is None or json.loads(stored[0]) != signature:
            return False
        vectors = connection.execute("SELECT e.* FROM chunks c LEFT JOIN embeddings e ON e.chunk_id=c.chunk_id WHERE c.document_id=? AND c.version=?", (row["rag_document_id"], row["rag_version"])).fetchall()
        if not vectors:
            return False
        for vector in vectors:
            if (vector["chunk_id"] is None or vector["dimension"] != signature["dimension"]
                    or vector["model_id"] != signature["model_id"] or vector["revision"] != signature["revision"]
                    or vector["prefix_policy"] != signature["prefix_policy"]
                    or len(vector["vector"]) != 4 * signature["dimension"]):
                return False
            values = np.frombuffer(vector["vector"], dtype="<f4")
            if not np.isfinite(values).all() or not np.any(values):
                return False
        return True

    async def upsert(self, data: dict) -> str:
        document, metadata = _prepare(data)
        document_id, digest, scope = metadata["document_id"], metadata["content_hash"], int(metadata["is_demo"])
        async with self._lock:
            if self.service.embedding.mode != "live":
                raise ValueError("backend_real_embedding_required")
            with self.store.connection() as c:
                active = c.execute("SELECT * FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND active=1", (scope, document_id)).fetchone()
                previous = c.execute("SELECT 1 FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND content_hash=?", (scope, document_id, digest)).fetchone()
                if active and active["content_hash"] != digest:
                    if previous:
                        raise ValueError("backend_revision_superseded")
                    old_time = _time(json.loads(active["metadata_json"]).get("version_created_at"))
                    new_time = _time(metadata.get("version_created_at"))
                    if old_time is not None and new_time is not None and new_time <= old_time:
                        raise ValueError("backend_revision_superseded")
            result = await self.service.ingest([document])
            item = result.items[0]
            if item.status == "failed" or item.document_id is None or item.version is None:
                raise ValueError("backend_document_ingestion_failed")
            await self.service.enrich([item.document_id])
            async with self.service._write_lock:
                await asyncio.to_thread(self._save, metadata, item.document_id, item.version)
        return metadata["index_id"]

    def _save(self, metadata, rag_id, version):
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                scope, doc_id, digest = int(metadata["is_demo"]), metadata["document_id"], metadata["content_hash"]
                candidate = {"rag_document_id": rag_id, "rag_version": version, "is_demo": scope,
                             "document_id": doc_id, "metadata_json": json.dumps(metadata, ensure_ascii=False)}
                if not self._queryable(c, candidate):
                    raise ValueError("backend_index_not_queryable")
                old = c.execute("SELECT rag_document_id FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND active=1", (scope, doc_id)).fetchone()
                c.execute("UPDATE backend_document_mappings SET active=0 WHERE is_demo=? AND document_id=?", (scope, doc_id))
                c.execute("INSERT INTO backend_document_mappings VALUES (?,?,?,?,?,?,?,1,?) ON CONFLICT(is_demo,document_id,content_hash) DO UPDATE SET rag_document_id=excluded.rag_document_id,rag_version=excluded.rag_version,index_id=excluded.index_id,operation=excluded.operation,active=1,metadata_json=excluded.metadata_json", (scope, doc_id, digest, rag_id, version, metadata["index_id"], metadata["operation"], candidate["metadata_json"]))
                c.execute("INSERT OR REPLACE INTO backend_index_proofs VALUES (?,?,?,?,?,?,?)", (metadata["index_id"], scope, doc_id, digest, metadata["operation"], rag_id, version))
                if old and old["rag_document_id"] != rag_id:
                    c.execute("INSERT OR IGNORE INTO document_tombstones VALUES (?)", (old["rag_document_id"],))
                    c.execute("UPDATE schema_meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    async def delete(self, document_id: str, content_hash: str, is_demo: bool) -> str:
        document_id, content_hash, is_demo = _identity(document_id, content_hash, is_demo)
        async with self._lock:
            async with self.service._write_lock:
                return await asyncio.to_thread(self._delete, document_id, content_hash, is_demo)

    def _assert_absent(self, connection, scope, document_id):
        """Prove absence from actual active mappings and orphan current imports."""
        if connection.execute("SELECT 1 FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND active=1", (scope, document_id)).fetchone():
            raise ValueError("backend_document_still_active")
        rows = connection.execute("SELECT v.payload FROM document_versions v JOIN documents d ON d.document_id=v.document_id AND d.current_version=v.version WHERE NOT EXISTS (SELECT 1 FROM document_tombstones t WHERE t.document_id=d.document_id)")
        for row in rows:
            payload = json.loads(row["payload"])
            raw = payload.get("raw_payload") or {}
            identity = raw.get("backend_identity", {})
            if (payload.get("external_id") == f"backend:{scope}:{document_id}"
                    or (identity.get("document_id") == document_id
                        and identity.get("is_demo") is bool(scope))):
                raise ValueError("backend_orphan_import_still_active")

    def _delete(self, document_id, content_hash, is_demo):
        scope = int(is_demo)
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                active = c.execute("SELECT * FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND active=1", (scope, document_id)).fetchone()
                if active and active["content_hash"] != content_hash:
                    raise ValueError("backend_delete_revision_mismatch")
                row = c.execute("SELECT * FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND content_hash=?", (scope, document_id, content_hash)).fetchone()
                if row is None:
                    self._assert_absent(c, scope, document_id)
                    proof_id = _index_id(document_id, content_hash, is_demo, "delete")
                    c.execute("INSERT OR REPLACE INTO backend_absence_proofs VALUES (?,?,?,?)", (proof_id, scope, document_id, content_hash))
                    c.execute("COMMIT")
                    return proof_id
                current = c.execute("SELECT current_version FROM documents WHERE document_id=?", (row["rag_document_id"],)).fetchone()
                if current is None or current[0] != row["rag_version"]:
                    raise ValueError("backend_delete_revision_mismatch")
                proof_id = _index_id(document_id, content_hash, is_demo, "delete")
                metadata = json.loads(row["metadata_json"])
                metadata.update(index_id=proof_id, operation="delete")
                before = c.total_changes
                c.execute("INSERT OR IGNORE INTO document_tombstones VALUES (?)", (row["rag_document_id"],))
                inserted_tombstone = c.total_changes > before
                c.execute("UPDATE backend_document_mappings SET active=0,index_id=?,operation='delete',metadata_json=? WHERE is_demo=? AND document_id=? AND content_hash=?", (proof_id, json.dumps(metadata, ensure_ascii=False), scope, document_id, content_hash))
                c.execute("INSERT OR REPLACE INTO backend_index_proofs VALUES (?,?,?,?,?,?,?)", (proof_id, scope, document_id, content_hash, "delete", row["rag_document_id"], row["rag_version"]))
                if inserted_tombstone or row["active"]:
                    c.execute("UPDATE schema_meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
                c.execute("COMMIT")
                return proof_id
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def mappings(self, is_demo: bool) -> dict[tuple[str, int], dict]:
        if type(is_demo) is not bool:
            raise ValueError("backend_scope_invalid")
        with self.store.connection() as c:
            c.execute("BEGIN")
            rows = c.execute("SELECT m.* FROM backend_document_mappings m JOIN documents d ON d.document_id=m.rag_document_id AND d.current_version=m.rag_version WHERE m.is_demo=? AND m.active=1 AND NOT EXISTS (SELECT 1 FROM document_tombstones t WHERE t.document_id=m.rag_document_id)", (int(is_demo),)).fetchall()
            result = {(r["rag_document_id"], r["rag_version"]): json.loads(r["metadata_json"]) for r in rows if self._queryable(c, r)}
            c.execute("COMMIT")
            return result

    def verify(self, request: dict) -> dict:
        if not isinstance(request, dict) or request.get("schema_version") != "1":
            raise ValueError("backend_proof_schema_invalid")
        document_id, digest, scope = _identity(request.get("document_id"), request.get("content_hash"), request.get("is_demo"))
        operation, proof_id = request.get("operation"), request.get("index_id")
        if operation not in {"add", "update", "delete"} or not isinstance(proof_id, str) or not 1 <= len(proof_id) <= 200:
            raise ValueError("backend_proof_request_invalid")
        with self.store.connection() as c:
            c.execute("BEGIN")
            proof = c.execute("SELECT * FROM backend_index_proofs WHERE index_id=?", (proof_id,)).fetchone()
            if proof is None:
                absence = c.execute("SELECT * FROM backend_absence_proofs WHERE index_id=?", (proof_id,)).fetchone()
                if absence is None:
                    raise KeyError("backend_index_proof_not_found")
                if (operation != "delete" or absence["document_id"] != document_id
                        or absence["content_hash"] != digest or absence["is_demo"] != int(scope)):
                    raise ValueError("backend_index_proof_mismatch")
                self._assert_absent(c, int(scope), document_id)
                c.execute("COMMIT")
                return {"schema_version": "1", "document_id": document_id, "content_hash": digest,
                        "index_id": proof_id, "is_demo": scope, "queryable": False, "removed": True}
            if (proof["document_id"] != document_id or proof["content_hash"] != digest
                    or proof["is_demo"] != int(scope) or proof["operation"] != operation):
                raise ValueError("backend_index_proof_mismatch")
            row = c.execute("SELECT * FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND content_hash=?", (int(scope), document_id, digest)).fetchone()
            if (row is None or row["index_id"] != proof_id or row["operation"] != operation
                    or row["rag_document_id"] != proof["rag_document_id"] or row["rag_version"] != proof["rag_version"]):
                raise ValueError("backend_index_proof_superseded")
            if operation == "delete":
                active = c.execute("SELECT 1 FROM backend_document_mappings WHERE is_demo=? AND document_id=? AND active=1", (int(scope), document_id)).fetchone()
                tombstone = c.execute("SELECT 1 FROM document_tombstones WHERE document_id=?", (row["rag_document_id"],)).fetchone()
                if active or row["active"] or tombstone is None:
                    raise ValueError("backend_index_delete_not_effective")
                queryable, removed = False, True
            else:
                if not row["active"] or not self._queryable(c, row):
                    raise ValueError("backend_index_not_queryable")
                queryable, removed = True, False
            c.execute("COMMIT")
        return {"schema_version": "1", "document_id": document_id, "content_hash": digest,
                "index_id": proof_id, "is_demo": scope, "queryable": queryable, "removed": removed}
