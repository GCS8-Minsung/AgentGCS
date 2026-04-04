import asyncio
import json
import re
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.config import settings as app_settings
from app.core.default_guideline import DEFAULT_AGENTGCS_GUIDELINE
from app.core.security import EncryptedPayload
from app.core.auth import get_current_user_id
from app.core.supabase_client import get_supabase_admin, save_agent_log
from app.dependencies import (
    context_manager,
    personal_agent_service,
    security_manager,
)
from app.models.schemas import (
    AgentChatRequest,
    DeepTaskRequest,
    DeepTaskStartResponse,
    PersonaStats,
    PersonalAgentTriggerRequest,
    PersonalSchoolActionRequest,
)
from app.services.claude_service import AgentPipeline, ClaudeService, ClaudeServiceError
from app.services.chatbot_service import ChatBotService
from app.services.dev_store import dev_store
from app.services.integrations import diagnose_google_workspace
from app.services.school_api_client import SchoolApiError, get_school_client_for_user
from app.tools.web_search import search_trusted_sources
from app.services.tool_registry import tool_registry

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


async def _build_user_claude_service(
    user_id: str,
    *,
    ai_provider_override: str | None = None,
    claude_model_override: str | None = None,
    openai_model_override: str | None = None,
) -> ClaudeService:
    user_settings = await _get_user_settings(user_id)
    ai_provider = str(ai_provider_override or user_settings.get("ai_provider") or "claude").lower()
    if ai_provider not in {"claude", "openai"}:
        ai_provider = "claude"
    claude_base = (
        user_settings.get("claude_base_url")
        or app_settings.anthropic_base_url
        or "https://claude.1000.school"
    )
    if ai_provider == "openai":
        preferred_model = (
            (openai_model_override or "").strip()
            or user_settings.get("openai_preferred_model")
            or app_settings.openai_fallback_model
            or "gpt-5-mini"
        )
    else:
        preferred_model = (
            (claude_model_override or "").strip()
            or user_settings.get("preferred_model")
            or app_settings.claude_model
        )

    school_token = _decrypt_key(await _get_user_key_row(user_id, "school_api_token"), user_id)
    anthropic_token = _decrypt_key(
        await _get_user_key_row(user_id, "anthropic_auth_token"), user_id
    )
    claude_api_key = _decrypt_key(await _get_user_key_row(user_id, "claude_api_key"), user_id)
    openai_api_key = _decrypt_key(await _get_user_key_row(user_id, "openai_api_key"), user_id)

    auth_token = anthropic_token or school_token or app_settings.anthropic_auth_token
    api_key = claude_api_key or app_settings.claude_api_key

    return ClaudeService(
        api_key=api_key,
        auth_token=auth_token,
        base_url=claude_base,
        preferred_model=preferred_model,
        openai_api_key=openai_api_key or app_settings.openai_api_key,
        openai_fallback_url=app_settings.openai_fallback_url,
        openai_fallback_model=app_settings.openai_fallback_model,
        primary_provider=ai_provider,
    )


async def _run_pipeline_background(
    *,
    user_id: str,
    run_id: str,
    task: str,
    persona_stats: PersonaStats,
    knowledge: str,
    use_mock: bool,
) -> None:
    user_claude = await _build_user_claude_service(user_id)
    pipeline = AgentPipeline(
        claude=user_claude,
        tool_call=tool_registry.call,
        ws_emit=personal_agent_service.ws_manager.emit,
        max_retries=3,
    )
    try:
        trace = await pipeline.run(
            user_id=user_id,
            mode="autonomous",
            message=task,
            persona_stats=persona_stats.model_dump(),
            knowledge=knowledge,
            thread_id=run_id,
            use_mock=use_mock,
        )
        await personal_agent_service.ws_manager.emit(
            user_id,
            "deep_task.completed",
            {
                "final_summary": trace.final_markdown,
                "arguments": {
                    row.step_id: {
                        "tool": row.tool,
                        "status": row.status,
                        "error": row.error,
                    }
                    for row in trace.execution_results
                },
                "sources": [
                    row.result
                    for row in trace.execution_results
                    if row.status == "ok" and isinstance(row.result, dict)
                ][:20],
            },
            run_id=run_id,
        )
    except Exception as exc:
        await personal_agent_service.ws_manager.emit(
            user_id,
            "deep_task.failed",
            {"message": str(exc)},
            run_id=run_id,
        )


@router.post("/deep-task/start", response_model=DeepTaskStartResponse)
async def start_deep_task(
    body: DeepTaskRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
) -> DeepTaskStartResponse:
    run_id = str(uuid4())
    user_settings = await _get_user_settings(user_id)
    knowledge = str(user_settings.get("knowledge_base_prompt") or "")
    background_tasks.add_task(
        _run_pipeline_background,
        user_id=user_id,
        run_id=run_id,
        task=body.task,
        persona_stats=body.persona_stats,
        knowledge=knowledge,
        use_mock=body.use_mock,
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
    user_claude = await _build_user_claude_service(
        user_id,
        ai_provider_override=body.ai_provider,
        claude_model_override=body.claude_model,
        openai_model_override=body.openai_model,
    )
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
        "필요 시 도구(웹 검색, 학교 API 컨텍스트)를 활용해 순차적으로 해결한다. "
        "내부 환경 제약을 변명하는 문구(예: '현재 저는 ... 접근 불가')는 출력하지 말고, "
        "실패 시 원인과 재시도 가능한 대안을 간결하게 제시한다."
        f"\n\n{DEFAULT_AGENTGCS_GUIDELINE}\n"
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
    lowered = message.lower()
    meta_markers = [
        "검색 기능",
        "웹 검색 기능",
        "인터넷 검색 기능",
        "web_search",
        "도구",
        "tool",
        "세션",
        "연결",
        "동작하지",
        "작동하지",
        "오류",
        "fallback",
    ]
    topical_markers = [
        "뉴스",
        "헤드라인",
        "시장",
        "트렌드",
        "리서치",
        "논문",
        "비교",
        "가격",
        "주가",
        "환율",
        "weather",
        "stock",
        "market",
        "research",
        "headline",
        "news",
    ]
    if any(marker in lowered for marker in meta_markers) and not any(
        marker in lowered for marker in topical_markers
    ):
        return False

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


def _as_json_markdown(value: object) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


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
        await personal_agent_service.ws_manager.emit(
            user_id,
            "chat.processing",
            {
                "stage": "tool_search_started",
                "tool": "web_search",
                "message": "웹 검색 파이프라인 실행 중",
            },
        )
        sources = await search_trusted_sources(message, max_results=5)
        if sources:
            contexts.append(
                {
                    "tool": "web_search",
                    "summary": "인터넷 검색 결과",
                    "data": sources,
                }
            )
        else:
            contexts.append(
                {
                    "tool": "web_search",
                    "summary": "인터넷 검색 결과 없음",
                    "data": {"query": message, "status": "no_results"},
                }
            )
        await personal_agent_service.ws_manager.emit(
            user_id,
            "chat.processing",
            {
                "stage": "tool_search_completed",
                "tool": "web_search",
                "result_count": len(sources),
            },
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


async def _list_recent_thread_messages(
    *,
    user_id: str,
    thread_id: str,
    limit: int = 24,
) -> list[dict]:
    limit = max(1, min(limit, 80))
    try:
        def _select():
            client = get_supabase_admin()
            return (
                client.table("chat_messages")
                .select("role,content,created_at")
                .eq("user_id", user_id)
                .eq("thread_id", thread_id)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )

        result = await asyncio.to_thread(_select)
        rows = result.data or []
        if rows:
            return rows
    except Exception:
        pass

    local_rows = await dev_store.list_messages(user_id, thread_id, limit=limit)
    return local_rows or []


def _build_history_context_text(messages: list[dict], *, max_messages: int = 16, max_chars: int = 5000) -> str:
    if not messages:
        return ""

    usable = [msg for msg in messages if msg.get("role") in {"user", "assistant"}]
    if not usable:
        return ""
    tail = usable[-max_messages:]

    lines: list[str] = []
    blocked_phrases = [
        "실행 가능한 소규모 검증(시장 테스트, 제작비 추정, 초기 고객 인터뷰)을 먼저 배치하고 리스크를 계량화해야 합니다."
    ]

    def _sanitize(text: str) -> str:
        sanitized = text
        for phrase in blocked_phrases:
            sanitized = sanitized.replace(phrase, "")
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        return sanitized

    for msg in tail:
        role = msg.get("role")
        if role == "assistant":
            role_label = "assistant"
        else:
            role_label = "user"
        content = _sanitize(str(msg.get("content") or "").strip())
        if not content:
            continue
        lines.append(f"{role_label}: {content}")

    if not lines:
        return ""

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def _trim_text(text: str, max_len: int) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= max_len:
        return compact
    return compact[: max(0, max_len - 3)] + "..."


def _build_compact_qa_memory(messages: list[dict], *, max_pairs: int = 5) -> list[dict[str, str]]:
    if not messages:
        return []
    pairs: list[dict[str, str]] = []
    pending_user: str | None = None
    for row in messages:
        role = str(row.get("role") or "")
        content = _trim_text(str(row.get("content") or ""), 720)
        if not content:
            continue
        if role == "user":
            pending_user = content
            continue
        if role == "assistant" and pending_user:
            pairs.append(
                {
                    "user": _trim_text(pending_user, 220),
                    "assistant": _trim_text(content, 340),
                }
            )
            pending_user = None
    return pairs[-max_pairs:]


def _format_qa_memory_block(pairs: list[dict[str, str]]) -> str:
    if not pairs:
        return ""
    lines = ["최근 대화 요약(최대 5세트):"]
    for idx, pair in enumerate(pairs, start=1):
        lines.append(f"{idx}. Q: {pair.get('user', '')}")
        lines.append(f"   A: {pair.get('assistant', '')}")
    return "\n".join(lines)


def _resolve_mode_stats(settings: dict, mode: str) -> dict[str, int]:
    chat_mode_personas = settings.get("chat_mode_personas")
    if isinstance(chat_mode_personas, dict):
        mode_stats = chat_mode_personas.get(mode)
        if isinstance(mode_stats, dict):
            return mode_stats
    return {
        "creativity": 70,
        "logic": 75,
        "critical_thinking": 75,
        "data_dependency": 70,
        "cautiousness": 60,
        "drive": 75,
    }


def _is_complex_request(message: str, mode: str) -> bool:
    if mode == "autonomous":
        return True
    msg = (message or "").strip()
    if len(msg) >= 280:
        return True
    if _needs_multi_agent(msg) or _needs_web_search(msg) or _looks_like_api_action(msg):
        return True
    complex_markers = [
        "계획",
        "전략",
        "분석",
        "비교",
        "근거",
        "리스크",
        "자동화",
        "워크플로우",
        "pipeline",
        "react",
        "plan-and-execute",
    ]
    lowered = msg.lower()
    return any(marker in msg or marker in lowered for marker in complex_markers)


def _select_contextual_knowledge(
    knowledge_text: str,
    *,
    message: str,
    qa_memory_block: str,
    is_complex: bool,
) -> str:
    base = _trim_text(knowledge_text or "", 700)
    sections: list[str] = []
    if base:
        sections.append(base)
    if qa_memory_block:
        sections.append(_trim_text(qa_memory_block, 1600))
    if not is_complex:
        return "\n\n".join(section for section in sections if section).strip()

    # Complex request: add keyword-matching snippets from long knowledge.
    lines = [line.strip() for line in (knowledge_text or "").splitlines() if line.strip()]
    tokens = set(re.findall(r"[A-Za-z0-9가-힣]{2,}", message.lower()))
    matched: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(token in lowered for token in tokens):
            matched.append(line)
        if len(matched) >= 12:
            break
    if matched:
        sections.append("관련 사전지식:\n" + "\n".join(f"- {item}" for item in matched))
    merged = "\n\n".join(section for section in sections if section).strip()
    return _trim_text(merged, 3200)


@router.post("/chat")
async def chat_with_agent(
    body: AgentChatRequest,
    background_tasks: BackgroundTasks,
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

    recent_messages = await _list_recent_thread_messages(
        user_id=user_id,
        thread_id=thread["id"],
        limit=40,
    )
    session_context_id = f"{user_id}:{thread['id']}"
    await context_manager.hydrate_if_empty(session_context_id, recent_messages)
    cached_context = await context_manager.get_context(session_context_id)
    if not cached_context or cached_context[-1].get("role") != "user" or str(
        cached_context[-1].get("content") or ""
    ) != body.message:
        await context_manager.append_message(
            session_context_id,
            role="user",
            content=body.message,
        )

    history_text = _build_history_context_text(recent_messages, max_messages=24, max_chars=2400)
    qa_memory_pairs = _build_compact_qa_memory(recent_messages, max_pairs=5)
    qa_memory_block = _format_qa_memory_block(qa_memory_pairs)

    user_settings = await _get_user_settings(user_id)
    stats_model = body.persona_stats or PersonaStats(**_resolve_mode_stats(user_settings, body.mode))
    is_complex_request = _is_complex_request(body.message, body.mode)

    user_claude = await _build_user_claude_service(
        user_id,
        ai_provider_override=body.ai_provider,
        claude_model_override=body.claude_model,
        openai_model_override=body.openai_model,
    )
    raw_knowledge_text = (body.knowledge_prompt or user_settings.get("knowledge_base_prompt") or "").strip()
    contextual_knowledge = _select_contextual_knowledge(
        raw_knowledge_text,
        message=body.message,
        qa_memory_block=qa_memory_block,
        is_complex=is_complex_request,
    )
    debug_raw_mode = bool(body.debug_raw or user_settings.get("debug_raw_mode", False))
    tool_contexts: list[dict] = []
    run_id: str | None = None
    needs_multi_agent = body.mode == "autonomous"
    intent_summary: dict | None = None
    try:
        chatbot = ChatBotService(
            context_manager=context_manager,
            ws_emit=personal_agent_service.ws_manager.emit,
        )
        run_result = await chatbot.handle_message(
            user_id=user_id,
            session_id=session_context_id,
            message=body.message,
            mode=body.mode,
            persona_stats=stats_model.model_dump(),
            knowledge_text=contextual_knowledge,
            claude=user_claude,
            tool_call=tool_registry.call,
            use_mock=body.use_mock,
        )
        run_id = run_result.run_id
        reply = run_result.reply
        tool_contexts = run_result.tool_contexts
        intent_summary = {
            "intent": run_result.intent.intent,
            "confidence": run_result.intent.confidence,
            "reason": run_result.intent.reason,
            "tools": run_result.intent.tools,
        }

        await context_manager.append_message(
            session_context_id,
            role="assistant",
            content=reply,
        )

        try:
            sources: list[dict] = []
            for ctx in tool_contexts:
                data = ctx.get("data")
                if isinstance(data, list):
                    sources.extend([item for item in data if isinstance(item, dict)])
                elif isinstance(data, dict):
                    sources.append(data)
            await save_agent_log(
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "task": body.message,
                    "persona_stats": stats_model.model_dump(),
                    "arguments": {"intent": intent_summary or {}},
                    "final_summary": reply,
                    "sources": sources[:32],
                    "feedback_score": None,
                }
            )
        except Exception:
            pass
    except ClaudeServiceError as exc:
        reply = (
            "AI 호출 실패\n"
            f"- code: `{exc.code}`\n"
            f"- message: {str(exc)}\n"
            "토큰 권한/쿼터/게이트웨이 상태를 점검해주세요."
        )
        tool_contexts = [
            {
                "tool": "pipeline_error",
                "summary": "pipeline failed",
                "data": {
                    "code": exc.code,
                    "retryable": exc.retryable,
                    "status_code": exc.status_code,
                    "details": exc.details,
                },
            }
        ]
    except Exception as exc:
        reply = f"파이프라인 실행 실패: {str(exc)}"
        tool_contexts = [
            {
                "tool": "pipeline_error",
                "summary": "unexpected pipeline failure",
                "data": {"error": str(exc)},
            }
        ]

    if debug_raw_mode:
        debug_payload = {
            "thread_id": thread["id"],
            "run_id": run_id,
            "mode": body.mode,
            "message": body.message,
            "needs_multi_agent": needs_multi_agent,
            "use_mock": body.use_mock,
            "claude": {
                "base_url": user_claude.base_url,
                "preferred_model": user_claude.preferred_model,
                "primary_provider": user_claude.primary_provider,
            },
            "knowledge_prompt": body.knowledge_prompt,
            "contextual_knowledge": contextual_knowledge,
            "is_complex_request": is_complex_request,
            "intent": intent_summary,
            "qa_memory_pairs": qa_memory_pairs,
            "history_context": history_text,
            "tool_contexts": tool_contexts,
            "generated_reply": reply,
        }
        reply = _as_json_markdown(debug_payload)

    assistant_message = await _append_message(
        user_id=user_id,
        thread_id=thread["id"],
        role="assistant",
        content=reply,
        metadata={
            "mode": body.mode,
            "multi_agent": needs_multi_agent,
            "run_id": run_id,
            "tools": [ctx["tool"] for ctx in tool_contexts],
            "debug_raw_mode": debug_raw_mode,
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
    openai = await user_claude.diagnose_openai_connection()

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
    google_refresh_found = await _find_key_exists("google_oauth_refresh_token")
    openai_key_found = await _find_key_exists("openai_api_key")

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

    google_workspace = await diagnose_google_workspace(user_id)
    if not google_workspace.get("token_saved"):
        google_workspace["token_saved"] = bool(google_key_found or google_refresh_found)

    web_search = {"reachable": False, "status": "error", "result_count": 0}
    try:
        probe_sources = await search_trusted_sources("오늘 서울 날씨", max_results=1)
        web_search = {
            "reachable": len(probe_sources) > 0,
            "status": "ok" if probe_sources else "no_results",
            "result_count": len(probe_sources),
        }
    except Exception as exc:
        web_search = {
            "reachable": False,
            "status": "error",
            "reason": str(exc)[:180],
            "result_count": 0,
        }

    tools_catalog = {"builtin_count": 0, "openapi_count": 0}
    try:
        catalog = await tool_registry.call("list_tools", {}, user_id)
        builtin = catalog.get("builtin_tools") if isinstance(catalog, dict) else []
        openapi = catalog.get("openapi_tools") if isinstance(catalog, dict) else []
        tools_catalog = {
            "builtin_count": len(builtin) if isinstance(builtin, list) else 0,
            "openapi_count": len(openapi) if isinstance(openapi, list) else 0,
        }
    except Exception:
        pass

    return {
        "claude": claude,
        "school_api": school_api,
        "google_workspace": google_workspace,
        "openai_fallback": {
            "token_saved": openai_key_found or bool(app_settings.openai_api_key),
            "reachable": openai.get("reachable", False),
            "status": openai.get("status", "not_configured"),
            "model": openai.get("model"),
        },
        "active_provider": user_claude.primary_provider,
        "database": database,
        "web_search": web_search,
        "tools_catalog": tools_catalog,
    }
