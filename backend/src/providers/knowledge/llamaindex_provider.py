"""
THE ONLY file that imports LlamaIndex directly.

Phase 0 scope, honestly stated: this wires a real LlamaIndex VectorStoreIndex per collection
("brand", "product"), backed by the local embedding model — enough to prove the plumbing works
end-to-end with no paid dependency. Phase 3 (Phases.md) upgrades "brand" specifically to a
Property Graph Index and "product" to a Document Summary Index as the real Brand DNA / Product DNA
ingestion logic (Brand DNA Agent, run_product_intelligence port) is built — this class's public
shape (index_document / query) does not need to change when that happens, per the portability
contract in Architecture.md section 4.
"""
from __future__ import annotations

from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.core.indices.base import BaseIndex

from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import KnowledgeProvider, RetrievedDocument
from .embeddings_local import LocalEmbedding

log = get_logger(__name__)


class LlamaIndexKnowledgeProvider(KnowledgeProvider):
    def __init__(self) -> None:
        Settings.embed_model = LocalEmbedding()
        # LLM-free indexing/query for Phase 0 — the OpenRouter LLMProvider does the actual
        # reasoning; LlamaIndex here is purely retrieval (Settings.llm intentionally left unset,
        # so query() below returns the retrieved text directly rather than trying to synthesize
        # an answer with its own default LLM).
        self._indices: dict[str, BaseIndex] = {}

    async def index_document(self, *, collection: str, doc_id: str, text: str) -> None:
        doc = Document(text=text, doc_id=doc_id)
        existing = self._indices.get(collection)
        if existing is None:
            self._indices[collection] = VectorStoreIndex.from_documents([doc])
        else:
            existing.insert(doc)
        log.info("knowledge_indexed", extra={"_extra_collection": collection, "_extra_doc_id": doc_id})

    async def query(self, *, collection: str, question: str) -> str:
        index = self._indices.get(collection)
        if index is None:
            raise ProviderUnavailable("llamaindex", f"collection '{collection}' has no indexed documents yet")
        retriever = index.as_retriever(similarity_top_k=3)
        nodes = retriever.retrieve(question)
        if not nodes:
            return ""
        return "\n\n".join(n.get_content() for n in nodes)

    async def query_top_k(
        self, *, collection: str, question: str, top_k: int = 3
    ) -> list[RetrievedDocument]:
        """Same retrieval as `query()`, but returns each matched document separately with its
        real `doc_id` (rather than one joined blob) — needed by Mood Board Search, where each hit
        maps back to one real asset with its own storage_ref, not a single synthesized answer."""
        index = self._indices.get(collection)
        if index is None:
            raise ProviderUnavailable("llamaindex", f"collection '{collection}' has no indexed documents yet")
        retriever = index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(question)
        return [
            RetrievedDocument(doc_id=n.node.ref_doc_id or n.node.node_id, text=n.get_content(), score=n.score)
            for n in nodes
        ]


_singleton: LlamaIndexKnowledgeProvider | None = None


def get_knowledge_provider() -> LlamaIndexKnowledgeProvider:
    """
    A singleton, because the local embedding model is expensive to load and Phase 0/1's indices
    are in-memory — every caller in one process must share the same instance to see the same
    indexed documents.
    """
    global _singleton
    if _singleton is None:
        _singleton = LlamaIndexKnowledgeProvider()
    return _singleton
