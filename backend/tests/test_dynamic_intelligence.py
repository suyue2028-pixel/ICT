"""Bounded official lookup tests with mocked HTTP only; no real network proof.

Tests use synthetic source payloads and never write the project database.
"""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from backend.api.v1 import dynamic_intelligence as api
from backend.db.session import get_db
from backend.services import dynamic_intelligence as dynamic

CVE = "CVE-2099-12345"
REAL_LIMIT = dynamic._limit


@pytest.fixture(autouse=True)
def isolated_cache_and_settings(monkeypatch):
    dynamic._CACHE.clear()
    dynamic._LAST.clear()
    monkeypatch.setattr(dynamic, "_limit", lambda _: None)
    monkeypatch.setattr(dynamic, "get_settings", lambda: SimpleNamespace(nvd_api_key=None))
    yield
    dynamic._CACHE.clear()
    dynamic._LAST.clear()


def mocked_http(monkeypatch, handler):
    calls, options = [], []
    real = httpx.Client
    def checked(request):
        calls.append(request)
        return handler(request)
    def factory(**kwargs):
        options.append(kwargs)
        return real(transport=httpx.MockTransport(checked), **kwargs)
    monkeypatch.setattr(dynamic.httpx, "Client", factory)
    return calls, options


def nvd_payload(identifier=CVE):
    return {"vulnerabilities": [{"cve": {"id": identifier,
        "published": "2099-01-01T12:00:00.000", "lastModified": "2099-01-02T13:00:00.000",
        "descriptions": [{"lang": "en", "value": "Synthetic AI agent file access issue."}],
        "metrics": {"cvssMetricV31": [
            {"source": "nvd@nist.gov", "cvssData": {"version": "3.1", "baseScore": 7.5, "baseSeverity": "HIGH", "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"}},
            {"source": "vendor.example", "cvssData": {"version": "3.1", "baseScore": 8.1, "baseSeverity": "HIGH", "vectorString": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H"}}]},
        "configurations": [{"nodes": [{"operator": "AND", "cpeMatch": [{"vulnerable": True,
            "criteria": "cpe:2.3:a:synthetic:agent:*:*:*:*:*:*:*:*", "versionEndExcluding": "9.9.9"}]}]}],
        "references": [{"url": "https://vendor.example/patch", "tags": ["Patch"]},
                       {"url": "https://vendor.example/advisory", "tags": ["Third Party Advisory"]}]}}]}


def test_fixed_nvd_query_preserves_source_scores_dates_and_unresolved_ranges(monkeypatch):
    calls, options = mocked_http(monkeypatch, lambda _: httpx.Response(200, json=nvd_payload()))
    value, checked, cached = dynamic.fetch("nvd_lookup", identifier=CVE)
    assert len(value["records"]) == 1 and not cached
    record = value["records"][0]
    assert str(calls[0].url).split("?")[0] == dynamic.NVD_URL
    assert calls[0].url.params["cveId"] == CVE and calls[0].url.params["resultsPerPage"] == "1"
    assert options[0]["follow_redirects"] is False and options[0]["trust_env"] is False
    assert record.external_id == CVE and record.vulnerability_id == CVE and record.is_demo is False
    assert str(record.source_url) == f"https://nvd.nist.gov/vuln/detail/{CVE}"
    assert record.published_at == datetime(2099, 1, 1, 12, tzinfo=timezone.utc)
    assert record.source_updated_at == datetime(2099, 1, 2, 13, tzinfo=timezone.utc)
    vectors = record.source_fields["cvss"]["vectors"]
    assert [(v["score"], v["source"]) for v in vectors] == [(7.5, "nvd@nist.gov"), (8.1, "vendor.example")]
    assert record.source_fields["affected"]["cpe_matches"][0]["versionEndExcluding"] == "9.9.9"
    assert record.source_fields["remediation"]["references"] == [{"url": "https://vendor.example/patch", "tags": ["Patch"]}]
    assert "fixed_version" not in record.source_fields
    assert datetime.fromisoformat(checked).tzinfo is not None


def test_nvd_entity_mismatch_rejected_and_failure_not_cached(monkeypatch):
    calls, _ = mocked_http(monkeypatch, lambda _: httpx.Response(200, json=nvd_payload("CVE-2099-99999")))
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_entity_mismatch"):
        dynamic.fetch("nvd_lookup", identifier=CVE)
    assert len(calls) == 1 and not dynamic._CACHE


@pytest.mark.parametrize("status,code", [(429, "intelligence_rate_limited"), (503, "intelligence_source_unavailable")])
def test_http_failure_never_becomes_no_vulnerability_fact(monkeypatch, status, code):
    calls, _ = mocked_http(monkeypatch, lambda _: httpx.Response(status, json={"error": "SYNTHETIC_DO_NOT_DISCLOSE"}))
    with pytest.raises(dynamic.IntelligenceError, match=code) as failed:
        dynamic.fetch("nvd_lookup", identifier=CVE)
    assert "SYNTHETIC_DO_NOT_DISCLOSE" not in str(failed.value) and not dynamic._CACHE
    assert len(calls) == (1 if status == 429 else 2)


def test_timeout_is_safe_failure_not_empty_result(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_BODY", request=request)
    calls, _ = mocked_http(monkeypatch, timeout)
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_timeout") as failed:
        dynamic.fetch("nvd_lookup", identifier=CVE)
    assert "SYNTHETIC_PRIVATE_BODY" not in str(failed.value) and not dynamic._CACHE
    assert len(calls) == 2


def test_kev_absence_is_catalogue_limited_and_checked_time_is_preserved_on_cache(monkeypatch):
    clock = [100.0]
    times = [datetime(2099, 2, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(dynamic.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(dynamic, "utc_now", lambda: times[0])
    calls, _ = mocked_http(monkeypatch, lambda _: httpx.Response(200, json={
        "dateReleased": "2099-01-31T00:00:00Z", "catalogVersion": "test-1", "vulnerabilities": []}))
    first, checked, cache = dynamic.fetch("kev_lookup", identifier=CVE)
    assert first["records"] == [] and first["note"] == "kev_no_entry_is_not_proof_of_no_exploitation"
    assert not cache and str(calls[0].url) == dynamic.CISA_URL
    clock[0] += 60
    times[0] += timedelta(minutes=1)
    second, checked_again, cache = dynamic.fetch("kev_lookup", identifier=CVE)
    assert cache and checked_again == checked and second == first and len(calls) == 1
    clock[0] += 901
    _, renewed_checked, cache = dynamic.fetch("kev_lookup", identifier=CVE)
    assert not cache and renewed_checked != checked and len(calls) == 2


def test_kev_official_mirror_is_bounded_fallback(monkeypatch):
    row = {"cveID": CVE, "product": "synthetic-agent", "requiredAction": "Apply vendor guidance."}
    def handler(request):
        if str(request.url) == dynamic.CISA_URL:
            return httpx.Response(503)
        assert str(request.url) == dynamic.CISA_MIRROR
        return httpx.Response(200, json={"dateReleased": "2099-01-31T00:00:00Z", "vulnerabilities": [row]})
    calls, _ = mocked_http(monkeypatch, handler)
    value, _, _ = dynamic.fetch("kev_lookup", identifier=CVE)
    assert len(calls) == 3 and str(value["records"][0].source_url) == dynamic.CISA_MIRROR
    assert value["records"][0].source_fields["exploitation"]["entry"] == row


def test_osv_uses_explicit_package_version_and_preserves_bounded_results(monkeypatch):
    payload = {"vulns": [{"id": "GHSA-aaaa-bbbb-cccc", "summary": "Synthetic agent package issue",
        "aliases": [CVE], "affected": [{"package": {"ecosystem": "PyPI", "name": "synthetic-agent"},
        "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "9.9.9"}]}]}]}],
        "next_page_token": "synthetic-next"}
    calls, _ = mocked_http(monkeypatch, lambda _: httpx.Response(200, json=payload))
    value, _, _ = dynamic.fetch("osv_lookup", ecosystem="PyPI", package="synthetic-agent", version="1.0.0")
    assert str(calls[0].url) == dynamic.OSV_URL and calls[0].method == "POST"
    assert json.loads(calls[0].content) == {"package": {"ecosystem": "PyPI", "name": "synthetic-agent"}, "version": "1.0.0"}
    assert value["note"] == "osv_bounded_first_page_not_complete" and value["records"][0].is_demo is False


def test_knowledge_lookup_uses_only_registered_topic_and_source_body(monkeypatch):
    html = '<h1>LLM Excessive Agency</h1><main>' + 'Synthetic LLM agency tool permission boundary. ' * 10 + '</main>'
    calls, _ = mocked_http(monkeypatch, lambda _: httpx.Response(200, content=html.encode()))
    value, _, _ = dynamic.fetch("knowledge_lookup", topic="agency")
    assert str(calls[0].url) == dynamic.OWASP["agency"]
    assert value["records"][0].is_demo is False and value["records"][0].metadata_json["publisher"] == "OWASP"
    with pytest.raises((KeyError, dynamic.IntelligenceError)):
        dynamic.fetch("knowledge_lookup", topic="http://127.0.0.1/private")
    assert len(calls) == 1


def test_knowledge_title_comes_from_risk_body_not_site_advertisement(monkeypatch):
    html='<h1>Join our unrelated event</h1><main><h2>LLM06:2025 Excessive Agency</h2>'+'Synthetic LLM agency controls. '*20+'</main>'
    mocked_http(monkeypatch,lambda _:httpx.Response(200,content=html.encode()))
    value,_,_=dynamic.fetch('knowledge_lookup',topic='agency')
    assert value['records'][0].title=='LLM06:2025 Excessive Agency'
    assert 'unrelated event' not in value['records'][0].content


@pytest.mark.parametrize("body", [
    {"tool": "shell", "command": "env"},
    {"tool": "knowledge_lookup", "topic": "agency", "url": "http://127.0.0.1/private"},
    {"tool": "knowledge_lookup", "topic": "unregistered"},
    {"tool": "nvd_lookup", "identifier": "not-a-cve"},
    {"tool": "osv_lookup", "ecosystem": "PyPI", "package": "synthetic-agent"},
    {"tool": "osv_lookup", "ecosystem": "PyPI", "version": "1.0.0"},
])
def test_request_whitelist_rejects_urls_unknown_tools_and_missing_package(body):
    with pytest.raises(ValidationError):
        api.LookupRequest.model_validate(body)


def test_dynamic_api_requires_write_credential_and_classifies_timeout(monkeypatch):
    config = SimpleNamespace(admin_api_key=SecretStr("synthetic-admin"), agent_write_token=SecretStr("synthetic-write"))
    monkeypatch.setattr("backend.core.security.get_settings", lambda: config)
    def fails(db, args):
        raise dynamic.IntelligenceError("intelligence_timeout")
    monkeypatch.setattr(api, "lookup", fails)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: object()
    with TestClient(app) as client:
        path = "/api/v1/internal/intelligence/lookup"
        data = {"tool": "nvd_lookup", "identifier": CVE}
        assert client.post(path, json=data, headers={"Authorization": "Bearer synthetic-user"}).status_code == 401
        response = client.post(path, json=data, headers={"X-Agent-Token": "synthetic-write"})
        assert response.status_code == 504 and response.json()["detail"] == "intelligence_timeout"
        assert client.post(path, json={**data, "url": "https://untrusted.example"}, headers={"X-Agent-Token": "synthetic-write"}).status_code == 422


@pytest.mark.parametrize("payload", [[], None, {"vulnerabilities": ["invalid-item"]},
    {"vulnerabilities": [{"cve": "invalid-cve"}]}])
def test_nvd_malformed_envelopes_are_safe_invalid_response_not_empty(monkeypatch, payload):
    mocked_http(monkeypatch, lambda _: httpx.Response(200, content=json.dumps(payload).encode()))
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_invalid_response"):
        dynamic.fetch("nvd_lookup", identifier=CVE)
    assert not dynamic._CACHE


def test_unknown_service_tool_is_rejected_before_any_network(monkeypatch):
    calls, _ = mocked_http(monkeypatch, lambda _: pytest.fail("unknown tool must not fetch"))
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_tool_unavailable"):
        dynamic.fetch("arbitrary_url", identifier="http://127.0.0.1/private")
    assert calls == []


def test_bodyless_nvd_not_found_has_no_invented_record(monkeypatch):
    mocked_http(monkeypatch, lambda _: httpx.Response(404))
    value, _, _ = dynamic.fetch("nvd_lookup", identifier=CVE)
    assert value["records"] == []
    assert not any("not_vulnerable" in str(v) for v in value.values())


@pytest.mark.parametrize("body", [b"", b" ", b"{}", b"not-json", b'{"vulnerabilities":null}'])
def test_nvd_successful_empty_or_malformed_body_is_invalid(monkeypatch, body):
    calls, _ = mocked_http(monkeypatch, lambda _: httpx.Response(200, content=body))
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_invalid_response"):
        dynamic.fetch("nvd_lookup", identifier=CVE)
    assert len(calls) == 1 and not dynamic._CACHE


@pytest.mark.parametrize("released", ["not-a-date", "2099-02-30T00:00:00Z", 42, None, "2099-01-01T00:00:00"])
def test_kev_catalogue_requires_valid_timezone_aware_release_time(monkeypatch, released):
    mocked_http(monkeypatch, lambda _: httpx.Response(200, json={"dateReleased": released, "vulnerabilities": []}))
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_invalid_response"):
        dynamic.fetch("kev_lookup", identifier=CVE)
    assert not dynamic._CACHE


@pytest.mark.parametrize("affected", [None, [], [{"package": {"ecosystem": "npm", "name": "synthetic-agent"}}],
    [{"package": {"ecosystem": "PyPI", "name": "wrong-agent"}}], ["invalid-shape"]])
def test_osv_wrong_or_missing_package_entity_is_rejected(monkeypatch, affected):
    item = {"id": "GHSA-aaaa-bbbb-cccc", "summary": "Synthetic record", "affected": affected}
    mocked_http(monkeypatch, lambda _: httpx.Response(200, json={"vulns": [item]}))
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_entity_mismatch"):
        dynamic.fetch("osv_lookup", ecosystem="PyPI", package="synthetic-agent", version="1.0.0")
    assert not dynamic._CACHE


def test_osv_pypi_name_normalization_does_not_create_false_mismatch(monkeypatch):
    item = {"id": "GHSA-aaaa-bbbb-cccc", "summary": "Synthetic record", "affected": [
        {"package": {"ecosystem": "PyPI", "name": "Synthetic_Agent"}}]}
    mocked_http(monkeypatch, lambda _: httpx.Response(200, json={"vulns": [item]}))
    value, _, _ = dynamic.fetch("osv_lookup", ecosystem="PyPI", package="synthetic-agent", version="1.0.0")
    assert len(value["records"]) == 1


def test_transient_source_retry_is_bounded_but_429_not_retried(monkeypatch):
    attempts = [0]
    def handler(request):
        attempts[0] += 1
        return httpx.Response(503) if attempts[0] == 1 else httpx.Response(200, json={"vulnerabilities": []})
    calls, options = mocked_http(monkeypatch, handler)
    value, _, _ = dynamic.fetch("nvd_lookup", identifier=CVE)
    assert value["records"] == [] and len(calls) == 2
    assert options[1]["timeout"] < options[0]["timeout"] <= 8


def test_timeout_with_exhausted_deadline_does_not_start_retry(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(dynamic.time, "monotonic", lambda: clock[0])
    def handler(request):
        clock[0] += 8
        raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_TIMEOUT", request=request)
    calls, _ = mocked_http(monkeypatch, handler)
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_timeout"):
        dynamic.fetch("nvd_lookup", identifier=CVE)
    assert len(calls) == 1 and not dynamic._CACHE


def test_shared_redis_limiter_uses_config_method_and_stops_on_denial(monkeypatch):
    from redis import Redis
    calls = []
    class FakeRedis:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def set(self, key, value, **options):
            calls.append((key, value, options))
            return False
    def factory(cls, url, **options):
        assert isinstance(url, str) and url == "redis://synthetic-limiter/0"
        return FakeRedis()
    monkeypatch.setattr(dynamic, "_limit", REAL_LIMIT)
    monkeypatch.setattr(dynamic, "get_settings", lambda: SimpleNamespace(redis_url=lambda: "redis://synthetic-limiter/0"))
    monkeypatch.setattr(Redis, "from_url", classmethod(factory))
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_rate_limited"):
        dynamic._limit("nvd_lookup")
    assert calls == [("ict:dynamic-source:nvd_lookup", "1", {"nx": True, "px": 6000})]


def test_success_body_after_shared_deadline_is_rejected(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(dynamic.time, "monotonic", lambda: clock[0])
    def handler(request):
        clock[0] += 8.1
        return httpx.Response(200, json=nvd_payload())
    calls, _ = mocked_http(monkeypatch, handler)
    with pytest.raises(dynamic.IntelligenceError, match="intelligence_timeout"):
        dynamic.fetch("nvd_lookup", identifier=CVE)
    assert len(calls) == 1 and not dynamic._CACHE
