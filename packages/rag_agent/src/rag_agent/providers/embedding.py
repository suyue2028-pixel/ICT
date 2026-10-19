from __future__ import annotations
import asyncio
import threading
from typing import Protocol
import numpy as np


class EmbeddingProvider(Protocol):
    mode: str
    dimension: int
    def signature(self) -> dict: ...
    async def tokenizer(self): ...
    async def passages(self, texts: list[str]) -> np.ndarray: ...
    async def query(self, text: str) -> np.ndarray: ...


class E5Embedding:
    mode = "live"
    dimension = 384

    def __init__(self, model_id="intfloat/multilingual-e5-small", revision="614241f622f53c4eeff9890bdc4f31cfecc418b3", device="cpu", *, allow_download=False):
        self.model_id, self.revision, self.device = model_id, revision, device
        self._model = None
        self.allow_download = allow_download
        self._load_lock = threading.Lock()

    def _load(self):
        with self._load_lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_id, revision=self.revision, device=self.device, trust_remote_code=False, local_files_only=not self.allow_download)
                self._model.max_seq_length = 512
                dimension_fn = getattr(self._model, "get_embedding_dimension", self._model.get_sentence_embedding_dimension)
                if dimension_fn() != self.dimension:
                    raise ValueError("E5 model must have 384 dimensions")
        return self._model

    def signature(self):
        return {"model_id": self.model_id, "revision": self.revision, "dimension": self.dimension, "prefix_policy": "e5-query-passage-v1"}

    async def tokenizer(self):
        return (await asyncio.to_thread(self._load)).tokenizer

    def _encode(self, texts):
        model = self._load()
        for text in texts:
            if len(model.tokenizer(text, add_special_tokens=True)["input_ids"]) > 512:
                raise ValueError("embedding input exceeds 512 tokens; re-chunk first")
        return np.asarray(model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False), dtype=np.float32)

    async def passages(self, texts):
        return await asyncio.to_thread(self._encode, ["passage: " + t for t in texts])

    async def query(self, text):
        return (await asyncio.to_thread(self._encode, ["query: " + text]))[0]
