from fastapi import APIRouter, Depends
from app.core.auth import get_current_user_id
from app.services.orchestrator import Orchestrator
from app.dependencies import claude_service, ws_manager

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


@router.post("/run")
async def run_orchestration(task: dict, user_id: str = Depends(get_current_user_id)):
    # WS event callback will attach run/session id if present in payload
    async def ws_event_cb(payload: dict):
        try:
            event_type = payload.get("type") or payload.get("event_type") or "orchestrator.event"
            run_id = payload.get("session_id") or payload.get("run_id")
            await ws_manager.emit(user_id, event_type, payload, run_id=run_id)
        except Exception:
            return

    orchestrator = Orchestrator(claude_service, ws_event_cb=ws_event_cb)
    result = await orchestrator.run(
        user_id=user_id,
        task=task.get("task") or "Untitled",
        persona_count=task.get("persona_count", 4),
        use_mock=task.get("use_mock", True),
    )
    return result
