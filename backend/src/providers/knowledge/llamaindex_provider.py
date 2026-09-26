"""
THE ONLY file that imports LlamaIndex directly.

Phase 0 scope, honestly stated: this wires a real LlamaIndex VectorStoreIndex per collection
("brand", "product"), backed by the local embedding model — enough to prove the plumbing works
end-to-end with no paid dependency. Phase 3 (Phases.md) upgrades "brand" specifically to a
Property Graph Index and "product" to a Document Summary Index as the real Brand DNA / Product DNA
ingestion logic (Brand DNA Agent, run_product_intelligence port) is built — this class's public
shape (index_document / query) does not need to change when that happens, per the portability
contract in Architecture.md section 4.

Real, live-found bug (2026-09-24, per an explicit user ask: "is the chat memory persistent"):
despite `chat_memory_service.py`'s own docstring calling this "persistent, semantic LLM context
caching", every index here used to live ONLY in `self._indices`, a plain in-memory dict on a
process-lifetime singleton — a backend restart (routine in dev, via `--reload`; also any crash)
silently wiped every collection back to empty, with no backfill and no warning. Every collection
this provider serves (chat memory per session, brand, product, mood board) now persists to disk
under `var/knowledge_index/<collection>/` (same real-disk pattern `core/local_storage.py` already
uses for generated assets, same `var/` root, already gitignored) and loads back from there lazily
on first access after a restart, rather than being silently recreated empty.
"""
from __future__ import annotations

from pathlib import Path

from llama_index.core import (
    Document,
    Settings,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core.indices.base import BaseIndex

from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import KnowledgeProvider, RetrievedDocument
from .embeddings_local import LocalEmbedding

log = get_logger(__name__)

_PERSIST_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "var" / "knowledge_index"


class LlamaIndexKnowledgeProvider(KnowledgeProvider):
    def __init__(self) -> None:
        Settings.embed_model = LocalEmbedding()
        # LLM-free indexing/query for Phase 0 — the OpenRouter LLMProvider does the actual
        # reasoning; LlamaIndex here is purely retrieval (Settings.llm intentionally left unset,
        # so query() below returns the retrieved text directly rather than trying to synthesize
        # an answer with its own default LLM).
        # An in-memory cache of already-loaded/created indices for THIS process's lifetime — never
        # the only copy of the data anymore (see `_persist_dir`/`_load_or_create` below), just an
        # avoids-re-reading-disk-every-call optimization.
        self._indices: dict[str, BaseIndex] = {}

    def _persist_dir(self, collection: str) -> Path:
        # `collection` names are internal (e.g. f"chat_memory_{session_id}", "brand", "product"),
        # never raw user input reaching the filesystem — no path traversal surface here to guard.
        return _PERSIST_ROOT / collection

    def _load_or_create(self, collection: str, first_doc: Document | None = None) -> BaseIndex:
        """The one place that decides between three real states: already in memory this process,
        real persisted data on disk from an earlier process, or genuinely new. Getting the order
        wrong here is exactly the bug this whole fix exists for — creating fresh when a real
        persisted index exists would silently orphan every previously-indexed document."""
        cached = self._indices.get(collection)
        if cached is not None:
            return cached

        persist_dir = self._persist_dir(collection)
        if persist_dir.exists():
            try:
                storage_context = StorageContext.from_defaults(persist_dir=str(persist_dir))
                index = load_index_from_storage(storage_context)
                self._indices[collection] = index
                log.info("knowledge_index_loaded_from_disk", extra={"_extra_collection": collection})
                return index
            except Exception as exc:
                # A real, disclosed degrade — corrupt/incompatible persisted data starts fresh
                # rather than crashing every call against this collection forever.
                log.warning(
                    "knowledge_index_load_failed",
                    extra={"_extra_collection": collection, "_extra_error": str(exc)},
                )

        if first_doc is None:
            raise ProviderUnavailable("llamaindex", f"collection '{collection}' has no indexed documents yet")
        index = VectorStoreIndex.from_documents([first_doc])
        self._indices[collection] = index
        return index

    async def index_document(self, *, collection: str, doc_id: str, text: str) -> None:
        doc = Document(text=text, doc_id=doc_id)
        index = self._load_or_create(collection, first_doc=doc)
        # `_load_or_create` already inserted `doc` if it just built a brand-new index from it —
        # inserting it again here would duplicate the very first document in a new collection.
        # Real, live-found bug caught while verifying THIS fix, not a hypothetical: checking
        # `index.docstore.docs` (keyed by internal per-CHUNK node id) against `doc_id` never
        # matched, so this guard silently never fired and every brand-new collection's first
        # document got embedded twice. `ref_doc_info` is the real per-DOCUMENT index, keyed by the
        # exact `doc_id` passed to `Document(...)` — confirmed live via `ref_doc_info` before
        # trusting this fix, the same way the bug itself was caught.
        if doc_id not in index.ref_doc_info:
            index.insert(doc)
        persist_dir = self._persist_dir(collection)
        persist_dir.mkdir(parents=True, exist_ok=True)
        index.storage_context.persist(persist_dir=str(persist_dir))
        log.info("knowledge_indexed", extra={"_extra_collection": collection, "_extra_doc_id": doc_id})

    async def query(self, *, collection: str, question: str) -> str:
        index = self._load_or_create(collection)
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
        index = self._load_or_create(collection)
        retriever = index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(question)
        return [
            RetrievedDocument(doc_id=n.node.ref_doc_id or n.node.node_id, text=n.get_content(), score=n.score)
            for n in nodes
        ]


_singleton: LlamaIndexKnowledgeProvider | None = None


def get_knowledge_provider() -> LlamaIndexKnowledgeProvider:
    """
    A singleton within one process — the local embedding model is expensive to load, and the
    in-memory `_indices` cache (now backed by real disk persistence, see the module docstring)
    still needs every caller in one process to share it to avoid redundant disk reads.
    """
    global _singleton
    if _singleton is None:
        _singleton = LlamaIndexKnowledgeProvider()
    return _singleton
