import asyncio
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.dependencies import claude_service, deep_task_orchestrator, personal_agent_service
from app.models.schemas import (
    AgentChatRequest,
    DeepTaskRequest,
    DeepTaskStartResponse,
    PersonalAgentTriggerRequest,
    PersonalSchoolActionRequest,
)
from app.services.dev_store import dev_store

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


def _mode_system_instruction(mode: str) -> str:
    base = "당신은 AgentGCS 워크스페이스의 협업형 AI 비서다. 한국어로 명확하고 실행 가능한 답변을 제공한다."
    if mode == "cautious":
        return base + " 현재 모드는 신중함이다. 가정은 최소화하고 검증 단계를 먼저 제시한다."
    if mode == "creative":
        return base + " 현재 모드는 창의적이다. 대안을 폭넓게 제시하되 실행 리스크도 함께 짚는다."
    if mode == "autonomous":
        return base + " 현재 모드는 완전자율이다. 필요한 하위 작업을 선제적으로 제안하고 우선순위를 정한다."
    return base + " 현재 모드는 균형형이다. 창의성과 실행 가능성을 균형 있게 제시한다."


async def _ensure_thread(user_id: str, thread_id: str | None, title: str | None) -> dict:
    local = await dev_store.ensure_thread(user_id, thread_id, title or "새 대화")
    try:
        def _upsert():
            client = get_supabase_admin()
            payload = {
                "id": local["id"],
                "user_id": user_id,
                "title": local["title"],
            }
            return client.table("chat_threads").upsert(payload).execute()

        result = await asyncio.to_thread(_upsert)
        row = (result.data or [local])[0]
        return row
    except Exception:
        return local


async def _append_message(
    *,
    user_id: str,
    thread_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
    local = await dev_store.append_message(
        user_id=user_id,
        thread_id=thread_id,
        role=role,
        content=content,
        metadata=metadata,
    )
    try:
        def _insert():
            client = get_supabase_admin()
            payload = {
                "id": local["id"],
                "thread_id": thread_id,
                "user_id": user_id,
                "role": role,
                "content": content,
                "metadata": metadata or {},
            }
            return client.table("chat_messages").insert(payload).execute()

        result = await asyncio.to_thread(_insert)
        row = (result.data or [local])[0]
        return row
    except Exception:
        return local


@router.post("/chat")
async def chat_with_agent(
    body: AgentChatRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    title = body.title or body.message[:30] or "새 대화"
    thread = await _ensure_thread(user_id, body.thread_id, title)

    await _append_message(
        user_id=user_id,
        thread_id=thread["id"],
        role="user",
        content=body.message,
        metadata={"mode": body.mode},
    )

    stats_text = ""
    if body.persona_stats:
        stats_text = (
            "\n참고 페르소나 성향(0~100): "
            + str(body.persona_stats.model_dump())
            + "\n이 성향에 맞춰 톤과 우선순위를 조절해 답변하라."
        )

    reply = await claude_service.generate(
        system_prompt=_mode_system_instruction(body.mode),
        user_prompt=f"{body.message}{stats_text}",
        use_mock=body.use_mock,
        cache_hint=f"chat-{body.mode}",
    )

    assistant_message = await _append_message(
        user_id=user_id,
        thread_id=thread["id"],
        role="assistant",
        content=reply,
        metadata={"mode": body.mode},
    )
    await personal_agent_service.ws_manager.emit(
        user_id,
        "chat.message_generated",
        {"thread_id": thread["id"], "mode": body.mode},
    )
    return {
        "thread_id": thread["id"],
        "reply": reply,
        "assistant_message": assistant_message,
        "mode": body.mode,
    }


@router.get("/connection-status")
async def get_connection_status(user_id: str = Depends(get_current_user_id)) -> dict:
    claude = await claude_service.diagnose_connection()

    school_key_found = False
    try:
        def _find_school_key():
            client = get_supabase_admin()
            return (
                client.table("user_keys")
                .select("id")
                .eq("user_id", user_id)
                .eq("key_name", "school_api_token")
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_find_school_key)
        school_key_found = bool(result.data)
    except Exception:
        row = await dev_store.get_user_key(user_id, "school_api_token")
        school_key_found = bool(row)

    return {
        "claude": claude,
        "school_api": {"token_saved": school_key_found},
    }
