from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.claude_service import ClaudeService
from app.services.integrations import (
    create_google_calendar_event,
    send_gmail_notification,
    upload_to_google_drive,
)
from app.services.school_api_client import SchoolApiError, get_school_client_for_user
from app.services.websocket_manager import WebSocketManager


class PersonalAgentService:
    def __init__(self, ws_manager: WebSocketManager, claude_service: ClaudeService) -> None:
        self.ws_manager = ws_manager
        self.claude_service = claude_service

    async def trigger_manual(
        self,
        *,
        user_id: str,
        instruction: str,
        use_mock: bool = True,
        claude_override: ClaudeService | None = None,
    ) -> dict:
        active_claude = claude_override or self.claude_service
        plan = await active_claude.generate(
            system_prompt=(
                "당신은 개인 업무 자동화 에이전트다. Gmail, Google Calendar, 교내 API, Kanban CRUD 도구를 "
                "호출한다는 가정으로 간결한 실행 계획을 제안한다."
            ),
            user_prompt=f"사용자 요청: {instruction}\n도구 호출 계획을 순서대로 4단계 이내로 작성해라.",
            use_mock=use_mock,
            cache_hint="personal-agent-plan",
        )

        await self.ws_manager.emit(
            user_id,
            "personal_agent.plan_ready",
            {
                "message": "개인 업무 에이전트 실행 계획이 준비되었습니다.",
                "plan": plan,
            },
        )
        return {"status": "ok", "plan": plan}

    async def execute_school_action(
        self,
        *,
        user_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Minimal action bridge to school API.
        Supported actions:
        - list_meeting_rooms
        - create_meeting_reservation
        - create_calendar_event
        - send_gmail
        - upload_drive_file
        """
        try:
            if action == "list_meeting_rooms":
                client = await get_school_client_for_user(user_id)
                rooms = await client.list_meeting_rooms()
                await self.ws_manager.emit(
                    user_id,
                    "personal_agent.school_action",
                    {"action": action, "result_count": len(rooms)},
                )
                return {"items": rooms}

            if action == "create_meeting_reservation":
                client = await get_school_client_for_user(user_id)
                result = await client.create_room_reservation(
                    room_id=int(payload["room_id"]),
                    start_at=str(payload["start_at"]),
                    end_at=str(payload["end_at"]),
                    purpose=payload.get("purpose"),
                )
                await self.ws_manager.emit(
                    user_id,
                    "toast.notification",
                    {
                        "title": "회의실 예약 완료",
                        "description": f"room_id={payload['room_id']} 예약이 생성되었습니다.",
                    },
                )
                return {"item": result}

            if action == "create_calendar_event":
                result = await create_google_calendar_event(
                    user_id=user_id,
                    summary=str(payload["summary"]),
                    start_at=str(payload["start_at"]),
                    end_at=str(payload["end_at"]),
                    description=payload.get("description"),
                    calendar_id=str(payload.get("calendar_id", "primary")),
                )
                await self.ws_manager.emit(
                    user_id,
                    "toast.notification",
                    {
                        "title": "캘린더 일정 처리",
                        "description": f"Google Calendar 일정 처리 상태: {result.get('status')}",
                        "meta": result,
                    },
                )
                return {"item": result}

            if action == "send_gmail":
                result = await send_gmail_notification(
                    user_id=user_id,
                    to_email=str(payload["to_email"]),
                    subject=str(payload["subject"]),
                    body=str(payload["body"]),
                )
                await self.ws_manager.emit(
                    user_id,
                    "toast.notification",
                    {
                        "title": "메일 발송 처리",
                        "description": f"Gmail 발송 상태: {result.get('status')}",
                        "meta": result,
                    },
                )
                return {"item": result}

            if action == "upload_drive_file":
                result = await upload_to_google_drive(
                    file_path=str(payload["file_path"]),
                    user_id=user_id,
                )
                await self.ws_manager.emit(
                    user_id,
                    "toast.notification",
                    {
                        "title": "드라이브 업로드 처리",
                        "description": f"Google Drive 업로드 상태: {result.get('status')}",
                        "meta": result,
                    },
                )
                return {"item": result}
        except SchoolApiError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": str(exc)}

        return {"error": f"Unsupported action: {action}"}

    async def handle_task_due_webhook(self, payload: dict[str, Any]) -> None:
        """
        Supabase DB Webhook payload example:
        {
          "type":"UPDATE",
          "record":{"user_id":"...","title":"...","due_date":"2026-04-04","status":"todo"}
        }
        """
        record = payload.get("record") or {}
        user_id = record.get("user_id")
        due_date = record.get("due_date")
        status = record.get("status")
        if not user_id or not due_date or status == "done":
            return

        try:
            deadline = datetime.fromisoformat(due_date).replace(tzinfo=timezone.utc)
        except ValueError:
            return

        hours_left = (deadline - datetime.now(tz=timezone.utc)).total_seconds() / 3600
        if hours_left <= 36:
            await self.ws_manager.emit(
                user_id,
                "toast.notification",
                {
                    "title": "마감 임박 알림",
                    "description": f"'{record.get('title', '작업')}' 마감까지 약 {max(1, int(hours_left))}시간 남았습니다.",
                    "action": {"label": "칸반 보드로 이동", "href": "/"},
                },
            )
