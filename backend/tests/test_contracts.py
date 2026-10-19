import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.collectors.registry import get_collector
from backend.collectors.live import parse_osv, parse_rss
from backend.core.config import get_settings
from backend.main import app
from backend.schemas.contracts import RawRecord


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def test_liveness_and_request_id(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["scope"] == "development-framework"
    assert response.headers["x-request-id"]


def test_collection_requires_admin(client):
    response = client.post("/api/v1/demo/import")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_qa_explicitly_unavailable(client, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: SimpleNamespace(
        deepseek_api_key=None, ai_service_url=None))
    key = get_settings().admin_api_key.get_secret_value()
    response = client.post("/api/v1/qa/ask", json={"question": "AI 模块实现了吗？"},
                           headers={"X-API-Key": key})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_service_not_configured"


def test_semantic_and_conversation_interfaces_require_admin(client):
    semantic = client.post("/api/v1/qa/semantic-search", json={"question": "torch"})
    conversation = client.post("/api/v1/qa/conversations", json={"is_demo": True})
    assert semantic.status_code == conversation.status_code == 401
    assert semantic.json()["error"]["code"] == "invalid_api_key"


def test_invalid_pagination_is_rejected_without_echoing_input(client):
    response = client.get("/api/v1/intelligence?page_size=101")
    assert response.status_code == 422
    assert "input" not in response.json()


def test_real_adapters_and_parsers_never_mark_demo_records():
    for adapter in ("osv", "cisco", "sans"):
        assert get_collector(adapter, {}).adapter == adapter
    osv = parse_osv({"id": "GHSA-AAAA-BBBB-CCCC", "summary": "torch issue",
                     "affected": [{"package": {"name": "torch"}}]})
    assert not osv.is_demo and osv.source_fields["affected"]["items"]
    xml = (b"<rss><channel><item><title>AI security CVE-2025-12345</title>"
           b"<link>https://example.org/advisory</link><description>report</description>"
           b"</item></channel></rss>")
    for adapter in ("cisco", "sans"):
        records = parse_rss(xml, adapter)
        assert len(records) == 1 and not records[0].is_demo


def test_collectors_must_mark_demo_and_use_timezone():
    data = {"external_id": "test", "title": "test", "content": "test",
            "source_url": "https://example.invalid/test", "document_type": "article"}
    with pytest.raises(ValidationError):
        RawRecord.model_validate(data)
    data.update(is_demo=True, published_at="2026-01-01T00:00:00")
    with pytest.raises(ValidationError):
        RawRecord.model_validate(data)
