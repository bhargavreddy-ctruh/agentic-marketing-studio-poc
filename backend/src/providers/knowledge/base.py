"""The KnowledgeProvider contract — Brand DNA / Product DNA indexing and lookup."""
from __future__ import annotations

from typing import Protocol


class KnowledgeProvider(Protocol):
    async def index_document(self, *, collection: str, doc_id: str, text: str) -> None: ...

    async def query(self, *, collection: str, question: str) -> str: ...
