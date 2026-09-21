"""The KnowledgeProvider contract — Brand DNA / Product DNA indexing and lookup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RetrievedDocument:
    """One indexed document matched by `query_top_k` — the `doc_id` passed to `index_document`
    at indexing time, returned here so a caller can look up whatever real record it points to
    (Mood Board Search: a `doc_id` is a `MoodBoardAssetModel.id`, resolved back to its real
    storage_ref by the service, never by this provider — a provider never touches the database)."""

    doc_id: str
    text: str
    score: float | None = None


class KnowledgeProvider(Protocol):
    async def index_document(self, *, collection: str, doc_id: str, text: str) -> None: ...

    async def query(self, *, collection: str, question: str) -> str: ...

    async def query_top_k(
        self, *, collection: str, question: str, top_k: int = 3
    ) -> list[RetrievedDocument]: ...
