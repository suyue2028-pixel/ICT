"""Scan public source candidates; report paths only, never matched secret values."""
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIRS = ("backend", "migrations", "tests", "scripts", "docs", ".github", ".vscode")
PATTERNS = [re.compile(rb"sk-[A-Za-z0-9_-]{24,}"),
            re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")]


def main():
    paths = [p for directory in PUBLIC_DIRS for p in (ROOT / directory).rglob("*")
             if p.is_file() and "__pycache__" not in p.parts]
    paths += [p for p in ROOT.iterdir() if p.is_file() and p.name in {
        "README.md", "pyproject.toml", "uv.lock", "requirements.txt", "requirements-dev.txt",
        "Dockerfile", "compose.yaml", ".env.example", ".gitignore", ".dockerignore", "alembic.ini"}]
    repository_workflow = ROOT.parent / ".github/workflows/backend-ci.yml"
    if repository_workflow.is_file():
        paths.append(repository_workflow)
    # Compare configured values in memory; they are never included in diagnostics.
    import sys
    sys.path.insert(0, str(ROOT))
    from backend.core.config import get_settings
    settings = get_settings()
    secrets = [getattr(settings, name) for name in ("postgres_password", "redis_password", "admin_api_key",
        "deepseek_api_key", "agent_tool_token", "agent_write_token", "agent_service_token", "embedding_api_key")]
    values = [s.get_secret_value().encode() for s in secrets if s and len(s.get_secret_value()) >= 16
              and not s.get_secret_value().startswith("ci-only-")]
    findings = set()
    for path in paths:
        content = path.read_bytes()
        if any(pattern.search(content) for pattern in PATTERNS) or any(value in content for value in values):
            findings.add(str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.relative_to(ROOT.parent)))
    # In a Git checkout additionally reject accidentally tracked local-only files.
    git = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True)
    tracked_violations = []
    if git.returncode == 0:
        for name in git.stdout.decode("utf-8").split("\0"):
            p = Path(name)
            if name and ((p.name.startswith(".env") and p.name != ".env.example")
                    or set(p.parts) & {".venv", "__pycache__", "output", ".secrets"}
                    or p.suffix.lower() in {".dump", ".sqlite", ".db", ".log"}):
                tracked_violations.append(name)
    result = {"passed": not findings and not tracked_violations, "files_scanned": len(paths),
        "suspect_files": sorted(findings), "forbidden_tracked_paths": tracked_violations,
        "limits": "Configured credential and common token patterns; not a proof that every possible secret format is absent."}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
