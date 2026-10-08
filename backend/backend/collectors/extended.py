"""Bounded, allowlisted NVD/Atom/RSS/official-document collectors.

Source endpoints are deliberately fixed; configuration cannot supply arbitrary URLs.
Standards are voluntary NIST framework sections; policy includes government guidance,
not a claim that every document is legally binding or that all new laws are covered.
"""
import json
import hashlib
import re
from datetime import timedelta
from urllib.parse import urlparse
from urllib.parse import quote
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator
from defusedxml import ElementTree

from backend.collectors.base import CollectionBatch
from backend.collectors.live import Transport, parse_rss, plain, timestamp
from backend.core.config import get_settings
from backend.schemas.contracts import RawRecord
from backend.services.jobs import utc_now

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
ARXIV_URL = "https://export.arxiv.org/api/query"
BLOG_URL = "https://blog.trailofbits.com/index.xml"
CROSSREF_URL = "https://api.crossref.org/works"
COMMUNITY_URL = "https://discuss.huggingface.co/latest.rss"
COMMUNITY_SEARCH = "https://discuss.huggingface.co/search.json"
NVIDIA_CATALOGUE = "https://www.nvidia.com/content/dam/en-zz/Solutions/product-security/product-security.json"
NIST_URLS = (
    "https://airc.nist.gov/airmf-resources/airmf/",
    "https://airc.nist.gov/airmf-resources/airmf/3-sec-characteristics/",
)
GOVUK_ROOT = "/government/collections/ai-cyber-security"
GOVUK_API = "https://www.gov.uk/api/content"
CHINA_REGULATION_URL = "https://www.gov.cn/zhengce/zhengceku/202307/content_6891752.htm"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


class ExtendedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keywords: list[str] = Field(default_factory=lambda: ["ollama", "vllm", "langchain"], min_length=1, max_length=5)
    page_size: int = Field(default=20, ge=1, le=50)
    initial_days: int = Field(default=30, ge=1, le=120)
    max_items: int = Field(default=10, ge=1, le=30)
    max_pages_per_cycle: int = Field(default=5, ge=1, le=20)
    overlap_hours: int = Field(default=24, ge=1, le=48)

    @field_validator("keywords")
    @classmethod
    def terms(cls, values):
        if any(not re.fullmatch(r"[A-Za-z0-9 ._-]{1,80}", value) for value in values):
            raise ValueError("invalid keyword")
        return values


def nvd_record(item):
    cve = item["cve"]
    identifier = cve["id"]
    descriptions = cve.get("descriptions", [])
    content = next((d["value"] for d in descriptions if d.get("lang") == "en"),
                   next((d["value"] for d in descriptions), identifier))
    vectors = []
    severity = "unknown"
    ranks = ["unknown", "low", "medium", "high", "critical"]
    for metrics in cve.get("metrics", {}).values():
        for metric in metrics:
            data = metric.get("cvssData", {})
            score = data.get("baseScore")
            if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10):
                raise ValueError("invalid_source_cvss")
            vectors.append({"version": data.get("version", "unknown"), "vector": data.get("vectorString"),
                            "score": score, "origin": "source_reported", "source": metric.get("source")})
            level = str(data.get("baseSeverity") or metric.get("baseSeverity") or "unknown").lower()
            if level in ranks and ranks.index(level) > ranks.index(severity):
                severity = level
    cwes = sorted({d["value"] for w in cve.get("weaknesses", []) for d in w.get("description", [])
                   if re.fullmatch(r"CWE-\d+", d.get("value", ""))})
    references = cve.get("references", [])
    matches = []

    def visit(node):
        if isinstance(node, dict):
            for match in node.get("cpeMatch", []):
                if match.get("vulnerable"):
                    matches.append({k: match[k] for k in ("criteria", "versionStartIncluding", "versionStartExcluding",
                                    "versionEndIncluding", "versionEndExcluding") if k in match})
            for key, value in node.items():
                if key != "cpeMatch":
                    visit(value)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(cve.get("configurations", []))
    fields = {"affected": {"cpe_matches": matches}, "cwe": {"ids": cwes}, "cvss": {"vectors": vectors},
              "remediation": {"references": [r for r in references if "Patch" in r.get("tags", [])]},
              "analysis": {"references": [r for r in references if set(r.get("tags", [])) & {"Technical Description", "Third Party Advisory"}]},
              "poc": {"references": [r for r in references if "Exploit" in r.get("tags", [])]}}
    if cve.get("cisaExploitAdd"):
        fields["exploitation"] = {"catalog": "CISA KEV", "added_at": cve["cisaExploitAdd"],
                                  "required_action": cve.get("cisaRequiredAction")}
    def nvd_time(value):
        # NVD's API timestamps use UTC but often omit the explicit Z suffix.
        return timestamp(value + "Z" if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT[0-9:.]+", value) else value)
    return RawRecord(external_id=identifier, title=identifier + ": " + content[:400],
        source_url=f"https://nvd.nist.gov/vuln/detail/{identifier}", document_type="vulnerability",
        content=content, vulnerability_id=identifier, aliases=[identifier], severity=severity,
        published_at=nvd_time(cve.get("published")), source_updated_at=nvd_time(cve.get("lastModified")),
        raw=cve, metadata_json={"references": references, "status": cve.get("vulnStatus")},
        source_fields={k: v for k, v in fields.items() if any(v.values())},
        content_completeness="full_text", is_demo=False)


def arxiv_records(payload):
    root = ElementTree.fromstring(payload)
    if root.tag != ATOM + "feed":
        raise ValueError("unexpected_atom_format")
    records = []
    for entry in root.findall(ATOM + "entry"):
        raw_id = entry.findtext(ATOM + "id", "")
        if "/abs/" not in raw_id:
            raise ValueError("arxiv_api_error")
        identifier = raw_id.split("/abs/", 1)[1]
        title = " ".join(entry.findtext(ATOM + "title", "").split())
        content = " ".join(entry.findtext(ATOM + "summary", "").split())
        metadata = {"arxiv_id": identifier, "doi": entry.findtext(ARXIV + "doi"),
                    "authors": [a.findtext(ATOM + "name") for a in entry.findall(ATOM + "author")],
                    "categories": [c.get("term") for c in entry.findall(ATOM + "category")],
                    "license": "Metadata/abstract API; full text rights not inferred"}
        records.append(RawRecord(external_id=identifier, title=title, document_type="paper",
            source_url="https://arxiv.org/abs/" + identifier, content=content,
            published_at=timestamp(entry.findtext(ATOM + "published")),
            source_updated_at=timestamp(entry.findtext(ATOM + "updated")),
            metadata_json=metadata, raw={"metadata": metadata, "abstract": content},
            content_completeness="summary", is_demo=False))
    return records


class ExtendedCollector:
    def __init__(self, adapter, config):
        self.adapter, self.config = adapter, ExtendedConfig.model_validate(config)

    def collect(self, cursor):
        s = get_settings()
        transport = Transport(proxy=s.outbound_proxy.get_secret_value() if s.outbound_proxy else None)
        transport.allowed_urls = {NVD_URL, ARXIV_URL, BLOG_URL, CROSSREF_URL, COMMUNITY_URL,
                                  *NIST_URLS, GOVUK_API + GOVUK_ROOT, CHINA_REGULATION_URL,
                                  COMMUNITY_SEARCH, NVIDIA_CATALOGUE}
        try:
            return getattr(self, "_" + self.adapter)(cursor, transport, s)
        finally:
            transport.close()

    def _nvd(self, cursor, t, s):
        now = utc_now()
        index = int(cursor.get("keyword_index", 0)) % len(self.config.keywords)
        start = cursor.get("window_start") or (now - timedelta(days=self.config.initial_days)).isoformat()
        end = cursor.get("window_end") or now.isoformat()
        # Offline recovery may span more than the API's 120-day window.
        if timestamp(end) - timestamp(start) > timedelta(days=120):
            end = (timestamp(start) + timedelta(days=120)).isoformat()
        offset = int(cursor.get("start_index", 0))
        headers = {"apiKey": s.nvd_api_key.get_secret_value()} if s.nvd_api_key else {}
        # Shared Redis limiter in Transport also protects concurrent NVD source instances.
        _, _, payload = t.request("GET", NVD_URL, headers=headers, params={
            "keywordSearch": self.config.keywords[index], "lastModStartDate": start, "lastModEndDate": end,
            "startIndex": offset, "resultsPerPage": self.config.page_size})
        result = json.loads(payload)
        records = [nvd_record(v) for v in result.get("vulnerabilities", [])]
        total = int(result.get("totalResults", 0))
        if int(result.get("startIndex", offset)) != offset or (not records and offset < total):
            raise ValueError("nvd_inconsistent_page")
        more = offset + len(records) < total
        after = {"window_start": start, "window_end": end, "keyword_index": index,
                 "start_index": offset + len(records)} if more else {
                 "window_start": start, "window_end": end, "keyword_index": index + 1, "start_index": 0}
        if not more and index + 1 >= len(self.config.keywords):
            after = {"window_start": (timestamp(end) - timedelta(hours=self.config.overlap_hours)).isoformat(), "keyword_index": 0}
        catch_up = timestamp(end) < now - timedelta(minutes=5)
        return CollectionBatch(records, after, more=more or index + 1 < len(self.config.keywords) or catch_up)

    def _arxiv(self, cursor, t, s):
        # Page latest updates to a persisted watermark. One request per task and a
        # one-day overlap avoid treating a top-N result as complete daily coverage.
        start = int(cursor.get("start", 0))
        if not 0 <= start < 10000:
            raise ValueError("arxiv_pagination_limit_reached")
        since = cursor.get("since") or (utc_now() - timedelta(days=self.config.initial_days)).isoformat()
        _, _, payload = t.request("GET", ARXIV_URL, params={
            "search_query": 'all:"prompt injection" OR all:"LLM security" OR all:"AI safety"',
            "start": start, "max_results": self.config.max_items,
            "sortBy": "lastUpdatedDate", "sortOrder": "descending"})
        rows = arxiv_records(payload)
        dates = [r.source_updated_at or r.published_at for r in rows]
        if any(d is None for d in dates):
            raise ValueError("arxiv_update_timestamp_missing")
        watermark = cursor.get("watermark") or (max(dates).isoformat() if dates else utc_now().isoformat())
        more = len(rows) == self.config.max_items and min(dates) > timestamp(since)
        after = {"since": since, "watermark": watermark, "start": start + len(rows)} if more else {
            "since": (timestamp(watermark) - timedelta(days=1)).isoformat(), "start": 0,
            "last_checked_at": utc_now().isoformat(), "strategy": "paged_last_updated_overlap"}
        return CollectionBatch(rows, after, more=more)

    def _china_policy(self, cursor, t, s):
        headers = {"If-None-Match": cursor["etag"]} if cursor.get("etag") else {}
        status, response_headers, payload = t.request("GET", CHINA_REGULATION_URL, headers=headers)
        if status == 304:
            return CollectionBatch([], cursor)
        html = payload.decode("utf-8")
        text = plain(html)
        title = "生成式人工智能服务管理暂行办法"
        start = text.find("第一章")
        ending = re.search(r"第二十四条\s*本办法自(\d{4})年(\d{1,2})月(\d{1,2})日起施行。", text)
        if title not in text or start < 0 or ending is None or ending.start() <= start:
            raise ValueError("regulation_body_missing")
        content = text[start:ending.end()]
        if len(content) < 2000 or "第二十三条" not in content:
            raise ValueError("regulation_body_incomplete")
        effective = datetime(*(int(x) for x in ending.groups())).date().isoformat()
        # The official mirror contains duplicate mobile/desktop sections. Preserve
        # one complete chapter/article body, and the entire original HTML separately.
        record = RawRecord(external_id=CHINA_REGULATION_URL, source_url=CHINA_REGULATION_URL,
            title=title, document_type="regulation", content=content, raw={"html": html},
            content_completeness="full_text", metadata_json={"publisher": "中国政府网",
                "jurisdiction": "CN", "legal_status": "departmental_regulation", "effective_at": effective,
                "coverage": "fixed_official_document", "publication_date": "2023-07-13", "date_precision": 3},
            # Only a publication date is known; do not invent a precise timestamp.
            is_demo=False)
        return CollectionBatch([record], {"etag": response_headers.get("etag"), "last_checked_at": utc_now().isoformat()})

    def _crossref(self, cursor, t, s):
        now = utc_now()
        if cursor.get("strategy") == "recent_and_backfill_v1":
            recent, backfill = dict(cursor["recent"]), cursor.get("backfill")
            backfill_until = timestamp(cursor.get("backfill_until"))
        else:
            since = timestamp(cursor.get("since"))
            if since is None and cursor.get("since"):
                since = datetime.fromisoformat(cursor["since"]).replace(tzinfo=timezone.utc)
            since = since or now - timedelta(days=self.config.initial_days)
            boundary = now - timedelta(hours=48)
            backfill = dict(cursor) if since < boundary else None
            if backfill is not None:
                backfill.setdefault("since", since.isoformat())
            backfill_until = boundary if backfill else None
            recent = {"since": max(since, boundary).isoformat()}
        # A historical cursor cannot keep today's arrivals behind weeks of backfill.
        current = self._crossref_window(recent, t, s)
        records = {r.external_id: r for r in current.records}
        history = self._crossref_window(backfill, t, s, stop_at=backfill_until) if backfill else None
        if history:
            for record in history.records:
                records.setdefault(record.external_id, record)
        remaining = history.next_cursor if history and history.more else None
        return CollectionBatch(list(records.values()), {
            "strategy": "recent_and_backfill_v1", "recent": current.next_cursor,
            "backfill": remaining, "backfill_until": backfill_until.isoformat() if remaining else None,
        }, more=current.more or bool(remaining),
            continuation_delay_seconds=max(current.continuation_delay_seconds,
                history.continuation_delay_seconds if history else 60))

    def _crossref_window(self, cursor, t, s, stop_at=None):
        from backend.services.pipeline import AI_PATTERN, SECURITY_PATTERN
        now = stop_at or utc_now()
        start_date = cursor.get("since") or (now - timedelta(days=self.config.initial_days)).isoformat()
        start_time = timestamp(start_date) or datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        end_date = cursor.get("window_end") or min(now, start_time + timedelta(days=7)).isoformat()
        api_cursor = cursor.get("cursor", "*")
        if cursor.get("strategy") != "fixed_index_window_v2":
            # Re-read the old watermark once when adding an upper bound. Reusing
            # a cursor from a different query could skip records; duplicates are safe.
            api_cursor = "*"
        # Crossref explicitly rejects timezone suffixes/fractions in date filters.
        def filter_time(value):
            parsed = timestamp(value) or datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        _, _, payload = t.request("GET", CROSSREF_URL, params={"query": "prompt injection LLM security",
            "filter": "from-index-date:" + filter_time(start_date) + ",until-index-date:" + filter_time(end_date),
            "rows": self.config.max_items, "cursor": api_cursor})
        result = json.loads(payload)["message"]
        if not isinstance(result.get("items"), list):
            raise ValueError("crossref_items_missing")
        page_hash = hashlib.sha256(json.dumps([x.get("DOI") for x in result["items"]]).encode()).hexdigest()
        if result["items"] and cursor.get("page_hash") == page_hash and api_cursor != "*":
            raise ValueError("crossref_repeated_page")
        records = []
        for item in result.get("items", []):
            title = plain(" ".join(item.get("title", [])))[:500]
            if not title or not item.get("DOI"):
                continue
            abstract = plain(item.get("abstract", ""))
            candidate_text = title + " " + abstract
            if not AI_PATTERN.search(candidate_text) or not SECURITY_PATTERN.search(candidate_text):
                continue
            date_parts = item.get("published", {}).get("date-parts", [[]])[0]
            published = None
            if date_parts:
                published = datetime(*(date_parts + [1] * (3 - len(date_parts))), tzinfo=timezone.utc)
            records.append(RawRecord(external_id=item["DOI"], title=title, document_type="paper",
                source_url="https://doi.org/" + quote(item["DOI"], safe="/"), content=abstract or title,
                published_at=published, source_updated_at=timestamp(item.get("indexed", {}).get("date-time")),
                raw=item, metadata_json={"doi": item["DOI"], "authors": item.get("author", []),
                    "publisher": item.get("publisher"), "date_precision": len(date_parts) if date_parts else None,
                    "license": item.get("license", []), "full_text_fetched": False},
                content_completeness="summary" if abstract else "metadata_only", is_demo=False))
        next_cursor = result.get("next-cursor")
        more = bool(result.get("items") and next_cursor
                    and len(result["items"]) >= self.config.max_items)
        if len(result["items"]) >= self.config.max_items and not next_cursor:
            raise ValueError("crossref_next_cursor_missing")
        pages = int(cursor.get("pages_in_cycle", 0)) + 1
        after = {"since": start_date, "window_end": end_date, "cursor": next_cursor, "page_hash": page_hash,
                 "strategy": "fixed_index_window_v2",
                 "pages_in_cycle": pages if pages < self.config.max_pages_per_cycle else 0} if more else {
            "since": (timestamp(end_date) - timedelta(hours=self.config.overlap_hours)).isoformat(),
            "strategy": "fixed_index_window_v2"}
        catch_up = timestamp(end_date) < now - timedelta(minutes=5)
        return CollectionBatch(records, after, more=more or catch_up,
            continuation_delay_seconds=180 if more and pages >= self.config.max_pages_per_cycle else 60)

    def _huggingface(self, cursor, t, s):
        # Target an actual security topic instead of classifying generic AI posts
        # from the latest feed as safety intelligence. Public Discourse search API.
        page = int(cursor.get("page", 1))
        if not 1 <= page <= 3:
            page = 1
        query = '"prompt injection" order:latest'
        _, _, payload = t.request("GET", COMMUNITY_SEARCH, params={"q": query, "page": page})
        result = json.loads(payload)
        topics = {x["id"]: x for x in result.get("topics", [])}
        records = []
        for post in result.get("posts", [])[:self.config.max_items]:
            topic = topics.get(post.get("topic_id"))
            if topic is None or not post.get("blurb"):
                continue
            body = plain(post["blurb"])
            records.append(RawRecord(external_id="post-" + str(post["id"]), title=topic["title"],
                source_url=f"https://discuss.huggingface.co/t/{post['topic_id']}" +
                    (f"/{post['post_number']}" if post.get("post_number") else ""),
                document_type="discussion", content=body, content_completeness="summary",
                published_at=timestamp(post.get("created_at")), source_updated_at=timestamp(post.get("updated_at")),
                metadata_json={"publisher": "Hugging Face community", "topic_id": post["topic_id"],
                    "post_id": post["id"], "search_query": query, "coverage": "latest_three_search_pages"},
                raw={"topic": topic, "post": post, "search_query": query}, is_demo=False))
        has_next = bool(result.get("grouped_search_result", {}).get("more_posts"))
        more = has_next and page < 3
        return CollectionBatch(records, {"page": page + 1 if more else 1,
            "last_checked_at": utc_now().isoformat(), "bounded_pages": 3}, more=more)

    def _nvidia(self, cursor, t, s):
        headers = {"If-None-Match": cursor["etag"]} if cursor.get("etag") else {}
        status, response_headers, payload = t.request("GET", NVIDIA_CATALOGUE, headers=headers)
        if status == 304:
            return CollectionBatch([], cursor)
        rows = json.loads(payload)["data"]
        records = []
        products = re.compile(r"\b(?:TensorRT-LLM|TensorRT|Triton Inference Server|ChatRTX|NeMo|Riva|Merlin|AI Enterprise)\b", re.I)
        for row in rows:
            title = plain(row.get("title", ""))
            product = products.search(title)
            identifiers = sorted(set(re.findall(r"CVE-\d{4}-\d{4,}", row.get("cve identifier(s)", ""))))
            if product is None or not identifiers:
                continue
            links = re.findall(r"href=['\"](https://nvidia\.custhelp\.com/[^'\"]+)['\"]", row["title"])
            severity = str(row.get("severity", "unknown")).lower()
            if severity not in {"unknown", "low", "medium", "high", "critical"}:
                severity = "unknown"
            def date_only(value):
                try:
                    return datetime.strptime(value, "%d %b %Y").date().isoformat()
                except (ValueError, TypeError):
                    return None
            # This is the vendor's catalogue row, not the linked bulletin body.
            content = (f"Official NVIDIA security bulletin catalogue: {title}. "
                       f"CVE identifiers: {', '.join(identifiers)}. Severity: {row.get('severity')}. "
                       f"Published date: {row.get('publish date')}. Updated date: {row.get('last updated')}.")
            records.append(RawRecord(external_id=str(row["bulletin id"]), title=title,
                source_url=NVIDIA_CATALOGUE, document_type="advisory", content=content,
                content_completeness="summary", aliases=identifiers, severity=severity,
                products=[product.group().lower()], metadata_json={"publisher": "NVIDIA",
                    "bulletin_id": str(row["bulletin id"]), "bulletin_url": links[0] if links else None,
                    "publication_date": date_only(row.get("publish date")),
                    "updated_date": date_only(row.get("last updated")), "date_precision": 3,
                    "coverage": "official_catalogue_row", "full_bulletin_fetched": False},
                raw=row, is_demo=False))
            if len(records) >= self.config.max_items:
                break
        return CollectionBatch(records, {"etag": response_headers.get("etag"), "last_checked_at": utc_now().isoformat()})

    def _trailofbits(self, cursor, t, s):
        return self._feed(cursor, t, BLOG_URL, "trailofbits")

    def _feed(self, cursor, t, url, adapter):
        headers = {"If-None-Match": cursor["etag"]} if cursor.get("etag") else {}
        status, response_headers, payload = t.request("GET", url, headers=headers)
        if status == 304:
            return CollectionBatch([], cursor)
        records = parse_rss(payload, adapter)[:self.config.max_items]
        root = ElementTree.fromstring(payload)
        by_url = {i.findtext("link"): i for i in root.findall("./channel/item")}
        for record in records:
            item = by_url.get(str(record.source_url))
            encoded = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") if item is not None else None
            if encoded:
                text = plain(encoded)
                record.content = text[:200000]
                record.content_completeness = "full_text" if len(text) <= 200000 else "partial"
            record.metadata_json = {"publisher": "Trail of Bits" if adapter == "trailofbits" else "Hugging Face community",
                                    "feed_url": url}
            if adapter == "huggingface":
                record.document_type = "discussion"
        return CollectionBatch(records, {"etag": response_headers.get("etag"), "last_checked_at": utc_now().isoformat()})

    def _nist(self, cursor, t, s):
        # Fixed official section catalogue: monitor changes, no unsupported latest-standard claim.
        url = NIST_URLS[int(cursor.get("section", 0)) % len(NIST_URLS)]
        _, headers, payload = t.request("GET", url)
        html = payload.decode("utf-8")
        main = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S | re.I)
        if not main:
            raise ValueError("official_document_main_missing")
        content = plain(main.group(1))
        if len(content) < 100:
            raise ValueError("official_document_content_missing")
        title_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", main.group(1), re.S | re.I)
        title = plain(title_match.group(1)) if title_match else "NIST AI RMF official section"
        record = RawRecord(external_id=url, source_url=url, title=title, document_type="standard",
            content=content[:200000], content_completeness="full_text" if len(content) <= 200000 else "partial",
            raw={"html": html}, metadata_json={"publisher": "NIST", "standard_id": "AI RMF",
                "legal_status": "voluntary_framework", "coverage": "official_html_section",
                "last_modified_header": headers.get("last-modified")}, is_demo=False)
        return CollectionBatch([record], {"section": (int(cursor.get("section", 0)) + 1) % len(NIST_URLS)})

    def _govuk(self, cursor, t, s):
        path = cursor.get("next_path") or GOVUK_ROOT
        if not re.fullmatch(r"/government/(?:collections|publications|consultations)/[a-z0-9/-]+", path):
            raise ValueError("policy_path_not_allowlisted")
        url = GOVUK_API + path
        t.allowed_urls.add(url)
        _, _, payload = t.request("GET", url)
        result = json.loads(payload)
        if result.get("base_path") != path:
            raise ValueError("policy_path_mismatch")
        details = result.get("details", {})
        body = details.get("body") or details.get("description") or ""
        if isinstance(body, list):
            body = " ".join(x.get("body", "") for x in body if isinstance(x, dict))
        content = plain(body) or plain(result.get("description", ""))
        # Select published policy/guidance records only, not the research subgroup.
        candidates = []
        for child in result.get("links", {}).get("documents", []):
            if child.get("document_type") not in {"policy_paper", "guidance", "detailed_guide", "consultation"}:
                continue
            candidate = child.get("base_path", "")
            if re.fullmatch(r"/government/(?:publications|consultations)/[a-z0-9/-]+", candidate):
                candidates.append(candidate)
        queue = list(cursor.get("remaining", []))
        if path == GOVUK_ROOT:
            queue = sorted(set(candidates))[:self.config.max_items]
        after = {"next_path": queue[0], "remaining": queue[1:]} if queue else {}
        record = RawRecord(external_id=result.get("content_id") or path, title=result["title"],
            source_url="https://www.gov.uk" + path, document_type="policy",
            content=content or result["title"], raw=result,
            published_at=timestamp(result.get("first_published_at")),
            source_updated_at=timestamp(result.get("public_updated_at")),
            content_completeness="full_text" if body else "summary",
            metadata_json={"publisher": "UK Government", "jurisdiction": "UK", "effective_at": None,
                           "legal_status": result.get("document_type"), "license": "Open Government Licence v3.0"},
            is_demo=False)
        return CollectionBatch([record], after, more=bool(queue))
