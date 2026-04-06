from __future__ import annotations

# ============================================================
# chatbot_service.py  –  AgentGCS 채팅 오케스트레이션 서비스
#
# ┌─────────────────────────────────────────────────────────────┐
# │             듀얼 모델 라우팅 아키텍처                         │
# │                                                             │
# │  메시지 → IntentClassifier(gpt-5-mini) → 의도 분류          │
# │              │                                             │
# │    ┌─────────┴──────────────────────┐                      │
# │    │                                │                      │
# │  general_chat               tool_required                  │
# │  (gpt-5-mini 생성)          (Claude Sonnet 생성)           │
# │    │                                │                      │
# │    └──── deep_task ────────────────►│                      │
# │          (DeepTaskOrchestrator      │                      │
# │           위임 – 미래 확장 훅)       │                      │
# │                                     │                      │
# │         autonomous mode:            │                      │
# │         ReActEngine 루프 사용        │                      │
# └─────────────────────────────────────────────────────────────┘
#
# 토큰 최적화 전략:
#   1. 도구 결과를 통째로 JSON 덤프하지 않고 핵심 필드만 추출
#   2. 컨텍스트 텍스트를 토큰 예산 내로 Truncate
#   3. 관찰(Observation) 블록을 구조화된 텍스트로 변환
#
# 미래 확장 포인트:
#   - INTENT_DEEP_TASK → _delegate_to_orchestrator() (현재 훅만 존재)
#   - MultiAgentGraph / DeepTaskOrchestrator 연결 준비 완료
# ============================================================

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

import httpx

from app.services.claude_service import ClaudeService
from app.services.context_manager import ContextManager
from app.services.intent_classifier import (
    INTENT_DEEP_TASK,
    INTENT_GENERAL_CHAT,
    INTENT_TOOL_REQUIRED,
    TOOL_CALENDAR_CREATE,
    TOOL_DRIVE_UPLOAD,
    TOOL_GMAIL_SEND,
    TOOL_SCHOOL_API,
    TOOL_WEB_SEARCH,
    IntentClassifier,
    IntentDecision,
)
from app.services.react_engine import ReActEngine
from app.services.traits import TraitSet

# ── 상수 ───────────────────────────────────────────────────
# 컨텍스트 텍스트 최대 문자 수 (약 650 토큰 기준, 한국어 1자 ≈ 1.5~2토큰)
_CTX_MAX_CHARS = 2_400
# 도구 Observation 블록 최대 문자 수
_OBS_MAX_CHARS = 2_000
# 단일 도구 결과의 최대 허용 문자 수
_SINGLE_OBS_MAX_CHARS = 600
# 웹 검색 결과에서 보존할 항목 수
_WEB_SEARCH_MAX_ITEMS = 4


@dataclass(slots=True)
class ChatRunResult:
    run_id: str
    reply: str
    intent: IntentDecision
    tool_contexts: list[dict[str, Any]]


class ChatBotService:
    def __init__(
        self,
        *,
        context_manager: ContextManager,
        ws_emit: Callable[[str, str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.context_manager = context_manager
        self.ws_emit = ws_emit

    # ── 메인 오케스트레이션 ────────────────────────────────

    async def handle_message(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        mode: str,
        persona_stats: dict[str, int],
        knowledge_text: str,
        claude: ClaudeService,
        tool_call: Callable[[str, dict[str, Any] | None, str | None], Awaitable[Any]],
        use_mock: bool,
    ) -> ChatRunResult:
        run_id = str(uuid4())

        await self._emit(
            user_id,
            "chat.processing",
            {"thread_id": session_id, "stage": "intent_classifying", "run_id": run_id},
            run_id=run_id,
        )

        # ── 의도 분류 (gpt-5-mini 우선, Claude 폴백) ───────
        classifier = IntentClassifier(claude)
        intent = await classifier.classify(message=message, use_mock=use_mock)

        await self._emit(
            user_id,
            "chat.intent.classified",
            {
                "thread_id": session_id,
                "run_id": run_id,
                "intent": intent.intent,
                "confidence": intent.confidence,
                "reason": intent.reason,
                "tools": intent.tools,
            },
            run_id=run_id,
        )

        # ── 페르소나 슬라이더 → LLM 생성 파라미터 ───────────
        # TraitSet이 temperature/top_p/max_tokens를 동적으로 계산한다.
        # gpt-5-mini 직접 호출 시 payload에 실제 적용되고,
        # Claude 호출 시에는 시스템 프롬프트 스타일 힌트로 반영된다.
        traits = TraitSet.from_dict(persona_stats)
        llm_params = traits.get_llm_params()

        # ── 컨텍스트 조회 (1시간 TTL, 토큰 보호 계층 포함) ─
        context_rows = await self.context_manager.get_context(session_id)
        context_text = self._build_prompt_context(context_rows, max_messages=10)
        # 토큰 예산 초과 방지: 최대 _CTX_MAX_CHARS 문자로 Truncate
        context_text = self._guard_context_tokens(context_text)

        # ── 시스템 프롬프트 구성 ──────────────────────────
        system_prompt = self._build_system_prompt(
            mode=mode,
            traits=traits,
            llm_params=llm_params,
            knowledge_text=knowledge_text,
            intent=intent,
        )

        # ======================================================
        # 라우팅 분기점 (mode/intent에 따라 다른 실행 경로)
        # ======================================================

        # ── 경로 A: autonomous 모드 → ReAct 루프 ───────────
        # 사용자가 '자율' 모드를 선택하면 단일 Call-and-Response가 아닌
        # ReActEngine의 반복 루프로 라우팅하여 목표 달성까지 자율 수행
        if mode == "autonomous" and not use_mock:
            return await self._run_autonomous_loop(
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                message=message,
                system_prompt=system_prompt,
                context_text=context_text,
                intent=intent,
                claude=claude,
                tool_call=tool_call,
                llm_params=llm_params,
            )

        # ── 경로 B: 심층 과제 → DeepTaskOrchestrator 위임 ──
        # [미래 확장 훅]
        # INTENT_DEEP_TASK가 감지되면 DeepTaskOrchestrator나
        # MultiAgentGraph로 위임할 수 있는 명확한 진입점.
        # 현재는 Placeholder(경고 emit + 일반 경로로 강등)이지만,
        # 구현체가 준비되면 아래 주석을 해제하고 연결하면 된다.
        # 내부 아키텍처는 건드리지 않아도 된다.
        if intent.intent == INTENT_DEEP_TASK:
            orchestrator_result = await self._delegate_to_orchestrator(
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                message=message,
                intent=intent,
                claude=claude,
                tool_call=tool_call,
            )
            if orchestrator_result is not None:
                # 오케스트레이터가 결과를 반환한 경우 바로 사용
                return orchestrator_result
            # None이면 아직 구현 안 됨 → tool_required로 강등하여 계속 진행
            intent = IntentDecision(
                intent=INTENT_TOOL_REQUIRED,
                confidence=intent.confidence,
                reason="deep_task_downgraded_to_tool_required",
                tools=intent.tools or [TOOL_WEB_SEARCH],
                tool_query=intent.tool_query,
                school_api_actions=intent.school_api_actions,
            )

        # ── 경로 C: 도구 필요 → 도구 실행 후 Claude Sonnet ─
        tool_contexts: list[dict[str, Any]] = []
        if intent.intent == INTENT_TOOL_REQUIRED:
            await self._emit(
                user_id,
                "chat.processing",
                {"thread_id": session_id, "stage": "tool_execution", "run_id": run_id},
                run_id=run_id,
            )
            tool_contexts = await self._execute_tools(
                user_id=user_id,
                message=message,
                intent=intent,
                tool_call=tool_call,
            )

        # ── 사용자 프롬프트 구성 (토큰 효율적 Observation 포함) ─
        user_prompt = self._build_user_prompt(
            message=message,
            context_text=context_text,
            tool_contexts=tool_contexts,
        )

        await self._emit(
            user_id,
            "chat.processing",
            {"thread_id": session_id, "stage": "answer_generating", "run_id": run_id},
            run_id=run_id,
        )

        # ── 최종 생성: 의도에 따라 모델 선택 ─────────────────
        # INTENT_GENERAL_CHAT  → gpt-5-mini (빠르고 저렴)
        # INTENT_TOOL_REQUIRED → Claude Sonnet (강력한 추론)
        if intent.intent == INTENT_GENERAL_CHAT:
            reply = await self._generate_fast(
                claude=claude,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                llm_params=llm_params,
                use_mock=use_mock,
                cache_hint="chatbot-general",
            )
        else:
            reply = await claude.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                use_mock=use_mock,
                cache_hint="chatbot-final",
            )

        await self._stream_reply(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            text=reply,
        )
        await self._emit(
            user_id,
            "chat.processing",
            {"thread_id": session_id, "stage": "done", "run_id": run_id},
            run_id=run_id,
        )
        return ChatRunResult(
            run_id=run_id,
            reply=reply,
            intent=intent,
            tool_contexts=tool_contexts,
        )

    # ── Autonomous 모드: ReAct 루프 ───────────────────────

    async def _run_autonomous_loop(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        message: str,
        system_prompt: str,
        context_text: str,
        intent: IntentDecision,
        claude: ClaudeService,
        tool_call: Callable[[str, dict[str, Any] | None, str | None], Awaitable[Any]],
        llm_params: dict[str, Any],
    ) -> ChatRunResult:
        """
        autonomous 모드 전용: ReActEngine을 사용하여 목표 달성까지 반복 수행한다.

        ReActEngine은 각 스텝에서 Thought → Action → Observation을 반복하고
        Claude가 final_answer를 반환하거나 max_iters에 도달하면 종료한다.
        """
        await self._emit(
            user_id,
            "chat.processing",
            {"thread_id": session_id, "stage": "autonomous_loop_starting", "run_id": run_id},
            run_id=run_id,
        )

        # WS 이벤트 콜백: ReAct 스텝마다 프론트엔드에 진행 상황 전송
        async def react_event_callback(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "react.step")
            await self._emit(
                user_id,
                event_type,
                {**event, "thread_id": session_id, "run_id": run_id},
                run_id=run_id,
            )

        engine = ReActEngine(
            claude=claude,
            tool_call=tool_call,
            max_iters=6,
            persistence_callback=None,   # 필요 시 Supabase 로깅 콜백 연결
            event_callback=react_event_callback,
        )

        # 컨텍스트를 포함한 사용자 프롬프트 구성
        react_user_prompt = (
            (f"이전 대화 맥락:\n{context_text}\n\n" if context_text else "")
            + f"사용자 요청:\n{message}\n\n"
            "도구를 활용하여 목표를 달성하라. 완료 시 final_answer를 반환하라."
        )

        result = await engine.run(
            system_prompt=system_prompt,
            user_prompt=react_user_prompt,
            use_mock=False,
            session_id=session_id,
            user_id=user_id,
        )

        # ReAct 루프 결과에서 최종 답변 추출
        final = result.get("final") or ""
        if isinstance(final, dict):
            reply = str(final.get("answer") or json.dumps(final, ensure_ascii=False))
        else:
            reply = str(final)

        if not reply.strip():
            reply = "자율 실행을 완료했지만 최종 답변을 생성하지 못했습니다."

        await self._stream_reply(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            text=reply,
        )
        await self._emit(
            user_id,
            "chat.processing",
            {
                "thread_id": session_id,
                "stage": "done",
                "run_id": run_id,
                "react_status": result.get("status"),
                "react_steps": len(result.get("history") or []),
            },
            run_id=run_id,
        )
        return ChatRunResult(
            run_id=run_id,
            reply=reply,
            intent=intent,
            tool_contexts=[],
        )

    # ── 심층 과제 위임 훅 (미래 확장 포인트) ──────────────

    async def _delegate_to_orchestrator(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        message: str,
        intent: IntentDecision,
        claude: ClaudeService,
        tool_call: Callable[[str, dict[str, Any] | None, str | None], Awaitable[Any]],
    ) -> ChatRunResult | None:
        """
        [미래 확장 훅] INTENT_DEEP_TASK 요청을 DeepTaskOrchestrator 또는
        MultiAgentGraph로 위임하는 진입점.

        현재 상태: Placeholder — None을 반환하여 호출자가 TOOL_REQUIRED로 강등.

        연결 방법 (구현체 준비 시):
        ─────────────────────────────────────────────────────
        from app.services.multi_agent_graph import MultiAgentGraph
        from app.services.orchestrator import DeepTaskOrchestrator

        orchestrator = DeepTaskOrchestrator(claude=claude, tool_call=tool_call)
        result = await orchestrator.run(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            goal=message,
        )
        reply = result.get("final_answer") or ""
        await self._stream_reply(user_id=user_id, session_id=session_id,
                                 run_id=run_id, text=reply)
        return ChatRunResult(
            run_id=run_id, reply=reply, intent=intent, tool_contexts=[]
        )
        ─────────────────────────────────────────────────────
        """
        # 심층 과제 감지를 프론트엔드에 알림
        await self._emit(
            user_id,
            "chat.deep_task.detected",
            {
                "thread_id": session_id,
                "run_id": run_id,
                "message": "심층 과제가 감지되었습니다. 현재는 도구 실행 모드로 처리합니다.",
                "intent": intent.intent,
            },
            run_id=run_id,
        )
        # TODO: 구현체 연결 시 여기서 orchestrator 호출 후 ChatRunResult 반환
        return None

    # ── gpt-5-mini 직접 생성 (INTENT_GENERAL_CHAT 전용) ───

    async def _generate_fast(
        self,
        *,
        claude: ClaudeService,
        system_prompt: str,
        user_prompt: str,
        llm_params: dict[str, Any],
        use_mock: bool,
        cache_hint: str = "chatbot-general",
    ) -> str:
        """
        gpt-5-mini를 직접 호출하여 일반 대화를 처리한다.
        TraitSet.get_llm_params()에서 계산된 temperature/top_p/max_tokens를
        실제 API payload에 적용하여 페르소나 슬라이더를 반영한다.

        ClaudeService의 openai_api_key / openai_fallback_url을 재사용하므로
        별도 자격증명 설정 불필요.

        실패(키 없음/타임아웃) 시 Claude Sonnet으로 자동 폴백.
        """
        if use_mock:
            return await claude.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                use_mock=True,
                cache_hint=cache_hint,
            )

        openai_key = (claude.openai_api_key or "").strip()
        if not openai_key:
            # OpenAI 키 없으면 Claude로 폴백
            return await claude.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                use_mock=False,
                cache_hint=cache_hint,
            )

        model = claude.openai_fallback_model or "gpt-5-mini"
        fallback_url = claude.openai_fallback_url or "https://api.openai.com/v1/chat/completions"

        # 페르소나 슬라이더에서 계산된 파라미터를 payload에 직접 적용
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": llm_params.get("temperature", 0.70),
            "top_p": llm_params.get("top_p", 0.90),
            "max_tokens": llm_params.get("max_tokens", 1200),
        }
        headers = {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(fallback_url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise ValueError(f"OpenAI HTTP {resp.status_code}: {(resp.text or '')[:180]}")
            data = resp.json() if resp.text else {}
            choice = (data.get("choices") or [])[0]
            message_obj = choice.get("message") if isinstance(choice, dict) else {}
            content = message_obj.get("content") if isinstance(message_obj, dict) else None
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception:
            pass

        # gpt-5-mini 실패 → Claude Sonnet 폴백
        return await claude.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            use_mock=False,
            cache_hint=cache_hint,
        )

    # ── 도구 실행 ─────────────────────────────────────────

    async def _execute_tools(
        self,
        *,
        user_id: str,
        message: str,
        intent: IntentDecision,
        tool_call: Callable[[str, dict[str, Any] | None, str | None], Awaitable[Any]],
    ) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        tools = intent.tools or []
        if not tools:
            tools = [TOOL_WEB_SEARCH]
        query = (intent.tool_query or message).strip()[:600]

        for tool in list(dict.fromkeys(tools))[:5]:
            normalized = tool.strip().lower()
            if normalized == TOOL_WEB_SEARCH:
                result = await tool_call("web_search", {"query": query, "max_results": 5}, user_id)
                contexts.append(
                    {
                        "tool": "web_search",
                        "summary": "웹 검색 결과",
                        "data": self._compress_web_search(result),
                    }
                )
                continue
            if normalized == TOOL_SCHOOL_API:
                action_contexts = await self._execute_school_api_actions(
                    user_id=user_id,
                    message=message,
                    intent=intent,
                    tool_call=tool_call,
                )
                contexts.extend(action_contexts)
                continue
            if normalized == TOOL_GMAIL_SEND:
                params = self._extract_gmail_params(message)
                if not params.get("to_email"):
                    contexts.append(
                        {
                            "tool": TOOL_GMAIL_SEND,
                            "summary": "Gmail 발송 생략(수신자 없음)",
                            "data": {
                                "status": "skipped",
                                "reason": "missing_to_email",
                                "hint": "메시지에 이메일 주소를 포함해 주세요.",
                            },
                        }
                    )
                    continue
                result = await tool_call(TOOL_GMAIL_SEND, params, user_id)
                contexts.append(
                    {
                        "tool": TOOL_GMAIL_SEND,
                        "summary": "Gmail 발송 결과",
                        "data": self._compress_generic(result),
                    }
                )
                continue
            if normalized == TOOL_CALENDAR_CREATE:
                params = self._extract_calendar_params(message)
                if not (params.get("start_at") and params.get("end_at")):
                    contexts.append(
                        {
                            "tool": TOOL_CALENDAR_CREATE,
                            "summary": "캘린더 생성 생략(시간 파라미터 부족)",
                            "data": {
                                "status": "skipped",
                                "reason": "missing_datetime",
                                "hint": "ISO 시간 2개(start/end)가 필요합니다.",
                            },
                        }
                    )
                    continue
                result = await tool_call(TOOL_CALENDAR_CREATE, params, user_id)
                contexts.append(
                    {
                        "tool": TOOL_CALENDAR_CREATE,
                        "summary": "Google Calendar 일정 생성 결과",
                        "data": self._compress_generic(result),
                    }
                )
                continue
            if normalized == TOOL_DRIVE_UPLOAD:
                params = self._extract_drive_params(message)
                if not params.get("file_path"):
                    contexts.append(
                        {
                            "tool": TOOL_DRIVE_UPLOAD,
                            "summary": "Drive 업로드 생략(파일 경로 없음)",
                            "data": {
                                "status": "skipped",
                                "reason": "missing_file_path",
                                "hint": "메시지에 실제 파일 경로를 포함해 주세요.",
                            },
                        }
                    )
                    continue
                result = await tool_call(TOOL_DRIVE_UPLOAD, params, user_id)
                contexts.append(
                    {
                        "tool": TOOL_DRIVE_UPLOAD,
                        "summary": "Google Drive 업로드 결과",
                        "data": self._compress_generic(result),
                    }
                )
                continue
            try:
                result = await tool_call(normalized, {"query": query}, user_id)
                contexts.append(
                    {
                        "tool": normalized,
                        "summary": f"{normalized} 실행 결과",
                        "data": self._compress_generic(result),
                    }
                )
            except Exception as exc:
                contexts.append(
                    {
                        "tool": normalized,
                        "summary": f"{normalized} 실행 실패",
                        "data": {"status": "error", "error": str(exc)[:220]},
                    }
                )
        return contexts

    async def _execute_school_api_actions(
        self,
        *,
        user_id: str,
        message: str,
        intent: IntentDecision,
        tool_call: Callable[[str, dict[str, Any] | None, str | None], Awaitable[Any]],
    ) -> list[dict[str, Any]]:
        actions = intent.school_api_actions or [
            {
                "method": "GET",
                "path": self._resolve_school_api_path(message),
                "summary": "fallback endpoint",
                "path_params": [],
                "query_params": [],
                "required_query_params": [],
                "body_required": False,
            }
        ]
        contexts: list[dict[str, Any]] = []
        named_values = self._extract_named_values(message)
        fallback_numbers = re.findall(r"(?<!\d)\d{1,8}(?!\d)", message)
        fallback_number = fallback_numbers[0] if fallback_numbers else None
        body_payload = self._extract_json_payload(message)

        for action in actions[:5]:
            method = str(action.get("method") or "GET").upper().strip()
            path_template = str(action.get("path") or "").strip()
            summary = str(action.get("summary") or path_template).strip()
            path_params = (
                [str(item) for item in action.get("path_params", []) if isinstance(item, str)]
                if isinstance(action.get("path_params"), list)
                else []
            )
            required_query_params = (
                [str(item) for item in action.get("required_query_params", []) if isinstance(item, str)]
                if isinstance(action.get("required_query_params"), list)
                else []
            )
            query_params = (
                [str(item) for item in action.get("query_params", []) if isinstance(item, str)]
                if isinstance(action.get("query_params"), list)
                else []
            )
            body_required = bool(action.get("body_required", False))

            resolved_path = path_template
            missing: list[str] = []
            for param_name in path_params:
                key = param_name.lower()
                value = named_values.get(key)
                if value is None and len(path_params) == 1 and fallback_number is not None:
                    value = fallback_number
                if value is None:
                    missing.append(f"path:{param_name}")
                    continue
                resolved_path = resolved_path.replace("{" + param_name + "}", str(value))

            query_payload: dict[str, Any] = {}
            for param_name in query_params:
                key = param_name.lower()
                if key in named_values:
                    query_payload[param_name] = named_values[key]
            for required_name in required_query_params:
                if required_name not in query_payload:
                    missing.append(f"query:{required_name}")

            body = body_payload if body_required else (body_payload if body_payload else None)
            if body_required and not isinstance(body, dict):
                missing.append("body")

            if missing:
                contexts.append(
                    {
                        "tool": "school_api_call",
                        "summary": f"{method} {path_template} 실행 생략(필수 파라미터 누락)",
                        "data": {
                            "status": "skipped",
                            "reason": "missing_required_parameters",
                            "missing": missing,
                            "hint": "`파라미터=값` 형식으로 포함해 주세요. 예) room_id=2",
                        },
                    }
                )
                continue

            try:
                result = await tool_call(
                    "school_api_call",
                    {
                        "method": method,
                        "path": resolved_path,
                        "query": query_payload or None,
                        "body": body if isinstance(body, dict) else None,
                    },
                    user_id,
                )
                contexts.append(
                    {
                        "tool": "school_api_call",
                        "summary": f"{method} {resolved_path} 호출 결과 ({summary})",
                        # 토큰 절약: 대용량 API 응답을 핵심 필드만 남김
                        "data": self._compress_school_api(result),
                    }
                )
            except Exception as exc:
                contexts.append(
                    {
                        "tool": "school_api_call",
                        "summary": f"{method} {resolved_path} 호출 실패",
                        "data": {
                            "status": "error",
                            "error": str(exc)[:260],
                            "path": resolved_path,
                        },
                    }
                )
        return contexts

    # ── 토큰 효율적 Observation 압축 헬퍼 ────────────────
    # 핵심: LLM에 전달되는 도구 결과 토큰을 최소화한다.
    # 이전 방식(json.dumps 전체)을 대체하여 토큰 낭비를 획기적으로 줄인다.

    def _compress_web_search(self, result: Any) -> list[dict[str, Any]]:
        """
        웹 검색 결과에서 title/url/snippet만 추출한다.
        전체 본문(body)은 제거하여 토큰을 절약한다.
        """
        if not isinstance(result, list):
            return [{"raw": str(result)[:_SINGLE_OBS_MAX_CHARS]}]
        compressed: list[dict[str, Any]] = []
        for item in result[:_WEB_SEARCH_MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            compressed.append(
                {
                    "title": str(item.get("title") or "")[:100],
                    "url": str(item.get("url") or item.get("href") or "")[:200],
                    "snippet": str(
                        item.get("snippet") or item.get("body") or item.get("description") or ""
                    )[:280],
                }
            )
        return compressed

    def _compress_school_api(self, result: Any) -> Any:
        """
        School API 응답에서 과도하게 큰 필드를 제거하거나 자른다.
        리스트 응답은 최대 10개 항목으로 제한한다.
        """
        if isinstance(result, list):
            trimmed = result[:10]
            return [self._trim_dict(item) for item in trimmed if isinstance(item, dict)]
        if isinstance(result, dict):
            return self._trim_dict(result)
        return {"raw": str(result)[:_SINGLE_OBS_MAX_CHARS]}

    def _compress_generic(self, result: Any) -> Any:
        """
        범용 도구 결과 압축: 딕셔너리는 큰 값을 자르고, 리스트는 5개로 제한.
        """
        if isinstance(result, dict):
            return self._trim_dict(result)
        if isinstance(result, list):
            return [self._trim_dict(i) if isinstance(i, dict) else i for i in result[:5]]
        return {"raw": str(result)[:_SINGLE_OBS_MAX_CHARS]}

    def _trim_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        """
        딕셔너리의 문자열 값을 _SINGLE_OBS_MAX_CHARS 이내로 자른다.
        중첩 딕셔너리는 JSON 직렬화 후 자른다.
        """
        result: dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, str):
                result[k] = v[:_SINGLE_OBS_MAX_CHARS]
            elif isinstance(v, (dict, list)):
                serialized = json.dumps(v, ensure_ascii=False)
                result[k] = serialized[:_SINGLE_OBS_MAX_CHARS] if len(serialized) > _SINGLE_OBS_MAX_CHARS else v
            else:
                result[k] = v
        return result

    # ── 파라미터 추출 헬퍼 ────────────────────────────────

    def _resolve_school_api_path(self, message: str) -> str:
        lowered = message.lower()
        explicit = re.findall(r"/[a-zA-Z0-9][a-zA-Z0-9/_-]*", message)
        allowed_paths = {
            "/auth/me", "/teams/me", "/users/me/league", "/snippet_date",
            "/leaderboards", "/meeting-rooms", "/daily-snippets",
            "/weekly-snippets", "/openapi.json",
        }
        for candidate in explicit:
            if candidate in allowed_paths:
                return candidate
        if "/teams/me" in lowered or "팀 정보" in message:
            return "/teams/me"
        if "/users/me/league" in lowered or "리그" in message:
            return "/users/me/league"
        if "회의실" in message:
            return "/meeting-rooms"
        if "리더보드" in message or "순위" in message:
            return "/leaderboards"
        if "스니펫" in message and "주간" in message:
            return "/weekly-snippets"
        if "스니펫" in message:
            return "/daily-snippets"
        return "/auth/me"

    def _extract_named_values(self, message: str) -> dict[str, str]:
        pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_-]{1,40})\s*[:=]\s*([A-Za-z0-9_.:/+-]+)", message)
        values: dict[str, str] = {}
        for name, value in pairs:
            values[name.lower()] = value
        return values

    def _extract_json_payload(self, message: str) -> dict[str, Any] | None:
        if "{" not in message or "}" not in message:
            return None
        candidate_match = re.search(r"\{.*\}", message, flags=re.S)
        if not candidate_match:
            return None
        try:
            parsed = json.loads(candidate_match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _extract_gmail_params(self, message: str) -> dict[str, Any]:
        email_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", message)
        to_email = email_match.group(1) if email_match else ""
        subject_match = re.search(r"(?:제목|subject)\s*[:：]\s*(.+)", message, flags=re.IGNORECASE)
        body_match = re.search(r"(?:내용|body)\s*[:：]\s*(.+)", message, flags=re.IGNORECASE)
        subject = subject_match.group(1).strip() if subject_match else "AgentGCS 알림"
        body = body_match.group(1).strip() if body_match else message.strip()
        return {"to_email": to_email, "subject": subject[:180], "body": body[:4000]}

    def _extract_calendar_params(self, message: str) -> dict[str, Any]:
        pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})"
        matches = re.findall(pattern, message)
        if len(matches) >= 2:
            start_at, end_at = matches[0], matches[1]
        else:
            now = datetime.now(timezone.utc)
            start_at = ""
            end_at = ""
            if "지금" in message or "오늘" in message:
                start = now + timedelta(minutes=30)
                end = start + timedelta(hours=1)
                start_at = start.isoformat()
                end_at = end.isoformat()
        summary_match = re.search(r"(?:제목|summary)\s*[:：]\s*(.+)", message, flags=re.IGNORECASE)
        summary = summary_match.group(1).strip() if summary_match else "AgentGCS 일정"
        desc_match = re.search(r"(?:설명|description)\s*[:：]\s*(.+)", message, flags=re.IGNORECASE)
        description = desc_match.group(1).strip() if desc_match else message.strip()
        return {
            "summary": summary[:180],
            "start_at": start_at,
            "end_at": end_at,
            "description": description[:1200],
            "calendar_id": "primary",
        }

    def _extract_drive_params(self, message: str) -> dict[str, Any]:
        quoted = re.findall(r"[\"']([^\"']+\.[A-Za-z0-9]{2,6})[\"']", message)
        windows_path = re.findall(r"[A-Za-z]:\\[^\n\r\"']+\.[A-Za-z0-9]{2,6}", message)
        unix_path = re.findall(r"/[^\s\"']+\.[A-Za-z0-9]{2,6}", message)
        candidate = ""
        for pool in (quoted, windows_path, unix_path):
            if pool:
                candidate = pool[0].strip()
                break
        return {"file_path": candidate}

    # ── 프롬프트 빌더 ─────────────────────────────────────

    def _build_system_prompt(
        self,
        *,
        mode: str,
        traits: TraitSet,
        llm_params: dict[str, Any],
        knowledge_text: str,
        intent: IntentDecision,
    ) -> str:
        """
        시스템 프롬프트를 구성한다.
        TraitSet의 summary_blurb()를 통해 페르소나 슬라이더가 반영되고,
        llm_params를 통해 Claude 호출 시 스타일 힌트가 주입된다.
        """
        mode_note = {
            "cautious": "신중한 검증 중심으로 답변한다.",
            "balanced": "균형형으로 간결하고 실용적으로 답변한다.",
            "creative": "창의적 대안을 포함하되 실행 가능성을 유지한다.",
            "autonomous": "완전자율 모드로 필요한 단계/리스크를 구조화해 제시한다. ReAct 루프 중이다.",
        }.get(mode, "균형형으로 답변한다.")

        # 페르소나 슬라이더 기반 스타일 힌트
        persona_blurb = traits.summary_blurb()

        # LLM 파라미터 기반 응답 길이 힌트 (Claude는 max_tokens를 직접 못 받으므로 여기서 힌트 제공)
        max_tokens = llm_params.get("max_tokens", 1200)
        if max_tokens >= 2000:
            length_hint = "상세하고 충분히 길게 답변하라."
        elif max_tokens <= 800:
            length_hint = "간결하게 핵심만 답변하라."
        else:
            length_hint = "적당한 길이로 답변하라."

        knowledge_block = f"\n사전지식(요약):\n{knowledge_text[:2200]}" if knowledge_text else ""

        return (
            "당신은 AgentGCS의 한국어 AI 어시스턴트다.\n"
            "내부 제약을 핑계로 출력하지 말고, 가능한 실행 대안을 제시하라.\n"
            "도구 결과가 있으면 결과를 우선 사용하라.\n"
            f"모드: {mode} ({mode_note})\n"
            f"페르소나 지침: {persona_blurb}\n"
            f"응답 길이 지침: {length_hint}\n"
            f"의도: {intent.intent}, confidence={intent.confidence:.2f}"
            f"{knowledge_block}"
        )

    def _build_user_prompt(
        self,
        *,
        message: str,
        context_text: str,
        tool_contexts: list[dict[str, Any]],
    ) -> str:
        """
        사용자 프롬프트를 구성한다.

        토큰 최적화:
        - 이전 방식: json.dumps(tool_contexts) → 전체 JSON 덤프 (토큰 낭비)
        - 현재 방식: 구조화된 텍스트 블록으로 변환 후 _OBS_MAX_CHARS 내로 제한
        """
        tool_block = ""
        if tool_contexts:
            # 각 도구 결과를 구조화된 텍스트로 변환 (JSON 덤프 대신)
            lines: list[str] = ["[도구 실행 결과]"]
            for ctx in tool_contexts:
                tool_name = str(ctx.get("tool") or "")
                summary = str(ctx.get("summary") or "")
                data = ctx.get("data")
                # 데이터 직렬화: 이미 압축된 형태이므로 간결하게 출력
                if isinstance(data, list):
                    data_text = json.dumps(data, ensure_ascii=False)
                elif isinstance(data, dict):
                    data_text = json.dumps(data, ensure_ascii=False)
                else:
                    data_text = str(data)
                lines.append(f"▶ {tool_name} ({summary}): {data_text}")
            tool_block_raw = "\n".join(lines)
            # 전체 Observation 블록 토큰 한도 적용
            tool_block = "\n" + tool_block_raw[:_OBS_MAX_CHARS]

        return (
            (f"이전 대화 맥락:\n{context_text}\n\n" if context_text else "")
            + f"사용자 요청:\n{message}\n"
            + tool_block
            + "\n\n요청을 해결하는 최종 답변을 마크다운으로 작성하라."
        )

    def _build_prompt_context(self, context_rows: list[dict[str, Any]], *, max_messages: int) -> str:
        if not context_rows:
            return ""
        lines: list[str] = []
        for row in context_rows[-max_messages:]:
            role = str(row.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content[:480]}")
        return "\n".join(lines)

    def _guard_context_tokens(self, context_text: str) -> str:
        """
        컨텍스트 텍스트가 _CTX_MAX_CHARS를 초과하면 앞부분을 잘라낸다.
        최근 메시지를 보존하기 위해 뒷부분(_CTX_MAX_CHARS)을 남긴다.

        향후 개선 방향: 단순 Truncate 대신 LLM 기반 요약(Summarize)으로
        중요 정보를 보존하면서 토큰을 줄일 수 있다.
        (현재는 TTL 1시간 + max_messages=20이 사실상 상한을 제공)
        """
        if len(context_text) <= _CTX_MAX_CHARS:
            return context_text
        # 앞부분 제거, 최신 맥락 유지
        return "...(이전 대화 생략)...\n" + context_text[-(_CTX_MAX_CHARS - 20):]

    # ── 스트리밍 ──────────────────────────────────────────

    async def _stream_reply(self, *, user_id: str, session_id: str, run_id: str, text: str) -> None:
        chunk_size = 120
        await self._emit(
            user_id,
            "chat.stream.started",
            {"thread_id": session_id, "run_id": run_id},
            run_id=run_id,
        )
        for idx in range(0, len(text), chunk_size):
            delta = text[idx : idx + chunk_size]
            await self._emit(
                user_id,
                "chat.stream.delta",
                {
                    "thread_id": session_id,
                    "run_id": run_id,
                    "index": idx // chunk_size,
                    "delta": delta,
                },
                run_id=run_id,
            )
            await asyncio.sleep(0)
        await self._emit(
            user_id,
            "chat.stream.completed",
            {"thread_id": session_id, "run_id": run_id, "length": len(text)},
            run_id=run_id,
        )

    async def _emit(
        self,
        user_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> None:
        try:
            await self.ws_emit(user_id, event_type, payload, run_id=run_id)  # type: ignore[misc]
        except TypeError:
            try:
                await self.ws_emit(user_id, event_type, payload)
            except Exception:
                return
        except Exception:
            return
