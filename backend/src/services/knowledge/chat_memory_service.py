"""
Chat Memory Service — Provides persistent, semantic LLM context caching for sessions via LlamaIndex.
Instead of the LLM only seeing the last N turns, this service retrieves semantically relevant past
conversations to preserve long-term context across massive sessions.
"""
from __future__ import annotations

from ...core.middleware.logging import get_logger
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...core.exceptions import ProviderUnavailable

log = get_logger(__name__)


class ChatMemoryService:
    def __init__(self):
        self._provider = get_knowledge_provider()

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
