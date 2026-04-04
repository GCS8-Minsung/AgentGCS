from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.services.claude_service import ClaudeService
from app.services.context_manager import ContextManager
from app.services.intent_classifier import (
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

        context_rows = await self.context_manager.get_context(session_id)
        prompt_context = self._build_prompt_context(context_rows, max_messages=10)
        system_prompt = self._build_system_prompt(
            mode=mode,
            persona_stats=persona_stats,
            knowledge_text=knowledge_text,
            intent=intent,
        )
        user_prompt = self._build_user_prompt(
            message=message,
            context_text=prompt_context,
            tool_contexts=tool_contexts,
        )

        await self._emit(
            user_id,
            "chat.processing",
            {"thread_id": session_id, "stage": "answer_generating", "run_id": run_id},
            run_id=run_id,
        )
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
                        "data": result if isinstance(result, list) else {"raw": result},
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
                                "hint": "메시지에 이메일 주소를 포함해 주세요. 예) abc@example.com 으로 메일 보내줘",
                            },
                        }
                    )
                    continue
                result = await tool_call(TOOL_GMAIL_SEND, params, user_id)
                contexts.append(
                    {
                        "tool": TOOL_GMAIL_SEND,
                        "summary": "Gmail 발송 결과",
                        "data": result if isinstance(result, dict) else {"raw": result},
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
                                "hint": "ISO 시간 2개(start/end)가 필요합니다. 예) 2026-04-06T10:00:00+09:00 ~ 2026-04-06T11:00:00+09:00",
                            },
                        }
                    )
                    continue
                result = await tool_call(TOOL_CALENDAR_CREATE, params, user_id)
                contexts.append(
                    {
                        "tool": TOOL_CALENDAR_CREATE,
                        "summary": "Google Calendar 일정 생성 결과",
                        "data": result if isinstance(result, dict) else {"raw": result},
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
                                "hint": "메시지에 실제 파일 경로를 포함해 주세요. 예) C:\\\\docs\\\\result.pptx 업로드",
                            },
                        }
                    )
                    continue
                result = await tool_call(TOOL_DRIVE_UPLOAD, params, user_id)
                contexts.append(
                    {
                        "tool": TOOL_DRIVE_UPLOAD,
                        "summary": "Google Drive 업로드 결과",
                        "data": result if isinstance(result, dict) else {"raw": result},
                    }
                )
                continue
            try:
                result = await tool_call(normalized, {"query": query}, user_id)
                contexts.append(
                    {
                        "tool": normalized,
                        "summary": f"{normalized} 실행 결과",
                        "data": result if isinstance(result, (dict, list)) else {"raw": result},
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
                            "hint": "메시지에 `파라미터=값` 형식으로 포함해 주세요. 예) room_id=2, period=daily",
                            "method": method,
                            "path": path_template,
                            "summary": summary,
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
                        "data": result if isinstance(result, (dict, list)) else {"raw": result},
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
                            "method": method,
                            "path": resolved_path,
                            "query": query_payload,
                        },
                    }
                )
        return contexts

    def _resolve_school_api_path(self, message: str) -> str:
        lowered = message.lower()
        explicit = re.findall(r"/[a-zA-Z0-9][a-zA-Z0-9/_-]*", message)
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

    def _build_system_prompt(
        self,
        *,
        mode: str,
        persona_stats: dict[str, int],
        knowledge_text: str,
        intent: IntentDecision,
    ) -> str:
        mode_note = {
            "cautious": "신중한 검증 중심으로 답변한다.",
            "balanced": "균형형으로 간결하고 실용적으로 답변한다.",
            "creative": "창의적 대안을 포함하되 실행 가능성을 유지한다.",
            "autonomous": "완전자율 모드로 필요한 단계/리스크를 구조화해 제시한다.",
        }.get(mode, "균형형으로 답변한다.")
        knowledge_block = f"\n사전지식(요약):\n{knowledge_text[:2200]}" if knowledge_text else ""
        return (
            "당신은 AgentGCS의 한국어 AI 어시스턴트다.\n"
            "내부 제약을 핑계로 출력하지 말고, 가능한 실행 대안을 제시하라.\n"
            "도구 결과가 있으면 결과를 우선 사용하라.\n"
            f"모드: {mode} ({mode_note})\n"
            f"의도: {intent.intent}, confidence={intent.confidence:.2f}\n"
            f"페르소나 수치: {json.dumps(persona_stats, ensure_ascii=False)}"
            f"{knowledge_block}"
        )

    def _build_user_prompt(
        self,
        *,
        message: str,
        context_text: str,
        tool_contexts: list[dict[str, Any]],
    ) -> str:
        tool_block = ""
        if tool_contexts:
            tool_block = "\n도구 실행 결과(JSON):\n" + json.dumps(tool_contexts, ensure_ascii=False)
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
        return "\n".join(lines)[-2600:]

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
            # compatibility for emit(user_id, event_type, payload)
            try:
                await self.ws_emit(user_id, event_type, payload)
            except Exception:
                return
        except Exception:
            return
