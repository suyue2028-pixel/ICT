from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path
import numpy as np
from ..schemas import Assertion, DocumentInput, EvidenceDetail, Relation

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO schema_meta VALUES ('schema_version','1'),('generation','0');
CREATE TABLE IF NOT EXISTS documents(document_id TEXT PRIMARY KEY, current_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS document_versions(document_id TEXT NOT NULL, version INTEGER NOT NULL,
 content_hash TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(document_id,version),
 FOREIGN KEY(document_id) REFERENCES documents(document_id));
CREATE TABLE IF NOT EXISTS chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
 ordinal INTEGER NOT NULL, text TEXT NOT NULL, start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
 FOREIGN KEY(document_id,version) REFERENCES document_versions(document_id,version));
CREATE TABLE IF NOT EXISTS embeddings(chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id),
 vector BLOB NOT NULL, dimension INTEGER NOT NULL, model_id TEXT NOT NULL, revision TEXT NOT NULL, prefix_policy TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(evidence_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
 payload TEXT NOT NULL, FOREIGN KEY(document_id,version) REFERENCES document_versions(document_id,version));
CREATE TABLE IF NOT EXISTS assertions(assertion_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
 payload TEXT NOT NULL, FOREIGN KEY(document_id,version) REFERENCES document_versions(document_id,version));
CREATE TABLE IF NOT EXISTS entities(entity_id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS relations(relation_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
 subject TEXT NOT NULL, object TEXT NOT NULL, payload TEXT NOT NULL,
 FOREIGN KEY(document_id,version) REFERENCES document_versions(document_id,version));
CREATE INDEX IF NOT EXISTS chunks_version ON chunks(document_id,version);
CREATE INDEX IF NOT EXISTS evidence_version ON evidence(document_id,version);
"""


@dataclass
class Snapshot:
    generation: int
    signature: dict | None
    documents: dict[str, DocumentInput]
    versions: dict[str, int]
    chunks: list[dict]
    evidence: dict[str, EvidenceDetail]
    assertions: list[Assertion]
    relations: list[Relation]


class SQLiteStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
            if version != "1":
                raise ValueError("unsupported database schema")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def current(self, doc_id):
        with self.connection() as c:
            r = c.execute("SELECT v.version,v.content_hash FROM document_versions v JOIN documents d ON d.document_id=v.document_id AND d.current_version=v.version WHERE d.document_id=?", (doc_id,)).fetchone()
            return dict(r) if r else None

    def check_signature(self, signature, connection=None):
        if connection is None:
            with self.connection() as c:
                return self.check_signature(signature, c)
        row = connection.execute("SELECT value FROM schema_meta WHERE key='embedding_signature'").fetchone()
        if row and json.loads(row[0]) != signature:
            raise ValueError("embedding index mismatch: rebuild into a new database; mixed vectors are forbidden")

    def commit_version(self, doc, doc_id, version, content_hash, chunks, vectors, signature, evidence, assertions):
        if vectors.shape != (len(chunks), signature["dimension"]) or not np.isfinite(vectors).all():
            raise ValueError("invalid embedding shape or non-finite vectors")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                self.check_signature(signature, c)
                current = c.execute("SELECT current_version FROM documents WHERE document_id=?", (doc_id,)).fetchone()
                if version != ((current[0] if current else 0) + 1):
                    raise ValueError("concurrent document update; retry ingestion")
                c.execute("INSERT OR IGNORE INTO schema_meta VALUES ('embedding_signature',?)", (json.dumps(signature, sort_keys=True),))
                c.execute("INSERT OR IGNORE INTO documents VALUES (?,?)", (doc_id, version))
                c.execute("INSERT INTO document_versions VALUES (?,?,?,?)", (doc_id, version, content_hash, doc.model_dump_json()))
                for i, chunk in enumerate(chunks):
                    chunk_id = f"{doc_id}:v{version}:c{i}"
                    c.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?)", (chunk_id, doc_id, version, i, chunk.text, chunk.start, chunk.end))
                    blob = np.asarray(vectors[i], dtype="<f4").tobytes()
                    c.execute("INSERT INTO embeddings VALUES (?,?,?,?,?,?)", (chunk_id, blob, signature["dimension"], signature["model_id"], signature["revision"], signature["prefix_policy"]))
                for ev in evidence:
                    c.execute("INSERT INTO evidence VALUES (?,?,?,?)", (ev.evidence_id, doc_id, version, ev.model_dump_json()))
                for a in assertions:
                    c.execute("INSERT INTO assertions VALUES (?,?,?,?)", (a.assertion_id, doc_id, version, a.model_dump_json()))
                c.execute("UPDATE documents SET current_version=? WHERE document_id=?", (version, doc_id))
                c.execute("UPDATE schema_meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def snapshot(self):
        # All tables are read inside one SQLite snapshot, including generation.
        with self.connection() as c:
            c.execute("BEGIN")
            meta = dict(c.execute("SELECT key,value FROM schema_meta").fetchall())
            rows = c.execute("SELECT v.* FROM document_versions v JOIN documents d ON v.document_id=d.document_id AND v.version=d.current_version").fetchall()
            docs = {r["document_id"]: DocumentInput.model_validate_json(r["payload"]) for r in rows}
            versions = {r["document_id"]: r["version"] for r in rows}
            chunks = []
            for r in c.execute("SELECT k.*,e.vector,e.dimension FROM chunks k JOIN documents d ON k.document_id=d.document_id AND k.version=d.current_version JOIN embeddings e ON e.chunk_id=k.chunk_id"):
                v = np.frombuffer(r["vector"], dtype="<f4").copy()
                if len(v) != r["dimension"] or not np.isfinite(v).all():
                    raise ValueError("corrupt stored embedding")
                chunks.append({**dict(r), "vector": v})
            def records(table, cls):
                return [cls.model_validate_json(r[0]) for r in c.execute(f"SELECT t.payload FROM {table} t JOIN documents d ON t.document_id=d.document_id AND t.version=d.current_version")]
            evidence = {e.evidence_id: e for e in records("evidence", EvidenceDetail)}
            assertions = records("assertions", Assertion)
            relations = records("relations", Relation)
            c.execute("COMMIT")
        return Snapshot(int(meta["generation"]), json.loads(meta["embedding_signature"]) if "embedding_signature" in meta else None,
                        docs, versions, chunks, evidence, assertions, relations)

    def get_evidence(self, evidence_id):
        with self.connection() as c:
            r = c.execute("SELECT payload FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
            if not r:
                raise KeyError(evidence_id)
            return EvidenceDetail.model_validate_json(r[0])

    def add_relations(self, relations, entities):
        added = retained = 0
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                for entity, kind, label in entities:
                    c.execute("INSERT OR IGNORE INTO entities VALUES (?,?,?)", (entity, kind, label))
                for r in relations:
                    row = c.execute("SELECT current_version FROM documents WHERE document_id=?", (r.document_id,)).fetchone()
                    if not row or row[0] != r.version:
                        raise ValueError("document changed during enrichment; retry")
                    before = c.total_changes
                    c.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?,?,?,?)", (r.relation_id, r.document_id, r.version, r.subject, r.object, r.model_dump_json()))
                    if c.total_changes > before:
                        added += 1
                    else:
                        retained += 1
                if added:
                    c.execute("UPDATE schema_meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return added, retained
