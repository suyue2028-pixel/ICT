"""Read two real stored-source records and verify derived fields retain source evidence."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import httpx
from sqlalchemy import select
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, Evidence


def main():
    settings = get_settings()
    rows = []
    with get_session_factory()() as db, httpx.Client(timeout=15, trust_env=False) as client:
        for adapter in ["nvd", "osv"]:
            candidates = db.execute(select(Document, Evidence).join(DataSource, Document.source_id == DataSource.id)
                .join(Evidence, (Evidence.document_id == Document.id) & (Evidence.content_hash == Document.content_hash))
                .where(DataSource.adapter == adapter, Document.is_demo.is_(False), Document.deleted_at.is_(None),
                       Evidence.field == "affected", Evidence.method == "source_reported").limit(30)).all()
            chosen = next(((d, e) for d,e in candidates if e.value.get("cpe_matches" if adapter == "nvd" else "items")), None)
            if chosen is None:
                rows.append({"adapter": adapter, "passed": False, "reason": "no_recorded_affected_field"})
                continue
            doc, evidence = chosen
            response = client.get(f"http://127.0.0.1:{settings.api_port}/api/v1/documents/{doc.id}/enrichment")
            normalized = response.json()["normalized"]["affected"]
            items = normalized["items"]
            good = (response.status_code == 200 and normalized["content_hash"] == doc.content_hash and
                normalized["evidence_id"] == str(evidence.id) and normalized["source_url"] == doc.source_url and bool(items))
            if adapter == "nvd":
                good = good and all(i["fixed_version"] is None and i["applicability"] == "not_evaluated" for i in items)
            rows.append({"adapter": adapter, "document_id": str(doc.id), "content_hash": doc.content_hash,
                "source_url": doc.source_url, "passed": bool(good), "normalized": normalized})
    result = {"time": datetime.now(timezone.utc).isoformat(), "passed": all(r["passed"] for r in rows), "records": rows,
        "limits": "Deterministic representation of two recorded public-source fields, not complete affected-asset or safe-version determination"}
    path = ROOT / "output/normalized-enrichment.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "records": [{k:v for k,v in r.items() if k != "normalized"} for r in rows]}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
