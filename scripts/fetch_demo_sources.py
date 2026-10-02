"""Bounded, fixed-source sampling. This is not a scheduler or monitoring service."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import httpx
from bs4 import BeautifulSoup
from rag_agent.schemas import DocumentInput, Identifier

SOURCES = [
    {"id": cve, "url": "https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=" + cve, "parser": "nvd", "doc_type": "vulnerability", "source": "NVD", "kind": "vulnerability_database"}
    for cve in ["CVE-2024-37032", "CVE-2024-34359", "CVE-2024-5998", "CVE-2024-3571", "CVE-2024-1569", "CVE-2024-5480"]
] + [
    {"id": paper, "url": "https://arxiv.org/abs/" + paper, "parser": "paper", "doc_type": "paper", "source": "arXiv", "kind": "academic"}
    for paper in ["2302.12173", "2307.15043", "2306.05499"]
] + [
    {"id": slug, "url": "https://genai.owasp.org/llmrisk/" + slug + "/", "parser": "html", "doc_type": "standard", "source": "OWASP GenAI", "kind": "industry_guidance"}
    for slug in ["llm01-prompt-injection", "llm022025-sensitive-information-disclosure", "llm032025-supply-chain", "llm042025-data-and-model-poisoning", "llm052025-improper-output-handling", "llm062025-excessive-agency", "llm072025-system-prompt-leakage", "llm082025-vector-and-embedding-weaknesses", "llm092025-misinformation", "llm102025-unbounded-consumption"]
] + [
    {"id": "AI-RMF", "url": "https://www.nist.gov/itl/ai-risk-management-framework", "parser": "html", "doc_type": "standard", "source": "NIST", "kind": "government_guidance"},
    {"id": "AI-RMF-GenAI", "url": "https://www.nist.gov/itl/ai-risk-management-framework/nist-ai-rmf-generative-ai-profile", "parser": "html", "doc_type": "standard", "source": "NIST", "kind": "government_guidance"},
    {"id": "EU-AI-Act", "url": "https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai", "parser": "html", "doc_type": "policy", "source": "European Commission", "kind": "government_policy"},
]
ALLOWED = {urlsplit(s["url"]).hostname for s in SOURCES}


def timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc) if not re.search(r"Z|[+-]\d\d:\d\d$", value) else datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse(spec, raw, collected):
    common = dict(external_id=spec["id"], source_name=spec["source"], source_kind=spec["kind"], source_url=spec["url"], doc_type=spec["doc_type"], collected_at=collected,
                  risk_category=["AI infrastructure" if spec["parser"] == "nvd" else "model/application security"])
    if spec["parser"] == "nvd":
        payload = json.loads(raw)
        cve = payload["vulnerabilities"][0]["cve"]
        if cve["id"] != spec["id"] or cve.get("vulnStatus") == "Rejected":
            raise ValueError("source record mismatch or rejected CVE")
        common["source_url"] = "https://nvd.nist.gov/vuln/detail/" + cve["id"]
        text = next(d["value"] for d in cve["descriptions"] if d["lang"] == "en")
        products = []
        for name in ["Ollama", "llama-cpp-python", "LangChain", "MLflow", "vLLM", "PyTorch", "Transformers", "lollms-webui", "LiteLLM", "Gradio"]:
            if name.casefold() in text.casefold():
                products.append(name)
        if not products:
            raise ValueError("AI product relevance not established for this sample")
        return DocumentInput(**common, title=cve["id"] + " — " + ", ".join(products), text=text,
                             identifiers=[Identifier(namespace="CVE", value=cve["id"])], product_entities=products,
                             published_at=timestamp(cve.get("published")), source_modified_at=timestamp(cve.get("lastModified")), raw_payload={"cve": cve})
    soup = BeautifulSoup(raw, "html.parser")
    if spec["parser"] == "paper":
        title = soup.select_one('meta[name="citation_title"]')
        abstract = soup.select_one("blockquote.abstract")
        if not title or not abstract:
            raise ValueError("paper title or abstract missing")
        published = soup.select_one('meta[name="citation_date"]')
        date = datetime.strptime(published["content"], "%Y/%m/%d").replace(tzinfo=timezone.utc) if published else None
        return DocumentInput(**common, title=title["content"], text=abstract.get_text(" ", strip=True), published_at=date,
                             identifiers=[Identifier(namespace="ARXIV", value=spec["id"])])
    for tag in soup.select("script,style,nav,header,footer,noscript,form,aside"):
        tag.decompose()
    title = soup.find("h1") or soup.find("h2", string=re.compile(r"LLM\d"))
    # OWASP's main element also contains a risk-card carousel; only keep the post body.
    content = soup.select_one(".xpro-elementor-content") or soup.find("article") or soup.find("main") or soup.body
    if not content:
        raise ValueError("main content missing")
    text = content.get_text("\n", strip=True)
    if len(text) < 200 or re.search(r"access denied|verify you are human", text[:500], re.I):
        raise ValueError("blocked or unusable content")
    meta_title = soup.select_one('meta[property="og:title"]')
    return DocumentInput(**common, title=meta_title["content"] if meta_title else title.get_text(" ", strip=True) if title else spec["id"], text=text)


async def fetch(out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw").mkdir(exist_ok=True)
    semaphore = asyncio.Semaphore(3)
    documents, records = [], []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "ICT-RAG-demo/0.1 (bounded academic sample)"}) as client:
        async def one(spec):
            async with semaphore:
                record = {**spec, "status": "failed", "attempts": 0}
                for attempt in range(2):
                    record["attempts"] = attempt + 1
                    try:
                        response = await client.get(spec["url"])
                        response.raise_for_status()
                        if urlsplit(str(response.url)).hostname not in ALLOWED:
                            raise ValueError("redirect left trusted source allowlist")
                        if len(response.content) > 5_000_000:
                            raise ValueError("source exceeds bounded sample size")
                        collected = datetime.now(timezone.utc)
                        doc = parse(spec, response.content, collected)
                        digest = hashlib.sha256(response.content).hexdigest()
                        (out / "raw" / (digest + ".source")).write_bytes(response.content)
                        record.update(status="ok", fetched_at=collected.isoformat(), sha256=digest,
                                      document_hash=hashlib.sha256(doc.model_dump_json().encode()).hexdigest(), title=doc.title,
                                      external_id=doc.external_id, final_url=str(response.url), bytes=len(response.content))
                        documents.append(doc)
                        break
                    except Exception as exc:
                        record["error"] = f"{type(exc).__name__}:{str(exc)[:180]}"
                        if attempt == 0:
                            await asyncio.sleep(0.3)
                records.append(record)
        await asyncio.gather(*(one(s) for s in SOURCES))
    documents.sort(key=lambda d: (d.source_name, d.external_id or ""))
    (out / "documents.jsonl").write_text("\n".join(d.model_dump_json() for d in documents) + "\n")
    summary = {"document_count": len(documents), "doc_types": sorted({d.doc_type for d in documents}),
               "channel_kinds": sorted({d.source_kind for d in documents}), "not_continuous_monitoring": True,
               "guidance_note": "OWASP risk guidance uses doc_type=standard but is not a formal normative standard",
               "generated_at": datetime.now(timezone.utc).isoformat(), "sources": sorted(records, key=lambda r: r["id"])}
    if (out / "manifest.json").exists():
        old = json.loads((out / "manifest.json").read_text())
        summary["previous_failed_attempts"] = old.get("previous_failed_attempts", []) + [r for r in old.get("sources", []) if r["status"] == "failed"]
    (out / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k not in {"sources", "previous_failed_attempts"}}, ensure_ascii=False, indent=2))
    print("failed:", [r["id"] for r in records if r["status"] != "ok"])


def reparse(out):
    manifest = json.loads((out / "manifest.json").read_text())
    documents = []
    by_id = {s["id"]: s for s in SOURCES}
    for record in manifest["sources"]:
        if record["status"] != "ok":
            continue
        raw = (out / "raw" / (record["sha256"] + ".source")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("raw sample hash mismatch")
        doc = parse(by_id[record["id"]], raw, datetime.fromisoformat(record["fetched_at"]))
        documents.append(doc)
        record["title"] = doc.title
        record["document_hash"] = hashlib.sha256(doc.model_dump_json().encode()).hexdigest()
    documents.sort(key=lambda d: (d.source_name, d.external_id or ""))
    (out / "documents.jsonl").write_text("\n".join(d.model_dump_json() for d in documents) + "\n")
    manifest["parser_revision"] = "v2-article-container-and-og-title"
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("Reparsed", len(documents), "verified raw samples")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/demo"))
    parser.add_argument("--reparse", action="store_true")
    args = parser.parse_args()
    if args.reparse:
        reparse(args.output)
    else:
        asyncio.run(fetch(args.output))
