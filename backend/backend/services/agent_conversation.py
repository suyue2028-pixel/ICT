"""Legacy offline intent helpers and semantic answer safety boundaries.

The live gateway validates response contents; it does not reclassify the user's
intent using the legacy full-match recognizer. Trusted platform prompts live in
llm_prompts so ordinary numbers and general conversation use one configuration.
"""
from __future__ import annotations

import re
import unicodedata
from backend.services.llm_prompts import CONVERSATION_SYSTEM


_INTENTS = {
    "greeting": r"你好(?:呀|啊|哇|亲|亲亲)?|您好|哈喽|嗨|hi|hello|hey|早(?:上好)?|上午好|中午好|下午好|晚上好|在吗|你在吗|有人吗",
    "thanks": r"谢谢(?:你|您|啦|了)?|感谢(?:你|您)?|多谢|辛苦了|好的|好哒|明白了|知道了|收到|ok|thanks|thank you",
    "farewell": r"再见|拜拜|晚安|下次聊|bye|goodbye|see you",
    "identity": r"你是谁|你是什么|你是什么助手|你是什么模型|你用的是什么模型|介绍一下(?:你自己|自己)|自我介绍|你是干什么的",
    "capabilities": r"你能做什么|你可以做什么|你有什么功能|你有哪些功能|你能帮我做什么|你可以帮我做什么|怎么使用你|如何使用你|怎么用你|介绍一下你的功能|你能联网吗|你可以联网吗|你能联网搜索吗",
    "smalltalk": r"你好吗|你怎么样|你还好吗|你在干嘛|你在做什么|今天怎么样|今天过得怎么样|我想聊聊天|聊聊天|陪我聊聊|我们聊聊|我(?:今天)?(?:有点|有些|很)(?:累|紧张|烦|焦虑)",
}

_REPLIES = {
    "greeting": "你好！我是这个项目的 AI 安全情报助手。你可以直接和我交流，也可以告诉我想了解的漏洞、产品或安全问题，我会结合库里的资料帮你分析。",
    "thanks": "不客气！需要继续解释刚才的内容，或者有新的问题，直接告诉我就好。",
    "farewell": "再见！需要继续讨论时，随时回来找我。",
    "identity": "我是这个项目的 AI 安全情报助手，可以和你交流，并根据知识库资料解释安全问题、查找漏洞信息和来源。",
    "capabilities": "我可以进行基础交流，也可以根据本系统知识库查找漏洞和安全资料、解释影响与处置依据。你可以问某个漏洞的触发条件，或让我比较不同资料的要点。信息不够时我会说明缺少什么；当前问答没有独立的实时联网搜索工具。",
    "smalltalk": "我在，可以陪你聊聊。你现在想聊项目进展，还是最近让你有些累或紧张的事情？",
}

def conversation_kind(question: str) -> str | None:
    text = unicodedata.normalize("NFKC", question).strip().strip(" ,.!?~，。！？～").strip()
    text = re.sub(r"\s+", " ", text)
    for kind, pattern in _INTENTS.items():
        if re.fullmatch(pattern, text, re.I):
            return kind
    # A combined greeting + help request is still bounded by a full match.
    remainder = re.sub(r"^(?:你好|您好|hi|hello)[,!.\s]+", "", text, count=1, flags=re.I)
    if remainder != text:
        for kind in ("identity", "capabilities", "smalltalk"):
            if re.fullmatch(_INTENTS[kind], remainder, re.I):
                return kind
    return None


def fallback_reply(kind: str) -> str:
    return _REPLIES[kind]


UNVERIFIED_EXPLANATION_NOTICE = "模型通用解释；未经过来源核验。"


def concrete_security_facts(text: str) -> bool:
    """Guard identifiers and specific security claims, independent of intent routing."""
    return bool(re.search(
        r"\b(?:CVE-\d|GHSA-[A-Za-z0-9-]+)|"
        r"CVSS[^\n。]{0,40}\d|"
        r"(?:已|已经|未|尚未|没有)[^\n。]{0,12}(?:列入|收录)[^\n。]{0,12}(?:KEV|目录)|"
        r"(?:已|已经|未|尚未|没有)[^\n。]{0,12}(?:在野利用|在网利用)|已被利用|未被利用|已遭利用|"
        r"(?:修复版本|受影响版本|fixed\s+in|affected\s+versions?)[^\n。]{0,40}\d|"
        r"(?:官方[^\n。]{0,8}(?:已|已经)(?:修复|发布补丁)|补丁版本[^\n。]{0,20}\d|修复于|已修复|不受影响|均安全)",
        text, re.I))


def safe_unreferenced_text(answer: str, *, limit: int) -> bool:
    """Allow ordinary numbers; reject fabricated citation/URL/security fact channels."""
    return (isinstance(answer, str) and bool(answer.strip()) and len(answer) <= limit
        and not re.search(r"https?://|www\.|\[[^\]]*\]|<[^>]+>", answer, re.I)
        and not concrete_security_facts(answer))


def safe_conversation_answer(answer: str) -> bool:
    """Conversation is semantic; math/numbers are permitted without citation claims."""
    return safe_unreferenced_text(answer, limit=4000)


def safe_general_explanation(answer: str, query: str | None) -> bool:
    """Unverified concepts cannot answer concrete identifier/version/freshness requests."""
    if not query or concrete_security_facts(query):
        return False
    if re.search(r"\d+(?:\.\d+)+|\bKEV\b|最新|当前|在野|在网|在用|修复版本|受影响版本|官方.*(?:修复|补丁)|"
                 r"CVSS[^\n。]{0,30}(?:评分|分数)|(?:评分|分数)[^\n。]{0,30}CVSS", query, re.I):
        return False
    concrete_need = re.search(r"漏洞|修复|补丁|\b(?:upgrade|affected|vulnerabilit(?:y|ies)|patch(?:es)?)\b", query, re.I)
    # Only a whole, generic definition/mechanism question qualifies. A named
    # product or appended repair request cannot borrow this narrow exception.
    normalized = unicodedata.normalize("NFKC", query).strip().strip(" ?？。.!！")
    concept = r"(?:安全漏洞|漏洞修复|漏洞|软件补丁|补丁管理|补丁)"
    pure_concept = re.fullmatch(
        rf"(?:(?:什么是|解释(?:一下)?|介绍(?:一下)?){concept}(?:的?(?:形成|发生)?(?:机制|原理|概念|含义))?|"
        rf"{concept}(?:是什么|的?(?:形成|发生)?(?:机制|原理|概念|含义)))", normalized)
    if concrete_need and not pure_concept:
        return False
    if not safe_unreferenced_text(answer, limit=4000):
        return False
    if re.search(r"\d+(?:\.\d+)+|升级|\bupgrade\b|(?:受影响|affected)[^\n。]{0,20}(?:版本|versions?)|"
                 r"(?:影响|修复|补丁)[^\n。]{0,12}版本|\bfixed\s+in\b", answer, re.I):
        return False
    generic_terms = {"ai", "llm", "llms", "agent", "agents", "rag", "api", "apis", "tool", "tools", "model", "models", "prompt"}
    for name in set(re.findall(r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_-]{1,80}(?![A-Za-z0-9_-])", answer)):
        if name.lower() in generic_terms:
            continue
        if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])[^\n。]{{0,50}}(?:漏洞|命令执行|代码执行|任意执行|未授权|权限绕过|受影响|修复|补丁|affected|vulnerab)", answer, re.I):
            return False
    return True
