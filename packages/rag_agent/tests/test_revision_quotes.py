"""Synthetic fixtures: structured quotes retain exact backend source values."""
import json
import pytest
from rag_agent.integration.api import _original_structured_excerpt, _locator, _public_references


LOCATOR = {"kind": "json_pointer", "pointer": "/raw_payload/source_fields/0/value"}


def test_source_key_order_is_restored_without_changing_values():
    affected = [{"package": "example", "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.2"}]}]}]
    revision = {"source_fields": {"affected": affected}}
    quote = _original_structured_excerpt(json.dumps(affected, sort_keys=True), LOCATOR, revision)
    assert quote == json.dumps(affected)
    text = json.dumps(revision)
    _, _, start, end = _locator("", text, quote).split(":")
    assert text[int(start):int(end)] == quote


@pytest.mark.parametrize("candidate", [
    [{"score": 9.8}], [{"score": True}], [{"score": 1, "invented": "value"}],
    [{"events": ["fixed", "introduced"]}], "invalid-json",
])
def test_changed_values_types_array_order_and_invalid_json_are_rejected(candidate):
    revision = {"source_fields": {"cvss": [{"score": 1}], "affected": [{"events": ["introduced", "fixed"]}]}}
    excerpt = candidate if isinstance(candidate, str) else json.dumps(candidate, sort_keys=True)
    assert _original_structured_excerpt(excerpt, LOCATOR, revision) is None


def test_internal_normalized_identifiers_are_not_source_quotes():
    excerpt = '[{"namespace": "CVE", "value": "CVE-2099-1234"}]'
    assert _original_structured_excerpt(excerpt, {"kind": "json_pointer", "pointer": "/identifiers"},
                                        {"aliases": ["CVE-2099-1234"]}) is None


def test_large_source_object_is_verified_before_quote_is_bounded():
    value = {"items": [{"package": "example", "detail": "original " * 600}], "parsed": False}
    revision = {"source_fields": {"affected": value}}
    quote = _original_structured_excerpt(json.dumps(value, sort_keys=True), LOCATOR, revision)
    assert quote == json.dumps(value)[:2000]
    assert quote in json.dumps(revision)


def test_citation_after_unfinished_json_array_uses_public_number():
    answer = 'affected: {"items": [{"events": [{"fixed": "1. [internal:field:1]'
    assert _public_references(answer, {"internal:field:1": 3}).endswith('[3]')
    assert _public_references('[unknown] [a,b]', {"a": 1, "b": 2}) == '[unknown] [1,2]'
