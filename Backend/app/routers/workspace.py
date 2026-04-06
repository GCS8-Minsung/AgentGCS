from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user_id
from app.core.default_guideline import DEFAULT_AGENTGCS_GUIDELINE
from app.core.supabase_client import get_supabase_admin
from app.dependencies import context_manager
from app.models.schemas import (
    ConversationMessageCreateRequest,
    PersonaProfile,
    ConversationThreadCreateRequest,
    UserSettingsPayload,
)
from app.services.dev_store import DEFAULT_PERSONA, DEFAULT_SETTINGS, dev_store

router = APIRouter(prefix="/workspace", tags=["workspace"])
MAX_TASK_PERSONAS = 6
MIN_DISCUSSION_ROUNDS = 2
MAX_DISCUSSION_ROUNDS = 5


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
    default_chat_mode_personas = deepcopy(
        DEFAULT_SETTINGS.get("chat_mode_personas")
        or {
            "cautious": deepcopy(DEFAULT_PERSONA["stats"]),
            "balanced": deepcopy(DEFAULT_PERSONA["stats"]),
            "creative": deepcopy(DEFAULT_PERSONA["stats"]),
            "autonomous": deepcopy(DEFAULT_PERSONA["stats"]),
        }
    )
    return {
        **deepcopy(DEFAULT_SETTINGS),
        "personas": deepcopy(DEFAULT_SETTINGS.get("personas") or [DEFAULT_PERSONA]),
        "active_persona_id": (
            DEFAULT_SETTINGS.get("active_persona_id")
            or (DEFAULT_SETTINGS.get("personas") or [DEFAULT_PERSONA])[0]["id"]
        ),
        "chat_mode_personas": default_chat_mode_personas,
        "discussion_rounds": int(DEFAULT_SETTINGS.get("discussion_rounds") or 3),
        "knowledge_base_prompt": DEFAULT_SETTINGS.get("knowledge_base_prompt"),
    }


def _normalize_personas(raw_personas: object) -> list[dict]:
    candidates = raw_personas if isinstance(raw_personas, list) else []
    default_profile = PersonaProfile(**deepcopy(DEFAULT_PERSONA))

    normalized: list[dict] = [default_profile.model_dump(mode="json")]
    seen_ids = {default_profile.id}

    for index, row in enumerate(candidates):
        if len(normalized) >= MAX_TASK_PERSONAS:
            break
        if not isinstance(row, dict):
            continue

        raw_id = str(row.get("id") or "").strip()
        if not raw_id or raw_id == default_profile.id or raw_id in seen_ids:
            continue

        raw_name = str(row.get("name") or "").strip()
        if not raw_name:
            raw_name = f"에이전트 {index + 1}"

        stats = row.get("stats")
        try:
            profile = PersonaProfile(id=raw_id, name=raw_name, stats=stats)
        except Exception:
            continue

        normalized.append(profile.model_dump(mode="json"))
        seen_ids.add(profile.id)

    return normalized


def _normalize_discussion_rounds(value: object) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(DEFAULT_SETTINGS.get("discussion_rounds") or 3)
    return max(MIN_DISCUSSION_ROUNDS, min(MAX_DISCUSSION_ROUNDS, parsed))


def _normalize_settings(raw: dict | None) -> dict:
    merged = _default_settings_payload()
    if raw and isinstance(raw, dict):
        merged.update(raw)
    if merged.get("claude_base_url") == "":
        merged["claude_base_url"] = None
    if merged.get("preferred_model") == "":
        merged["preferred_model"] = None
    if merged.get("openai_preferred_model") == "":
        merged["openai_preferred_model"] = None
    if merged.get("ai_provider") not in {"claude", "openai"}:
        merged["ai_provider"] = "claude"
    if merged.get("knowledge_base_prompt") == "":
        merged["knowledge_base_prompt"] = None
    if not merged.get("knowledge_base_prompt"):
        merged["knowledge_base_prompt"] = DEFAULT_AGENTGCS_GUIDELINE
    notebook_profile = str(merged.get("notebooklm_profile") or "").strip()
    merged["notebooklm_profile"] = notebook_profile or None
    merged["notebooklm_allow_oauth_mismatch"] = bool(
        merged.get("notebooklm_allow_oauth_mismatch", True)
    )
    merged["notebooklm_auto_switch_on_slide_failure"] = bool(
        merged.get("notebooklm_auto_switch_on_slide_failure", True)
    )
    personas = _normalize_personas(merged.get("personas"))
    merged["personas"] = personas

    requested_active_id = str(merged.get("active_persona_id") or "").strip()
    valid_ids = {persona["id"] for persona in personas if isinstance(persona, dict)}
    if requested_active_id not in valid_ids:
        requested_active_id = personas[0]["id"]
    merged["active_persona_id"] = requested_active_id

    chat_mode_personas = merged.get("chat_mode_personas")
    if not isinstance(chat_mode_personas, dict):
        chat_mode_personas = {}
    for mode in ("cautious", "balanced", "creative", "autonomous"):
        if not isinstance(chat_mode_personas.get(mode), dict):
            chat_mode_personas[mode] = deepcopy(DEFAULT_PERSONA["stats"])
    merged["chat_mode_personas"] = chat_mode_personas
    merged["discussion_rounds"] = _normalize_discussion_rounds(merged.get("discussion_rounds"))

    return UserSettingsPayload(**merged).model_dump(mode="json")


def _is_placeholder_thread_title(title: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", str(title or "").strip())
    if not normalized:
        return True
    return normalized.startswith("새 대화") or normalized.startswith("새 워크플로우")


def _derive_thread_title(content: str, *, max_len: int = 42) -> str:
    compact = re.sub(r"\s+", " ", (content or "").strip())
    if not compact:
        return "새 대화"
    return compact[:max_len]


async def _latest_user_message_content(user_id: str, thread_id: str) -> str | None:
    try:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("chat_messages")
                .select("content")
                .eq("user_id", user_id)
                .eq("thread_id", thread_id)
                .eq("role", "user")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        row = (result.data or [None])[0]
        if row and isinstance(row.get("content"), str) and row.get("content"):
            return str(row["content"])
    except Exception:
        pass

    try:
        local_rows = await dev_store.list_messages(user_id, thread_id, limit=80)
        for row in reversed(local_rows):
            if str(row.get("role") or "") != "user":
                continue
            content = str(row.get("content") or "").strip()
            if content:
                return content
    except Exception:
        pass
    return None


async def _sync_thread_title_from_message(
    *,
    user_id: str,
    thread_id: str,
    role: str,
    content: str,
) -> None:
    if role not in {"user", "assistant"}:
        return
    candidate_content = content
    if role == "assistant":
        latest_user = await _latest_user_message_content(user_id, thread_id)
        if latest_user:
            candidate_content = latest_user
    candidate = _derive_thread_title(candidate_content)
    if not candidate:
        return

    try:
        local_thread = await dev_store.get_thread(user_id, thread_id)
        if local_thread and _is_placeholder_thread_title(str(local_thread.get("title") or "")):
            await dev_store.ensure_thread(user_id, thread_id, candidate)
    except Exception:
        pass

    try:
        def _select_thread():
            client = get_supabase_admin()
            return (
                client.table("chat_threads")
                .select("id,title")
                .eq("id", thread_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

        selected = await asyncio.to_thread(_select_thread)
        row = (selected.data or [None])[0]
        if not row or not _is_placeholder_thread_title(str(row.get("title") or "")):
            return

        def _update_thread():
            client = get_supabase_admin()
            return (
                client.table("chat_threads")
                .update({"title": candidate, "updated_at": datetime.now(tz=timezone.utc).isoformat()})
                .eq("id", thread_id)
                .eq("user_id", user_id)
                .execute()
            )

        await asyncio.to_thread(_update_thread)
    except Exception:
        return


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
    async def _touch_thread_updated_at() -> None:
        try:
            def _update():
                client = get_supabase_admin()
                return (
                    client.table("chat_threads")
                    .update({"updated_at": datetime.now(tz=timezone.utc).isoformat()})
                    .eq("id", thread_id)
                    .eq("user_id", user_id)
                    .execute()
                )

            await asyncio.to_thread(_update)
        except Exception:
            return

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
        await _touch_thread_updated_at()
        await _sync_thread_title_from_message(
            user_id=user_id,
            thread_id=thread_id,
            role=body.role,
            content=body.content,
        )
        return {"item": row, "source": "supabase"}
    except Exception:
        await _touch_thread_updated_at()
        await _sync_thread_title_from_message(
            user_id=user_id,
            thread_id=thread_id,
            role=body.role,
            content=body.content,
        )
        return {"item": local_row, "source": "dev_store"}


@router.delete("/conversations/{thread_id}")
async def delete_conversation(
    thread_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    deleted = False
    source = "dev_store"
    try:
        def _delete():
            client = get_supabase_admin()
            return (
                client.table("chat_threads")
                .delete()
                .eq("id", thread_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = await asyncio.to_thread(_delete)
        deleted = bool(result.data)
        if deleted:
            source = "supabase"
    except Exception:
        pass

    local_deleted = await dev_store.delete_thread(user_id, thread_id)
    await context_manager.clear(f"{user_id}:{thread_id}")
    if not deleted:
        deleted = local_deleted
    return {"deleted": deleted, "thread_id": thread_id, "source": source}
