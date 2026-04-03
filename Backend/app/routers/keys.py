import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.dependencies import security_manager
from app.models.schemas import KeyStoreRequest
from app.services.dev_store import dev_store

router = APIRouter(prefix="/keys", tags=["keys"])


async def _ensure_user_row(user_id: str) -> None:
    payload = {
        "id": user_id,
        "email": f"{user_id}@local.agentgcs.invalid",
    }

    def _upsert_user() -> None:
        client = get_supabase_admin()
        client.table("users").upsert(payload).execute()

    try:
        await asyncio.to_thread(_upsert_user)
    except Exception:
        return


@router.post("", status_code=status.HTTP_201_CREATED)
async def upsert_user_key(
    body: KeyStoreRequest, user_id: str = Depends(get_current_user_id)
) -> dict:
    await _ensure_user_row(user_id)
    encrypted = security_manager.encrypt_text(body.plaintext_key, aad=user_id)
    payload = {
        "user_id": user_id,
        "key_name": body.key_name,
        "encrypted_value": encrypted.ciphertext,
        "nonce": encrypted.nonce,
        "key_version": encrypted.key_version,
    }

    def _upsert() -> None:
        client = get_supabase_admin()
        client.table("user_keys").upsert(payload, on_conflict="user_id,key_name").execute()

    try:
        await asyncio.to_thread(_upsert)
        source = "supabase"
    except Exception:
        source = "dev_store"

    await dev_store.upsert_user_key(
        user_id=user_id,
        key_name=body.key_name,
        encrypted_value=encrypted.ciphertext,
        nonce=encrypted.nonce,
        key_version=encrypted.key_version,
    )
    return {"status": "stored", "key_name": body.key_name, "source": source}


def _merge_key_rows(
    supabase_rows: list[dict] | None, dev_rows: list[dict] | None
) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in dev_rows or []:
        key_name = row.get("key_name")
        if not key_name:
            continue
        merged[key_name] = {
            "id": row.get("id"),
            "key_name": key_name,
            "key_version": row.get("key_version", 1),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    for row in supabase_rows or []:
        key_name = row.get("key_name")
        if not key_name:
            continue
        merged[key_name] = row

    return sorted(
        merged.values(),
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )


@router.get("")
async def list_user_keys(user_id: str = Depends(get_current_user_id)) -> dict:
    def _select():
        client = get_supabase_admin()
        return (
            client.table("user_keys")
            .select("id,key_name,key_version")
            .eq("user_id", user_id)
            .order("key_name", desc=False)
            .execute()
        )

    def _select_no_order():
        client = get_supabase_admin()
        return (
            client.table("user_keys")
            .select("id,key_name,key_version")
            .eq("user_id", user_id)
            .execute()
        )

    supabase_rows: list[dict] = []
    source = "supabase"
    try:
        result = await asyncio.to_thread(_select)
        supabase_rows = result.data or []
    except Exception:
        try:
            result = await asyncio.to_thread(_select_no_order)
            supabase_rows = result.data or []
            source = "supabase"
        except Exception:
            source = "dev_store"

    dev_rows = await dev_store.list_user_keys(user_id)
    merged = _merge_key_rows(supabase_rows, dev_rows)

    if source == "supabase":
        final_source = "supabase+dev_store" if dev_rows else "supabase"
    else:
        final_source = "dev_store"

    return {"items": merged, "source": final_source}
