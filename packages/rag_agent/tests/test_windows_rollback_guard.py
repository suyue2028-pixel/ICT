"""An old-image rollback must fail before any managed process is stopped."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher uses Windows runtime paths")


def launcher():
    path = Path(__file__).resolve().parents[3] / "scripts/run_fullstack_windows.py"
    spec = importlib.util.spec_from_file_location("windows_launcher_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rollback_refuses_unknown_schema_without_mutation(monkeypatch):
    m = launcher()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=b"0007_runtime_observations\n" if "psql" in command else json.dumps(["0005_document_invalidation"]).encode())
    monkeypatch.setattr(m.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Rollback refused"):
        m.ensure_rollback_schema({"POSTGRES_USER": "test-only", "POSTGRES_DB": "test-only"}, "test-only-image")
    assert len(calls) == 2 and "--network=none" in calls[1] and "--pull=never" in calls[1]
    assert not any("tag" in c or "stop" in c for c in calls)


def test_rollback_checks_matching_head(monkeypatch):
    m = launcher()
    def run(command, **kwargs):
        return SimpleNamespace(stdout=b"0007_runtime_observations\n" if "psql" in command else b'["0007_runtime_observations"]')
    monkeypatch.setattr(m.subprocess, "run", run)
    m.ensure_rollback_schema({"POSTGRES_USER": "test-only", "POSTGRES_DB": "test-only"}, "test-only-image")
