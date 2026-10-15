"""Queue real documents for evidence-only eight-dimension processing; no model calls."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from sqlalchemy import select, func

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db.session import get_session_factory
from backend.models import Document, TaskRun, Evidence
from backend.services.eight_enrichment import DIMENSIONS, METHOD, create_enrichment_task, processing_stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", action="store_true", help="Enqueue missing/outdated work; default is read-only stats")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--retry-failed", action="store_true", help="Explicitly retry incomplete current versions with failed jobs; keeps failure history")
    args = parser.parse_args()
    if not 1 <= args.limit <= 5000:
        parser.error("--limit must be 1..5000")
    if args.retry_failed and not args.queue:
        parser.error("--retry-failed requires --queue")
    queued = 0
    with get_session_factory()() as db, db.begin():
        if args.queue:
            query = select(Document).where(Document.is_demo.is_(False), Document.deleted_at.is_(None))
            if args.retry_failed:
                from sqlalchemy import cast, String
                failed = select(TaskRun.id).where(TaskRun.kind == "enrich", TaskRun.status == "failed",
                    TaskRun.result["document_id"].as_string() == cast(Document.id, String),
                    TaskRun.result["content_hash"].as_string() == Document.content_hash).correlate(Document).exists()
                complete = select(func.count()).select_from(Evidence).where(Evidence.document_id == Document.id,
                    Evidence.content_hash == Document.content_hash, Evidence.method == METHOD,
                    Evidence.field.in_(DIMENSIONS)).correlate(Document).scalar_subquery()
                query = query.where(failed, complete < len(DIMENSIONS))
            docs = list(db.scalars(query.order_by(Document.source_id, Document.id).limit(args.limit)))
            for doc in docs:
                queued += create_enrichment_task(db, doc, force=args.retry_failed)[1]
        stats = processing_stats(db)
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "queued": queued,
        "model_calls": 0, "source_content_modified": False, "stats": stats}
    folder = ROOT / "output/enrichment-eight"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("progress-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"report": str(path), **report}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("Enrichment queue failed: " + type(error).__name__ + "; no secrets printed") from None
