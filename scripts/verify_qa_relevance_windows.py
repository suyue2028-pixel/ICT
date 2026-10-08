"""Bounded live QA comparison; private configuration never enters the report."""
import json
import sys
import time
from datetime import datetime, timezone
from run_fullstack_windows import LOCAL, configuration, call, token

QUESTIONS = [
    ("access", "访问控制相关情报有哪些？", None),
    ("audit", "智能体工具审计有什么风险？", None),
    ("exact", "CVE-2026-73558 的风险有哪些？请根据来源用中文说明。", None),
    ("follow", "它影响哪些版本？", "exact"),
    ("follow2", "怎么修复？", "follow"),
    ("switch", "数据投毒有哪些风险？", "follow2"),
    ("absent", "CVE-2099-99999999 的修复版本是什么？", None),
]

def main():
    phase = sys.argv[1]
    if phase not in {"before", "after"}:
        raise SystemExit("Use before or after")
    env = configuration()
    token(env)
    session = json.loads((LOCAL / "session.json").read_text(encoding="utf-8"))
    headers = {"Authorization": "Bearer " + session["access_token"]}
    rows, sessions = [], {}
    only = sys.argv[2] if len(sys.argv) > 2 else None
    path = LOCAL / ("qa-relevance-" + phase + ("-" + only if only else "") + ".json")
    if path.exists():
        archive = path.with_name(path.stem + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + path.suffix)
        archive.write_bytes(path.read_bytes())
    for name, question, parent in QUESTIONS:
        if only and name != only:
            continue
        body = {"query": question, "is_demo": False}
        if parent:
            if parent not in sessions:
                continue
            body["session_id"] = sessions[parent]
        started = time.monotonic()
        try:
            answer, meta = call("/api/v1/agent/query", env, body=body, headers=headers, base="http://127.0.0.1:5173")
            sessions[name] = answer["session_id"]
            meta = {k.lower(): v for k, v in meta.items()}
            row = {"name": name, "question": question, "result": answer,
                   "mode": meta.get("x-rag-answer-mode"), "llm_calls": meta.get("x-rag-llm-calls")}
        except Exception as exc:
            row = {"name": name, "question": question, "error": type(exc).__name__, "http_status": getattr(exc, "code", None)}
        row["duration_ms"] = round((time.monotonic() - started) * 1000)
        rows.append(row)
        path.write_text(json.dumps({"phase": phase, "time": datetime.now(timezone.utc).isoformat(), "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in row.items() if k not in {"question", "result"}}, ensure_ascii=False), flush=True)
    return 1 if any("error" in row for row in rows) else 0

if __name__ == "__main__":
    raise SystemExit(main())
