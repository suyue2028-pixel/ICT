"""Apply current deterministic triage to existing raw data; preserve every version."""
import json
import sys
from pathlib import Path
from sqlalchemy import select, text
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db.session import get_session_factory
from backend.models import Document
from backend.schemas.contracts import RawRecord
from backend.services.pipeline import relevance, sync_vulnerability_relevance

with get_session_factory()() as db, db.begin():
    db.execute(text("SELECT pg_advisory_xact_lock(7429031)"))
    docs = db.scalars(select(Document).where(Document.deleted_at.is_(None)).with_for_update()).all()
    changed, positive = 0, 0
    for doc in docs:
        assessment = relevance(RawRecord.model_validate(doc.raw_payload))
        changed += doc.relevance != assessment
        doc.relevance, doc.ai_relevant = assessment, assessment["relevant"]
        positive += doc.ai_relevant and not doc.is_demo
    sync_vulnerability_relevance(db, [d.id for d in docs])
    print(json.dumps({"method": "rule-v4", "documents_reassessed": len(docs),
        "labels_changed": changed, "real_positive_candidates": positive,
        "accuracy": None, "note": "Deterministic candidates, not independent review"}))
