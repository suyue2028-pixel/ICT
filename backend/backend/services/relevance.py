"""Explainable AI-security triage, separate from source truth or human review."""
import re

RELEVANCE_VERSION = "rule-v5"
AI_PRODUCTS = {"torch", "pytorch", "transformers", "langchain", "tensorflow", "keras",
    "onnx", "vllm", "ollama", "gradio", "llama", "scikit-learn", "openai", "tensorrt",
    "tensorrt-llm", "triton inference server", "chatrtx", "nemo", "riva", "merlin",
    "ai enterprise", "llama-index", "llama-index-core", "sentence-transformers"}
SECURITY_PATTERN = re.compile(r"\b(?:security|safety|vulnerabilit\w*|CVE-\d{4}-\d{4,}|CWE-\d+|exploit\w*|attack\w*|"
    r"unsafe|deserializ\w*|malicious|injections?|poison\w*|privacy|risk\w*|guardrails?|sandbox\w*)\b|"
    r"安全|漏洞|投毒|注入|攻击|风险|泄露|法规", re.I)
AI_PATTERN = re.compile(r"\b(?:AI|artificial intelligence|machine learning|deep learning|(?:large )?language models?|"
    r"LLMs?|prompt[ -]injections?|model weights|neural networks?|generative AI)\b|人工智能|大语言模型|提示词注入", re.I)
AI_TERM = r"(?:\b(?:AI|LLMs?|artificial intelligence|machine learning|deep learning|(?:large )?language models?|generative AI)\b|人工智能|大语言模型)"
DIRECT = re.compile(AI_TERM + r"[ -]+(?:cyber[ -]+)?(?:security|safety|privacy|risks?|governance|risk management)\b|"
    r"\b(?:security|safety|privacy|risks?|governance)\s+(?:guidance\s+)?(?:of|for|in)\s+(?:generative\s+)?" + AI_TERM + r"|"
    r"\b(?:protect|secure|safeguard|defend)\b[^.!?\n]{0,100}" + AI_TERM + r"|"
    + AI_TERM + r"[^.!?\n]{0,110}\b(?:risks?|threats?)\b|"
    r"\b(?:risks?|threats?)\b[^.!?\n]{0,110}" + AI_TERM + r"|"
    r"人工智能.{0,20}(?:安全|风险|治理|法规)|(?:安全|风险|治理).{0,20}人工智能", re.I)
SPECIFIC = re.compile(r"\b(?:vulnerabilit\w*|exploit\w*|attack\w*|unsafe|deserializ\w*|malicious|"
    r"injections?|poison\w*|privacy|guardrails?|sandbox\w*|jailbreak\w*|adversarial|"
    r"exfiltrat\w*|leak\w*|unauthori\w*|backdoors?|approval integrity|instruction hierarch\w*|"
    r"CVE-\d{4}-\d{4,}|CWE-\d+)\b|漏洞|投毒|注入|攻击|泄露", re.I)
GOVERNANCE = re.compile(r"risk management framework|trustworth\w*|model governance|"
    r"code of practice|regulat\w*\s+(?:the\s+)?(?:use\s+of\s+)?artificial intelligence|"
    r"人工智能.{0,20}(?:管理办法|监管|法规)|生成式人工智能服务", re.I)
AI_FOR_SECURITY = re.compile(AI_TERM + r"[^.!?\n]{0,180}(?:CSF|cybersecurity|threat detection|code audit|security (?:audit|review|evaluation)|malware detection|fraud detection)|"
    r"(?:CSF|cybersecurity|threat detection|security (?:audit|review|evaluation)|malware detection|fraud detection)[^.!?\n]{0,180}" + AI_TERM, re.I)
PHYSICAL_SECURITY = re.compile(r"(?:face|facial) recognition|door access|home security|physical security|人脸识别|门禁", re.I)
ML_ATTACK = re.compile(r"machine learning[ -]based modeling attacks?|AI[ -]enabled attacks?", re.I)


def product_context(product, text):
    """Vendor-qualified names, never the cryptographic tool Merlin by name alone."""
    if product not in {"merlin", "nemo", "riva"}:
        return True
    for match in re.finditer(r"(?<![\w-])" + re.escape(product) + r"(?![\w-])", text, re.I):
        context = text[max(0, match.start() - 90):match.end() + 90]
        if re.search(r"\bnvidia(?:®)?\b", context, re.I):
            return True
        if product == "nemo" and re.search(r"\bnemo\s+(?:framework|toolkit|guardrails)\b", context, re.I):
            return True
        if product == "riva" and re.search(r"\briva\s+(?:speech|ASR|TTS)\b", context, re.I):
            return True
        if product == "merlin" and re.search(r"\bmerlin\s+(?:recommender|recommendation)\b", context, re.I):
            return True
    return False


def assess_relevance(record):
    body = re.sub(r"AI-Accelerated\s+Vulnerability\s+Discovery", "", record.content, flags=re.I)
    body = re.sub(r"injection[ -]mou?ld(?:ed|ing)?|(?:hydrate\s+)?inhibitor\s+injection", " ", body, flags=re.I)
    if re.search(r"energetic materials?|molecular|dinitro|force field", body, re.I):
        body = re.sub(r"\bLLM-\d{2,}\b", " ", body, flags=re.I)
    # Title can qualify a vendor/product, but cannot establish security relevance.
    qualified_text = record.title + "\n" + body
    raw_products = {p.lower().replace("_", "-") for p in record.products} & AI_PRODUCTS
    raw_body = {p for p in AI_PRODUCTS if re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", body, re.I)}
    products = sorted(p for p in raw_products if product_context(p, qualified_text))
    body_products = sorted(p for p in raw_body if product_context(p, qualified_text))
    ignored = sorted((raw_products | raw_body) - set(products) - set(body_products))
    ai_matches, security_matches = list(AI_PATTERN.finditer(body)), list(SECURITY_PATTERN.finditer(body))
    terms = sorted({m.group().lower() for m in ai_matches})
    security_terms = sorted({m.group().lower() for m in security_matches})
    has_body = record.content_completeness != "metadata_only" and record.content.strip() != record.title.strip()
    narrow_matches = list(SPECIFIC.finditer(body))
    # Abstracts often introduce the model and discuss attacks several sentences
    # later. Their relation must not disappear at an arbitrary character cutoff.
    related_risk = bool(ai_matches and narrow_matches)
    direct = bool(DIRECT.search(body))
    governance = bool(terms and record.document_type in {"policy", "regulation", "standard"} and GOVERNANCE.search(body))
    security_use = bool(terms and AI_FOR_SECURITY.search(body))
    physical_use = bool(terms and PHYSICAL_SECURITY.search(body) and security_terms)
    enabled_attack = bool(terms and ML_ATTACK.search(body))
    product_security = bool((products or body_products) and (security_terms or record.document_type == "vulnerability"))
    relevant = bool((products and product_security) or has_body and (product_security or
                    terms and (direct or related_risk or governance or security_use or physical_use or enabled_attack)))
    if relevant:
        scope = ("ai_enabled_attacks" if enabled_attack else "ai_for_physical_security" if physical_use and not (direct or related_risk)
                 else "ai_for_security" if security_use and not (direct or product_security or related_risk)
                 else "ai_security_governance" if governance else "ai_system_security")
        state, reason = "candidate", "qualified_product_or_related_security_evidence"
    elif ignored and not terms:
        scope, state, reason = "general_security", "excluded", "ambiguous_product_without_ai_context"
    elif terms and security_terms:
        scope, state, reason = "adjacent_topics", "needs_review", "ai_and_security_cooccurrence_without_relation"
    else:
        scope, state, reason = "unrelated", "excluded", "insufficient_ai_security_evidence"
    categories = []
    if relevant and (products or body_products):
        categories.append("ai_infrastructure")
    if relevant and re.search(r"prompt[ -]injections?|data poisoning|model weights|提示词|数据投毒", body, re.I):
        categories.append("model_and_data")
    if relevant and re.search(r"\bagents?\b|langchain|智能体", body, re.I):
        categories.append("application_and_agent")
    if relevant and re.search(r"supply.chain|dependency|供应链", body, re.I):
        categories.append("supply_chain")
    return {"relevant": relevant, "method": RELEVANCE_VERSION, "product_matches": products,
        "body_terms": terms, "body_products": body_products, "security_terms": security_terms,
        "body_available": has_body, "risk_categories": categories, "topic_scope": scope,
        "triage_state": state, "reason_code": reason, "ambiguous_products_ignored": ignored,
        "limitation": "Rule-based triage, not independent accuracy. Adjacent scopes remain labelled; title alone is insufficient."}
