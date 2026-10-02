"""Regression cases for the observed marketing/boilerplate false positives."""
import json
from backend.collectors.extended import ExtendedCollector
from backend.schemas.contracts import RawRecord
from backend.services.pipeline import relevance


def article(body, title="Fictional source"):
    return RawRecord(external_id="quality-test", title=title, content=body,
        document_type="discussion", source_url="https://example.invalid/quality-test", is_demo=True,
        content_completeness="summary")


def test_ordinary_ai_marketing_and_disclosure_link_are_not_security_intelligence():
    assert not relevance(article("The AI ecosystem is improving. Please translate our courses."))["relevant"]
    assert not relevance(article("AI image generator, art styles and private vault. All free."))["relevant"]
    assert not relevance(article("Security advisories for routers. See our response to AI-Accelerated Vulnerability Discovery."))["relevant"]
    assert relevance(article("This LLM has a prompt injection vulnerability."))["relevant"]
    assert not relevance(article("AI security title", title="AI security title"))["relevant"]


def test_real_source_shapes_vendor_catalogue_and_community_search():
    class Catalogue:
        def request(self, *args, **kwargs):
            return 200, {}, json.dumps({"data": [{"title": "<a href='https://nvidia.custhelp.com/app/answers/detail/a_id/9999'>NVIDIA TensorRT-LLM - Test</a>",
                "bulletin id": "test-only-9999", "cve identifier(s)": "CVE-2099-12345, CVE-2099-12346",
                "severity": "High", "publish date": "02 Oct 2026", "last updated": "02 Oct 2026"}]}).encode()
    row = ExtendedCollector("nvidia", {})._nvidia({}, Catalogue(), None).records[0]
    assert row.content_completeness == "summary" and not row.metadata_json["full_bulletin_fetched"]
    assert row.published_at is None and row.metadata_json["publication_date"] == "2026-10-02"
    assert len(row.aliases) == 2 and relevance(row)["relevant"]
    class Search:
        def request(self, *args, **kwargs):
            assert '"prompt injection"' in kwargs["params"]["q"]
            return 200, {}, json.dumps({"topics": [{"id": 99999, "title": "Fictional security discussion"}],
                "posts": [{"id": 33333, "topic_id": 99999, "blurb": "A <b>prompt injection</b> attack on LLMs."}],
                "grouped_search_result": {"more_posts": True}}).encode()
    batch = ExtendedCollector("huggingface", {})._huggingface({}, Search(), None)
    assert batch.more and batch.next_cursor["page"] == 2
    assert str(batch.records[0].source_url) == "https://discuss.huggingface.co/t/99999"
    assert relevance(batch.records[0])["relevant"]


def test_crossref_skips_generic_papers_and_pauses_without_losing_cursor():
    class Metadata:
        def request(self, *args, **kwargs):
            return 200, {}, json.dumps({"message": {"items": [{"DOI": "10.test/generic",
                "title": ["Medical clinical cohort"], "published": {"date-parts": [[2026, 1, 1]]}}],
                "next-cursor": "test-next-page"}}).encode()
    collector = ExtendedCollector("crossref", {"max_items": 1, "max_pages_per_cycle": 1})
    batch = collector._crossref({"since": "2026-01-01"}, Metadata(), None)
    assert not batch.records and not batch.more
    assert batch.next_cursor["cursor"] == "test-next-page" and batch.next_cursor["since"] == "2026-01-01"
