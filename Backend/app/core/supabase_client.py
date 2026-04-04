from __future__ import annotations

import asyncio
from functools import lru_cache
from uuid import uuid4

from supabase import Client, create_client

from app.core.config import settings


def is_supabase_enabled() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_role_key)


@lru_cache(maxsize=1)
def get_supabase_admin() -> Client:
    if not is_supabase_enabled():
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured.")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def save_session(session: dict) -> dict:
    """
    Session persistence adapter.
    Uses chat_threads as session storage to align with current DB schema.
    Required keys: id, user_id. Optional: title/status/autonomy_mode.
    """

    def _upsert():
        client = get_supabase_admin()
        payload = {
            "id": session.get("id") or str(uuid4()),
            "user_id": session["user_id"],
            "title": session.get("title") or session.get("task") or "자율 실행 세션",
        }
        return client.table("chat_threads").upsert(payload, on_conflict="id").execute()

    result = await asyncio.to_thread(_upsert)
    return (result.data or [session])[0]


async def get_session(session_id: str) -> dict | None:
    def _select():
        client = get_supabase_admin()
        return client.table("chat_threads").select("*").eq("id", session_id).limit(1).execute()

    result = await asyncio.to_thread(_select)
    return (result.data or [None])[0]


async def append_session_message(
    *,
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
    if role not in {"user", "assistant", "system"}:
        role = "system"

    def _insert():
        client = get_supabase_admin()
        payload = {
            "id": str(uuid4()),
            "thread_id": session_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        return client.table("chat_messages").insert(payload).execute()

    result = await asyncio.to_thread(_insert)
    return (result.data or [{}])[0]


async def list_session_messages(session_id: str, user_id: str, limit: int = 40) -> list[dict]:
    limit = max(1, min(limit, 200))

    def _select():
        client = get_supabase_admin()
        return (
            client.table("chat_messages")
            .select("id,thread_id,user_id,role,content,metadata,created_at")
            .eq("thread_id", session_id)
            .eq("user_id", user_id)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )

    result = await asyncio.to_thread(_select)
    return result.data or []


async def save_agent_log(agent_log: dict) -> dict:
    """
    Persists final/step agent logs.
    - If payload matches agent_logs schema, insert into agent_logs.
    - Otherwise, store as system message in chat_messages(session thread).
    """
    run_id = str(agent_log.get("run_id") or agent_log.get("session_id") or "")
    user_id = str(agent_log.get("user_id") or "")
    if run_id and user_id and "final_summary" in agent_log and "task" in agent_log:
        def _insert():
            client = get_supabase_admin()
            payload = {
                "run_id": run_id,
                "user_id": user_id,
                "task": str(agent_log.get("task") or ""),
                "persona_stats": agent_log.get("persona_stats") or {},
                "arguments": agent_log.get("arguments") or {},
                "final_summary": str(agent_log.get("final_summary") or ""),
                "sources": agent_log.get("sources") or [],
                "feedback_score": agent_log.get("feedback_score"),
            }
            return client.table("agent_logs").insert(payload).execute()

        result = await asyncio.to_thread(_insert)
        return (result.data or [agent_log])[0]

    # step-log fallback: store in session conversation as system message
    if run_id and user_id:
        content = str(agent_log.get("content") or "")
        role = "system"
        if agent_log.get("role") in {"assistant", "user", "system"}:
            role = agent_log["role"]
        return await append_session_message(
            session_id=run_id,
            user_id=user_id,
            role=role,
            content=content,
            metadata={
                "agent_id": agent_log.get("agent_id"),
                "step_index": agent_log.get("step_index"),
                "meta": agent_log.get("meta") or {},
            },
        )
    return agent_log


async def list_agent_logs(session_id: str) -> list:
    def _select():
        client = get_supabase_admin()
        return (
            client.table("agent_logs")
            .select("*")
            .eq("run_id", session_id)
            .order("created_at", desc=False)
            .execute()
        )

    result = await asyncio.to_thread(_select)
    return result.data or []

