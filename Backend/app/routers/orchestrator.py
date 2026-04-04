from fastapi import APIRouter, Depends
from app.core.auth import get_current_user_id
from app.services.orchestrator import Orchestrator
from app.dependencies import deep_task_orchestrator

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


@router.post("/run")
async def run_orchestration(task: dict, user_id: str = Depends(get_current_user_id)):
    orchestrator = Orchestrator(deep_task_orchestrator)
    result = await orchestrator.run(
        user_id=user_id,
        task=task.get("task") or "Untitled",
        persona_count=task.get("persona_count", 4),
        use_mock=task.get("use_mock", True),
    )
    return result
