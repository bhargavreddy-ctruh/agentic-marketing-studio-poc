"""
Mood Board Service — the real backing for `asset_mood_board_search` (Architecture.md section 1b),
which returned an honest "not configured" stub until now because no internal library of
prior-campaign assets existed at all. Mirrors `brand_dna_service.py`'s shape: persist the real
record, index a text description into LlamaIndex under its own collection ("mood_board"), and
search by asking the index which descriptions best match a query.

Business logic lives here, not in the route handler or the tool itself (Rules.md section 2).
Depends on the MoodBoardRepository Protocol, never a concrete SQLite class.
"""
from __future__ import annotations

import uuid

from ...core.local_storage import save_asset
from ...models.mood_board_asset import MoodBoardAssetModel
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...repositories.base import MoodBoardRepository

_COLLECTION = "mood_board"


class MoodBoardService:
    def __init__(self, assets: MoodBoardRepository):
        self._assets = assets

    async def add_asset(self, *, data: bytes, mime_type: str, description: str) -> MoodBoardAssetModel:
        storage_ref = await save_asset(data, mime_type, metadata={"source": "mood_board_upload"})
        asset = MoodBoardAssetModel(
            id=uuid.uuid4().hex, storage_ref=storage_ref, mime_type=mime_type, description=description
        )
        asset = await self._assets.add(asset)

        # Real LlamaIndex ingestion, same pattern as Brand DNA — the description is the only thing
        # actually searchable (Phase 0 scope: no image-similarity/CLIP embedding, text only).
        await get_knowledge_provider().index_document(
            collection=_COLLECTION, doc_id=asset.id, text=description
        )
        return asset

    async def search(self, *, query: str, top_k: int = 3) -> list[MoodBoardAssetModel]:
        """Returns real assets, ranked by real similarity to `query` — never invents a result.
        A doc_id LlamaIndex returns that no longer has a real row (deleted, or a stale in-memory
        index — Phase 0's own documented "in-memory, lost on restart" limitation) is silently
        skipped rather than raised, matching `session_service.py`'s "fall back honestly on a stale
        reference" precedent."""
        hits = await get_knowledge_provider().query_top_k(
            collection=_COLLECTION, question=query, top_k=top_k
        )
        assets: list[MoodBoardAssetModel] = []
        for hit in hits:
            asset = await self._assets.get(hit.doc_id)
            if asset is not None:
                assets.append(asset)
        return assets

    async def list_all(self) -> list[MoodBoardAssetModel]:
        return await self._assets.list_all()


async def reindex_all_mood_board_assets(assets: MoodBoardRepository) -> None:
    """Rehydrates LlamaIndex's in-memory 'mood_board' collection from the real, persisted SQL
    records on every app startup — the exact same real gap `reindex_all_brands` closed for Brand
    DNA (Memory.md, Phase 3): the index itself is in-memory-only, so a server restart would
    otherwise silently make every uploaded asset unsearchable even though it's still in the DB."""
    knowledge = get_knowledge_provider()
    for asset in await assets.list_all():
        await knowledge.index_document(collection=_COLLECTION, doc_id=asset.id, text=asset.description)
