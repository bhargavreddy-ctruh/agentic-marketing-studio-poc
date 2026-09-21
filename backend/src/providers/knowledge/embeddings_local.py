"""
A local, free, open-source embedding model wrapped as a LlamaIndex-compatible embedding class.

Zero API cost, zero rate limits, no new API key — chosen specifically over a paid embedding API
since indexing brand/product docs can involve many embedding calls (Architecture.md section 3).
"""
from __future__ import annotations

from llama_index.core.base.embeddings.base import BaseEmbedding
from pydantic import PrivateAttr

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class LocalEmbedding(BaseEmbedding):
    """A small, CPU-friendly sentence-transformers model — no vendor SDK, no API key."""

    _model = PrivateAttr()

    def __init__(self, model_name: str = _DEFAULT_MODEL, **kwargs):
        super().__init__(**kwargs)
        from sentence_transformers import SentenceTransformer  # lazy import — heavy dependency

        self._model = SentenceTransformer(model_name)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)
