"""Launcher credential-boundary tests; no services or paid requests are started."""
from pathlib import Path
from scripts import run_fullstack as launcher


def synthetic_env():
    return {"AGENT_SERVICE_TOKEN": "synthetic-service", "AGENT_TOOL_TOKEN": "synthetic-tool",
            "AGENT_WRITE_TOKEN": "synthetic-write", "RAG_LLM_KEY": "synthetic-supplier",
            "DEEPSEEK_API_KEY": "synthetic-deepseek", "DASHSCOPE_API_KEY": "synthetic-qwen",
            "EMBEDDING_API_KEY": "synthetic-embedding", "NVD_API_KEY": "synthetic-nvd",
            "LLM_API_KEY_FILE": "/private/secret-key-file", "VITE_LLM_API_KEY": "synthetic-client-bad",
            "TAVILY_API_KEY": "synthetic-search", "TAVILY_API_KEY_FILE": "/private/search-key-file"}


def test_agent_and_sync_do_not_receive_supplier_credentials():
    env = launcher.agent_environment(synthetic_env(), Path("/unused/backend"), live=True)
    assert env["RAG_LLM_TRANSPORT"] == "backend" and env["RAG_LLM_MODE"] == "live"
    assert env["RAG_LLM_SERVICE_TOKEN"] == "synthetic-tool"
    assert not any(name in env for name in launcher.MODEL_CREDENTIAL_NAMES)
    assert "VITE_LLM_API_KEY" not in env
    assert "synthetic-supplier" not in env.values() and "/private/secret-key-file" not in env.values()


def test_backend_receives_file_path_without_launcher_opening_it(tmp_path, monkeypatch):
    def prohibited_read(*args, **kwargs):
        raise AssertionError("launcher must not read the model key")
    monkeypatch.setattr(Path, "read_text", prohibited_read)
    env = launcher.backend_environment({"LLM_API_KEY_FILE": "/private/secret-key-file"}, tmp_path, live=True)
    assert env["LLM_API_KEY_FILE"] == "/private/secret-key-file"
    assert env["LLM_PROVIDER"] == "deepseek" and env["LLM_MODEL"] == "deepseek-flash"


def test_legacy_key_only_migrates_to_backend(tmp_path):
    env = {"RAG_LLM_KEY_FILE": "/private/legacy-key"}
    backend = launcher.backend_environment(env, tmp_path, live=True)
    assert backend["LLM_API_KEY_FILE"] == "/private/legacy-key"
    assert "RAG_LLM_KEY_FILE" not in launcher.without_model_credentials(backend)
    assert "LLM_API_KEY_FILE" not in launcher.without_model_credentials(backend)


def test_frontend_has_no_supplier_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "PRIVATE", tmp_path / "fullstack.env")
    env = launcher.frontend_environment(synthetic_env(), "/local/node")
    assert not any(name in env for name in launcher.MODEL_CREDENTIAL_NAMES)
    assert "VITE_LLM_API_KEY" not in env
    assert (tmp_path / "fullstack-agent-status.env").read_text() == "RAG_SERVICE_TOKEN=synthetic-service\n"


def test_extractive_agent_does_not_require_model_key(tmp_path):
    env = launcher.agent_environment({"AGENT_SERVICE_TOKEN": "service", "AGENT_TOOL_TOKEN": "tool",
                                     "AGENT_WRITE_TOKEN": "write"}, tmp_path, live=False)
    assert env["RAG_LLM_MODE"] == "extractive"
    assert not any(name in env for name in launcher.MODEL_CREDENTIAL_NAMES)


def test_switch_to_qwen_preserves_backend_provider_default_model(tmp_path):
    env = launcher.backend_environment({"LLM_PROVIDER": "qwen", "DASHSCOPE_API_KEY": "synthetic-qwen"}, tmp_path, live=True)
    assert env["LLM_PROVIDER"] == "qwen"
    assert "LLM_MODEL" not in env
