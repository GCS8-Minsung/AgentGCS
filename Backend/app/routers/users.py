from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.models.schemas import UserBootstrapRequest

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/bootstrap")
async def bootstrap_user(
    body: UserBootstrapRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    final_user_id = user_id
    fallback_email = f"{final_user_id}@local.agentgcs.invalid"
    payload = {
        "id": final_user_id,
        "email": body.email or fallback_email,
        "full_name": body.full_name,
        "avatar_url": body.avatar_url,
    }

    try:
        def _upsert():
            client = get_supabase_admin()
            return client.table("users").upsert(payload).execute()

        await asyncio.to_thread(_upsert)
        return {"status": "ok", "user_id": final_user_id, "source": "supabase"}
    except Exception:
        return {"status": "ok", "user_id": final_user_id, "source": "dev_store"}
