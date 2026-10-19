"""Eight evidence-backed dimensions from saved public records; no model invention."""
from collections import defaultdict
from datetime import timedelta
from functools import lru_cache
import json
import re
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func, select, cast, String, or_, and_
from sqlalchemy.orm import load_only
from backend.models import DataSource, Document, Evidence, TaskRun, Outbox, VulnerabilityDocument
from backend.services.jobs import utc_now

DIMENSIONS = ("affected", "cwe", "cvss", "remediation", "analysis", "poc", "papers", "exploitation")
PROVIDER = "backend-evidence-v1"
METHOD = "external:" + PROVIDER
PROCESSOR_VERSION = "eight-evidence-20261015-v2"
TOPICS = {
    "prompt_injection": r"prompt[ -]injections?|提示词注入", "deserialization": r"deserializ\w*|反序列化",
    "tool_authorization": r"tool.{0,25}(?:audit|permission|authoriz)|工具.{0,12}(?:审计|授权)",
    "model_extraction": r"model extraction|模型窃取", "poisoning": r"data poisoning|training.{0,20}poison|数据投毒",
    "sandbox": r"sandbox escape|sandboxing|沙箱逃逸", "jailbreak": r"jailbreak\w*|越狱",
    "privacy": r"privacy leakage|cross.tenant|隐私泄露", "supply_chain": r"supply.chain|供应链",
    "access_control": r"access control|authorization bypass|访问控制|权限绕过",
}


def topic_keys(value):
    return {name for name, pattern in TOPICS.items() if re.search(pattern, value, re.I)}


@lru_cache(maxsize=1024)
def paper_topics(content_hash, content):
    return frozenset(topic_keys(content))


def citation(doc, excerpt, representation="revision_json"):
    content = doc.content if representation == "content" else json.dumps(doc.raw_payload, ensure_ascii=False)
    start = content.find(excerpt)
    if start < 0 or not excerpt:
        raise ValueError("enrichment_quote_not_in_version")
    excerpt = excerpt[:1500]
    return {"document_id": str(doc.id), "content_hash": doc.content_hash, "source_url": doc.source_url,
        "locator": f"{representation}:chars:{start}:{start + len(excerpt)}", "excerpt": excerpt}


def quote_row(doc, row):
    return citation(doc, row.excerpt or json.dumps(row.value, ensure_ascii=False)[:1500])


def field_value(field, value):
    if field == "affected":
        items = list(value.get("items", []))
        for match in value.get("cpe_matches", []):
            from backend.services.enrichment import cpe_components
            parsed = cpe_components(match.get("criteria"))
            items.append({"package": {"name": parsed["product"]["display"] if parsed else match["criteria"],
                "ecosystem": "CPE"}, "ranges": [], "cpe_criteria": match["criteria"],
                "version_bounds": {key: item for key, item in match.items() if key.startswith("version")},
                "version_state": "source_recorded_not_asset_evaluated"})
        return items
    if field == "cwe":
        return value.get("ids", [])
    if field == "cvss":
        from backend.services.cvss_base import calculate_v3_base
        vectors = []
        for original in value.get("vectors", []):
            row = dict(original)
            if row.get("score") is None:
                try:
                    score = calculate_v3_base(row.get("vector"))
                except (ValueError, KeyError, TypeError):
                    score = None
                if score is not None:
                    row.update(score=score, origin="system_estimate", derivation="cvss_v3_base_formula",
                               score_kind="calculated_base_not_official_score")
            vectors.append(row)
        return vectors
    if field == "exploitation":
        return value
    return value.get("references", [])


def create_enrichment_task(db, document, force=False):
    # Match collection's source -> document lock order, including the source FK
    # lock acquired by a new TaskRun. Do not hold a document while waiting on it.
    db.execute(select(DataSource.id).where(DataSource.id == document.source_id).with_for_update(read=True, key_share=True))
    document = db.scalar(select(Document).where(Document.id == document.id).with_for_update().execution_options(populate_existing=True))
    if document.deleted_at is not None:
        raise HTTPException(409, "document_invalidated")
    active = db.scalar(select(TaskRun).where(TaskRun.kind == "enrich",
        TaskRun.result["document_id"].as_string() == str(document.id), TaskRun.status.in_(("queued", "running"))))
    if active:
        return active, False
    if not force:
        failed = db.scalar(select(TaskRun).where(TaskRun.kind == "enrich",
            TaskRun.result["document_id"].as_string() == str(document.id),
            TaskRun.result["content_hash"].as_string() == document.content_hash, TaskRun.status == "failed",
            TaskRun.result["processor_version"].as_string() == PROCESSOR_VERSION,
            TaskRun.finished_at >= utc_now() - timedelta(hours=4)).order_by(TaskRun.finished_at.desc()).limit(1))
        if failed:
            return failed, False
        done = db.scalar(select(func.count()).select_from(Evidence).where(Evidence.document_id == document.id,
            Evidence.content_hash == document.content_hash, Evidence.method == METHOD,
            Evidence.value["processor_version"].as_string() == PROCESSOR_VERSION,
            Evidence.updated_at >= utc_now() - timedelta(hours=4), Evidence.field.in_(DIMENSIONS)))
        if done == len(DIMENSIONS):
            return None, False
    task = TaskRun(id=uuid4(), source_id=document.source_id, kind="enrich", status="queued",
        result={"document_id": str(document.id), "content_hash": document.content_hash, "provider": PROVIDER,
                "processor_version": PROCESSOR_VERSION})
    db.add(task); db.flush(); db.add(Outbox(task_id=task.id))
    return task, True


def enqueue_missing(db, limit=10):
    checked = select(Evidence.id).where(Evidence.document_id == Document.id,
        Evidence.content_hash == Document.content_hash, Evidence.method == METHOD, Evidence.field == "analysis",
        Evidence.value["processor_version"].as_string() == PROCESSOR_VERSION,
        Evidence.updated_at >= utc_now() - timedelta(hours=4)).correlate(Document).exists()
    pending = select(TaskRun.id).where(TaskRun.kind == "enrich",
        TaskRun.result["document_id"].as_string() == cast(Document.id, String),
        or_(TaskRun.status.in_(("queued", "running")), and_(TaskRun.status == "failed",
            TaskRun.result["content_hash"].as_string() == Document.content_hash,
            TaskRun.result["processor_version"].as_string() == PROCESSOR_VERSION,
            TaskRun.finished_at >= utc_now() - timedelta(hours=4)))).correlate(Document).exists()
    documents = list(db.scalars(select(Document).where(Document.is_demo.is_(False), Document.deleted_at.is_(None),
        ~checked, ~pending).order_by(Document.source_id, Document.id).limit(limit)))
    return sum(create_enrichment_task(db, doc)[1] for doc in documents)


def build_dimensions(db, doc):
    peers = [doc]
    if doc.document_type in {"vulnerability", "advisory"}:
        linked = list(db.scalars(select(VulnerabilityDocument.vulnerability_id).where(VulnerabilityDocument.document_id == doc.id)))
        # Borrow whole-document fields only when BOTH records refer to exactly
        # one identical vulnerability. A multi-CVE bulletin has no field-level
        # attribution and must not leak another CVE's versions or scores.
        peer_ids = select(VulnerabilityDocument.document_id).group_by(VulnerabilityDocument.document_id).having(
            func.count() == 1,
            func.bool_and(VulnerabilityDocument.vulnerability_id == (linked[0] if len(linked) == 1 else None)))
        # Deduplicate identifiers in a subquery, not entire JSON-bearing rows:
        # PostgreSQL JSON columns intentionally have no equality operator.
        peers.extend(db.scalars(select(Document)
            .where(Document.id.in_(peer_ids), Document.id != doc.id,
                Document.is_demo == doc.is_demo, Document.deleted_at.is_(None),
                Document.document_type.in_(("vulnerability", "advisory"))).order_by(Document.id).limit(12)))
    peer_map = {row.id: row for row in peers}
    rows = list(db.scalars(select(Evidence).join(Document, Document.id == Evidence.document_id).where(
        Evidence.document_id.in_(peer_map), Evidence.content_hash == Document.content_hash,
        Evidence.method == "source_reported", Evidence.state == "known", Evidence.field.in_(DIMENSIONS))))
    values, citations = defaultdict(list), defaultdict(list)
    for row in rows:
        native = field_value(row.field, row.value)
        if native:
            if row.field == "exploitation":
                values[row.field].append(native)
            else:
                values[row.field].extend(native)
            citations[row.field].append(quote_row(peer_map[row.document_id], row))
        if row.field == "affected":
            for affected in row.value.get("items", []):
                name = affected.get("package", {}).get("name", "source package")
                for interval in affected.get("ranges", []):
                    for event in interval.get("events", []):
                        if "fixed" in event:
                            label = "修复提交" if interval.get("type") == "GIT" else "来源记录修复版本"
                            values["remediation"].append({"url": peer_map[row.document_id].source_url,
                                "title": f"{name} · {label}：{event['fixed']}",
                                "description": f"{label}，仅按原始范围事件保存；不推断其他版本安全。"})
                            citations["remediation"].append(quote_row(peer_map[row.document_id], row))
    if not values["affected"] and doc.document_type in {"vulnerability", "advisory"}:
        products = doc.raw_payload.get("products", [])
        if products:
            values["affected"] = [{"package": {"name": name, "ecosystem": "source metadata"}, "ranges": [],
                "version_state": "unknown"} for name in products if name]
            citations["affected"].append(citation(doc, json.dumps(products, ensure_ascii=False)))
    if not values["exploitation"] and doc.source_url.startswith("https://nvd.nist.gov/"):
        # This NVD narrative template says an exploit is public, not that active
        # in-the-wild exploitation or a CISA KEV listing has been confirmed.
        match = re.search(r"(?:^|[.!?]\s+)(The exploit has been disclosed to the public and may be used\.)", doc.content, re.I)
        if match:
            values["exploitation"].append({"status": "public_exploit_disclosed_not_confirmed_active",
                "claim": "Source narrative reports a public exploit; active exploitation is not established.",
                "catalog": "NVD source narrative; not a KEV confirmation"})
            citations["exploitation"].append(citation(doc, match[1], "content"))
    if doc.content.strip():
        excerpt = doc.content[:800]
        values["analysis"].append({"url": doc.source_url, "title": "原始来源说明/摘要", "description": excerpt,
            "analysis_kind": "source_excerpt_not_independent_analysis"})
        citations["analysis"].append(citation(doc, excerpt, "content"))
    # Papers are selected by explicit public links/identifiers first. Topic-only
    # suggestions stay pending_review and never claim to support a fix/version.
    papers = list(db.scalars(select(Document).options(load_only(Document.id, Document.external_id, Document.content_hash, Document.source_url,
        Document.title, Document.content)).where(Document.document_type == "paper", Document.is_demo == doc.is_demo,
        Document.deleted_at.is_(None), Document.ai_relevant.is_(True), Document.id != doc.id)
        .order_by(Document.created_at.desc(), Document.id).limit(500)))
    query_topics = topic_keys(doc.content)
    references = json.dumps(doc.raw_payload.get("metadata_json", {}).get("references", []), ensure_ascii=False)
    candidates = []
    for paper in papers:
        explicit = paper.source_url in references or paper.external_id in references
        shared = query_topics & paper_topics(paper.content_hash, paper.content)
        if explicit or shared:
            candidates.append((not explicit, -len(shared), str(paper.id), paper))
    candidates.sort(key=lambda item: item[:3])
    paper_pending = False
    for not_explicit, _, _, paper in candidates[:2]:
        paper_pending |= not_explicit
        values["papers"].append({"url": paper.source_url, "title": paper.title,
            "relation": "topic_candidate_needs_review" if not_explicit else "explicit_source_reference"})
        citations["papers"].append(citation(paper, paper.content[:700], "content"))
    results = {}
    for field in DIMENSIONS:
        unique = {json.dumps(item, sort_keys=True, ensure_ascii=False): item for item in values[field]}
        found = list(unique.values())[:100]
        applicable = doc.document_type in {"vulnerability", "advisory"} or field in {"analysis", "papers"}
        state = "pending_review" if field == "papers" and paper_pending else "known" if found else "not_found"
        value = found[0] if field == "exploitation" and found else found
        proof = list({json.dumps(c, sort_keys=True): c for c in citations[field]}.values())[:20]
        if found and not proof:
            raise ValueError("enrichment_facts_without_evidence")
        results[field] = {"state": state, "payload": {"value": value, "evidence": proof,
            "extraction_method": "rule", "review_state": "pending_review" if state == "pending_review" else "source_supported",
            "applicability": "applicable" if applicable or found else "not_applicable",
            "reason_code": "source_evidence" if found else "not_applicable_to_document_type" if not applicable else "not_found_in_saved_sources",
            "checked_at": utc_now().isoformat(), "scope": "current_saved_sources_and_exact_identifier_peers",
            "paper_candidate_scan_limit": 500,
            "processor_version": PROCESSOR_VERSION,
            "limitations": "No PoC execution, no invented versions, no claim that absence of evidence means safety."}}
    return results


def store_dimensions(db, doc):
    results = build_dimensions(db, doc)
    existing = {row.field: row for row in db.scalars(select(Evidence).where(Evidence.document_id == doc.id,
        Evidence.content_hash == doc.content_hash, Evidence.method == METHOD))}
    for field, result in results.items():
        row = existing.get(field)
        if row is None:
            row = Evidence(document_id=doc.id, content_hash=doc.content_hash, field=field, method=METHOD,
                           source_url=doc.source_url)
            db.add(row)
        row.state, row.value = result["state"], result["payload"]
        first = result["payload"]["evidence"][:1]
        row.source_url = first[0]["source_url"] if first else doc.source_url
        row.excerpt = first[0]["excerpt"] if first else None
        row.locator = first[0]["locator"] if first else "external.not_found_in_saved_sources"
    db.flush()
    return {field: {"state": item["state"], "applicability": item["payload"]["applicability"]} for field, item in results.items()}


def summary_dimensions(rows, task=None):
    summary = {}
    for field in DIMENSIONS:
        selected = [row for row in rows if row.field == field]
        source = next((row for row in selected if row.method == "source_reported" and row.state == "known"), None)
        derived = next((row for row in selected if row.method == METHOD), None)
        value = derived.value if derived else {}
        state = "known" if source else derived.state if derived else "unprocessed"
        if not source and not derived and task:
            state = {"queued": "queued", "running": "processing", "failed": "failed"}.get(task.status, state)
        if not source and derived and derived.state == "not_found" and value.get("applicability") == "not_applicable":
            state = "not_applicable"
        summary[field] = {"state": state, "checked_at": value.get("checked_at"), "reason_code": value.get("reason_code"),
            "scope": value.get("scope"), "applicability": value.get("applicability", "unknown")}
    return summary


def processing_stats(db, is_demo=False):
    total = db.scalar(select(func.count()).select_from(Document).where(Document.is_demo == is_demo, Document.deleted_at.is_(None)))
    applicability = Evidence.value["applicability"].as_string()
    query = select(Evidence.field, Evidence.state, applicability, func.count()).join(
        Document, Document.id == Evidence.document_id).where(Document.is_demo == is_demo, Document.deleted_at.is_(None),
        Evidence.content_hash == Document.content_hash, Evidence.method == METHOD, Evidence.field.in_(DIMENSIONS))
    query = query.where(Evidence.value["processor_version"].as_string() == PROCESSOR_VERSION)
    groups = db.execute(query.group_by(Evidence.field, Evidence.state, applicability)).all()
    fields = {name: defaultdict(int) for name in DIMENSIONS}
    for field, state, applicability, count in groups:
        fields[field]["not_applicable" if state == "not_found" and applicability == "not_applicable" else state] += count
    for counts in fields.values():
        counts["unprocessed"] = total - sum(counts.values())
    done = select(func.count()).select_from(Evidence).where(Evidence.document_id == Document.id,
        Evidence.content_hash == Document.content_hash, Evidence.method == METHOD,
        Evidence.value["processor_version"].as_string() == PROCESSOR_VERSION,
        Evidence.field.in_(DIMENSIONS)).correlate(Document).scalar_subquery()
    completed = db.scalar(select(func.count()).select_from(Document).where(Document.is_demo == is_demo,
        Document.deleted_at.is_(None), done == len(DIMENSIONS)))
    return {"provider": PROVIDER, "processor_version": PROCESSOR_VERSION, "measured_at": utc_now(), "is_demo": is_demo, "documents": total,
        "all_eight_processed_documents": completed, "remaining_documents": total - completed,
        "dimensions": {key: dict(value) for key, value in fields.items()}, "human_accuracy": None,
        "scope": "All current saved documents; not_found is local-source absence, not absence in the world"}
