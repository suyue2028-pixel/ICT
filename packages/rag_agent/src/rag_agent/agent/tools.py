from pydantic import Field
from ..schemas import Model, Filters
from ..retrieval.exact import exact_lookup
from ..retrieval.relations import related_evidence


class ExactArgs(Model):
    identifiers: list[str] = Field(min_length=1, max_length=8)
    limit: int = Field(default=8, ge=1, le=8)


class HybridArgs(Model):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=8)


class RelatedArgs(Model):
    evidence_ids: list[str] = Field(max_length=8)


class AssertionArgs(Model):
    document_ids: list[str] = Field(max_length=8)


class Tools:
    ALLOWED = {"exact_lookup", "hybrid_search", "related_evidence", "get_assertions"}

    def __init__(self, retriever):
        self.retriever = retriever

    async def call(self, name, args, state):
        if name not in self.ALLOWED:
            raise ValueError("tool not in whitelist")
        state["budget"].tool(name)
        snapshot, filters = state["snapshot"], state["explicit_filters"]
        if name == "exact_lookup":
            parsed = ExactArgs.model_validate(args)
            # Target identifiers are controlled by the original request, not the model.
            if set(parsed.identifiers) != set(state["identifiers"]):
                raise ValueError("exact tool cannot change requested identifiers")
            return exact_lookup(snapshot, parsed.identifiers, filters, parsed.limit)
        if name == "hybrid_search":
            parsed = HybridArgs.model_validate(args)
            if state["identifiers"]:
                raise ValueError("identifier request must use exact lookup")
            return await self.retriever.search(snapshot, parsed.query, filters, parsed.limit)
        if name == "related_evidence":
            parsed = RelatedArgs.model_validate(args)
            selected = {e.evidence_id: e for e in state["evidence"]}
            if not set(parsed.evidence_ids).issubset(selected):
                raise ValueError("relation seeds must be retrieved evidence")
            return related_evidence(snapshot, [selected[i] for i in parsed.evidence_ids], filters, state["identifiers"])
        parsed = AssertionArgs.model_validate(args)
        selected_docs = {e.document_id for e in state["evidence"]}
        if not set(parsed.document_ids).issubset(selected_docs):
            raise ValueError("assertions must belong to selected evidence documents")
        return [a for a in snapshot.assertions if a.document_id in parsed.document_ids]
