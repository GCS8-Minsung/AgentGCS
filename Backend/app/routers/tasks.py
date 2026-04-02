import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.models.schemas import TaskCreate, TaskUpdate

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

    result = await asyncio.to_thread(_select)
    return {"items": result.data or []}


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

    result = await asyncio.to_thread(_insert)
    return {"item": (result.data or [None])[0]}


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

    result = await asyncio.to_thread(_update)
    return {"item": (result.data or [None])[0]}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, user_id: str = Depends(get_current_user_id)) -> None:
    def _delete():
        client = get_supabase_admin()
        return client.table("tasks").delete().eq("id", task_id).eq("user_id", user_id).execute()

    await asyncio.to_thread(_delete)

