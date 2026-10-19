import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit
from ..schemas import DocumentInput

ID_PATTERN = re.compile(r"\b(?:CVE-\d{4}-\d{4,}|GHSA-[23456789cfghjmpqrvwx]{4}-[23456789cfghjmpqrvwx]{4}-[23456789cfghjmpqrvwx]{4})\b", re.I)


def identifiers(text: str) -> list[str]:
    return list(dict.fromkeys(i.upper() for i in ID_PATTERN.findall(text)))


def normalized_url(value: str) -> str:
    p = urlsplit(value)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))


def stable_id(document: DocumentInput) -> str:
    key = document.source_name.strip().casefold() + ":" + (document.external_id or normalized_url(document.source_url))
    return "doc_" + hashlib.sha256(key.encode()).hexdigest()[:24]


def content_hash(document: DocumentInput) -> str:
    # A new collection timestamp alone is not a source update.
    payload = document.model_dump(mode="json", exclude={"collected_at"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
