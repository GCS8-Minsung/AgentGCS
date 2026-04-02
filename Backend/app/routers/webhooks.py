from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import settings
from app.dependencies import personal_agent_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/supabase/tasks", status_code=status.HTTP_202_ACCEPTED)
async def handle_supabase_task_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="x-webhook-secret"),
) -> dict:
    if settings.supabase_webhook_secret:
        if x_webhook_secret != settings.supabase_webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    payload = await request.json()
    await personal_agent_service.handle_task_due_webhook(payload)
    return {"status": "accepted"}

