import asyncio

from sqlalchemy import text

from src.models.base import async_session_factory
from src.repositories.sqlite.sqlite_session_repository import SqliteSessionRepository
from src.services.knowledge.guardrail_service import GuardrailService


async def main():
    async with async_session_factory() as db:
        sessions_repo = SqliteSessionRepository(db)
        svc = GuardrailService(sessions_repo)
        
        result = await db.execute(text("SELECT id FROM sessions"))
        session_ids = [row[0] for row in result]
        
        for sid in session_ids:
            try:
                # Force re-derive by clearing guardrails in brief and calling link_profiles
                session = await sessions_repo.get(sid)
                if not session:
                    continue
                # Keep human rules
                existing_guardrails = session.brief.get("guardrails", {}).get("rules", [])
                human_rules = [r for r in existing_guardrails if r.get("source") not in ("brand", "product", "project")]
                
                # Clear all guardrails temporarily
                session.brief["guardrails"] = {"version": 1, "rules": human_rules}
                await sessions_repo.update(session)
                
                # Re-link which will re-derive and merge the human rules
                await svc.link_profiles(sid)
                print(f"Re-derived guardrails for session {sid}")
            except Exception as e:
                print(f"Failed session {sid}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
