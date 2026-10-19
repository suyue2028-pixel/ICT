"""Explicit deterministic test doubles. Never reported as semantic embeddings."""
import hashlib
import numpy as np


class CharacterTokenizer:
    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=False):
        result = {"input_ids": list(range(len(text) + (2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result["offset_mapping"] = [(i, i + 1) for i in range(len(text))]
        return result


class MockEmbedding:
    mode = "mock"
    dimension = 384
    def __init__(self, revision="fixture-v1"):
        self.revision = revision

    def signature(self):
        return {"model_id": "synthetic/hash-bigrams", "revision": self.revision, "dimension": self.dimension, "prefix_policy": "mock-only-v1"}

    async def tokenizer(self):
        return CharacterTokenizer()

    def encode(self, text):
        vector = np.zeros(self.dimension, dtype=np.float32)
        text = text.lower()
        for token in [text[i:i + 2] for i in range(max(1, len(text) - 1))]:
            vector[int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "little") % self.dimension] += 1
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    async def passages(self, texts):
        return np.asarray([self.encode(t) for t in texts], dtype=np.float32)

    async def query(self, text):
        return self.encode(text)


class MockLLM:
    mode = "mock"
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []

    async def complete(self, purpose, payload, budget, timeout):
        budget.llm()
        self.requests.append((purpose, payload))
        if self.responses:
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        if purpose == "plan":
            return {"route": "hybrid", "subqueries": [payload["question"]], "expand_relations": False}
        if purpose == "rewrite":
            return {"question": payload["question"]}
        e = payload["evidence"][0]
        return {"claims": [{"text": e["excerpt"][:160], "evidence_ids": [e["evidence_id"]], "kind": "fact"}]}
