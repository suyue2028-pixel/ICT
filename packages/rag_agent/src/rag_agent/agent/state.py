from dataclasses import dataclass, field
import time
from typing import Any, TypedDict


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    deadline: float
    max_llm: int = 3
    max_tools: int = 8
    llm_calls: int = 0
    tool_calls: int = 0
    retries: int = 0
    events: list[dict] = field(default_factory=list)
    max_answer_tokens: int = 2048

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise BudgetExceeded("request deadline exceeded")
        return seconds

    def llm(self):
        self.remaining()
        if self.llm_calls >= self.max_llm:
            raise BudgetExceeded("LLM call budget exhausted")
        self.llm_calls += 1

    def tool(self, name):
        self.remaining()
        if self.tool_calls >= self.max_tools:
            raise BudgetExceeded("tool call budget exhausted")
        self.tool_calls += 1
        event = {"tool": name, "at": time.monotonic(), "status": "running", "duration_ms": 0,
                 "document_ids": [], "input_summary": "validated local retrieval"}
        self.events.append(event)
        return event


class AgentState(TypedDict, total=False):
    request: Any
    original_question: str
    resolved_question: str
    explicit_filters: Any
    identifiers: list[str]
    plan: Any
    subqueries: list[str]
    evidence: list[Any]
    assertions: list[Any]
    missing_fields: list[str]
    conflicts: list[dict]
    retry_count: int
    budget: Budget
    deadline: float
    warnings: list[str]
    trace_id: str
    response: Any
    snapshot: Any
    draft: Any
    clarification: str
    trace: list[dict]
    started: float
    output_mode: str
    selected_evidence: list[Any]
    relevance_assignments: dict[str, Any]
    requested_topics: list[dict[str, str]]
    llm_error: str
