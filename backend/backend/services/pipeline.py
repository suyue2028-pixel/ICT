"""Transactional normalization, conservative entity matching and versioned evidence."""
import hashlib
import json
import re
from time import perf_counter

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.collectors.base import CollectionBatch
from backend.models import (
    DataSource, Document, DocumentRevision, Evidence, KnowledgeEvent,
    TaskRun, TaskStep, Vulnerability, VulnerabilityAlias, VulnerabilityDocument,
)

RISK_ORDER = {name: i for i, name in enumerate(("unknown", "low", "medium", "high", "critical"))}
AI_PRODUCTS = {"torch", "pytorch", "transformers", "langchain", "tensorflow", "keras",
               "onnx", "vllm", "ollama", "gradio", "llama", "scikit-learn", "openai",
               "tensorrt", "tensorrt-llm", "triton inference server", "chatrtx", "nemo", "riva", "merlin", "ai enterprise"}
SECURITY_PATTERN = re.compile(r"\b(?:security|safety|vulnerabilit\w*|CVE-\d{4}-\d{4,}|CWE-\d+|exploit\w*|attack\w*|"
    r"unsafe|deserializ\w*|malicious|injection|poison\w*|privacy|risk\w*|guardrails?|sandbox\w*)\b|"
    r"安全|漏洞|投毒|注入|攻击|风险|泄露|法规", re.I)
AI_PATTERN = re.compile(
    r"\b(?:AI|artificial intelligence|machine learning|deep learning|large language model|"
    r"LLMs?|prompt injection|model weights|neural network|generative AI)\b|"
    r"人工智能|大语言模型|提示词注入", re.I,
)
ID_PATTERN = re.compile(
    r"^(?:CVE-\d{4}-\d{4,}|GHSA-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}|"
    r"PYSEC-[A-Z0-9-]+|OSV-[A-Z0-9-]+)$", re.I,
)


def payload_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def relevance(record) -> dict:
    # A link to a publisher's AI-assisted disclosure process does not make every
    # advisory an AI product vulnerability. Ordinary AI marketing is not security.
    assessed_text = re.sub(r"AI-Accelerated\s+Vulnerability\s+Discovery", "", record.content, flags=re.I)
    products = sorted({p.lower().replace("_", "-") for p in record.products} & AI_PRODUCTS)
    terms = sorted({match.group(0).lower() for match in AI_PATTERN.finditer(assessed_text)})
    security_terms = sorted({match.group(0).lower() for match in SECURITY_PATTERN.finditer(assessed_text)})
    body_products = sorted(p for p in AI_PRODUCTS if re.search(
        r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", assessed_text, re.I))
    has_body = record.content_completeness != "metadata_only" and record.content.strip() != record.title.strip()
    categories = []
    if products or body_products:
        categories.append("ai_infrastructure")
    if re.search(r"prompt injection|data poisoning|model weights|提示词|数据投毒", record.content, re.I):
        categories.append("model_and_data")
    if re.search(r"\bagents?\b|langchain|智能体", record.content, re.I):
        categories.append("application_and_agent")
    if re.search(r"supply.chain|dependency|供应链", record.content, re.I):
        categories.append("supply_chain")
    return {"relevant": bool((products or (has_body and (terms or body_products))) and
                             (security_terms or record.document_type == "vulnerability")), "method": "rule-v4",
            "product_matches": products, "body_terms": terms, "body_products": body_products,
            "security_terms": security_terms,
            "body_available": has_body,
            "risk_categories": categories,
            "limitation": "Rule-based triage; title alone is not evidence or measured accuracy"}


def _link_once(db: Session, vulnerability_id, document_id):
    link = db.scalar(select(VulnerabilityDocument).where(
        VulnerabilityDocument.vulnerability_id == vulnerability_id,
        VulnerabilityDocument.document_id == document_id,
    ))
    if link is None:
        db.add(VulnerabilityDocument(vulnerability_id=vulnerability_id, document_id=document_id))
        db.flush()


def sync_vulnerability_relevance(db, document_ids):
    if not document_ids:
        return
    db.flush()
    affected = select(VulnerabilityDocument.vulnerability_id).where(
        VulnerabilityDocument.document_id.in_(document_ids))
    has_ai_doc = select(Document.id).join(VulnerabilityDocument,
        VulnerabilityDocument.document_id == Document.id).where(
        VulnerabilityDocument.vulnerability_id == Vulnerability.id,
        Document.ai_relevant.is_(True), Document.deleted_at.is_(None)).correlate(Vulnerability).exists()
    db.execute(update(Vulnerability).where(Vulnerability.id.in_(affected)).values(ai_relevant=has_ai_doc))


def _aliases(record, document: Document) -> list[str]:
    aliases = sorted({value.upper() for value in [*record.aliases, record.vulnerability_id or ""]
                      if ID_PATTERN.fullmatch(value)})
    if aliases or record.document_type != "vulnerability":
        return aliases
    # No title similarity: only exact normalized product+body; otherwise document URL.
    products = sorted({p.strip().casefold().replace("_", "-") for p in record.products if p.strip()})
    body = " ".join(record.content.casefold().split())
    identity = json.dumps([products, body], ensure_ascii=False) if products and len(body) >= 80 else document.source_url
    return ["LOCAL-" + hashlib.sha256(identity.encode()).hexdigest()]


def _attach_vulnerabilities(db: Session, document: Document, record):
    aliases = _aliases(record, document)
    # A text article may mention several unrelated CVEs; only explicit
    # vulnerability records can declare those aliases equivalent.
    groups = [aliases] if record.document_type == "vulnerability" else [[value] for value in aliases]
    for group in groups:
        if not group:
            continue
        matches = db.scalars(select(VulnerabilityAlias).where(
            VulnerabilityAlias.value.in_(group), VulnerabilityAlias.is_demo == record.is_demo
        )).all()
        candidate_ids = sorted({row.vulnerability_id for row in matches}, key=str)
        vulnerability = db.get(Vulnerability, candidate_ids[0]) if candidate_ids else None
        if vulnerability is None:
            canonical = next((item for item in group if item.startswith("CVE-")), group[0])
            vulnerability = Vulnerability(identifier=canonical, title=record.title,
                                          severity=record.severity, is_demo=record.is_demo,
                                          ai_relevant=document.ai_relevant)
            db.add(vulnerability)
            db.flush()
        for duplicate_id in candidate_ids[1:]:
            duplicate = db.get(Vulnerability, duplicate_id)
            references = db.scalars(select(VulnerabilityDocument).where(
                VulnerabilityDocument.vulnerability_id == duplicate_id)).all()
            for reference in references:
                _link_once(db, vulnerability.id, reference.document_id)
            db.execute(delete(VulnerabilityDocument).where(
                VulnerabilityDocument.vulnerability_id == duplicate_id))
            db.execute(update(VulnerabilityAlias).where(
                VulnerabilityAlias.vulnerability_id == duplicate_id).values(vulnerability_id=vulnerability.id))
            vulnerability.ai_relevant |= duplicate.ai_relevant
            if RISK_ORDER[duplicate.severity] > RISK_ORDER[vulnerability.severity]:
                vulnerability.severity = duplicate.severity
            db.delete(duplicate)
            db.flush()
        for value in group:
            if not db.scalar(select(VulnerabilityAlias.id).where(
                VulnerabilityAlias.value == value, VulnerabilityAlias.is_demo == record.is_demo)):
                db.add(VulnerabilityAlias(value=value, is_demo=record.is_demo,
                                          vulnerability_id=vulnerability.id))
        db.flush()
        all_aliases = db.scalars(select(VulnerabilityAlias.value).where(
            VulnerabilityAlias.vulnerability_id == vulnerability.id)).all()
        vulnerability.identifier = next((value for value in sorted(all_aliases)
                                         if value.startswith("CVE-")), vulnerability.identifier)
        vulnerability.ai_relevant |= document.ai_relevant
        if RISK_ORDER[record.severity] > RISK_ORDER[vulnerability.severity]:
            vulnerability.severity = record.severity
        _link_once(db, vulnerability.id, document.id)


def ingest_batch(db: Session, source: DataSource, batch: CollectionBatch, task: TaskRun) -> dict:
    """Caller commits this single transaction, including the source cursor and task result."""
    counts = {"received": len(batch.records), "created": 0, "updated": 0,
              "unchanged": 0, "ai_relevant": 0, "is_demo": source.adapter == "offline"}
    elapsed = {name: 0.0 for name in ("normalize", "deduplicate_and_store",
                                     "source_enrichment", "knowledge_event")}
    observed_ids = []
    for record in batch.records:
        started = perf_counter()
        if record.is_demo != (source.adapter == "offline"):
            raise ValueError("source_demo_scope_mismatch")
        payload = record.model_dump(mode="json")
        digest = payload_hash(payload)
        assessment = relevance(record)
        elapsed["normalize"] += perf_counter() - started
        started = perf_counter()
        document = db.scalar(select(Document).where(
            Document.source_id == source.id, Document.external_id == record.external_id))
        if document is not None and document.deleted_at is not None:
            # Do not silently resurrect administrator-invalidated records on the next poll.
            counts["unchanged"] += 1
            continue
        if document is not None and document.content_hash == digest:
            if document.relevance != assessment:
                document.relevance, document.ai_relevant = assessment, assessment["relevant"]
            observed_ids.append(document.id)
            counts["unchanged"] += 1
            counts["ai_relevant"] += int(document.ai_relevant)
            elapsed["deduplicate_and_store"] += perf_counter() - started
            continue
        operation = "update" if document else "add"
        if document is None:
            document = Document(source_id=source.id, external_id=record.external_id)
            db.add(document)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        for name in ("title", "document_type", "content", "published_at", "is_demo",
                     "metadata_json", "content_completeness", "source_updated_at"):
            setattr(document, name, getattr(record, name))
        document.source_url = str(record.source_url)
        document.raw_payload, document.content_hash = payload, digest
        document.relevance = assessment
        document.ai_relevant = document.relevance["relevant"]
        db.flush()
        if not db.scalar(select(DocumentRevision.id).where(
            DocumentRevision.document_id == document.id, DocumentRevision.content_hash == digest)):
            db.add(DocumentRevision(document_id=document.id, content_hash=digest, payload=payload))
        elapsed["deduplicate_and_store"] += perf_counter() - started
        started = perf_counter()
        if not db.scalar(select(KnowledgeEvent.id).where(
            KnowledgeEvent.document_id == document.id, KnowledgeEvent.content_hash == digest)):
            db.add(KnowledgeEvent(document_id=document.id, content_hash=digest, operation=operation,
                                  status="pending", detail={"reason": "awaiting_external_indexer",
                                                            "is_demo": record.is_demo}))
        elapsed["knowledge_event"] += perf_counter() - started
        started = perf_counter()
        for field, value in record.source_fields.items():
            if not any(value.values()):
                continue
            if not db.scalar(select(Evidence.id).where(
                Evidence.document_id == document.id, Evidence.content_hash == digest,
                Evidence.field == field, Evidence.method == "source_reported")):
                db.add(Evidence(document_id=document.id, content_hash=digest, field=field,
                    value=value, state="known", excerpt=json.dumps(value, ensure_ascii=False)[:2000],
                    source_url=document.source_url, locator=f"source_fields.{field}",
                    method="source_reported"))
        elapsed["source_enrichment"] += perf_counter() - started
        started = perf_counter()
        _attach_vulnerabilities(db, document, record)
        observed_ids.append(document.id)
        db.flush()
        elapsed["deduplicate_and_store"] += perf_counter() - started
        counts["ai_relevant"] += int(document.ai_relevant)
    sync_vulnerability_relevance(db, observed_ids)
    source.cursor = batch.next_cursor
    for name in ("normalize", "deduplicate_and_store", "source_enrichment", "knowledge_event"):
        db.add(TaskStep(task_id=task.id, name=name, attempt=task.attempts,
                        status="succeeded", detail={"duration_ms": round(elapsed[name] * 1000, 3),
                                                    "received": counts["received"]}))
    return counts
