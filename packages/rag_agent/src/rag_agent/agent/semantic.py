"""Validated LLM-first routing; rules guard entities after semantic decisions."""
from typing import Literal, Annotated
import re
from pydantic import Field, StrictBool, model_validator
from ..schemas import Model
from ..ingestion.normalize import identifiers
from .conversation import safe_conversation_answer, conversation_kind


class RoutingEntities(Model):
    cve_ids: list[Annotated[str,Field(min_length=1,max_length=50,strict=True)]] = Field(default_factory=list,max_length=8)
    products: list[Annotated[str,Field(min_length=1,max_length=100,strict=True)]] = Field(default_factory=list,max_length=8)


class SemanticClassification(Model):
    intent: Literal['general','security','clarification']
    entities: RoutingEntities
    information_needs: list[Annotated[str,Field(min_length=1,max_length=120,strict=True)]] = Field(max_length=8)
    freshness_required: StrictBool
    conversation_answer: str | None = Field(default=None,max_length=4000)

    @model_validator(mode='after')
    def direct_answer(self):
        if self.intent=='general' and not safe_conversation_answer(self.conversation_answer or ''):
            raise ValueError('invalid general answer')
        if self.intent!='general' and self.conversation_answer not in {None,''}:
            raise ValueError('professional classification cannot contain unverified answer')
        return self


class RoutingAmbiguity(ValueError):
    """A historical target is not uniquely determined by the latest topic."""


def semantic_followup(question):
    follows=bool(re.search(r'它|这个|该漏洞|上述|刚才|前面|上面|那个|this|that|previous',question,re.I))
    if len(question)<100 and not re.search(r'什么是|定义|原理',question):
        follows=follows or bool(re.search(r'有没有|是否|怎么|如何|有哪些|有什么',question)
            and re.search(r'修复|缓解|受影响|在野|补丁|利用',question))
    return follows


def validated_targets(decision,request):
    """A model cannot introduce a CVE absent from the question/user context."""
    explicit=list(dict.fromkeys(identifiers(request.question)))
    proposed=[value.upper() for value in decision.entities.cve_ids]
    if explicit:
        if not set(proposed).issubset(explicit):raise ValueError('classifier invented target')
        return explicit
    if not proposed:return []
    if not semantic_followup(request.question):raise ValueError('classifier invented target')
    prior=[]
    all_user_ids=set()
    for message in request.history:
        if message.role=='user':all_user_ids.update(identifiers(message.content))
    if not set(proposed).issubset(all_user_ids):raise ValueError('classifier invented target')
    for message in reversed(request.history):
        if message.role!='user':continue
        ids=list(dict.fromkeys(identifiers(message.content)))
        if ids:
            prior=ids
            break
        if conversation_kind(message.content) in {'greeting','thanks'}:continue
        if semantic_followup(message.content):continue
        # Any new user-authored topic ends the target scan.
        break
    plural=bool(re.search(r'这两个|它们|这些漏洞|上述漏洞|both|these vulnerabilities',request.question,re.I))
    if not prior or (len(prior)>1 and not plural):
        raise RoutingAmbiguity('historical target is ambiguous')
    if plural and set(proposed)!=set(prior):raise RoutingAmbiguity('incomplete plural target')
    allowed=set(prior)
    if not set(proposed).issubset(allowed):
        raise RoutingAmbiguity('historical target changed topic')
    return list(dict.fromkeys(proposed))


def can_explain_unverified(state):
    """Only stable concepts, never product vulnerability or live hard facts."""
    q=state['resolved_question']
    if state.get('identifiers') or identifiers(q):return False
    classification=state.get('classification')
    if classification and classification.entities.products:return False
    from .workflow import package_entities
    if package_entities(q) or state.get('fresh_required'):return False
    generic=re.fullmatch(r'\s*(?:请)?(?:(?:什么是|解释(?:一下)?|介绍(?:一下)?)(?:软件)?(?:漏洞|补丁|修复)(?:的?(?:形成)?(?:机制|概念|原理))?|'
        r'(?:软件)?(?:漏洞|补丁|修复)(?:是什么|什么意思|如何产生|的形成机制))[？?。\s]*',q)
    if not generic and re.search(r'漏洞|修复|补丁|升级|vulnerabilit|patch|upgrade',q,re.I):return False
    return not re.search(r'(?<!\d)\d+(?:\.\d+)+(?!\d)|\b(?:CVE|GHSA|KEV)\b|CVSS.*(?:分数|评分|score)|受影响.*版本|'
        r'修复.*版本|补丁.*版本|官方.*(?:修复|补丁)|在野|已知.*利用|affected.*version|fixed.*version',q,re.I)


UNVERIFIED_NOTICE='模型通用解释；未经过来源核验。'


def safe_unverified_answer(answer):
    return (isinstance(answer,str) and bool(answer.strip()) and len(answer)<=4000
        and not re.search(r'https?://|www\.|<[^>]+>|\[[^\]]*\]|(?<!\d)\d+(?:\.\d+)+(?!\d)|\bv\d|\b(?:CVE-\d|GHSA-)|'
            r'CVSS.{0,30}\d|(?:修复版本|受影响版本|fixed in|affected version).{0,30}\d|'
            r'(?:升级|更新)(?:到|至|为)|upgrade\s+to|'
            r'(?:已被|没有|尚未|已经).{0,10}(?:在野利用|列入KEV)',answer,re.I))
