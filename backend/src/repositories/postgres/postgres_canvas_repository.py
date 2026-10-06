from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.canvas_element import CanvasElementModel
from ...models.canvas_element_version import CanvasElementVersionModel


class PostgresCanvasRepository:
    """Satisfies the CanvasRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add_element(self, element: CanvasElementModel) -> CanvasElementModel:
        self._db.add(element)
        await self._db.commit()
        await self._db.refresh(element)
        return element

    async def get_element(self, element_id: str) -> CanvasElementModel | None:
        result = await self._db.execute(
            select(CanvasElementModel).where(CanvasElementModel.id == element_id)
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: str) -> list[CanvasElementModel]:
        result = await self._db.execute(
            select(CanvasElementModel)
            .where(CanvasElementModel.session_id == session_id)
            .order_by(CanvasElementModel.created_at)
        )
        return list(result.scalars().all())

    async def update_element(self, element: CanvasElementModel) -> CanvasElementModel:
        await self._db.commit()
        await self._db.refresh(element)
        return element

    async def delete_element(self, element_id: str) -> None:
        """Permanently removes one canvas element and its version history.

        `canvas_element_versions.element_id` has no `ON DELETE CASCADE` (and this codebase's own
        established pattern — see `PostgresSessionRepository.delete` — is explicit, ordered manual
        deletes rather than relying on DB-level cascade), so versions are deleted first, then the
        element row itself. A dangling `parent_element_id` on some OTHER element that pointed at
        this one is left as-is — not a real foreign key, and the frontend already degrades that
        gracefully (falls back to showing nothing extra for a lineage badge whose parent is gone).
        Storage (Cloudinary/local disk) is intentionally untouched — no code anywhere in this
        codebase deletes the underlying asset bytes on any other delete path either (session
        delete doesn't either); out of scope here for the same reason.
        """
        await self._db.execute(
            delete(CanvasElementVersionModel).where(CanvasElementVersionModel.element_id == element_id)
        )
        await self._db.execute(
            delete(CanvasElementModel).where(CanvasElementModel.id == element_id)
        )
        await self._db.commit()

    async def update_compliance_status(self, element_id: str, status: str) -> None:
        """Update just the compliance status of an element."""
        from sqlalchemy import update
        await self._db.execute(
            update(CanvasElementModel)
            .where(CanvasElementModel.id == element_id)
            .values(compliance_status=status)
        )
        await self._db.commit()
