from functools import lru_cache

from supabase import Client, create_client

from app.core.config import settings


@lru_cache(maxsize=1)
def get_supabase_admin() -> Client:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured."
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# Convenience helpers for sessions/messages/agent logs
async def save_session(session: dict) -> dict:
    def _insert():
        client = get_supabase_admin()
        return client.table("sessions").insert(session).execute()

    result = await __import__("asyncio").to_thread(_insert)
    return (result.data or [session])[0]


async def get_session(session_id: str) -> dict | None:
    def _select():
        client = get_supabase_admin()
        return client.table("sessions").select("*").eq("id", session_id).limit(1).execute()

    result = await __import__("asyncio").to_thread(_select)
    return (result.data or [None])[0]


async def save_agent_log(agent_log: dict) -> dict:
    def _insert():
        client = get_supabase_admin()
        return client.table("agent_logs").insert(agent_log).execute()

    result = await __import__("asyncio").to_thread(_insert)
    return (result.data or [agent_log])[0]


async def list_agent_logs(session_id: str) -> list:
    def _select():
        client = get_supabase_admin()
        return (
            client.table("agent_logs").select("*").eq("session_id", session_id).order("created_at", desc=False).execute()
        )

    result = await __import__("asyncio").to_thread(_select)
    return result.data or []

