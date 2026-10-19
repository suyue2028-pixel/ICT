"""Create a version-bound real-data review set; unreviewed cases never produce scores."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, Evidence


def main():
    with get_session_factory()() as db:
        sources = db.scalars(select(DataSource).where(DataSource.adapter != "offline")).all()
        cases = []
        for source in sources:
            docs = db.scalars(select(Document).where(Document.source_id == source.id,
                Document.is_demo.is_(False), Document.deleted_at.is_(None)).order_by(Document.id).limit(10)).all()
            for doc in docs:
                fields = db.scalars(select(Evidence).where(Evidence.document_id == doc.id,
                    Evidence.content_hash == doc.content_hash)).all()
                cases.append({"case_id": str(doc.id), "document_id": str(doc.id), "content_hash": doc.content_hash,
                    "source_name": source.name, "category": source.category, "document_type": doc.document_type,
                    "source_url": doc.source_url, "content_completeness": doc.content_completeness,
                    "content": doc.content, "predicted_ai_relevant": doc.ai_relevant,
                    "prediction_method": doc.relevance.get("method"),
                    "review": {"reviewer": None, "reviewed_at": None, "ai_relevant": None,
                               "expected_fields": None, "answer_correct": None, "citation_correct": None},
                    "predicted_fields": [{"field": e.field, "state": e.state, "value": e.value,
                                           "locator": e.locator, "excerpt": e.excerpt} for e in fields]})
    target = ROOT / "output" / "evaluation-review.json"
    if target.exists():
        # Never overwrite previously entered review labels.
        target = target.with_name("evaluation-review-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
    target.write_text(json.dumps({"schema_version": "1", "status": "unreviewed",
        "source": "real version-bound documents", "cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "cases": len(cases), "status": "unreviewed", "accuracy": None, "recall": None}))


if __name__ == "__main__":
    main()
