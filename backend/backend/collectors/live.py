"""Public, allowlisted sources; adapted from the verified D:\\cit collector.

No user-provided URLs are fetched. Network failures never become demo records.
"""
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree
from pydantic import BaseModel, ConfigDict, Field

from backend.collectors.base import CollectionBatch
from backend.core.config import get_settings
from backend.schemas.contracts import RawRecord

OSV_URL = "https://api.osv.dev/v1/query"
CISCO_URL = "https://sec.cloudapps.cisco.com/security/center/psirtrss20/CiscoSecurityAdvisory.xml"
SANS_URL = "https://isc.sans.edu/rssfeed.xml"
FEEDS = {"cisco": CISCO_URL, "sans": SANS_URL}


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packages: list[str] = Field(default_factory=lambda: ["torch", "transformers", "langchain",
        "vllm", "gradio", "tensorflow", "keras", "onnx", "llama-index-core", "sentence-transformers"],
                                min_length=1, max_length=20)
    max_pages: int = Field(default=3, ge=1, le=5)

    @classmethod
    def validate_for(cls, adapter: str, data: dict) -> "SourceConfig":
        if adapter not in {"osv", "cisco", "sans"}:
            raise ValueError("unsupported_adapter")
        config = cls.model_validate(data)
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", package)
               for package in config.packages):
            raise ValueError("invalid_package_name")
        return config


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value: str) -> str:
    parser = _PlainText()
    parser.feed(unescape(value or ""))
    return " ".join(" ".join(parser.parts).split())


def timestamp(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def parse_osv(item: dict) -> RawRecord:
    identifier = item["id"]
    refs = item.get("references", [])
    affected = item.get("affected", [])
    specific = item.get("database_specific") or {}
    severity = str(specific.get("severity", "unknown")).lower()
    if severity == "moderate":
        severity = "medium"
    if severity not in {"unknown", "low", "medium", "high", "critical"}:
        severity = "unknown"
    vectors = []
    for entry in item.get("severity", []):
        vector = entry.get("score")
        version = vector.split("/")[0].removeprefix("CVSS:") if isinstance(vector, str) and vector.startswith("CVSS:") else (
            "2.0" if entry.get("type") == "CVSS_V2" else "unknown")
        vectors.append({"version": version, "vector": vector, "score": None, "origin": "source_reported"})
    values = {
        "affected": {"items": affected},
        "cwe": {"ids": specific.get("cwe_ids", [])},
        "cvss": {"vectors": vectors},
        "remediation": {"references": [ref for ref in refs if ref.get("type") == "FIX"]},
        "analysis": {"references": [ref for ref in refs if ref.get("type") in {"ARTICLE", "REPORT", "ADVISORY"}]},
    }
    fields = {key: value for key, value in values.items() if any(value.values())}
    return RawRecord(
        external_id=identifier, title=item.get("summary") or identifier,
        source_url=f"https://osv.dev/vulnerability/{quote(identifier, safe='')}",
        document_type="vulnerability", content=item.get("details") or item.get("summary") or identifier,
        vulnerability_id=next((alias.upper() for alias in [identifier, *item.get("aliases", [])]
                               if re.fullmatch(r"CVE-\d{4}-\d{4,}", alias, re.I)), None),
        severity=severity, published_at=timestamp(item.get("published")), is_demo=False,
        source_updated_at=timestamp(item.get("modified")), content_completeness="full_text",
        metadata_json={"references": refs},
        aliases=[identifier, *item.get("aliases", [])],
        products=[affected_entry.get("package", {}).get("name", "") for affected_entry in affected],
        raw=item, source_fields=fields,
    )


def parse_rss(payload: bytes, adapter: str) -> list[RawRecord]:
    root = ElementTree.fromstring(payload)
    if root.find("./channel") is None:
        raise ValueError("unexpected_feed_format")
    records = []
    for item in root.findall("./channel/item"):
        values = {child.tag: child.text or "" for child in item}
        link = values.get("link", "").strip()
        if not link.startswith("https://"):
            continue
        title = plain(values.get("title", "")) or "Untitled advisory"
        content = plain(values.get("description", "")) or title
        aliases = sorted({alias.upper() for alias in re.findall(
            r"\bCVE-\d{4}-\d{4,}\b", title + " " + content, re.I)})
        records.append(RawRecord(
            external_id=values.get("guid") or link, title=title, source_url=link,
            document_type="advisory" if adapter == "cisco" else "discussion" if adapter == "sans" else "article",
            content=content, published_at=timestamp(values.get("pubDate")),
            vulnerability_id=aliases[0] if len(aliases) == 1 else None,
            aliases=aliases, is_demo=False, raw=values,
            content_completeness="summary",
        ))
    return records


class Transport:
    def __init__(self, proxy: str | None = None, client: httpx.Client | None = None,
                 sleeper=time.sleep):
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(15, connect=8), proxy=proxy, follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "CIT-AISecurity-Research/0.2"},
        )
        self.sleeper = sleeper
        self.allowed_urls = {OSV_URL, CISCO_URL, SANS_URL}
        self.enforce_limit = client is None

    def rate_limit(self, url):
        if not self.enforce_limit:
            return
        import hashlib
        from redis import Redis
        settings = get_settings()
        interval = 7000 if "services.nvd.nist.gov" in url else 3100 if "arxiv.org" in url else 1000
        # Content API child pages and search share a host-wide budget across workers.
        key = "source-rate:" + hashlib.sha256(urlsplit(url).netloc.lower().encode()).hexdigest()
        with Redis.from_url(settings.redis_url(), socket_timeout=3, socket_connect_timeout=3) as redis:
            for _ in range(30):
                if redis.set(key, "1", nx=True, px=interval):
                    return
                ttl = redis.pttl(key)
                self.sleeper(max(.05, min(interval, max(ttl, 50)) / 1000))
        raise RuntimeError("source_rate_limit_busy")

    def request(self, method: str, url: str, **kwargs):
        if url not in self.allowed_urls:
            raise ValueError("source_url_not_allowlisted")
        for attempt in range(3):
            self.rate_limit(url)
            try:
                with self.client.stream(method, url, **kwargs) as response:
                    if response.status_code in {429, 500, 502, 503, 504}:
                        if attempt == 2:
                            response.raise_for_status()
                        retry_after = response.headers.get("retry-after", "")
                        self.sleeper(min(10, int(retry_after)) if retry_after.isdigit() else 2 ** attempt)
                        continue
                    if response.status_code == 304:
                        return 304, dict(response.headers), b""
                    response.raise_for_status()
                    parts, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > 8_000_000:
                            raise ValueError("source_response_too_large")
                        parts.append(chunk)
                    return response.status_code, dict(response.headers), b"".join(parts)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise
                self.sleeper(2 ** attempt)
        raise RuntimeError("source_request_failed")

    def close(self):
        self.client.close()


class LiveCollector:
    def __init__(self, adapter: str, config: dict):
        self.adapter = adapter
        self.config = SourceConfig.validate_for(adapter, config)

    def collect(self, cursor: dict) -> CollectionBatch:
        settings = get_settings()
        proxy = settings.outbound_proxy.get_secret_value() if settings.outbound_proxy else None
        transport = Transport(proxy=proxy)
        try:
            if self.adapter == "osv":
                return self._collect_osv(cursor, transport)
            return self._collect_rss(cursor, transport)
        finally:
            transport.close()

    def _collect_osv(self, cursor: dict, transport: Transport) -> CollectionBatch:
        packages = self.config.packages
        index = int(cursor.get("package_index", 0)) % len(packages)
        token = cursor.get("page_token")
        records = []
        for _ in range(self.config.max_pages):
            query = {"package": {"ecosystem": "PyPI", "name": packages[index]}}
            if token:
                query["page_token"] = token
            _, _, payload = transport.request("POST", OSV_URL, json=query)
            result = json.loads(payload)
            records.extend(parse_osv(item) for item in result.get("vulns", []))
            token = result.get("next_page_token")
            if not token:
                break
            transport.sleeper(0.5)
        cursor_after = {"package_index": index, "page_token": token} if token else {
            "package_index": (index + 1) % len(packages)}
        return CollectionBatch(records=records, next_cursor=cursor_after,
                               more=bool(token) or index + 1 < len(packages), coverage={
            "mode": "package_inventory", "package": packages[index], "configured_packages": packages,
            "package_page_complete": not bool(token), "inventory_cycle_complete": not token and index + 1 == len(packages),
            "exhaustive_internet_coverage": False})

    def _collect_rss(self, cursor: dict, transport: Transport) -> CollectionBatch:
        headers = {}
        if cursor.get("etag"):
            headers["If-None-Match"] = cursor["etag"]
        if cursor.get("last_modified"):
            headers["If-Modified-Since"] = cursor["last_modified"]
        status, headers, payload = transport.request("GET", FEEDS[self.adapter], headers=headers)
        if status == 304:
            return CollectionBatch(records=[], next_cursor=cursor)
        return CollectionBatch(records=parse_rss(payload, self.adapter), next_cursor={
            "etag": headers.get("etag"), "last_modified": headers.get("last-modified"),
        })
