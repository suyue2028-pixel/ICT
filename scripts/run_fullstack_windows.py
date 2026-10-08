r"""Windows bridge: existing Docker volumes + current backend source + local Agent/Vite.

Run with D:\cit\.venv\Scripts\python.exe. Never prints credentials or removes volumes.
Backend source is D:\cit\后端开发; .env and data volumes are retained. Only this launcher's recorded
Windows processes can be stopped; the existing single Docker beat is reused.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "reports/local/windows-fullstack"
FRONTEND = ROOT / "reports/local/integration/frontend"
SOURCE = Path(os.environ.get("ICT_EXISTING_BACKEND_DIR", r"D:\cit\后端开发"))
BACKEND = Path(os.environ.get("ICT_RUNTIME_BACKEND_DIR", str(SOURCE)))
PRIVATE = ROOT / ".secrets/windows-integration.env"
STATE = LOCAL / "processes.json"
OVERLAY = LOCAL / "compose.windows.json"
DOCKER = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe"
NODE = Path(os.environ.get("USERPROFILE", "")) / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
AGENT_PYTHON = ROOT / ".venv/Scripts/python.exe"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def configuration():
    values = read_env(SOURCE / ".env")
    required = ["POSTGRES_USER", "POSTGRES_DB", "ADMIN_API_KEY", "AGENT_TOOL_TOKEN", "AGENT_WRITE_TOKEN"]
    if any(not values.get(key) for key in required):
        raise RuntimeError("Required existing backend settings missing; no secrets printed")
    if not PRIVATE.exists():
        PRIVATE.parent.mkdir(parents=True, exist_ok=True)
        with PRIVATE.open("x", encoding="utf-8") as handle:
            handle.write("# Local Agent service credential; never commit.\nAGENT_SERVICE_TOKEN=" + secrets.token_hex(32) + "\n")
    values.update(read_env(PRIVATE))
    return {**os.environ, **values, "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}


def call(path, env, *, body=None, method=None, admin=False, headers=None, base=None):
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    if admin:
        request_headers["X-API-Key"] = env["ADMIN_API_KEY"]
    url = (base or "http://127.0.0.1:" + env.get("API_PORT", "8010")) + path
    payload = json.dumps(body).encode() if body is not None else None
    with OPENER.open(urllib.request.Request(url, data=payload, headers=request_headers, method=method), timeout=40) as response:
        return json.loads(response.read(2 * 1024 * 1024)), dict(response.headers)


def compose(env, *args, log=None):
    command = [str(DOCKER), "compose", "--project-directory", str(SOURCE), "--env-file", str(SOURCE / ".env"),
               "-f", str(SOURCE / "compose.yaml"), "-f", str(OVERLAY), *args]
    result = subprocess.run(command, cwd=SOURCE, env=env, stdout=log, stderr=subprocess.STDOUT if log else None)
    if result.returncode:
        raise RuntimeError("Docker operation failed; inspect windows-fullstack logs")


def identity(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        # An exited process may still have a handle; require a zero exit timestamp.
        if times[1].dwHighDateTime or times[1].dwLowDateTime:
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def alive(record):
    return identity(record["pid"]) == record["created"]


def stop_local(only=None):
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    retained = {}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for name, record in reversed(list(state.get("processes", {}).items())):
        if only is not None and name != only:
            retained[name] = record
            continue
        if not alive(record):
            continue
        handle = kernel.OpenProcess(0x0001 | 0x1000, False, record["pid"])
        if handle:
            try:
                if alive(record):
                    kernel.TerminateProcess(handle, 0)
            finally:
                kernel.CloseHandle(handle)
        time.sleep(0.1)
        if alive(record):
            retained[name] = record
    state["processes"] = retained
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if any(alive(record) for name, record in retained.items() if only is None or name == only):
        raise RuntimeError("Some managed processes remain; state retained")
    print("Managed local process stopped; existing Docker database and volumes retained")


def wait_ready(url, headers=None, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with OPENER.open(urllib.request.Request(url, headers=headers or {}), timeout=4) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("Service did not become ready; inspect project logs")


def token(env):
    session, _ = call("/api/v1/auth/tokens", env, body={"owner_id": "windows-local-integration", "expires_in_hours": 24}, admin=True)
    path = LOCAL / "session.json"
    path.write_text(json.dumps({key: session[key] for key in ["access_token", "expires_at"]}), encoding="utf-8")
    print("24-hour ordinary user session prepared; no administrator/provider key sent to frontend")


def agent_environment(env, mode):
    rag = {**env, "PYTHONPATH": str(ROOT / "packages/rag_agent/src"), "HF_HOME": str(ROOT / ".cache/huggingface"),
           "HF_HUB_OFFLINE": "1", "RAG_BACKEND_DB_PATH": str(ROOT / "data/backend-rag.db"),
           "RAG_BACKEND_URL": "http://127.0.0.1:" + env.get("API_PORT", "8010"),
           "RAG_SERVICE_TOKEN": env["AGENT_SERVICE_TOKEN"], "RAG_BACKEND_TOOL_TOKEN": env["AGENT_TOOL_TOKEN"],
           "RAG_BACKEND_WRITE_TOKEN": env["AGENT_WRITE_TOKEN"], "RAG_LLM_MODE": mode,
           "RAG_LLM_MODEL": env.get("DEEPSEEK_MODEL", "deepseek-flash")}
    if mode == "live":
        if not env.get("DEEPSEEK_API_KEY"):
            raise RuntimeError("DeepSeek key missing from existing local .env")
        rag["RAG_LLM_KEY"] = env["DEEPSEEK_API_KEY"]
    return rag


def restart_agent(mode=None):
    """Replace only the Agent PID this launcher owns, keeping the existing index."""
    if not STATE.exists():
        raise RuntimeError("Managed state missing; use start instead")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    selected_mode = mode or state.get("generation_mode", "extractive")
    env = configuration()
    rag = agent_environment(env, selected_mode)
    stop_local(only="agent")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 9001))
        except OSError:
            raise RuntimeError("Port 9001 still occupied; no unmanaged process stopped") from None
    with (LOCAL / "logs/agent.log").open("a", encoding="utf-8") as log:
        child = subprocess.Popen([str(AGENT_PYTHON), "-m", "uvicorn", "examples.backend_api:app",
            "--host", "127.0.0.1", "--port", "9001"], cwd=ROOT, env=rag,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    created = identity(child.pid)
    if created is None:
        raise RuntimeError("Agent failed to start; inspect logs")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    state["processes"]["agent"] = {"pid": child.pid, "created": created}
    state["generation_mode"] = selected_mode
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    wait_ready("http://127.0.0.1:9001/v1/health", {"Authorization": "Bearer " + env["AGENT_SERVICE_TOKEN"]}, seconds=60)
    print("Agent restarted on 9001; generation=" + selected_mode)


def start(mode, rebuild=True):
    env = configuration()
    LOCAL.mkdir(parents=True, exist_ok=True)
    (LOCAL / "logs").mkdir(exist_ok=True)
    if STATE.exists() and any(alive(item) for item in json.loads(STATE.read_text(encoding="utf-8")).get("processes", {}).values()):
        raise RuntimeError("Managed stack is already running; use status or stop")
    for port in [5173, 9001]:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(f"Port {port} is occupied; no existing process stopped") from None
    if mode == "live" and not env.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DeepSeek key missing from existing local .env")
    # Health metadata alone does not prove embeddings are usable. Refuse to
    # launch sync or alter Docker configuration until a real cached vector works.
    model_env = {**env, "PYTHONPATH": str(ROOT / "packages/rag_agent/src"),
                 "HF_HOME": str(ROOT / ".cache/huggingface"), "HF_HUB_OFFLINE": "1"}
    with (LOCAL / "logs/model-check.log").open("a", encoding="utf-8") as log:
        result = subprocess.run([str(AGENT_PYTHON), "-c",
            "import asyncio; from rag_agent.config import Settings; from rag_agent.providers.embedding import E5Embedding; "
            "s=Settings.from_env(); p=E5Embedding(s.embedding_model,s.embedding_revision,s.embedding_device); "
            "v=asyncio.run(p.query('model readiness')); assert len(v)==384; print('Real cached E5 vector verified')"],
            cwd=ROOT, env=model_env, stdout=log, stderr=log)
    if result.returncode:
        raise RuntimeError("Real E5 model not ready. Download it first; sync was not started")
    shared = {"INDEX_PROVIDER": "external", "AGENT_SERVICE_URL": "http://host.docker.internal:9001",
              "AGENT_SERVICE_TOKEN": "${AGENT_SERVICE_TOKEN:?Local service credential required}"}
    OVERLAY.write_text(json.dumps({"services": {
        "migrate": {"build": {"context": str(BACKEND)}, "environment": shared},
        **{name: {"environment": shared} for name in ["api", "worker", "beat"]}}}, indent=2), encoding="utf-8")
    with (LOCAL / "logs/docker.log").open("a", encoding="utf-8") as log:
        compose(env, "config", "--quiet", log=log)
        if rebuild:
            # Snapshot existing database before changing the application image.
            dump = LOCAL / ("pre-integration-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".dump")
            with dump.open("xb") as handle:
                subprocess.run([str(DOCKER), "compose", "-f", str(SOURCE / "compose.yaml"), "exec", "-T", "postgres",
                                "pg_dump", "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"], "-Fc"],
                               cwd=SOURCE, env=env, stdout=handle, stderr=log, check=True)
            # Keep a tag of the previous app image for a non-destructive rollback.
            existing_tag = subprocess.run([str(DOCKER), "image", "inspect", "cit-backend-development:pre-windows-integration", "--format", "{{.Id}}"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if existing_tag.returncode:
                subprocess.run([str(DOCKER), "tag", "cit-backend-development:local", "cit-backend-development:pre-windows-integration"],
                               stdout=log, stderr=log, check=True)
            compose(env, "build", "migrate", log=log)
        compose(env, "run", "--rm", "migrate", log=log)
        compose(env, "up", "-d", "api", "worker", "beat", log=log)
    wait_ready("http://127.0.0.1:" + env.get("API_PORT", "8010") + "/health/ready", seconds=60)
    rag = agent_environment(env, mode)
    state = {"generation_mode": mode, "processes": {}, "created_at": datetime.now(timezone.utc).isoformat()}

    def spawn(name, command, cwd, environment):
        with (LOCAL / ("logs/" + name + ".log")).open("a", encoding="utf-8") as log:
            child = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        created = identity(child.pid)
        if created is None:
            raise RuntimeError(name + " failed to start; inspect logs")
        state["processes"][name] = {"pid": child.pid, "created": created}
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        spawn("agent", [str(AGENT_PYTHON), "-m", "uvicorn", "examples.backend_api:app", "--host", "127.0.0.1", "--port", "9001"], ROOT, rag)
        wait_ready("http://127.0.0.1:9001/v1/health", {"Authorization": "Bearer " + env["AGENT_SERVICE_TOKEN"]})
        spawn("sync", [str(AGENT_PYTHON), str(ROOT / "scripts/sync_backend.py"), "--all-scopes", "--poll-interval", "5"], ROOT,
              {**rag, "RAG_LLM_MODE": "extractive"})
        token(env)
        spawn("frontend", [str(NODE), str(FRONTEND / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5173", "--strictPort"], FRONTEND,
              {**env, "ICT_LOCAL_SESSION_FILE": str(LOCAL / "session.json"), "ICT_BACKEND_ORIGIN": "http://127.0.0.1:" + env.get("API_PORT", "8010")})
        wait_ready("http://127.0.0.1:5173", seconds=30)
    except Exception:
        stop_local()
        raise
    print("READY frontend=http://127.0.0.1:5173 backend=http://127.0.0.1:8010 agent=http://127.0.0.1:9001 generation=" + mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status", "token", "restart-agent", "restore-backend"])
    parser.add_argument("--mode", choices=["extractive", "live"])
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "start":
            start(args.mode or "extractive", not args.no_build)
        elif args.command == "restart-agent":
            restart_agent(args.mode)
        elif args.command == "stop":
            stop_local()
        elif args.command == "token":
            token(configuration())
        elif args.command == "status":
            state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
            print(json.dumps({"generation_mode": state.get("generation_mode"), "processes": {
                key: "running" if alive(value) else "stopped" for key, value in state.get("processes", {}).items()}}, indent=2))
        else:
            stop_local()
            subprocess.run([str(DOCKER), "tag", "cit-backend-development:pre-windows-integration", "cit-backend-development:local"], check=True)
            subprocess.run([str(DOCKER), "compose", "-f", str(SOURCE / "compose.yaml"), "up", "-d", "--force-recreate", "api", "worker", "beat"], cwd=SOURCE, check=True)
            print("Original backend image and configuration restored; database volumes retained")
    except Exception as error:
        import traceback
        frames = traceback.extract_tb(error.__traceback__)
        print(str(error) if isinstance(error, RuntimeError) else "Runtime operation failed: " + type(error).__name__)
        print("Location: " + " -> ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
