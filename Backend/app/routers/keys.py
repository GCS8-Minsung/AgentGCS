import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.dependencies import security_manager
from app.models.schemas import KeyStoreRequest
from app.services.dev_store import dev_store

router = APIRouter(prefix="/keys", tags=["keys"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upsert_user_key(
    body: KeyStoreRequest, user_id: str = Depends(get_current_user_id)
) -> dict:
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
        await dev_store.upsert_user_key(
            user_id=user_id,
            key_name=body.key_name,
            encrypted_value=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        source = "dev_store"

    return {"status": "stored", "key_name": body.key_name, "source": source}


@router.get("")
async def list_user_keys(user_id: str = Depends(get_current_user_id)) -> dict:
    def _select():
        client = get_supabase_admin()
        return (
            client.table("user_keys")
            .select("id,key_name,key_version,created_at,updated_at")
            .eq("user_id", user_id)
            .order("updated_at", desc=True)
            .execute()
        )

    try:
        result = await asyncio.to_thread(_select)
        rows = result.data or []
        return {"items": rows, "source": "supabase"}
    except Exception:
        rows = await dev_store.list_user_keys(user_id)
        sanitized = [
            {
                "id": row.get("id"),
                "key_name": row.get("key_name"),
                "key_version": row.get("key_version", 1),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            for row in rows
        ]
        return {"items": sanitized, "source": "dev_store"}
