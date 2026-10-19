"""Run the EXISTING Agent sync worker using this backend's private local config.

No new credentials/index/model installation and no paid LLM calls. Start the
existing authenticated Agent API first; do not start a duplicate sync worker.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-root", required=True, type=Path)
    parser.add_argument("--agent-python", required=True, type=Path)
    parser.add_argument("--backend-url", help="Non-secret loopback/LAN URL; default local API_PORT")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Require existing cached E5; do not download")
    parser.add_argument("--limit", type=int, choices=range(1, 21), default=5)
    parser.add_argument("--poll-interval", type=int, choices=range(1, 301), default=5)
    args = parser.parse_args()
    from backend.core.config import get_settings
    from urllib.parse import urlsplit
    settings = get_settings()
    if settings.index_provider != "external":
        raise ValueError("Set INDEX_PROVIDER=external in this backend's local configuration")
    agent_root, python = args.agent_root.resolve(), args.agent_python.resolve()
    worker = agent_root / "scripts/sync_backend.py"
    if not worker.is_file() or not python.is_file() or not (agent_root / "packages/rag_agent/src").is_dir():
        raise ValueError("Existing Agent root/runtime is required; no installation attempted")
    url = args.backend_url or f"http://127.0.0.1:{settings.api_port}"
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Backend URL must not contain credentials, paths or query parameters")
    env = dict(os.environ)
    for source, target in (("agent_tool_token", "RAG_BACKEND_TOOL_TOKEN"),
                           ("agent_write_token", "RAG_BACKEND_WRITE_TOKEN"),
                           ("agent_service_token", "RAG_SERVICE_TOKEN")):
        value = getattr(settings, source)
        if not value or not value.get_secret_value():
            raise ValueError("Required local Agent token is not configured")
        env[target] = value.get_secret_value()
    env.update(PYTHONPATH=str(agent_root / "packages/rag_agent/src"),
        RAG_BACKEND_URL=url.rstrip("/"), RAG_BACKEND_DB_PATH=str(agent_root / "data/backend-rag.db"),
        RAG_LLM_MODE="extractive")
    env.pop("RAG_LLM_KEY", None); env.pop("RAG_LLM_KEY_FILE", None)
    cache = agent_root / ".cache/huggingface"
    if cache.is_dir():
        env["HF_HOME"] = str(cache)
    if args.offline:
        env["HF_HUB_OFFLINE"] = "1"
    command = [str(python), str(worker), "--limit", str(args.limit), "--poll-interval", str(args.poll_interval)]
    if args.once:
        command.append("--once")
    # Tokens stay in the child's environment, never command arguments or output.
    return subprocess.call(command, cwd=agent_root, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print("Agent sync could not start: " + type(error).__name__ + "; check local paths/configuration", file=sys.stderr)
        raise SystemExit(2) from None
