"""
Chat Memory Service — Provides persistent, semantic LLM context caching for sessions via LlamaIndex.
Instead of the LLM only seeing the last N turns, this service retrieves semantically relevant past
conversations to preserve long-term context across massive sessions.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from ...models.chat_turn import ChatTurnModel
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider

log = get_logger(__name__)


class ChatMemoryService:
    def __init__(self):
        self._provider = get_knowledge_provider()

    async def keyword_search(
        self, db: AsyncSession, session_id: str, query: str, *, limit: int = 5
    ) -> list[ChatTurnModel]:
        """Recall's first, cheap pass (2026-10-05) — a plain substring match over this session's
        own real, persisted turns, no embedding call and no index to go stale. Tried before the
        semantic fallback below; "on demand" recall should reach for the expensive path only when
        the cheap one genuinely comes up empty, not by default."""
        terms = [t.strip() for t in query.split() if len(t.strip()) > 2]
        if not terms:
            return []
        conditions = [
            or_(ChatTurnModel.user_text.ilike(f"%{t}%"), ChatTurnModel.assistant_text.ilike(f"%{t}%"))
            for t in terms
        ]
        result = await db.execute(
            select(ChatTurnModel)
            .where(ChatTurnModel.session_id == session_id, or_(*conditions))
            .order_by(ChatTurnModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def add_turn(self, session_id: str, turn_id: str, user_text: str, assistant_text: str) -> None:
        """Indexes a chat turn into the session's memory collection."""
        collection_name = f"chat_memory_{session_id}"
        # A clear, structured representation of the turn for the embedding model.
        text = f"User Request: {user_text}\nAssistant Output: {assistant_text}"
        try:
            await self._provider.index_document(
                collection=collection_name, 
                doc_id=turn_id, 
                text=text
            )
        except Exception as e:
            log.warning("failed_to_index_chat_memory", extra={"_extra_session": session_id, "_extra_error": str(e)})

    async def get_relevant_history(self, session_id: str, query: str) -> str:
        """Retrieves the most semantically relevant historical turns for the given query."""
        collection_name = f"chat_memory_{session_id}"
        try:
            return await self._provider.query(collection=collection_name, question=query)
        except ProviderUnavailable:
            # Collection hasn't been created yet (no chat history indexed)
            return ""
        except Exception as e:
            log.warning("failed_to_retrieve_chat_memory", extra={"_extra_session": session_id, "_extra_error": str(e)})
            return ""
