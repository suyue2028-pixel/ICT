"""Freeze a dry-run plan, then apply triage without changing source data."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import sys
from pathlib import Path
from uuid import UUID
from sqlalchemy import select, text
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db.session import get_session_factory
from backend.models import Document
from backend.schemas.contracts import RawRecord
from backend.services.enrichment_quality import canonical
from backend.services.pipeline import relevance, sync_vulnerability_relevance
from backend.services.reclassification import apply_assessment
from backend.services.relevance import RELEVANCE_VERSION

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def save(data, phase):
    folder = ROOT / "output/relevance"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (phase + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def preview():
    records = []
    with get_session_factory()() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        db.execute(text("SET TRANSACTION READ ONLY"))
        docs = db.scalars(select(Document).where(Document.is_demo.is_(False), Document.deleted_at.is_(None))
                          .order_by(Document.id))
        for doc in docs:
            assessment = relevance(RawRecord.model_validate(doc.raw_payload))
            records.append({"document_id": str(doc.id), "content_hash": doc.content_hash,
                "previous_relevance_hash": digest(doc.relevance), "previous_ai_relevant": doc.ai_relevant,
                "title": doc.title, "source_url": doc.source_url, "assessment": assessment})
    plan = {"schema_version": "relevance-plan-1", "method": RELEVANCE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(), "is_demo": False, "records": records}
    plan["fingerprint"] = digest(plan)
    path = save(plan, "plan")
    flips = [row for row in records if row["previous_ai_relevant"] != row["assessment"]["relevant"]]
    print(json.dumps({"plan": str(path), "fingerprint": plan["fingerprint"], "documents": len(records),
        "scope_changes": [{"document_id": row["document_id"], "title": row["title"],
            "before": row["previous_ai_relevant"], "after": row["assessment"]["relevant"],
            "scope": row["assessment"]["topic_scope"], "reason": row["assessment"]["reason_code"]}
                          for row in flips], "database_modified": False}, ensure_ascii=False, indent=2))


def apply(path):
    plan = json.loads(path.read_text(encoding="utf-8"))
    fingerprint = plan.pop("fingerprint")
    if digest(plan) != fingerprint or plan["schema_version"] != "relevance-plan-1" or plan["method"] != RELEVANCE_VERSION or plan["is_demo"] is not False:
        raise ValueError("invalid_relevance_plan")
    records, counts, changes = plan["records"], Counter(), []
    ids = [UUID(row["document_id"]) for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_relevance_plan_document")
    with get_session_factory()() as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(7429031)"))
        documents = {str(d.id): d for d in db.scalars(select(Document).where(Document.id.in_(ids))
                     .order_by(Document.id).with_for_update().execution_options(populate_existing=True))}
        for row in records:
            doc = documents.get(row["document_id"])
            if doc is None or doc.deleted_at is not None or doc.is_demo or doc.content_hash != row["content_hash"]:
                raise ValueError("relevance_plan_scope_or_version_conflict")
            if relevance(RawRecord.model_validate(doc.raw_payload)) != row["assessment"]:
                raise ValueError("relevance_plan_algorithm_conflict")
            already = doc.relevance == row["assessment"] and doc.ai_relevant == row["assessment"]["relevant"]
            if not already and (digest(doc.relevance) != row["previous_relevance_hash"] or doc.ai_relevant != row["previous_ai_relevant"]):
                raise ValueError("relevance_plan_previous_decision_conflict")
        for row in records:
            doc = documents[row["document_id"]]
            result = apply_assessment(db, doc, row["assessment"])
            counts["documents"] += 1
            counts["updated" if result["changed"] else "already_applied"] += 1
            if result["scope_changed"]:
                counts[result["operation"] + "_events"] += 1
                changes.append({"document_id": str(doc.id), "content_hash": doc.content_hash,
                    "title": doc.title, "ai_relevant": doc.ai_relevant,
                    "topic_scope": doc.relevance["topic_scope"], **result})
        sync_vulnerability_relevance(db, [UUID(row["document_id"]) for row in changes])
        db.flush()
        if any(documents[row["document_id"]].content_hash != row["content_hash"] for row in records):
            raise ValueError("source_version_was_modified")
    report = {"method": RELEVANCE_VERSION, "plan": str(path), "fingerprint": fingerprint,
        "applied_at": datetime.now(timezone.utc).isoformat(), "counts": dict(counts), "changes": changes,
        "source_versions_retained": True, "source_data_deleted": False, "human_accuracy": None, "model_calls": 0}
    output = save(report, "applied")
    print(json.dumps({"report": str(output), **report}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    if args.apply and args.plan is None:
        parser.error("--apply requires a reviewed --plan; default is read-only dry-run")
    if args.apply:
        apply(args.plan)
    else:
        preview()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("Reassessment failed; transaction rolled back: " + type(error).__name__ + "; no secrets printed") from None
