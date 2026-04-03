import asyncio
import json
import re
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.config import settings as app_settings
from app.core.security import EncryptedPayload
from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin
from app.dependencies import (
    deep_task_orchestrator,
    personal_agent_service,
    security_manager,
)
from app.models.schemas import (
    AgentChatRequest,
    DeepTaskRequest,
    DeepTaskStartResponse,
    PersonalAgentTriggerRequest,
    PersonalSchoolActionRequest,
)
from app.services.claude_service import ClaudeService
from app.services.dev_store import dev_store
from app.services.school_api_client import SchoolApiError, get_school_client_for_user
from app.tools.web_search import search_trusted_sources

router = APIRouter(prefix="/agents", tags=["agents"])


async def _ensure_supabase_user(user_id: str) -> None:
    def _upsert_user():
        client = get_supabase_admin()
        payload = {"id": user_id, "email": f"{user_id}@local.agentgcs.invalid"}
        return client.table("users").upsert(payload).execute()

    try:
        await asyncio.to_thread(_upsert_user)
    except Exception:
        return


async def _get_user_settings(user_id: str) -> dict:
    try:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("user_settings")
                .select("settings_json")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        row = (result.data or [None])[0]
        if row and isinstance(row.get("settings_json"), dict):
            return row["settings_json"]
    except Exception:
        pass
    return await dev_store.get_settings(user_id)


async def _get_user_key_row(user_id: str, key_name: str) -> dict | None:
    try:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("user_keys")
                .select("encrypted_value,nonce,key_version")
                .eq("user_id", user_id)
                .eq("key_name", key_name)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        row = (result.data or [None])[0]
        if row:
            return row
    except Exception:
        pass

    return await dev_store.get_user_key(user_id, key_name)


def _decrypt_key(row: dict | None, user_id: str) -> str | None:
    if not row:
        return None
    encrypted = row.get("encrypted_value")
    nonce = row.get("nonce")
    if not encrypted or not nonce:
        return None
    try:
        return security_manager.decrypt_text(
            EncryptedPayload(
                nonce=nonce,
                ciphertext=encrypted,
                key_version=row.get("key_version", 1),
            ),
            aad=user_id,
        )
    except Exception:
        return None


async def _build_user_claude_service(user_id: str) -> ClaudeService:
    user_settings = await _get_user_settings(user_id)
    claude_base = user_settings.get("claude_base_url") or app_settings.anthropic_base_url
    preferred_model = user_settings.get("preferred_model") or app_settings.claude_model

    school_token = _decrypt_key(await _get_user_key_row(user_id, "school_api_token"), user_id)
    anthropic_token = _decrypt_key(
        await _get_user_key_row(user_id, "anthropic_auth_token"), user_id
    )
    claude_api_key = _decrypt_key(await _get_user_key_row(user_id, "claude_api_key"), user_id)

    auth_token = (
        anthropic_token
        or school_token
        or app_settings.anthropic_auth_token
        or app_settings.school_api_token
    )
    api_key = claude_api_key or app_settings.claude_api_key

    return ClaudeService(
        api_key=api_key,
        auth_token=auth_token,
        base_url=claude_base,
        preferred_model=preferred_model,
    )


@router.post("/deep-task/start", response_model=DeepTaskStartResponse)
async def start_deep_task(
    body: DeepTaskRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
) -> DeepTaskStartResponse:
    run_id = str(uuid4())
    user_claude = await _build_user_claude_service(user_id)
    background_tasks.add_task(
        deep_task_orchestrator.run_and_stream,
        user_id=user_id,
        run_id=run_id,
        request=body,
        claude_override=user_claude,
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
    current_settings = await _get_user_settings(user_id)
    use_mock = bool(current_settings.get("dev_mode", False))
    user_claude = await _build_user_claude_service(user_id)
    return await personal_agent_service.trigger_manual(
        user_id=user_id,
        instruction=body.instruction,
        use_mock=use_mock,
        claude_override=user_claude,
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
    base = (
        "당신은 AgentGCS 워크스페이스의 협업형 AI 비서다. 한국어로 답변한다. "
        "문제 해결은 ReAct 방식으로 단계화하되, 내부 추론은 노출하지 말고 "
        "사용자에게는 실행 가능한 결과와 핵심 근거만 제시한다. "
        "필요 시 도구(웹 검색, 학교 API 컨텍스트)를 활용해 순차적으로 해결한다."
    )
    if mode == "cautious":
        return base + " 현재 모드는 신중함이다. 가정은 최소화하고 검증 단계를 먼저 제시한다."
    if mode == "creative":
        return base + " 현재 모드는 창의적이다. 대안을 폭넓게 제시하되 실행 리스크도 함께 짚는다."
    if mode == "autonomous":
        return base + " 현재 모드는 완전자율이다. 필요한 하위 작업을 선제적으로 제안하고 우선순위를 정한다."
    return base + " 현재 모드는 균형형이다. 창의성과 실행 가능성을 균형 있게 제시한다."


def _needs_multi_agent(message: str) -> bool:
    patterns = [
        r"심층\s*토론",
        r"토론\s*진행",
        r"멀티\s*에이전트",
        r"다각도",
        r"깊(게|이)\s*분석",
        r"에이전트\s*회의",
    ]
    lowered = message.lower()
    return any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns) or (
        "multi-agent" in lowered
    )


def _needs_web_search(message: str) -> bool:
    keywords = [
        "검색",
        "인터넷",
        "웹",
        "web",
        "search",
        "근거",
        "출처",
        "최신",
        "시장",
        "트렌드",
        "뉴스",
        "리서치",
        "조사",
        "비교",
    ]
    return any(keyword in message for keyword in keywords)


def _looks_like_api_action(message: str) -> bool:
    lowered = message.lower()
    markers = [
        "api 호출",
        "api call",
        "api 요청",
        "api request",
        "내 정보",
        "auth/me",
        "팀 정보",
        "리더보드",
        "회의실",
        "스니펫",
        "openapi",
        "엔드포인트",
        "endpoint",
        "snippet",
        "league",
        "leaderboard",
    ]
    if any(marker in lowered for marker in markers):
        return True
    if "api.1000.school" in lowered:
        return True
    if "/auth/" in lowered or "/teams/" in lowered or "/users/" in lowered:
        return True
    if "api" in lowered and ("1000" in lowered or "호출" in lowered or "call" in lowered):
        return True
    return False


def _extract_requested_paths(message: str) -> list[str]:
    candidates = set(re.findall(r"/[a-zA-Z0-9][a-zA-Z0-9/_-]*", message))
    allowed_paths = {
        "/auth/me",
        "/teams/me",
        "/users/me/league",
        "/snippet_date",
        "/leaderboards",
        "/meeting-rooms",
        "/daily-snippets",
        "/weekly-snippets",
        "/openapi.json",
    }
    return sorted(path for path in candidates if path in allowed_paths)


def _is_explicit_tool_result_request(message: str) -> bool:
    lowered = message.lower()
    direct_markers = [
        "api 호출",
        "api 요청",
        "결과 보여",
        "raw",
        "json",
        "auth/me",
        "openapi",
        "엔드포인트",
    ]
    if any(marker in lowered for marker in direct_markers):
        return True
    return bool(re.search(r"/[a-zA-Z0-9][a-zA-Z0-9/_-]*", message))


def _compact_json_for_reply(value: object, max_len: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n... (truncated)"


def _format_school_api_action_reply(actions: list[dict]) -> str:
    if not actions:
        return "학교 API 액션 결과가 없습니다."

    ok_count = sum(1 for action in actions if action.get("status") == "ok")
    lines = ["학교 API 실행 결과 요약", f"- 성공: {ok_count}건 / 실패: {len(actions) - ok_count}건", ""]

    for action in actions:
        action_name = str(action.get("action") or "unknown")
        status = str(action.get("status") or "unknown")
        lines.append(f"### {action_name} [{status}]")
        lines.append("```json")
        lines.append(_compact_json_for_reply(action.get("data", {})))
        lines.append("```")
        lines.append("")

    if ok_count == 0:
        lines.append("점검 포인트")
        lines.append("1) 설정에서 `school_api_token`이 저장되어 있는지 확인")
        lines.append("2) 토큰 권한/만료 상태 확인")
        lines.append("3) 요청 경로와 메서드가 OpenAPI 스펙과 일치하는지 확인")

    return "\n".join(lines).strip()


async def _execute_school_api_actions(user_id: str, message: str) -> list[dict]:
    actions: list[dict] = []
    if not _looks_like_api_action(message):
        return actions

    lowered = message.lower()
    try:
        client = await get_school_client_for_user(user_id)
    except Exception as exc:
        return [
            {
                "tool": "school_api",
                "action": "init",
                "status": "error",
                "data": {"error": str(exc)},
            }
        ]

    async def safe_run(action_name: str, coro):
        try:
            data = await coro
            return {
                "tool": "school_api",
                "action": action_name,
                "status": "ok",
                "data": data,
            }
        except Exception as exc:
            return {
                "tool": "school_api",
                "action": action_name,
                "status": "error",
                "data": {"error": str(exc)},
            }

    jobs: list[tuple[str, object]] = []

    if any(key in message for key in ["내 정보", "프로필"]) or "auth/me" in lowered:
        jobs.append(("auth.me", client.get_auth_me()))
    if "팀" in message or "team" in lowered:
        jobs.append(("teams.me", client.get_my_team()))
    if "리그" in message or "league" in lowered:
        jobs.append(("users.me.league", client.get_my_league()))
    if "리더보드" in message or "순위" in message or "leaderboard" in lowered:
        period = "weekly" if ("주간" in message or "weekly" in lowered) else "daily"
        jobs.append(("leaderboards", client.get_leaderboard(period=period, limit=20, offset=0)))
    if "회의실" in message or "meeting" in lowered:
        jobs.append(("meeting-rooms", client.list_meeting_rooms()))
    if "일간" in message or "daily snippet" in lowered or "daily-snippet" in lowered:
        jobs.append(("daily-snippets", client.list_daily_snippets(limit=20, offset=0)))
    if "주간" in message or "weekly snippet" in lowered or "weekly-snippet" in lowered:
        jobs.append(("weekly-snippets", client.list_weekly_snippets(limit=20, offset=0)))
    if "스니펫 날짜" in message or "snippet date" in lowered:
        jobs.append(("snippet-date", client.get_snippet_date()))
    if "openapi" in lowered or "스펙" in message or "엔드포인트" in message:
        jobs.append(("openapi.summary", client.get_openapi_summary()))

    for path in _extract_requested_paths(message):
        jobs.append((f"path:{path}", client.request_known_path(method="GET", path=path)))

    # API 관련 요청인데 구체 액션이 없는 경우 최소 진단 액션을 수행한다.
    if not jobs:
        jobs.append(("auth.me", client.get_auth_me()))

    tasks = [safe_run(action_name, coroutine) for action_name, coroutine in jobs]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    actions.extend(results)
    return actions


async def _collect_tool_context(user_id: str, message: str) -> list[dict]:
    contexts: list[dict] = []

    if _needs_web_search(message) or _needs_multi_agent(message):
        sources = await search_trusted_sources(message, max_results=5)
        contexts.append(
            {
                "tool": "web_search",
                "summary": "인터넷 검색 결과",
                "data": sources,
            }
        )

    if any(keyword in message for keyword in ["회의실", "예약", "meeting room"]):
        try:
            client = await get_school_client_for_user(user_id)
            rooms = await client.list_meeting_rooms()
            contexts.append(
                {
                    "tool": "school_api",
                    "summary": "회의실 목록 조회 결과",
                    "data": rooms[:8],
                }
            )
        except SchoolApiError as exc:
            contexts.append(
                {
                    "tool": "school_api",
                    "summary": "교내 API 호출 실패",
                    "data": {"error": str(exc)},
                }
            )
        except Exception as exc:
            contexts.append(
                {
                    "tool": "school_api",
                    "summary": "교내 API 예외",
                    "data": {"error": str(exc)},
                }
            )

    if any(keyword in message for keyword in ["스니펫", "snippet", "회고", "daily", "weekly"]):
        try:
            client = await get_school_client_for_user(user_id)
            daily = await client.list_daily_snippets(limit=5, offset=0)
            weekly = await client.list_weekly_snippets(limit=5, offset=0)
            contexts.append(
                {
                    "tool": "school_api",
                    "summary": "스니펫 조회 결과",
                    "data": {
                        "daily": daily,
                        "weekly": weekly,
                    },
                }
            )
        except Exception as exc:
            contexts.append(
                {
                    "tool": "school_api",
                    "summary": "스니펫 조회 실패",
                    "data": {"error": str(exc)},
                }
            )

    return contexts


async def _ensure_thread(user_id: str, thread_id: str | None, title: str | None) -> dict:
    await _ensure_supabase_user(user_id)
    requested_thread_id = thread_id

    async def _find_owned_thread(target_id: str) -> dict | None:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("chat_threads")
                .select("id,user_id,title,created_at,updated_at")
                .eq("id", target_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        return (result.data or [None])[0]

    final_thread_id = str(uuid4())
    if requested_thread_id:
        try:
            owned = await _find_owned_thread(requested_thread_id)
            if owned:
                final_thread_id = owned["id"]
            else:
                final_thread_id = requested_thread_id
        except Exception:
            final_thread_id = requested_thread_id

    local = await dev_store.ensure_thread(user_id, final_thread_id, title or "새 대화")
    try:
        def _insert():
            client = get_supabase_admin()
            payload = {
                "id": final_thread_id,
                "user_id": user_id,
                "title": local["title"],
            }
            return client.table("chat_threads").insert(payload).execute()

        result = await asyncio.to_thread(_insert)
        row = (result.data or [local])[0]
        return row
    except Exception:
        if requested_thread_id:
            try:
                owned = await _find_owned_thread(requested_thread_id)
                if owned:
                    return owned
            except Exception:
                pass

        fallback_thread_id = str(uuid4())
        fallback_local = await dev_store.ensure_thread(
            user_id, fallback_thread_id, title or "새 대화"
        )
        try:
            def _insert_fallback():
                client = get_supabase_admin()
                payload = {
                    "id": fallback_thread_id,
                    "user_id": user_id,
                    "title": fallback_local["title"],
                }
                return client.table("chat_threads").insert(payload).execute()

            fallback_result = await asyncio.to_thread(_insert_fallback)
            row = (fallback_result.data or [fallback_local])[0]
            return row
        except Exception:
            return fallback_local


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
    await personal_agent_service.ws_manager.emit(
        user_id,
        "chat.processing",
        {"thread_id": thread["id"], "stage": "received", "mode": body.mode},
    )

    user_settings = await _get_user_settings(user_id)
    stats_text = ""
    if body.persona_stats:
        stats_text = (
            "\n참고 페르소나 성향(0~100): "
            + str(body.persona_stats.model_dump())
            + "\n이 성향에 맞춰 톤과 우선순위를 조절해 답변하라."
        )

    user_claude = await _build_user_claude_service(user_id)
    knowledge_text = (body.knowledge_prompt or "").strip()
    if knowledge_text:
        knowledge_text = f"\n사전 지식:\n{knowledge_text}\n"

    tool_context_task = asyncio.create_task(_collect_tool_context(user_id, body.message))
    api_actions_task = asyncio.create_task(_execute_school_api_actions(user_id, body.message))
    tool_contexts, api_actions = await asyncio.gather(tool_context_task, api_actions_task)

    if api_actions:
        await personal_agent_service.ws_manager.emit(
            user_id,
            "chat.processing",
            {
                "thread_id": thread["id"],
                "stage": "api_actions_done",
                "actions": [action.get("action") for action in api_actions],
            },
        )
    if api_actions:
        tool_contexts.append(
            {
                "tool": "school_api_actions",
                "summary": "사용자 요청 기반 API 액션 실행 결과",
                "data": api_actions,
            }
        )
    if tool_contexts:
        await personal_agent_service.ws_manager.emit(
            user_id,
            "chat.processing",
            {
                "thread_id": thread["id"],
                "stage": "tool_context_ready",
                "tools": [ctx["tool"] for ctx in tool_contexts],
            },
        )
    tool_context_text = ""
    if tool_contexts:
        compact_contexts = []
        for ctx in tool_contexts:
            compact_contexts.append(
                {
                    "tool": ctx.get("tool"),
                    "summary": ctx.get("summary"),
                    "data": ctx.get("data"),
                }
            )
        tool_context_text = "\n도구 결과(JSON):\n" + json.dumps(
            compact_contexts, ensure_ascii=False
        )

    explicit_tool_result_mode = _is_explicit_tool_result_request(body.message)
    if explicit_tool_result_mode and api_actions and not _needs_multi_agent(body.message):
        reply = _format_school_api_action_reply(api_actions)
    elif _needs_multi_agent(body.message):
        await personal_agent_service.ws_manager.emit(
            user_id,
            "chat.processing",
            {"thread_id": thread["id"], "stage": "multi_agent_reasoning"},
        )
        task_personas = user_settings.get("personas") or []
        if not isinstance(task_personas, list):
            task_personas = []
        persona_lines: list[str] = []
        for persona in task_personas[:8]:
            if not isinstance(persona, dict):
                continue
            name = str(persona.get("name") or "에이전트")
            stats = persona.get("stats") or {}
            persona_lines.append(f"- {name}: {stats}")
        if not persona_lines:
            persona_lines = ["- 기본 에이전트: 균형형 시각으로 분석"]

        reply = await user_claude.generate(
            system_prompt=(
                _mode_system_instruction(body.mode)
                + knowledge_text
                + "\n현재 요청은 멀티 에이전트 토론이 필요하다. "
                "아래 과제 에이전트들을 활용해 단계별(문제정의->근거검증->대안비교->결론)로 종합 답변하라. "
                "도구 결과가 주어졌다면 이를 최우선 근거로 사용하고, "
                "환경 제약(권한 없음/접근 불가) 일반론으로 회피하지 마라."
            ),
            user_prompt=(
                f"사용자 요청:\n{body.message}\n"
                f"{stats_text}\n"
                f"과제 에이전트:\n" + "\n".join(persona_lines) + "\n"
                f"{tool_context_text}\n"
                "출력 형식:\n"
                "1) 에이전트별 핵심 주장 요약\n"
                "2) 통합 결론\n"
                "3) 즉시 실행 항목 3개"
            ),
            use_mock=body.use_mock,
            cache_hint=f"chat-multi-agent-{body.mode}",
        )
    else:
        reply = await user_claude.generate(
            system_prompt=(
                _mode_system_instruction(body.mode)
                + knowledge_text
                + "\n도구 결과가 주어졌다면 해당 결과를 근거로 답변하라. "
                "키 접근 불가/외부 API 접근 불가 같은 일반적 변명을 하지 마라."
            ),
            user_prompt=f"{body.message}{stats_text}{tool_context_text}",
            use_mock=body.use_mock,
            cache_hint=f"chat-{body.mode}",
        )

    assistant_message = await _append_message(
        user_id=user_id,
        thread_id=thread["id"],
        role="assistant",
        content=reply,
        metadata={
            "mode": body.mode,
            "multi_agent": _needs_multi_agent(body.message),
            "tools": [ctx["tool"] for ctx in tool_contexts],
            "explicit_tool_result_mode": explicit_tool_result_mode,
        },
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
    user_claude = await _build_user_claude_service(user_id)
    claude = await user_claude.diagnose_connection()

    async def _find_key_exists(key_name: str) -> bool:
        try:
            def _find_key():
                client = get_supabase_admin()
                return (
                    client.table("user_keys")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq("key_name", key_name)
                    .limit(1)
                    .execute()
                )

            result = await asyncio.to_thread(_find_key)
            if result.data:
                return True
        except Exception:
            pass

        row = await dev_store.get_user_key(user_id, key_name)
        return bool(row)

    school_key_found = await _find_key_exists("school_api_token")
    google_key_found = await _find_key_exists("google_oauth_access_token")

    school_api = {
        "token_saved": school_key_found,
        "reachable": False,
        "status": "not_configured",
        "reason": None,
        "source": "none",
    }
    if school_key_found or app_settings.school_api_token or app_settings.anthropic_auth_token:
        source = (
            "user_key"
            if school_key_found
            else ("env_fallback" if (app_settings.school_api_token or app_settings.anthropic_auth_token) else "none")
        )
        try:
            school_client = await get_school_client_for_user(user_id)
            auth_me = await school_client.get_auth_me()
            authenticated = bool(auth_me.get("authenticated", False))
            school_api = {
                "token_saved": school_key_found,
                "reachable": authenticated,
                "status": "ok" if authenticated else "unauthenticated",
                "reason": None if authenticated else "auth/me returned authenticated=false",
                "source": source,
            }
        except Exception as exc:
            school_api = {
                "token_saved": school_key_found,
                "reachable": False,
                "status": "error",
                "reason": str(exc)[:220],
                "source": source,
            }

    database = {"connected": False, "source": "dev_store", "reason": None}
    try:
        def _probe_database():
            client = get_supabase_admin()
            return (
                client.table("users")
                .select("id")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )

        await asyncio.to_thread(_probe_database)
        database = {"connected": True, "source": "supabase", "reason": None}
    except Exception as exc:
        database = {
            "connected": False,
            "source": "dev_store",
            "reason": str(exc)[:180],
        }

    return {
        "claude": claude,
        "school_api": school_api,
        "google_workspace": {"token_saved": google_key_found},
        "database": database,
    }
