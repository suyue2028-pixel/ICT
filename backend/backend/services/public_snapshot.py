"""Read-only export and transactional, conservative import of public intelligence.

No SQL/code/credentials/index files are accepted from a packet. Original payloads
are kept byte-semantically intact: dropping an unapproved field rejects the whole
document instead of silently changing its hash or quote offsets.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.collectors.registry import SOURCE_DEFINITIONS
from backend.models import (AssociationEvidence, DataSource, Document, DocumentRevision,
    Evidence, KnowledgeEvent, SnapshotImport, SnapshotMember, SnapshotSource,
    Vulnerability, VulnerabilityAlias, VulnerabilityDocument)
from backend.schemas.contracts import RawRecord
from backend.schemas.public_snapshot import (AssociationPacket, DocumentPacket,
    EvidencePacket, FilePacket, Manifest, RevisionPacket, SourcePacket, VulnerabilityPacket)
from backend.services.pipeline import _aliases, _attach_vulnerabilities, payload_hash, relevance
from backend.services.traceability import association_candidates, locate_quote

TOOL_VERSION = "1.0.0"
REQUIRED_MIGRATION = "0010_public_snapshots"
MAX_LINE = 16 * 1024 * 1024
MAX_PACKAGE = 256 * 1024 * 1024
PUBLIC_HOSTS = {
    "osv": {"osv.dev"}, "nvd": {"nvd.nist.gov"},
    "cisco": {"sec.cloudapps.cisco.com", "tools.cisco.com"}, "sans": {"isc.sans.edu"},
    "trailofbits": {"blog.trailofbits.com"}, "arxiv": {"arxiv.org", "export.arxiv.org"},
    "crossref": {"doi.org"}, "huggingface": {"discuss.huggingface.co"},
    "nvidia": {"www.nvidia.com", "nvidia.custhelp.com"},
    "nist": {"airc.nist.gov", "www.nist.gov", "csrc.nist.gov"},
    "nist_drafts": {"csrc.nist.gov", "www.nist.gov"},
    "govuk": {"www.gov.uk"}, "govuk_search": {"www.gov.uk"}, "china_policy": {"www.gov.cn"},
}
# Reviewed public provider schemas. Unknown envelopes fail closed, never strip
# fields from a hashed payload. Nested credential keys and all strings are scanned.
FEED_KEYS = {"title", "description", "link", "guid", "pubDate", "category", "source",
    "{http://purl.org/rss/1.0/modules/content/}encoded", "{http://purl.org/dc/elements/1.1/}creator",
    "{http://www.discourse.org/}topicArchived", "{http://www.discourse.org/}topicClosed",
    "{http://www.discourse.org/}topicPinned"}
GOV_KEYS = set("analytics_identifier base_path content_id description details document_type first_published_at links locale phase public_updated_at publishing_app publishing_request_id publishing_scheduled_at rendering_app scheduled_publishing_delay_seconds schema_name title updated_at withdrawn_notice".split())
RAW_KEYS = {
    "osv": set("affected aliases database_specific details id modified published references related schema_version severity summary withdrawn credits".split()),
    "nvd": set("affected configurations cveTags descriptions id lastModified metrics published references sourceIdentifier vulnStatus weaknesses evaluatorComment evaluatorSolution evaluatorImpact vendorComments".split()),
    "arxiv": {"metadata", "abstract"},
    "crossref": set("DOI ISBN ISSN URL abstract alternative-id archive article-number assertion author container-title content-domain created deposited edition-number editor event funder indexed is-referenced-by-count isbn-type issn-type issue issued journal-issue language license link member page prefix published published-online published-print publisher publisher-location reference reference-count references-count relation resource score short-container-title source special_numbering subtitle title type update-policy updated-by volume".split()),
    "huggingface": FEED_KEYS | {"topic", "post", "search_query"},
    "cisco": FEED_KEYS, "sans": FEED_KEYS, "trailofbits": FEED_KEYS,
    "govuk": GOV_KEYS, "govuk_search": GOV_KEYS,
    "nvidia": {"bulletin id", "cve identifier(s)", "last updated", "publish date", "severity", "title"},
    "nist": {"html"}, "china_policy": {"html"},
    "nist_drafts": {"content", "id", "link", "published", "summary", "title", "updated"},
}
METADATA_KEYS = set("references status authors date_precision doi full_text_fetched license publisher arxiv_id categories coverage feed_url post_id search_query topic_id effective_at jurisdiction legal_status last_modified_header standard_id bulletin_id bulletin_url full_bulletin_fetched publication_date updated_date comment_period updated_date_precision".split())
DIMENSIONS = {"affected", "cwe", "cvss", "remediation", "analysis", "papers", "poc", "exploitation"}
VALUE_KEYS = {"cpe_matches", "ids", "items", "references", "vectors"}
SECRET_KEYS = {"apikey", "accesstoken", "refreshtoken", "password", "passwd", "secret",
    "authorization", "clientsecret", "privatekey", "adminkey", "adminapikey", "servicetoken",
    "deepseekapikey", "agenttooltoken", "agentwritetoken", "agentservicetoken", "cookie", "setcookie"}
TOKEN_PATTERNS = [re.compile(r"sk-[A-Za-z0-9_-]{24,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"[?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|signature|secret)=[^&#\s]{12,}", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{24,}", re.I),
    re.compile(r"https?://[^\s/@:]+:[^\s/@]+@", re.I)]


class SnapshotError(ValueError):
    """Only controlled codes and safe record IDs/path names may appear in errors."""
    def __init__(self, code, record=None):
        self.code, self.record = code, str(record) if record is not None else None
        super().__init__(code + (":" + self.record if self.record else ""))


def configured_secrets(settings):
    values = []
    for name, field in type(settings).model_fields.items():
        value = getattr(settings, name)
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
            if value and len(value) >= 8:
                values.append(value)
    return values


def scan(value, secrets=(), *, record=None, depth=0):
    if depth > 40:
        raise SnapshotError("json_nesting_limit", record)
    if isinstance(value, dict):
        for key, item in value.items():
            if re.sub(r"[^a-z0-9]", "", key.lower()) in SECRET_KEYS:
                raise SnapshotError("suspected_sensitive_content", record)
            scan(key, secrets, record=record, depth=depth + 1)
            scan(item, secrets, record=record, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            scan(item, secrets, record=record, depth=depth + 1)
    elif isinstance(value, str):
        if any(p.search(value) for p in TOKEN_PATTERNS) or any(s in value for s in secrets):
            raise SnapshotError("suspected_sensitive_content", record)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("duplicate_json_key")
        result[key] = value
    return result


def _parse(raw):
    try:
        return json.loads(raw, object_pairs_hook=_no_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(SnapshotError("non_finite_json")))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, SnapshotError):
            raise
        raise SnapshotError("invalid_json") from None


def source_key(source):
    return f"public:{source.adapter}:{source.id}"


def _source_valid(source):
    if (source.adapter not in PUBLIC_HOSTS or source.category != SOURCE_DEFINITIONS[source.adapter]["category"]
            or source.key != f"public:{source.adapter}:{source.id}" or source.config):
        raise SnapshotError("source_not_public_allowlisted", source.id)


def _public_url(url, adapter, record):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"https", "http"} or parsed.hostname not in PUBLIC_HOSTS[adapter]
                or parsed.username or parsed.password or parsed.port not in {None, 80, 443}):
            raise ValueError()
    except ValueError:
        raise SnapshotError("source_url_not_allowlisted", record) from None


def _payload_valid(payload, adapter, external_id, digest, record):
    if set(payload) - set(RawRecord.model_fields):
        raise SnapshotError("payload_field_not_allowlisted", record)
    try:
        parsed = RawRecord.model_validate(payload)
    except ValueError:
        raise SnapshotError("invalid_raw_record", record) from None
    if parsed.is_demo or parsed.external_id != external_id:
        raise SnapshotError("payload_scope_or_identity_mismatch", record)
    _public_url(str(parsed.source_url), adapter, record)
    if (set(parsed.raw) - RAW_KEYS[adapter] or set(parsed.metadata_json) - METADATA_KEYS
            or set(parsed.source_fields) - DIMENSIONS
            or any(set(value) - VALUE_KEYS for value in parsed.source_fields.values())):
        raise SnapshotError("payload_field_not_allowlisted", record)
    if payload_hash(payload) != digest:
        raise SnapshotError("revision_hash_mismatch", record)
    return parsed


def _packet_valid(packet, source, secrets=()):
    scan(packet.model_dump(mode="json"), secrets, record=packet.id)
    versions = {}
    for revision in packet.revisions:
        if revision.content_hash in versions:
            raise SnapshotError("duplicate_revision", packet.id)
        _payload_valid(revision.payload, source.adapter, packet.external_id, revision.content_hash, packet.id)
        versions[revision.content_hash] = revision
    if packet.current_hash not in versions:
        raise SnapshotError("current_revision_missing", packet.id)
    evidence_keys, association_keys = set(), set()
    for item in packet.evidence:
        payload = versions.get(item.content_hash)
        key = (item.content_hash, item.field, item.method)
        if payload is None or key in evidence_keys:
            raise SnapshotError("evidence_reference_or_duplicate", packet.id)
        evidence_keys.add(key)
        payload = payload.payload
        if item.source_url != payload["source_url"]:
            raise SnapshotError("evidence_source_mismatch", packet.id)
        if item.method == "source_reported":
            expected = payload.get("source_fields", {}).get(item.field)
            if (expected != item.value or item.locator != "source_fields." + item.field
                    or item.excerpt != json.dumps(item.value, ensure_ascii=False)[:2000]):
                raise SnapshotError("evidence_payload_mismatch", packet.id)
        elif item.method.startswith("external:") and item.excerpt:
            try:
                locate_quote(payload, item.excerpt, item.locator)
            except Exception:
                raise SnapshotError("evidence_locator_invalid", packet.id) from None
        else:
            raise SnapshotError("evidence_method_not_supported", packet.id)
    for item in packet.associations:
        revision = versions.get(item.content_hash)
        key = (item.content_hash, item.identifier)
        if revision is None or key in association_keys:
            raise SnapshotError("association_reference_or_duplicate", packet.id)
        association_keys.add(key)
        expected = association_candidates(revision.payload, revision.payload["source_url"])
        if {k: getattr(item, k) for k in ("identifier", "method", "locator", "excerpt", "group_identifiers")} not in expected:
            raise SnapshotError("association_proof_mismatch", packet.id)


@dataclass
class Bundle:
    manifest: Manifest
    manifest_sha256: str
    sources: list[SourcePacket]
    documents: list[DocumentPacket]
    vulnerabilities: list[VulnerabilityPacket]


def packet_counts(sources, documents, vulnerabilities):
    return {"sources": len(sources), "documents": len(documents), "vulnerabilities": len(vulnerabilities),
        "revisions": sum(len(d.revisions) for d in documents),
        "evidence": sum(len(d.evidence) for d in documents),
        "associations": sum(len(d.associations) for d in documents),
        "aliases": sum(len(v.aliases) for v in vulnerabilities),
        "links": sum(len(d.vulnerability_ids) for d in documents)}


def validate_bundle(bundle, secrets=()):
    sources = {s.key: s for s in bundle.sources}
    vulns = {v.id: v for v in bundle.vulnerabilities}
    if (len(sources) != len(bundle.sources) or len({s.id for s in bundle.sources}) != len(sources)
            or len(vulns) != len(bundle.vulnerabilities)
            or len({d.id for d in bundle.documents}) != len(bundle.documents)
            or len({(d.source_key, d.external_id) for d in bundle.documents}) != len(bundle.documents)):
        raise SnapshotError("duplicate_record")
    seen_aliases = set()
    for source in bundle.sources:
        _source_valid(source)
        scan(source.model_dump(mode="json"), secrets, record=source.id)
    linked, justified_aliases = set(), set()
    for document in bundle.documents:
        source = sources.get(document.source_key)
        if source is None or len(set(document.vulnerability_ids)) != len(document.vulnerability_ids):
            raise SnapshotError("document_reference_invalid", document.id)
        _packet_valid(document, source, secrets)
        for revision in document.revisions:
            justified_aliases.update(i["identifier"] for i in association_candidates(
                revision.payload, revision.payload["source_url"]))
        current = next(r for r in document.revisions if r.content_hash == document.current_hash)
        ids = {i["identifier"] for i in association_candidates(current.payload, current.payload["source_url"])}
        represented = set()
        for vid in document.vulnerability_ids:
            if vid not in vulns or not ids.intersection(vulns[vid].aliases):
                raise SnapshotError("vulnerability_link_missing_proof", document.id)
            linked.add(vid)
            represented.update(vulns[vid].aliases)
        if not ids.issubset(represented):
            raise SnapshotError("current_identifier_link_missing", document.id)
    for vuln in bundle.vulnerabilities:
        scan(vuln.model_dump(mode="json"), secrets, record=vuln.id)
        if (not vuln.aliases or vuln.identifier not in vuln.aliases
                or not set(vuln.aliases).issubset(justified_aliases)
                or len(set(vuln.aliases)) != len(vuln.aliases)
                or seen_aliases.intersection(vuln.aliases) or vuln.id not in linked):
            raise SnapshotError("vulnerability_alias_or_reference_invalid", vuln.id)
        seen_aliases.update(vuln.aliases)
    if packet_counts(bundle.sources, bundle.documents, bundle.vulnerabilities) != bundle.manifest.counts:
        raise SnapshotError("manifest_counts_mismatch")
    return bundle


def read_bundle(directory, secrets=()):
    directory = Path(directory).resolve()
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 1024 * 1024:
        raise SnapshotError("invalid_manifest_file")
    raw = manifest_path.read_bytes()
    try:
        manifest = Manifest.model_validate(_parse(raw))
    except ValueError:
        raise SnapshotError("invalid_manifest_format") from None
    scan(manifest.model_dump(mode="json"), secrets, record="manifest.json")
    allowed = {"manifest.json"} | {f.path for f in manifest.files}
    if len(allowed) != len(manifest.files) + 1 or {p.name for p in directory.iterdir()} != allowed:
        raise SnapshotError("unlisted_or_duplicate_package_file")
    if sum(f.bytes for f in manifest.files) > MAX_PACKAGE:
        raise SnapshotError("package_size_limit")
    records = {"sources": [], "documents": [], "vulnerabilities": []}
    models = {"sources": SourcePacket, "documents": DocumentPacket, "vulnerabilities": VulnerabilityPacket}
    for entry in manifest.files:
        path = directory / entry.path
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory)
                or path.stat().st_size != entry.bytes or not entry.path.startswith(entry.kind + "-")):
            raise SnapshotError("invalid_packet_file", entry.path)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry.sha256:
            raise SnapshotError("file_checksum_mismatch", entry.path)
        lines = content.splitlines()
        if len(lines) != entry.records:
            raise SnapshotError("file_record_count_mismatch", entry.path)
        for line in lines:
            if len(line) > MAX_LINE or not line:
                raise SnapshotError("invalid_record_size", entry.path)
            try:
                records[entry.kind].append(models[entry.kind].model_validate(_parse(line)))
            except ValueError as error:
                if isinstance(error, SnapshotError):
                    raise SnapshotError(error.code, entry.path) from None
                raise SnapshotError("invalid_packet_schema", entry.path) from None
    return validate_bundle(Bundle(manifest, hashlib.sha256(raw).hexdigest(), **records), secrets)


def write_bundle(directory, sources, documents, vulnerabilities, *, snapshot_id=None, exclusions=None,
                 secrets=(), shard_bytes=8 * 1024 * 1024):
    directory = Path(directory)
    if directory.exists():
        raise SnapshotError("output_directory_already_exists")
    if not 1024 <= shard_bytes <= 16 * 1024 * 1024:
        raise SnapshotError("invalid_shard_size")
    now = datetime.now(timezone.utc)
    manifest = Manifest(format="cit-public-intelligence", format_version=1,
        snapshot_id=snapshot_id or now.strftime("public-%Y%m%dT%H%M%SZ").lower() + "-" + uuid4().hex[:8],
        generated_at=now, required_migration=REQUIRED_MIGRATION, exporter_version=TOOL_VERSION,
        scope="known-public-sources-real-data-only", files=[],
        counts=packet_counts(sources, documents, vulnerabilities), exclusions=dict(exclusions or {}),
        limitations=["Periodic public snapshot, not a live shared database; hashes are integrity checks, not signatures.",
            "Public availability is not a blanket redistribution licence; retain publisher notices, use for team research and check source terms before broader redistribution.",
            "Original payload/hash/key order retained; unknown provider fields or suspected secrets reject the whole document.",
            "No users, conversations, tasks, outbox, credentials, model calls, embeddings or index completion claims.",
            "Source timestamps/first discovery belong to the exporting installation. Import time is stored separately.",
            "Only strictly later source_updated_at promotes a different current version; ambiguous ordering remains historical.",
            "Aliases/links are reconstructed by existing conservative ingestion rules; local entity UUIDs may differ."])
    bundle = validate_bundle(Bundle(manifest, "", sources, documents, vulnerabilities), secrets)
    # Validate everything before creating output; no partial sensitive packet is written.
    directory.mkdir(parents=True)
    for kind, packets in (("sources", sources), ("documents", documents), ("vulnerabilities", vulnerabilities)):
        shards, buffer, size = [], [], 0
        for packet in packets:
            encoded = (_json(packet.model_dump(mode="json")) + "\n").encode("utf-8")
            if len(encoded) > MAX_LINE:
                raise SnapshotError("record_too_large", packet.id)
            if buffer and size + len(encoded) > shard_bytes:
                shards.append(buffer); buffer, size = [], 0
            buffer.append(encoded); size += len(encoded)
        if buffer or not shards:
            shards.append(buffer)
        for number, lines in enumerate(shards, 1):
            data = b"".join(lines)
            name = f"{kind}-{number:04d}.jsonl"
            (directory / name).write_bytes(data)
            manifest.files.append(FilePacket(path=name, kind=kind, records=len(lines),
                bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    # Revalidate appended file models, then verify the exact on-disk package.
    manifest = Manifest.model_validate(manifest.model_dump())
    (directory / "manifest.json").write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return read_bundle(directory, secrets)


def export_snapshot(engine, directory, *, secrets=(), snapshot_id=None, shard_bytes=8 * 1024 * 1024,
                    rejected_records=None):
    """One PostgreSQL REPEATABLE READ, READ ONLY transaction covers every table."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with Session(connection) as db:
            sources = {s.id: s for s in db.scalars(select(DataSource).order_by(DataSource.id))}
            selected, excluded, source_packets = [], Counter(), {}
            docs = list(db.scalars(select(Document).order_by(Document.id)))
            ids = [d.id for d in docs]
            revisions, evidence, associations = defaultdict(list), defaultdict(list), defaultdict(list)
            for model, target in ((DocumentRevision, revisions), (Evidence, evidence), (AssociationEvidence, associations)):
                for row in db.scalars(select(model).where(model.document_id.in_(ids)).order_by(model.id)):
                    target[row.document_id].append(row)
            links = defaultdict(list)
            for row in db.scalars(select(VulnerabilityDocument).where(VulnerabilityDocument.document_id.in_(ids))):
                links[row.document_id].append(row.vulnerability_id)
            for doc in docs:
                source = sources[doc.source_id]
                if doc.is_demo or source.adapter == "offline":
                    excluded["demo"] += 1; continue
                if doc.deleted_at is not None:
                    excluded["deleted"] += 1; continue
                if source.adapter not in PUBLIC_HOSTS or source.config:
                    excluded["unapproved_source_or_config"] += 1; continue
                sp = SourcePacket(id=source.id, key=source_key(source), name=source.name,
                    adapter=source.adapter, category=source.category, config={})
                _source_valid(sp)
                packet = DocumentPacket(id=doc.id, source_key=sp.key, external_id=doc.external_id,
                    current_hash=doc.content_hash, created_at=doc.created_at, updated_at=doc.updated_at,
                    first_collected_at=doc.first_collected_at, last_checked_at=doc.last_checked_at,
                    content_changed_at=doc.content_changed_at,
                    revisions=[RevisionPacket(content_hash=r.content_hash, payload=r.payload,
                        created_at=r.created_at, updated_at=r.updated_at) for r in revisions[doc.id]],
                    evidence=[EvidencePacket(**{k: getattr(e, k) for k in EvidencePacket.model_fields}) for e in evidence[doc.id]],
                    associations=[AssociationPacket(**{k: getattr(e, k) for k in AssociationPacket.model_fields}) for e in associations[doc.id]],
                    vulnerability_ids=sorted(links[doc.id], key=str))
                current = next((r for r in packet.revisions if r.content_hash == doc.content_hash), None)
                if current is None or current.payload != doc.raw_payload:
                    raise SnapshotError("current_document_revision_mismatch", doc.id)
                parsed = RawRecord.model_validate(current.payload)
                if any(getattr(doc, field) != getattr(parsed, field) for field in
                       ("title", "content", "document_type", "published_at", "source_updated_at",
                        "metadata_json", "content_completeness")) or doc.source_url != str(parsed.source_url):
                    raise SnapshotError("current_document_payload_mismatch", doc.id)
                try:
                    _packet_valid(packet, sp, secrets)
                except SnapshotError as error:
                    if error.code != "suspected_sensitive_content":
                        raise
                    # Keep the ENTIRE document and all its versions/evidence out
                    # of the packet; never redact a hashed original in place.
                    excluded["suspected_sensitive_document"] += 1
                    if rejected_records is not None:
                        rejected_records.append({"record_id": str(doc.id), "code": error.code})
                    continue
                selected.append(packet); source_packets[sp.key] = sp
            vid = {v for d in selected for v in d.vulnerability_ids}
            aliases = defaultdict(list)
            for a in db.scalars(select(VulnerabilityAlias).where(VulnerabilityAlias.vulnerability_id.in_(vid),
                                                              VulnerabilityAlias.is_demo.is_(False))):
                aliases[a.vulnerability_id].append(a.value)
            vulns = [VulnerabilityPacket(id=v.id, identifier=v.identifier, title=v.title, severity=v.severity,
                aliases=sorted(aliases[v.id])) for v in db.scalars(select(Vulnerability).where(
                    Vulnerability.id.in_(vid), Vulnerability.is_demo.is_(False)).order_by(Vulnerability.id))]
            return write_bundle(directory, list(source_packets.values()), selected, vulns,
                snapshot_id=snapshot_id, exclusions=excluded, secrets=secrets, shard_bytes=shard_bytes)


def check_migration(db, required=REQUIRED_MIGRATION):
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from backend.core.config import PROJECT_ROOT
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("path_separator", "os")
    try:
        rows = list(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
        scripts = ScriptDirectory.from_config(cfg)
        for current in rows:
            if required in {r.revision for r in scripts.iterate_revisions(current, "base")}:
                return
    except Exception:
        pass
    raise SnapshotError("database_migration_incompatible")


def _find_source(db, source):
    mapping = db.get(SnapshotSource, source.key)
    if mapping:
        row = db.get(DataSource, mapping.source_id)
        if row is None or row.adapter != source.adapter or row.config:
            raise SnapshotError("source_mapping_conflict", source.id)
        return row
    # Match only the known adapter, name AND empty public configuration.
    candidates = list(db.scalars(select(DataSource).where(DataSource.adapter == source.adapter,
                                                        DataSource.name == source.name)))
    return next((s for s in candidates if not s.config and s.category == source.category), None)


def _decision(document, packet):
    if document is None:
        return "new"
    if document.deleted_at is not None or document.is_demo:
        return "conflict"
    if document.content_hash == packet.current_hash:
        return "reused"
    incoming = next(r for r in packet.revisions if r.content_hash == packet.current_hash)
    incoming_time = RawRecord.model_validate(incoming.payload).source_updated_at
    if incoming_time and document.source_updated_at:
        if incoming_time > document.source_updated_at:
            return "updated"
        if incoming_time < document.source_updated_at:
            return "historical"
    return "conflict"  # Collection/export/first discovery time NEVER orders source versions.


def plan_import(db, bundle):
    """Query-only plan: not even transient INSERTs or rolled-back writes."""
    check_migration(db, bundle.manifest.required_migration)
    check_migration(db)
    batch = db.scalar(select(SnapshotImport).where(SnapshotImport.snapshot_id == bundle.manifest.snapshot_id))
    if batch and batch.manifest_sha256 != bundle.manifest_sha256:
        raise SnapshotError("snapshot_id_content_conflict")
    result = Counter({key: 0 for key in ("new", "updated", "reused", "historical", "conflict", "skipped", "failed", "new_sources", "uuid_remapped", "new_revisions")})
    found = {}
    for source in bundle.sources:
        row = _find_source(db, source)
        found[source.key] = row
        result["new_sources"] += row is None
    for packet in bundle.documents:
        source = found[packet.source_key]
        document = db.scalar(select(Document).where(Document.source_id == source.id,
            Document.external_id == packet.external_id)) if source else None
        result[_decision(document, packet)] += 1
        hinted = db.get(Document, packet.id)
        result["uuid_remapped"] += bool(hinted and (document is None or hinted.id != document.id))
        existing = set(db.scalars(select(DocumentRevision.content_hash).where(
            DocumentRevision.document_id == document.id))) if document else set()
        result["new_revisions"] += len({r.content_hash for r in packet.revisions} - existing)
    return {"snapshot_id": bundle.manifest.snapshot_id, "dry_run": True, **result,
            "already_imported": batch is not None, "counts": bundle.manifest.counts}


def import_snapshot(db, bundle):
    """Caller owns a single transaction. Errors roll back the entire import.

    The existing RawRecord, content hashing, relevance, alias grouping, evidence
    and association validators are reused. Incoming UUIDs are never assigned to
    new entities. Current-version writes obey stronger ordering than collectors.
    """
    check_migration(db, bundle.manifest.required_migration); check_migration(db)
    db.execute(text("SELECT pg_advisory_xact_lock(731946210)"))
    plan = plan_import(db, bundle)
    batch = db.scalar(select(SnapshotImport).where(SnapshotImport.snapshot_id == bundle.manifest.snapshot_id))
    if batch:
        verification = verify_import(db, bundle)
        return {**plan, "dry_run": False, "skipped": len(bundle.documents), "new": 0,
                "updated": 0, "new_revisions": 0, "new_sources": 0, "events_created": 0,
                "verification": verification}
    source_map = {}
    for source in bundle.sources:
        row = _find_source(db, source)
        if row is None:
            name = source.name
            if db.scalar(select(DataSource.id).where(DataSource.name == name)):
                name = name[:70] + " [snapshot-" + str(source.id)[:8] + "]"
            if db.scalar(select(DataSource.id).where(DataSource.name == name)):
                raise SnapshotError("source_name_collision", source.id)
            row = DataSource(name=name, adapter=source.adapter, category=source.category, config={},
                enabled=False, cursor={}, interval_seconds=86400)
            db.add(row); db.flush()
        source_map[source.key] = row
        if db.get(SnapshotSource, source.key) is None:
            db.add(SnapshotSource(source_key=source.key, source_id=row.id))
    db.flush()
    batch = SnapshotImport(snapshot_id=bundle.manifest.snapshot_id,
                           manifest_sha256=bundle.manifest_sha256, summary={})
    db.add(batch); db.flush()
    counts, events_created = Counter(), 0
    for packet in bundle.documents:
        source = source_map[packet.source_key]
        document = db.scalar(select(Document).where(Document.source_id == source.id,
            Document.external_id == packet.external_id).with_for_update())
        decision = _decision(document, packet)
        counts[decision] += 1
        if document is not None and (document.deleted_at is not None or document.is_demo):
            # Keep tombstones and private/demo records untouched; record conflict.
            db.add(SnapshotMember(import_id=batch.id, remote_document_id=str(packet.id),
                document_id=document.id, content_hash=packet.current_hash, disposition="skipped_conflict"))
            continue
        current = next(r for r in packet.revisions if r.content_hash == packet.current_hash)
        record = RawRecord.model_validate(current.payload)
        if decision == "new":
            document = Document(source_id=source.id, external_id=packet.external_id, is_demo=False,
                created_at=packet.created_at, updated_at=packet.updated_at,
                first_collected_at=packet.first_collected_at)
            db.add(document)
        if decision in {"new", "updated"}:
            # Original source fields/time are restored; import time belongs to batch.
            for name in ("title", "document_type", "content", "published_at", "source_updated_at",
                         "metadata_json", "content_completeness"):
                setattr(document, name, getattr(record, name))
            document.source_url = str(record.source_url)
            document.raw_payload, document.content_hash = current.payload, packet.current_hash
            document.relevance = relevance(record)
            document.ai_relevant = document.relevance["relevant"]
            document.last_checked_at = packet.last_checked_at
            document.content_changed_at = packet.content_changed_at
            document.indexed_at = None
            document.updated_at = packet.updated_at
            db.flush()
        existing = set(db.scalars(select(DocumentRevision.content_hash).where(DocumentRevision.document_id == document.id)))
        for revision in packet.revisions:
            if revision.content_hash not in existing:
                db.add(DocumentRevision(document_id=document.id, **revision.model_dump()))
        db.flush()
        for model, packets, unique_fields in ((Evidence, packet.evidence, ("content_hash", "field", "method")),
                (AssociationEvidence, packet.associations, ("content_hash", "identifier"))):
            saved = {tuple(getattr(r, k) for k in unique_fields): r for r in db.scalars(select(model).where(model.document_id == document.id))}
            for item in packets:
                key = tuple(getattr(item, k) for k in unique_fields)
                if key not in saved:
                    db.add(model(document_id=document.id, **item.model_dump()))
                elif any(getattr(saved[key], k) != v for k, v in item.model_dump().items()
                         if k not in {"created_at", "updated_at"}):
                    # Immutable version facts must agree; never overwrite local evidence.
                    raise SnapshotError("immutable_evidence_conflict", packet.id)
        db.flush()
        if decision in {"new", "updated"}:
            _attach_vulnerabilities(db, document, record)
            # _attach_vulnerabilities uses the existing conservative ingestion
            # rules and save_associations; historical imports do not call it.
            operation = "add" if decision == "new" else "update"
            event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == document.id,
                KnowledgeEvent.content_hash == document.content_hash, KnowledgeEvent.operation == operation))
            if event is None:
                db.add(KnowledgeEvent(document_id=document.id, content_hash=document.content_hash,
                    operation=operation, status="pending", detail={"reason": "public_snapshot_import",
                        "snapshot_id": bundle.manifest.snapshot_id, "is_demo": False}))
                events_created += 1
        db.add(SnapshotMember(import_id=batch.id, remote_document_id=str(packet.id), document_id=document.id,
            content_hash=packet.current_hash, disposition=decision))
        db.flush()
    result = {**plan, **counts, "dry_run": False, "events_created": events_created}
    batch.summary = result
    db.flush()
    result["verification"] = verify_import(db, bundle)
    return result


def verify_import(db, bundle):
    """Check this batch, permitting extra local records and newer current versions."""
    check_migration(db)
    batch = db.scalar(select(SnapshotImport).where(SnapshotImport.snapshot_id == bundle.manifest.snapshot_id))
    if batch is None or batch.manifest_sha256 != bundle.manifest_sha256:
        raise SnapshotError("snapshot_not_imported_or_changed")
    members = {m.remote_document_id: m for m in db.scalars(select(SnapshotMember).where(SnapshotMember.import_id == batch.id))}
    counts, failures = Counter(), []
    for packet in bundle.documents:
        member = members.get(str(packet.id))
        document = db.get(Document, member.document_id) if member else None
        if not document or member.disposition == "skipped_conflict":
            failures.append({"record_id": str(packet.id), "code": "document_missing_or_conflicting"}); continue
        counts["documents"] += 1
        counts["current_matches_snapshot"] += document.content_hash == packet.current_hash
        counts["local_current_differs"] += document.content_hash != packet.current_hash
        revisions = {r.content_hash: r for r in db.scalars(select(DocumentRevision).where(DocumentRevision.document_id == document.id))}
        if document.content_hash == packet.current_hash:
            current = next(r for r in packet.revisions if r.content_hash == packet.current_hash)
            parsed = RawRecord.model_validate(current.payload)
            if (document.raw_payload != current.payload or any(getattr(document, field) != getattr(parsed, field)
                    for field in ("title", "content", "document_type", "published_at", "source_updated_at",
                                  "metadata_json", "content_completeness"))
                    or document.source_url != str(parsed.source_url)):
                failures.append({"record_id": str(packet.id), "code": "current_document_payload_mismatch"})
        for revision in packet.revisions:
            saved = revisions.get(revision.content_hash)
            if saved and saved.payload == revision.payload and payload_hash(saved.payload) == revision.content_hash:
                counts["revisions"] += 1
            else:
                failures.append({"record_id": str(packet.id), "code": "revision_mismatch"})
        for model, packets, keys, label in ((Evidence, packet.evidence, ("content_hash", "field", "method"), "evidence"),
                (AssociationEvidence, packet.associations, ("content_hash", "identifier"), "associations")):
            saved = {tuple(getattr(r, k) for k in keys): r for r in db.scalars(select(model).where(model.document_id == document.id))}
            for item in packets:
                row = saved.get(tuple(getattr(item, k) for k in keys))
                if row and all(getattr(row, k) == v for k, v in item.model_dump().items()
                               if k not in {"created_at", "updated_at"}):
                    counts[label] += 1
                else:
                    failures.append({"record_id": str(packet.id), "code": label + "_mismatch"})
        if document.content_hash == packet.current_hash:
            # Entity UUIDs and canonical IDs can differ after a valid local alias
            # merge. Check actual current identifier-to-document links instead.
            expected = {i["identifier"] for i in association_candidates(document.raw_payload, document.source_url)}
            actual = set(db.scalars(select(VulnerabilityAlias.value).join(VulnerabilityDocument,
                VulnerabilityDocument.vulnerability_id == VulnerabilityAlias.vulnerability_id).where(
                    VulnerabilityDocument.document_id == document.id, VulnerabilityAlias.is_demo.is_(False))))
            counts["linked_identifiers"] += len(expected & actual)
            if not expected.issubset(actual):
                failures.append({"record_id": str(packet.id), "code": "current_alias_link_missing"})
    states = Counter(db.scalars(select(KnowledgeEvent.status).join(SnapshotMember,
        (SnapshotMember.document_id == KnowledgeEvent.document_id) &
        (SnapshotMember.content_hash == KnowledgeEvent.content_hash)).where(SnapshotMember.import_id == batch.id)))
    return {"snapshot_id": bundle.manifest.snapshot_id, "complete": not failures and counts["documents"] == len(bundle.documents),
        "matched": dict(counts), "expected": bundle.manifest.counts, "index_event_states": dict(states),
        "failures": failures[:100], "failure_count": len(failures),
        "imported_at": batch.imported_at.isoformat(), "indexed_claim": False,
        "note": "Batch integrity only; extra local data is retained. Different current versions are historical/ambiguous conflicts, not missing batch data. Index states are observed, not manufactured."}
