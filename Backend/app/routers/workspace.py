from __future__ import annotations

import asyncio
from copy import deepcopy
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.models.schemas import (
    ConversationMessageCreateRequest,
    ConversationThreadCreateRequest,
    UserSettingsPayload,
)
from app.services.dev_store import DEFAULT_PERSONA, DEFAULT_SETTINGS, dev_store

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _placeholder_email(user_id: str) -> str:
    return f"{user_id}@local.agentgcs.invalid"


async def _ensure_supabase_user(user_id: str) -> None:
    def _upsert_user():
        client = get_supabase_admin()
        payload = {"id": user_id, "email": _placeholder_email(user_id)}
        return client.table("users").upsert(payload).execute()

    try:
        await asyncio.to_thread(_upsert_user)
    except Exception:
        return


def _default_settings_payload() -> dict:
    return {
        **deepcopy(DEFAULT_SETTINGS),
        "personas": deepcopy(DEFAULT_SETTINGS.get("personas") or [DEFAULT_PERSONA]),
        "active_persona_id": (
            DEFAULT_SETTINGS.get("active_persona_id")
            or (DEFAULT_SETTINGS.get("personas") or [DEFAULT_PERSONA])[0]["id"]
        ),
    }


def _normalize_settings(raw: dict | None) -> dict:
    merged = _default_settings_payload()
    if raw and isinstance(raw, dict):
        merged.update(raw)
    if merged.get("default_notify_email") == "":
        merged["default_notify_email"] = None
    if merged.get("claude_base_url") == "":
        merged["claude_base_url"] = None
    if merged.get("preferred_model") == "":
        merged["preferred_model"] = None
    personas = merged.get("personas")
    if not isinstance(personas, list) or not personas:
        personas = [deepcopy(DEFAULT_PERSONA)]
        merged["personas"] = personas
    if not merged.get("active_persona_id"):
        merged["active_persona_id"] = personas[0]["id"]
    return UserSettingsPayload(**merged).model_dump(mode="json")


@router.get("/settings")
async def get_settings(user_id: str = Depends(get_current_user_id)) -> dict:
    try:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("user_settings")
                .select("settings_json")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        row = (result.data or [None])[0]
        if row and isinstance(row.get("settings_json"), dict):
            normalized = _normalize_settings(row["settings_json"])
            await dev_store.upsert_settings(user_id, normalized)
            return {"settings": normalized, "source": "supabase"}
    except Exception:
        pass

    fallback = await dev_store.get_settings(user_id)
    return {"settings": _normalize_settings(fallback), "source": "dev_store"}


@router.put("/settings")
async def upsert_settings(
    body: UserSettingsPayload, user_id: str = Depends(get_current_user_id)
) -> dict:
    normalized = _normalize_settings(body.model_dump(mode="json"))
    await dev_store.upsert_settings(user_id, normalized)

    try:
        def _upsert():
            client = get_supabase_admin()
            payload = {
                "user_id": user_id,
                "settings_json": normalized,
            }
            return client.table("user_settings").upsert(payload, on_conflict="user_id").execute()

        await asyncio.to_thread(_upsert)
        return {"settings": normalized, "source": "supabase"}
    except Exception:
        return {"settings": normalized, "source": "dev_store"}


@router.get("/conversations")
async def list_conversations(
    user_id: str = Depends(get_current_user_id),
    limit: int = 20,
) -> dict:
    limit = max(1, min(limit, 100))
    try:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("chat_threads")
                .select("id,user_id,title,created_at,updated_at")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .limit(limit)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        rows = result.data or []
        if rows:
            return {"items": rows, "source": "supabase"}
    except Exception:
        pass

    items = await dev_store.list_threads(user_id, limit=limit)
    return {"items": items, "source": "dev_store"}


@router.post("/conversations")
async def create_conversation(
    body: ConversationThreadCreateRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    requested_thread_id = body.thread_id
    title = body.title or "새 대화"
    await _ensure_supabase_user(user_id)

    async def _find_owned_thread(thread_id: str) -> dict | None:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("chat_threads")
                .select("id,user_id,title,created_at,updated_at")
                .eq("id", thread_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return (result.data or [None])[0]

    thread_id = str(uuid4())
    if requested_thread_id:
        try:
            owned = await _find_owned_thread(requested_thread_id)
            if owned:
                thread_id = owned["id"]
        except Exception:
            thread_id = requested_thread_id

    local_row = await dev_store.ensure_thread(user_id, thread_id, title)
    try:
        def _insert():
            client = get_supabase_admin()
            payload = {"id": thread_id, "user_id": user_id, "title": title}
            return client.table("chat_threads").insert(payload).execute()

        result = await asyncio.to_thread(_insert)
        row = (result.data or [local_row])[0]
        return {"item": row, "source": "supabase"}
    except Exception:
        if requested_thread_id:
            try:
                owned = await _find_owned_thread(requested_thread_id)
                if owned:
                    return {"item": owned, "source": "supabase"}
            except Exception:
                pass

        fallback_thread_id = str(uuid4())
        fallback_local = await dev_store.ensure_thread(user_id, fallback_thread_id, title)
        try:
            def _insert_fallback():
                client = get_supabase_admin()
                payload = {"id": fallback_thread_id, "user_id": user_id, "title": title}
                return client.table("chat_threads").insert(payload).execute()

            fallback_result = await asyncio.to_thread(_insert_fallback)
            row = (fallback_result.data or [fallback_local])[0]
            return {"item": row, "source": "supabase"}
        except Exception:
            return {"item": fallback_local, "source": "dev_store"}


@router.get("/conversations/{thread_id}/messages")
async def list_conversation_messages(
    thread_id: str,
    user_id: str = Depends(get_current_user_id),
    limit: int = 100,
) -> dict:
    limit = max(1, min(limit, 300))
    try:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("chat_messages")
                .select("id,thread_id,user_id,role,content,metadata,created_at")
                .eq("user_id", user_id)
                .eq("thread_id", thread_id)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        rows = result.data or []
        if rows:
            return {"items": rows, "source": "supabase"}
    except Exception:
        pass

    items = await dev_store.list_messages(user_id, thread_id, limit=limit)
    return {"items": items, "source": "dev_store"}


@router.post("/conversations/{thread_id}/messages")
async def create_conversation_message(
    thread_id: str,
    body: ConversationMessageCreateRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    await _ensure_supabase_user(user_id)
    await dev_store.ensure_thread(user_id, thread_id, "새 대화")
    local_row = await dev_store.append_message(
        user_id=user_id,
        thread_id=thread_id,
        role=body.role,
        content=body.content,
        metadata=body.metadata,
    )
    try:
        def _insert():
            client = get_supabase_admin()
            payload = {
                "id": local_row["id"],
                "thread_id": thread_id,
                "user_id": user_id,
                "role": body.role,
                "content": body.content,
                "metadata": body.metadata,
            }
            return client.table("chat_messages").insert(payload).execute()

        result = await asyncio.to_thread(_insert)
        row = (result.data or [local_row])[0]
        return {"item": row, "source": "supabase"}
    except Exception:
        return {"item": local_row, "source": "dev_store"}
