from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.auth import get_current_user_id
from app.dependencies import deep_task_orchestrator, personal_agent_service
from app.models.schemas import (
    DeepTaskRequest,
    DeepTaskStartResponse,
    PersonalSchoolActionRequest,
    PersonalAgentTriggerRequest,
)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/deep-task/start", response_model=DeepTaskStartResponse)
async def start_deep_task(
    body: DeepTaskRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
) -> DeepTaskStartResponse:
    run_id = str(uuid4())
    background_tasks.add_task(
        deep_task_orchestrator.run_and_stream,
        user_id=user_id,
        run_id=run_id,
        request=body,
    )
    return DeepTaskStartResponse(
        run_id=run_id,
        status="started",
        websocket_path=f"/ws/agents?user_id={user_id}",
    )


@router.post("/personal/trigger")
async def trigger_personal_agent(
    body: PersonalAgentTriggerRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    return await personal_agent_service.trigger_manual(
        user_id=user_id,
        instruction=body.instruction,
        use_mock=True,
    )


@router.post("/personal/school-action")
async def personal_school_action(
    body: PersonalSchoolActionRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    return await personal_agent_service.execute_school_action(
        user_id=user_id,
        action=body.action,
        payload=body.payload,
    )
