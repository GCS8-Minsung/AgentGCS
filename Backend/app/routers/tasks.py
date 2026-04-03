import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.models.schemas import TaskCreate, TaskUpdate
from app.services.dev_store import dev_store

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(user_id: str = Depends(get_current_user_id)) -> dict:
    def _select():
        client = get_supabase_admin()
        return (
            client.table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=False)
            .execute()
        )

    try:
        result = await asyncio.to_thread(_select)
        rows = result.data or []
        if rows:
            return {"items": rows, "source": "supabase"}
    except Exception:
        pass

    items = await dev_store.list_tasks(user_id)
    source = "dev_store" if items else "supabase"
    return {"items": items, "source": source}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, user_id: str = Depends(get_current_user_id)) -> dict:
    payload = {
        "id": str(uuid4()),
        "user_id": user_id,
        **body.model_dump(mode="json"),
    }

    def _insert():
        client = get_supabase_admin()
        return client.table("tasks").insert(payload).execute()

    try:
        result = await asyncio.to_thread(_insert)
        row = (result.data or [None])[0]
        if row:
            return {"item": row, "source": "supabase"}
    except Exception:
        pass

    row = await dev_store.create_task(user_id, payload)
    return {"item": row, "source": "dev_store"}


@router.patch("/{task_id}")
async def update_task(task_id: str, body: TaskUpdate, user_id: str = Depends(get_current_user_id)) -> dict:
    updates = {k: v for k, v in body.model_dump(mode="json").items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    def _update():
        client = get_supabase_admin()
        return (
            client.table("tasks")
            .update(updates)
            .eq("id", task_id)
            .eq("user_id", user_id)
            .execute()
        )

    try:
        result = await asyncio.to_thread(_update)
        row = (result.data or [None])[0]
        if row:
            return {"item": row, "source": "supabase"}
    except Exception:
        pass

    row = await dev_store.update_task(user_id, task_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"item": row, "source": "dev_store"}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, user_id: str = Depends(get_current_user_id)) -> None:
    def _delete():
        client = get_supabase_admin()
        return client.table("tasks").delete().eq("id", task_id).eq("user_id", user_id).execute()

    try:
        await asyncio.to_thread(_delete)
    except Exception:
        await dev_store.delete_task(user_id, task_id)
