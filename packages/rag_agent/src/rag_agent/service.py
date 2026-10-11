from __future__ import annotations
import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from .config import Settings
from .schemas import DocumentInput, EvidenceDetail, IngestItem, IngestResult, QueryRequest, QueryResponse, Metrics
from .storage.sqlite import SQLiteStore
from .ingestion.normalize import stable_id, content_hash
from .ingestion.chunk import split_document
from .ingestion.extract import extract
from .enrichment.rules import enrich as enrich_rules
from .providers.embedding import E5Embedding
from .providers.mock import MockEmbedding, MockLLM
from .providers.llm import RemoteLLM
from .retrieval.hybrid import HybridRetriever
from .agent.tools import Tools
from .agent.nodes import Nodes
from .agent.graph import build_graph
from .agent.state import Budget, BudgetExceeded
from .agent.conversation import conversation_kind, fallback_reply, safe_conversation_answer

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, settings=None, *, embedding=None, llm=None):
        self.settings = settings or Settings.from_env()
        self.store = SQLiteStore(self.settings.db_path)
        self.embedding = embedding or (MockEmbedding() if self.settings.embedding_provider == "mock" else E5Embedding(self.settings.embedding_model, self.settings.embedding_revision, self.settings.embedding_device))
        self.llm = llm or (RemoteLLM(self.settings) if self.settings.llm_mode == "live" else MockLLM() if self.settings.llm_mode == "mock" else None)
        self._write_lock = asyncio.Lock()
        self._run_summaries = OrderedDict()
        self.retriever = HybridRetriever(self.embedding, rank_pool=self.settings.retrieval_rank_pool,
                                         vector_threshold=self.settings.retrieval_vector_threshold)
        self.nodes = Nodes(self.settings, Tools(self.retriever), self.llm)
        self.graph = build_graph(self.nodes)

    async def ingest(self, documents: list[DocumentInput]) -> IngestResult:
        result = IngestResult()
        for document in documents:
            doc_id = None
            try:
                doc = DocumentInput.model_validate(document)
                doc_id, digest = stable_id(doc), content_hash(doc)
                async with self._write_lock:
                    self.store.check_signature(self.embedding.signature())
                    current = self.store.current(doc_id)
                    if current and current["content_hash"] == digest:
                        self.store.activate(doc_id)
                        item = IngestItem(external_id=doc.external_id, document_id=doc_id, version=current["version"], status="unchanged")
                    else:
                        version = current["version"] + 1 if current else 1
                        tokenizer = await self.embedding.tokenizer()
                        chunks = await asyncio.to_thread(split_document, doc.text, doc.title, tokenizer, self.settings.chunk_tokens, self.settings.overlap_tokens)
                        if not chunks:
                            raise ValueError("document has no tokens")
                        vectors = await self.embedding.passages([doc.title + "\n" + c.text for c in chunks])
                        evidence = [EvidenceDetail(evidence_id=f"{doc_id}:v{version}:c{i}", document_id=doc_id, version=version,
                                                   title=doc.title, url=doc.source_url, excerpt=c.text,
                                                   locator={"kind": "char_range", "start": c.start, "end": c.end},
                                                   published_at=doc.published_at, collected_at=doc.collected_at) for i, c in enumerate(chunks)]
                        assertions, field_evidence = extract(doc, doc_id, version, evidence)
                        await asyncio.to_thread(self.store.commit_version, doc, doc_id, version, digest, chunks, vectors,
                                                self.embedding.signature(), evidence + field_evidence, assertions)
                        item = IngestItem(external_id=doc.external_id, document_id=doc_id, version=version, status="updated" if current else "inserted")
            except Exception as exc:
                # Source/provider bodies and keys are never copied into public errors.
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                item = IngestItem(external_id=getattr(document, "external_id", None), document_id=doc_id, status="failed", error=reason[:240])
            setattr(result, item.status, getattr(result, item.status) + 1)
            result.items.append(item)
            if doc_id and item.status != "failed":
                result.document_ids.append(doc_id)
        return result

    async def enrich(self, document_ids):
        async with self._write_lock:
            return await asyncio.to_thread(enrich_rules, self.store, document_ids)

    async def get_evidence(self, evidence_id):
        return await asyncio.to_thread(self.store.get_evidence, evidence_id)

    async def deactivate(self, document_id):
        async with self._write_lock:
            await asyncio.to_thread(self.store.deactivate, document_id)

    def get_run_summary(self, trace_id):
        """Bounded, process-local diagnostic summaries; backend persists sessions/logs."""
        return deepcopy(self._run_summaries[trace_id])

    async def converse(self, state, kind):
        """Social/help replies need no fabricated evidence or retrieval calls."""
        answer, mode = fallback_reply(kind), "extractive"
        state["trace"].append({"node": "conversation", "intent": kind})
        if self.llm and self.llm.mode == "live":
            try:
                payload = {"question": state["request"].question, "intent": kind}
                if "模型" in state["request"].question:
                    payload["configured_model"] = self.settings.llm_model
                result = await self.llm.complete("converse", payload, state["budget"],
                                                 min(self.settings.llm_timeout, 8.0))
                candidate = result.get("answer")
                if not isinstance(candidate, str) or not safe_conversation_answer(candidate):
                    raise ValueError("invalid conversation answer")
                answer, mode = candidate.strip(), "live"
            except (ValueError, RuntimeError, KeyError):
                state["warnings"].append("conversation_local_reply:after_remote_attempt")
        else:
            state["warnings"].append("conversation_local_reply:no_remote_llm")
        return QueryResponse(status="ok", answer=answer, response_kind="conversation",
                             trace_id=state["trace_id"], warnings=state["warnings"], mode=mode)

    async def query(self, request: QueryRequest, *, document_ids=None, document_versions=None,
                    max_tool_calls=None, max_answer_tokens=None, timeout=None) -> QueryResponse:
        """Trusted adapter controls: scope and limits are never model-generated."""
        request = QueryRequest.model_validate(request)
        if max_tool_calls is not None and (type(max_tool_calls) is not int or max_tool_calls < 1):
            raise ValueError("max_tool_calls must be a positive integer")
        if max_answer_tokens is not None and (type(max_answer_tokens) is not int or max_answer_tokens < 1):
            raise ValueError("max_answer_tokens must be a positive integer")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300):
            raise ValueError("timeout must be between zero and 300 seconds")
        duration = min(timeout, self.settings.request_timeout) if timeout is not None else self.settings.request_timeout
        tools_limit = min(max_tool_calls, self.settings.max_tool_calls) if max_tool_calls is not None else self.settings.max_tool_calls
        answer_limit = min(max_answer_tokens, 2048) if max_answer_tokens is not None else 2048
        scope = frozenset(document_ids) if document_ids is not None else None
        versions = dict(document_versions) if document_versions is not None else None
        started = time.monotonic()
        budget = Budget(started + duration, self.settings.max_llm_calls, tools_limit, max_answer_tokens=answer_limit)
        state = {"request": request, "original_question": request.question, "resolved_question": request.question,
                 "explicit_filters": request.filters, "plan": None, "subqueries": [], "evidence": [], "assertions": [],
                 "missing_fields": [], "conflicts": [], "retry_count": 0, "budget": budget, "deadline": budget.deadline,
                 "warnings": [], "trace_id": uuid.uuid4().hex, "trace": [], "started": started, "clarification": ""}
        if self.embedding.mode == "mock":
            state["warnings"].append("mock_embedding:仅用于流程回归，不代表真实语义检索")
        try:
            async with asyncio.timeout(duration):
                kind = conversation_kind(request.question)
                has_filters = any(value not in (None, [], "") for value in request.filters.model_dump().values())
                if kind and not has_filters:
                    response = await self.converse(state, kind)
                else:
                    snapshot = await asyncio.to_thread(self.store.snapshot, scope, versions)
                    state["snapshot"] = snapshot
                    if snapshot.signature and snapshot.signature != self.embedding.signature():
                        raise ValueError("embedding index mismatch; rebuild required")
                    final = await self.graph.ainvoke(state, config={"recursion_limit": 24})
                    response = final["response"]
                    state = final
        except asyncio.CancelledError:
            raise  # Cancellation propagates to pending remote HTTP, never swallowed.
        except (TimeoutError, BudgetExceeded) as exc:
            response = QueryResponse(status="error", answer="请求已因超时或调用预算终止。请缩小问题范围后重试。",
                                     trace_id=state["trace_id"], warnings=[*state["warnings"], type(exc).__name__], mode="extractive")
        except Exception as exc:
            logger.warning("query_failed trace_id=%s type=%s", state["trace_id"], type(exc).__name__)
            failure_notes = ["retrieval_failed"] if any(event.get("status") == "failed" for event in budget.events) else []
            response = QueryResponse(status="error", answer="查询失败，请检查索引模型与运行配置。", trace_id=state["trace_id"],
                                     warnings=[*state["warnings"], *failure_notes, str(exc)[:200] if isinstance(exc, ValueError) else type(exc).__name__], mode="extractive")
        response.metrics = Metrics(elapsed_ms=(time.monotonic() - started) * 1000,
                                   llm_calls=budget.llm_calls, tool_calls=budget.tool_calls, retries=budget.retries,
                                   embedding_mode=self.embedding.mode, index_generation=state.get("snapshot").generation if state.get("snapshot") else 0)
        summary = {"trace_id": response.trace_id, "nodes": state["trace"], "tools": deepcopy(budget.events),
                   "evidence_ids": [e.evidence_id for e in state["evidence"]], "metrics": response.metrics.model_dump()}
        self._run_summaries[response.trace_id] = summary
        while len(self._run_summaries) > 128:
            self._run_summaries.popitem(last=False)
        logger.info("query_trace %s", summary)
        return response
