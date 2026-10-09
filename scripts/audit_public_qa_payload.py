"""Read-only provenance audit before bounded external QA; never prints credentials/content."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit
from run_fullstack_windows import configuration, LOCAL, SOURCE
sys.path.insert(0, str(SOURCE))
from backend.db.session import get_session_factory
from backend.models import DataSource, Document
from sqlalchemy import select

PUBLIC_ADAPTERS = {"osv", "nvd", "cisco", "sans", "huggingface", "trailofbits", "nvidia", "crossref", "arxiv", "nist", "govuk", "china_policy"}


def main():
    env = configuration()
    secret_names = ("ADMIN_API_KEY", "DEEPSEEK_API_KEY", "AGENT_SERVICE_TOKEN", "AGENT_TOOL_TOKEN", "AGENT_WRITE_TOKEN",
                    "POSTGRES_PASSWORD", "REDIS_PASSWORD", "EMBEDDING_API_KEY")
    values = [env[k] for k in secret_names if env.get(k) and len(env[k]) >= 8]
    with get_session_factory()() as db:
        rows = db.execute(select(Document, DataSource).join(DataSource, Document.source_id == DataSource.id)
            .where(Document.is_demo.is_(False), Document.deleted_at.is_(None))).all()
        unknown = sum(source.adapter not in PUBLIC_ADAPTERS for document, source in rows)
        hosts = sorted({urlsplit(document.source_url).hostname for document, source in rows})
        secret_hits = sum(any(secret in document.content or secret in json.dumps(document.raw_payload, ensure_ascii=False)
            for secret in values) for document, source in rows)
        categories = sorted({source.category for document, source in rows})
    # RAG prompts read indexed evidence, not .env/Windows files. Audit the actual
    # persisted evidence strings as well as the recorded source material.
    with sqlite3.connect((LOCAL.parents[2] / "data/backend-rag.db").as_uri() + "?mode=ro", uri=True) as connection:
        columns = [r[1] for r in connection.execute("PRAGMA table_info(evidence)")]
        evidence = connection.execute("SELECT * FROM evidence").fetchall()
        index_secret_hits = sum(any(secret in json.dumps(row, ensure_ascii=False, default=str) for secret in values) for row in evidence)
    base = env.get("RAG_LLM_BASE_URL", "https://api.deepseek.com")
    parsed = urlsplit(base)
    report = {"time": datetime.now(timezone.utc).isoformat(), "public_source_documents": len(rows),
        "source_categories": categories, "unknown_source_adapters": unknown, "source_hosts": hosts,
        "known_credential_matches_in_source": secret_hits, "indexed_evidence_rows": len(evidence),
        "known_credential_matches_in_evidence": index_secret_hits,
        "provider_origin": parsed.scheme + "://" + (parsed.hostname or ""),
        "planned_validation": "2 new-session English questions before restart; 9 questions after; max 3 HTTP attempts per question, no new paid service",
        "history_scope": "new validation sessions only; no pre-existing user conversation sent",
        "authorization": "User explicitly requested finite real DeepSeek validation through the frontend's backend entrypoint",
        "limits": "Provenance/known-secret audit only; not a universal PII classifier or proof of public content accuracy"}
    path = LOCAL / "public-qa-payload-audit.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
