"""Official publication discovery, bounded requests and explicit catalogue scope."""
from datetime import timedelta
import hashlib
import json
import re
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field
from backend.collectors.base import CollectionBatch
from backend.collectors.live import Transport, plain, timestamp
from backend.collectors.extended import ExtendedCollector
from backend.core.config import get_settings
from backend.schemas.contracts import RawRecord
from backend.services.jobs import utc_now

NIST_DRAFTS = "https://csrc.nist.gov/CSRC/media/feeds/pubs/drafts-open-for-comment.json"
GOVUK_SEARCH = "https://www.gov.uk/api/search.json"
POLICY_QUERIES = ('"AI cyber security"', '"generative AI"')
POLICY_TYPES = ("guidance", "policy_paper", "detailed_guide", "open_consultation", "closed_consultation")


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_size: int = Field(default=5, ge=1, le=10)
    initial_days: int = Field(default=120, ge=1, le=365)


def dated_metadata(value, prefix="publication"):
    """An unspecified timezone or midnight calendar date is not a precise instant."""
    if not value:
        return None, {}
    date = str(value)[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        parsed = timestamp(value)
        if parsed and "T00:00:00" not in str(value):
            return parsed, {}
        return None, {prefix + "_date": date,
            "date_precision" if prefix == "publication" else "updated_date_precision": 3}
    return None, {}


class DiscoveryCollector:
    def __init__(self, adapter, config):
        self.adapter, self.config = adapter, DiscoveryConfig.model_validate(config)

    def collect(self, cursor):
        settings = get_settings()
        proxy = settings.outbound_proxy.get_secret_value() if settings.outbound_proxy else None
        transport = Transport(proxy=proxy)
        transport.allowed_urls |= {NIST_DRAFTS, GOVUK_SEARCH}
        try:
            return getattr(self, "_" + self.adapter)(cursor, transport, settings)
        finally:
            transport.close()

    def _nist_drafts(self, cursor, transport, settings):
        headers = {"If-None-Match": cursor["etag"]} if cursor.get("etag") else {}
        status, response_headers, payload = transport.request("GET", NIST_DRAFTS, headers=headers)
        if status == 304:
            return CollectionBatch([], cursor)
        data = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(data.get("entries"), list):
            raise ValueError("nist_draft_feed_invalid")
        from backend.services.pipeline import AI_PATTERN, SECURITY_PATTERN
        records = []
        for item in data["entries"]:
            title, content = plain(item.get("title", "")), plain(item.get("summary", ""))
            if not AI_PATTERN.search(title + " " + content) or not SECURITY_PATTERN.search(title + " " + content):
                continue
            link = item.get("link", "")
            parsed = urlsplit(link)
            if parsed.scheme != "https" or parsed.netloc != "csrc.nist.gov" or not parsed.path.startswith("/pubs/"):
                raise ValueError("nist_publication_link_invalid")
            published, pub_meta = dated_metadata(item.get("published"))
            updated, update_meta = dated_metadata(item.get("updated"), "updated")
            records.append(RawRecord(external_id=link, source_url=link, title=title, content=content or title,
                document_type="standard", content_completeness="summary", raw=item, is_demo=False,
                published_at=published, source_updated_at=updated, metadata_json={
                    "publisher": "NIST", "legal_status": "draft_open_for_comment", "coverage": "current_comment_feed",
                    "comment_period": plain(item.get("content", "")), "feed_url": NIST_DRAFTS,
                    **pub_meta, **update_meta}))
        # The full feed is bounded by the transport's 8 MB limit; never slice eligible drafts away.
        ids = sorted({record.external_id for record in records})
        signature = hashlib.sha256(json.dumps(ids).encode()).hexdigest()
        return CollectionBatch(records, {"etag": response_headers.get("etag"), "snapshot_hash": signature},
            coverage={"mode": "current_draft_catalogue", "catalogue_rows": len(data["entries"]),
                "eligible_count": len(ids), "eligible_ids": ids, "snapshot_hash": signature, "cycle_complete": True,
                "exhaustive_internet_coverage": False})

    def _govuk_search(self, cursor, transport, settings):
        now = utc_now()
        if cursor.get("strategy") == "recent_and_backfill_v1":
            recent, history = dict(cursor["recent"]), cursor.get("backfill")
        else:
            recent = {"from": (now - timedelta(hours=48)).isoformat()}
            history = {"from": (now - timedelta(days=self.config.initial_days)).isoformat(),
                       "until": (now - timedelta(hours=48)).isoformat()}
        current = self._policy_window(recent, transport, settings)
        past = self._policy_window(history, transport, settings, historical=True) if history else None
        records = {record.external_id: record for record in current.records}
        for record in past.records if past else []:
            records.setdefault(record.external_id, record)
        remaining = past.next_cursor if past and past.more else None
        return CollectionBatch(list(records.values()), {"strategy": "recent_and_backfill_v1",
            "recent": current.next_cursor, "backfill": remaining}, more=current.more or bool(remaining),
            coverage={"mode": "official_search_windows", "queries": list(POLICY_QUERIES),
                "recent_window": current.coverage, "backfill_pending": bool(remaining),
                "exhaustive_internet_coverage": False})

    def _policy_window(self, cursor, transport, settings, historical=False):
        now = utc_now()
        index, offset = int(cursor.get("query_index", 0)), int(cursor.get("offset", 0))
        if not 0 <= index < len(POLICY_QUERIES) or not 0 <= offset <= 10000:
            raise ValueError("policy_cursor_invalid")
        since, until = cursor["from"], cursor.get("until") or now.isoformat()
        params = [("q", POLICY_QUERIES[index]), ("count", self.config.page_size), ("start", offset),
            ("order", "-public_timestamp"), ("filter_public_timestamp", f"from:{since},to:{until}")]
        params += [("filter_content_store_document_type", kind) for kind in POLICY_TYPES]
        _, _, payload = transport.request("GET", GOVUK_SEARCH, params=params)
        data = json.loads(payload)
        if not isinstance(data.get("results"), list) or int(data.get("start", -1)) != offset:
            raise ValueError("policy_search_page_invalid")
        items, total = data["results"], int(data["total"])
        if not items and offset < total:
            raise ValueError("policy_search_empty_page")
        records = []
        reader = ExtendedCollector("govuk", {})
        for item in items:
            path = item.get("link", "")
            if not re.fullmatch(r"/government/(?:publications|consultations)/[a-z0-9/-]+", path):
                continue
            # Only the allowlisted official Content API is read, never arbitrary search links.
            result = reader._govuk({"next_path": path}, transport, settings)
            for record in result.records:
                record.metadata_json.update(coverage="official_search_content_api")
                records.append(record)
        page_more = offset + len(items) < total
        after = {"from": since, "until": until, "query_index": index, "offset": offset + len(items)} if page_more else {
            "from": since, "until": until, "query_index": index + 1, "offset": 0}
        more = page_more or index + 1 < len(POLICY_QUERIES)
        if not more:
            after = {"from": (timestamp(until) - timedelta(hours=48)).isoformat()}
        return CollectionBatch(records, after, more=more, coverage={"query": POLICY_QUERIES[index],
            "from": since, "until": until, "total_results": total, "offset": offset,
            "results_examined": len(items), "historical": historical, "more_in_query": page_more})
