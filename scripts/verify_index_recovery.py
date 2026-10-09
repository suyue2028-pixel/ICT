"""Online SQLite backup into a NEW private file; production RAG index is never replaced."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "reports/local/windows-fullstack"


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = LOCAL / ("index-recovery-" + stamp + ".db")
    if target.exists():
        raise SystemExit("Verification backup already exists; not overwritten")
    source = ROOT / "data/backend-rag.db"
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as live, sqlite3.connect(target) as backup:
        live.execute("BEGIN")
        tables = [r[0] for r in live.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {name: live.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
        live.backup(backup)
        restored = {name: backup.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
        integrity = backup.execute("PRAGMA quick_check").fetchone()[0]
    result = {"time": datetime.now(timezone.utc).isoformat(), "passed": counts == restored and integrity == "ok",
        "backup": str(target), "integrity": integrity, "source_counts": counts, "restored_counts": restored,
        "source_modified": False, "limits": "Consistent SQLite structure/row-count recovery; model cache and remote credentials are separate."}
    path = LOCAL / ("index-recovery-" + stamp + ".json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "backup": str(target), "integrity": integrity, "counts": restored}, ensure_ascii=False))


if __name__ == "__main__":
    main()
