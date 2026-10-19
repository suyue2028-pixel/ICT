"""Ten real-corpus questions with versioned evidence candidates, no fake gold answers."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, Vulnerability, VulnerabilityAlias, VulnerabilityDocument


def main():
    cases = []
    with get_session_factory()() as db:
        cves = list(db.scalars(select(VulnerabilityAlias.value).join(Vulnerability).where(
            Vulnerability.is_demo.is_(False), Vulnerability.ai_relevant.is_(True),
            VulnerabilityAlias.value.startswith("CVE-")).order_by(VulnerabilityAlias.value.desc()).limit(2)))
        if len(cves) < 2:
            raise RuntimeError("Two real AI-relevant CVEs are required")
        first, second = cves
        questions = [f"{first} 影响哪些产品和版本？", f"{first} 的 CWE 分类和依据是什么？",
            f"{first} 的 CVSS 版本、向量和分值有哪些？缺失请明确。", f"{first} 有哪些公开修复建议？",
            f"{first} 有哪些别名及不同来源的记录？", f"比较 {first} 与 {second} 的公开证据，不要因主题相似合并漏洞。"]
        for number, question in enumerate(questions, 1):
            refs = db.scalars(select(Document).join(VulnerabilityDocument,
                VulnerabilityDocument.document_id == Document.id).join(VulnerabilityAlias,
                VulnerabilityAlias.vulnerability_id == VulnerabilityDocument.vulnerability_id).where(
                VulnerabilityAlias.value.in_([first, second] if number == 6 else [first]),
                Document.is_demo.is_(False), Document.deleted_at.is_(None))).unique().all()
            cases.append(make_case(number, question, refs))
        for number, adapter, question in (
                (7, "arxiv", "这篇论文研究的 AI 安全问题是什么？只依据摘要，不声称读过全文："),
                (8, "nist", "这份 NIST 框架章节讨论什么？请说明其自愿框架性质："),
                (9, "china_policy", "这份法规对生成式人工智能服务规定了什么？请引用条款并说明生效时间：")):
            doc = db.scalar(select(Document).join(DataSource).where(DataSource.adapter == adapter,
                Document.is_demo.is_(False), Document.deleted_at.is_(None), Document.ai_relevant.is_(True))
                .order_by(Document.id).limit(1))
            if doc is None:
                raise RuntimeError("Required real source missing: " + adapter)
            cases.append(make_case(number, question + doc.title, [doc]))
        cases.append(make_case(10, "请查询 CVE-2099-00000。如果本地没有证据，请明确说明，不要补造。", []))
    target = ROOT / "output" / "agent-cases.json"
    if target.exists():
        target = target.with_name("agent-cases-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({"schema_version": "1", "status": "unreviewed",
        "note": "Candidate documents are not gold answers or proof of correct retrieval", "cases": cases},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "cases": len(cases), "status": "unreviewed", "qa_accuracy": None}))


def make_case(number, question, documents):
    return {"case_id": f"agent-{number:02}", "question": question,
        "request": {"query": question, "is_demo": False}, "response": None,
        "candidate_evidence": [{"document_id": str(d.id), "content_hash": d.content_hash,
            "source_url": d.source_url, "title": d.title} for d in documents],
        "follow_up": "刚才提到的内容还有哪些证据局限？",
        "review": {"reviewer": None, "reviewed_at": None, "answer_correct": None, "citation_correct": None}}


if __name__ == "__main__":
    main()
