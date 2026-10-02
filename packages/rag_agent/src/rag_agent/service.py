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

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, settings=None, *, embedding=None, llm=None):
        self.settings = settings or Settings.from_env()
        self.store = SQLiteStore(self.settings.db_path)
        self.embedding = embedding or (MockEmbedding() if self.settings.embedding_provider == "mock" else E5Embedding(self.settings.embedding_model, self.settings.embedding_revision, self.settings.embedding_device))
        self.llm = llm or (RemoteLLM(self.settings) if self.settings.llm_mode == "live" else MockLLM() if self.settings.llm_mode == "mock" else None)
        self._write_lock = asyncio.Lock()
        self._run_summaries = OrderedDict()
        self.retriever = HybridRetriever(self.embedding)
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

    def get_run_summary(self, trace_id):
        """Bounded, process-local diagnostic summaries; backend persists sessions/logs."""
        return deepcopy(self._run_summaries[trace_id])

    async def query(self, request: QueryRequest) -> QueryResponse:
        request = QueryRequest.model_validate(request)
        started = time.monotonic()
        budget = Budget(started + self.settings.request_timeout, self.settings.max_llm_calls, self.settings.max_tool_calls)
        state = {"request": request, "original_question": request.question, "resolved_question": request.question,
                 "explicit_filters": request.filters, "plan": None, "subqueries": [], "evidence": [], "assertions": [],
                 "missing_fields": [], "conflicts": [], "retry_count": 0, "budget": budget, "deadline": budget.deadline,
                 "warnings": [], "trace_id": uuid.uuid4().hex, "trace": [], "started": started, "clarification": ""}
        if self.embedding.mode == "mock":
            state["warnings"].append("mock_embedding:仅用于流程回归，不代表真实语义检索")
        try:
            async with asyncio.timeout(self.settings.request_timeout):
                snapshot = await asyncio.to_thread(self.store.snapshot)
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
            response = QueryResponse(status="error", answer="查询失败，请检查索引模型与运行配置。", trace_id=state["trace_id"],
                                     warnings=[*state["warnings"], str(exc)[:200] if isinstance(exc, ValueError) else type(exc).__name__], mode="extractive")
        response.metrics = Metrics(elapsed_ms=(time.monotonic() - started) * 1000,
                                   llm_calls=budget.llm_calls, tool_calls=budget.tool_calls, retries=budget.retries,
                                   embedding_mode=self.embedding.mode, index_generation=state.get("snapshot").generation if state.get("snapshot") else 0)
        summary = {"trace_id": response.trace_id, "nodes": state["trace"], "tools": budget.events,
                   "evidence_ids": [e.evidence_id for e in state["evidence"]], "metrics": response.metrics.model_dump()}
        self._run_summaries[response.trace_id] = summary
        while len(self._run_summaries) > 128:
            self._run_summaries.popitem(last=False)
        logger.info("query_trace %s", summary)
        return response
