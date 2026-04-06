from __future__ import annotations

# ============================================================
# tool_registry.py  –  AgentGCS 도구 레지스트리
#
# 호출 경로 우선순위 (call() 메서드 내부):
#   1. 내장 도구 (web_search, gmail, calendar, drive, pptx, notebooklm)
#   2. SCHOOL_API_ALIASES  ← [핵심 추가] 자연어 이름 → School API 직접 매핑
#   3. ToolRegistry.openapi_actions (긴 school_{operation_id} 정확 매칭)
#   4. generic school_api_call / school_api 핸들러 (method+path 직접 지정)
#   5. unknown_tool 반환
#
# SCHOOL_API_ALIASES 도입 배경:
#   - openapi_actions에 로드된 98개 액션은 모두 'school_me_auth_me_get',
#     'school_get_leaderboard_leaderboards_get' 같은 긴 이름
#   - ReActEngine(Claude)이 자연어로 'get_my_info', 'leaderboard',
#     'list_rooms' 등을 호출하면 매칭 실패 → {"error": "unknown_tool"}
#   - 짧은 자연어 alias → (method, path, path_params 목록) 매핑으로 해결
# ============================================================

import json
import os
import re
from pathlib import Path
from typing import Any

from app.services.integrations import (
    create_google_calendar_event,
    send_gmail_notification,
    upload_to_google_drive,
)
from app.services.notebooklm import generate_notebooklm_assets
from app.services.school_api_client import get_school_client_for_user
from app.tools.web_search import (
    search_academic_sources,
    search_general_sources,
    search_trusted_sources,
)

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi_1000school.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# ── School API 자연어 별칭 테이블 ───────────────────────────
#
# 형식: alias → {"method": str, "path": str, "path_params": [str]}
#
# path_params: URL 템플릿에서 추출해야 할 파라미터 이름 목록
#   값은 호출 시 params 딕셔너리의 최상위 키 또는
#   params["path_params"] 하위 키에서 찾는다.
#
# 쿼리 파라미터: params["query"] 딕셔너리 또는 최상위 평탄 params 값에서 읽는다.
# 바디:         params["body"] 딕셔너리 또는 content/purpose 같은 최상위 키에서 읽는다.
#
SCHOOL_API_ALIASES: dict[str, dict[str, Any]] = {

    # ── 인증 / 내 정보 ──────────────────────────────────────
    "get_auth_me":          {"method": "GET",  "path": "/auth/me"},
    "auth_me":              {"method": "GET",  "path": "/auth/me"},
    "get_me":               {"method": "GET",  "path": "/auth/me"},
    "my_info":              {"method": "GET",  "path": "/auth/me"},
    "whoami":               {"method": "GET",  "path": "/auth/me"},
    "get_csrf_token":       {"method": "GET",  "path": "/auth/csrf"},
    "list_tokens":          {"method": "GET",  "path": "/auth/tokens"},
    "auth_tokens":          {"method": "GET",  "path": "/auth/tokens"},

    # ── 팀 ─────────────────────────────────────────────────
    "get_my_team":          {"method": "GET",  "path": "/teams/me"},
    "my_team":              {"method": "GET",  "path": "/teams/me"},
    "teams_me":             {"method": "GET",  "path": "/teams/me"},
    "team_info":            {"method": "GET",  "path": "/teams/me"},
    "list_teams":           {"method": "GET",  "path": "/teams"},
    "rename_my_team":       {"method": "PATCH","path": "/teams/me"},
    "join_team":            {"method": "POST", "path": "/teams/join"},
    "leave_team":           {"method": "POST", "path": "/teams/leave"},

    # ── 리그 ────────────────────────────────────────────────
    "get_my_league":        {"method": "GET",  "path": "/users/me/league"},
    "my_league":            {"method": "GET",  "path": "/users/me/league"},
    "league":               {"method": "GET",  "path": "/users/me/league"},

    # ── 리더보드 ────────────────────────────────────────────
    "get_leaderboard":      {"method": "GET",  "path": "/leaderboards"},
    "leaderboard":          {"method": "GET",  "path": "/leaderboards"},
    "leaderboards":         {"method": "GET",  "path": "/leaderboards"},
    "rankings":             {"method": "GET",  "path": "/leaderboards"},
    "rank":                 {"method": "GET",  "path": "/leaderboards"},

    # ── 업적 ────────────────────────────────────────────────
    "get_my_achievements":      {"method": "GET", "path": "/achievements/me"},
    "my_achievements":          {"method": "GET", "path": "/achievements/me"},
    "achievements":             {"method": "GET", "path": "/achievements/me"},
    "get_recent_achievements":  {"method": "GET", "path": "/achievements/recent"},
    "recent_achievements":      {"method": "GET", "path": "/achievements/recent"},

    # ── 회의실 목록 ─────────────────────────────────────────
    "list_meeting_rooms":   {"method": "GET",  "path": "/meeting-rooms"},
    "meeting_rooms":        {"method": "GET",  "path": "/meeting-rooms"},
    "rooms":                {"method": "GET",  "path": "/meeting-rooms"},
    "get_rooms":            {"method": "GET",  "path": "/meeting-rooms"},

    # ── 회의실 예약 목록 (room_id 경로 파라미터 필요) ─────────
    "list_room_reservations": {
        "method": "GET",
        "path": "/meeting-rooms/{room_id}/reservations",
        "path_params": ["room_id"],
    },
    "room_reservations": {
        "method": "GET",
        "path": "/meeting-rooms/{room_id}/reservations",
        "path_params": ["room_id"],
    },

    # ── 회의실 예약 생성 (room_id + body: start_at, end_at, purpose) ─
    "create_room_reservation": {
        "method": "POST",
        "path": "/meeting-rooms/{room_id}/reservations",
        "path_params": ["room_id"],
    },
    "book_room": {
        "method": "POST",
        "path": "/meeting-rooms/{room_id}/reservations",
        "path_params": ["room_id"],
    },
    "reserve_room": {
        "method": "POST",
        "path": "/meeting-rooms/{room_id}/reservations",
        "path_params": ["room_id"],
    },

    # ── 회의실 예약 취소 (reservation_id 경로 파라미터 필요) ───
    "cancel_room_reservation": {
        "method": "DELETE",
        "path": "/meeting-rooms/reservations/{reservation_id}",
        "path_params": ["reservation_id"],
    },
    "cancel_reservation": {
        "method": "DELETE",
        "path": "/meeting-rooms/reservations/{reservation_id}",
        "path_params": ["reservation_id"],
    },
    "delete_reservation": {
        "method": "DELETE",
        "path": "/meeting-rooms/reservations/{reservation_id}",
        "path_params": ["reservation_id"],
    },

    # ── 스니펫 날짜 ─────────────────────────────────────────
    "get_snippet_date":     {"method": "GET",  "path": "/snippet_date"},
    "snippet_date":         {"method": "GET",  "path": "/snippet_date"},

    # ── 일간 스니펫 ─────────────────────────────────────────
    "list_daily_snippets":  {"method": "GET",  "path": "/daily-snippets"},
    "daily_snippets":       {"method": "GET",  "path": "/daily-snippets"},
    "get_daily_snippets":   {"method": "GET",  "path": "/daily-snippets"},
    "daily_snippet_page":   {"method": "GET",  "path": "/daily-snippets/page-data"},
    "daily_snippet_feedback": {"method": "GET","path": "/daily-snippets/feedback"},
    "organize_daily_snippets": {"method": "POST","path": "/daily-snippets/organize"},

    "get_daily_snippet": {
        "method": "GET",
        "path": "/daily-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },
    "create_daily_snippet": {"method": "POST", "path": "/daily-snippets"},
    "write_daily_snippet":  {"method": "POST", "path": "/daily-snippets"},
    "update_daily_snippet": {
        "method": "PUT",
        "path": "/daily-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },
    "edit_daily_snippet": {
        "method": "PUT",
        "path": "/daily-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },
    "delete_daily_snippet": {
        "method": "DELETE",
        "path": "/daily-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },

    # ── 주간 스니펫 ─────────────────────────────────────────
    "list_weekly_snippets": {"method": "GET",  "path": "/weekly-snippets"},
    "weekly_snippets":      {"method": "GET",  "path": "/weekly-snippets"},
    "get_weekly_snippets":  {"method": "GET",  "path": "/weekly-snippets"},
    "weekly_snippet_page":  {"method": "GET",  "path": "/weekly-snippets/page-data"},
    "weekly_snippet_feedback": {"method": "GET","path": "/weekly-snippets/feedback"},
    "organize_weekly_snippets": {"method": "POST","path": "/weekly-snippets/organize"},

    "get_weekly_snippet": {
        "method": "GET",
        "path": "/weekly-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },
    "create_weekly_snippet": {"method": "POST", "path": "/weekly-snippets"},
    "write_weekly_snippet":  {"method": "POST", "path": "/weekly-snippets"},
    "update_weekly_snippet": {
        "method": "PUT",
        "path": "/weekly-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },
    "edit_weekly_snippet": {
        "method": "PUT",
        "path": "/weekly-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },
    "delete_weekly_snippet": {
        "method": "DELETE",
        "path": "/weekly-snippets/{snippet_id}",
        "path_params": ["snippet_id"],
    },

    # ── 학생 목록 ────────────────────────────────────────────
    "list_students":        {"method": "GET",  "path": "/users/students"},
    "students":             {"method": "GET",  "path": "/users/students"},
    "search_students":      {"method": "GET",  "path": "/users/students/search"},

    # ── 알림 ─────────────────────────────────────────────────
    "list_notifications":   {"method": "GET",  "path": "/notifications"},
    "notifications":        {"method": "GET",  "path": "/notifications"},
    "unread_count":         {"method": "GET",  "path": "/notifications/unread-count"},
    "mark_all_read":        {"method": "PATCH","path": "/notifications/read-all"},
    "notification_settings":{"method": "GET",  "path": "/notifications/settings"},
    "mark_notification_read": {
        "method": "PATCH",
        "path": "/notifications/{notification_id}/read",
        "path_params": ["notification_id"],
    },

    # ── 댓글 ─────────────────────────────────────────────────
    "list_comments":        {"method": "GET",  "path": "/comments"},
    "comments":             {"method": "GET",  "path": "/comments"},
    "create_comment":       {"method": "POST", "path": "/comments"},
    "update_comment": {
        "method": "PUT",
        "path": "/comments/{comment_id}",
        "path_params": ["comment_id"],
    },
    "delete_comment": {
        "method": "DELETE",
        "path": "/comments/{comment_id}",
        "path_params": ["comment_id"],
    },
    "mentionable_users":    {"method": "GET",  "path": "/comments/mentionable-users"},

    # ── 피어 리뷰 ────────────────────────────────────────────
    "list_peer_review_sessions": {"method": "GET",  "path": "/peer-reviews/sessions"},
    "peer_reviews":              {"method": "GET",  "path": "/peer-reviews/sessions"},
    "peer_review_sessions":      {"method": "GET",  "path": "/peer-reviews/sessions"},
    "create_peer_review_session":{"method": "POST", "path": "/peer-reviews/sessions"},
    "get_peer_review_session": {
        "method": "GET",
        "path": "/peer-reviews/sessions/{session_id}",
        "path_params": ["session_id"],
    },
    "peer_review_progress": {
        "method": "GET",
        "path": "/peer-reviews/sessions/{session_id}/progress",
        "path_params": ["session_id"],
    },
    "peer_review_results": {
        "method": "GET",
        "path": "/peer-reviews/sessions/{session_id}/results",
        "path_params": ["session_id"],
    },
    "get_peer_review_form": {
        "method": "GET",
        "path": "/peer-reviews/forms/{token}",
        "path_params": ["token"],
    },
    "submit_peer_review": {
        "method": "POST",
        "path": "/peer-reviews/forms/{token}/submit",
        "path_params": ["token"],
    },

    # ── 토너먼트 ──────────────────────────────────────────────
    "list_tournament_sessions":  {"method": "GET",  "path": "/tournaments/sessions"},
    "tournaments":               {"method": "GET",  "path": "/tournaments/sessions"},
    "tournament_sessions":       {"method": "GET",  "path": "/tournaments/sessions"},
    "my_tournaments":            {"method": "GET",  "path": "/tournaments/sessions/mine"},
    "create_tournament_session": {"method": "POST", "path": "/tournaments/sessions"},
    "get_tournament_session": {
        "method": "GET",
        "path": "/tournaments/sessions/{session_id}",
        "path_params": ["session_id"],
    },
    "get_tournament_bracket": {
        "method": "GET",
        "path": "/tournaments/sessions/{session_id}/bracket",
        "path_params": ["session_id"],
    },
    "tournament_results": {
        "method": "GET",
        "path": "/tournaments/sessions/{session_id}/results",
        "path_params": ["session_id"],
    },
    "get_tournament_match": {
        "method": "GET",
        "path": "/tournaments/matches/{match_id}",
        "path_params": ["match_id"],
    },
    "submit_tournament_vote": {
        "method": "POST",
        "path": "/tournaments/matches/{match_id}/vote",
        "path_params": ["match_id"],
    },
    "my_tournament_vote": {
        "method": "GET",
        "path": "/tournaments/matches/{match_id}/my-vote",
        "path_params": ["match_id"],
    },
    "my_tournament_score": {
        "method": "GET",
        "path": "/tournaments/sessions/{session_id}/my-score",
        "path_params": ["session_id"],
    },

    # ── OpenAPI 스펙 요약 ─────────────────────────────────────
    "get_openapi_summary":  {"method": "GET", "path": "/openapi.json"},
    "openapi_summary":      {"method": "GET", "path": "/openapi.json"},
    "openapi_spec":         {"method": "GET", "path": "/openapi.json"},
    "api_spec":             {"method": "GET", "path": "/openapi.json"},

    # ── 이용 약관 / 동의 ──────────────────────────────────────
    "get_terms":            {"method": "GET",  "path": "/terms"},
    "create_consent":       {"method": "POST", "path": "/consents"},
}

# 정규화된 alias 키 집합 (빠른 룩업용)
_NORMALIZED_ALIASES: frozenset[str] = frozenset(SCHOOL_API_ALIASES.keys())

# 모든 조회 가능한 alias 이름 목록 (list_tools에 표시)
_ALIAS_DISPLAY_LIST: list[str] = sorted(SCHOOL_API_ALIASES.keys())


def _normalize_action_name(name: str) -> str:
    lowered = name.strip().lower()
    lowered = re.sub(r"[^a-z0-9_]+", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered


def _extract_path_params(
    params: dict[str, Any],
    path_param_names: list[str],
) -> tuple[str, dict[str, Any]]:
    """
    params 딕셔너리에서 경로 파라미터 값을 추출하고 URL 템플릿을 치환한다.

    검색 순서:
      1. params["path_params"][param_name]  (명시적 중첩 dict)
      2. params[param_name]                (최상위 직접 키)

    반환: (해결된 path 문자열은 호출자가 처리, path_param_values dict)
    """
    path_param_values: dict[str, Any] = {}
    nested = params.get("path_params") or {}
    if not isinstance(nested, dict):
        nested = {}
    for pname in path_param_names:
        val = nested.get(pname) or params.get(pname)
        if val is not None:
            path_param_values[pname] = str(val)
    return path_param_values


def _extract_query_params(
    params: dict[str, Any],
    path_param_names: list[str],
) -> dict[str, Any] | None:
    """
    params["query"] 우선, 없으면 path_params/body/meta 키를 제외한 평탄 파라미터를 쿼리로 사용.
    """
    if "query" in params:
        q = params["query"]
        return q if isinstance(q, dict) else None

    # 평탄 파라미터: 알려진 메타 키와 경로 파라미터 제거
    skip = {"method", "path", "body", "path_params", "query", "user_id"} | set(path_param_names)
    flat = {k: v for k, v in params.items() if k not in skip and v is not None}
    return flat or None


def _extract_body(
    params: dict[str, Any],
    path_param_names: list[str],
) -> dict[str, Any] | None:
    """
    params["body"] 우선, 없으면 content/purpose 등 의미 있는 키를 body로 조립.
    """
    if "body" in params and isinstance(params["body"], dict):
        return params["body"]

    # POST/PUT/PATCH에 자주 사용되는 body 필드 목록
    body_candidate_keys = {
        "content", "purpose", "start_at", "end_at",
        "name", "title", "description", "message",
        "summary", "text", "data",
    }
    skip = {"method", "path", "body", "path_params", "query", "user_id"} | set(path_param_names)
    body = {
        k: v for k, v in params.items()
        if k in body_candidate_keys and k not in skip and v is not None
    }
    return body or None


class ToolRegistry:
    def __init__(self) -> None:
        self.spec: dict[str, Any] | None = None
        self.openapi_actions: dict[str, dict[str, str]] = {}
        self._load_openapi_spec()

    def _load_openapi_spec(self) -> None:
        if not OPENAPI_PATH.exists():
            self.spec = None
            self.openapi_actions = {}
            return
        try:
            self.spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        except Exception:
            self.spec = None
            self.openapi_actions = {}
            return

        paths = self.spec.get("paths") if isinstance(self.spec, dict) else None
        if not isinstance(paths, dict):
            self.openapi_actions = {}
            return

        generated: dict[str, dict[str, str]] = {}
        for path, methods in paths.items():
            if not isinstance(path, str) or not isinstance(methods, dict):
                continue
            for method, operation in methods.items():
                if method not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                if isinstance(operation_id, str) and operation_id.strip():
                    base_name = _normalize_action_name(operation_id)
                else:
                    base_name = _normalize_action_name(f"{method}_{path.replace('/', '_')}")
                action_name = f"school_{base_name}"
                generated[action_name] = {"method": method.upper(), "path": path}
        self.openapi_actions = generated

    async def call(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> Any:
        params = params or {}
        normalized_action = _normalize_action_name(action)

        # ── 1. 내장 도구 ────────────────────────────────────
        if normalized_action in {"web_search", "search_web"}:
            query = str(params.get("query") or params.get("q") or "")
            max_results = int(params.get("max_results") or 5)
            return await search_trusted_sources(query, max_results=max(1, min(max_results, 12)))

        if normalized_action in {"web_search_general", "search_general"}:
            query = str(params.get("query") or params.get("q") or "")
            max_results = int(params.get("max_results") or 5)
            return await search_general_sources(query, max_results=max(1, min(max_results, 12)))

        if normalized_action in {"web_search_academic", "search_academic"}:
            query = str(params.get("query") or params.get("q") or "")
            max_results = int(params.get("max_results") or 5)
            return await search_academic_sources(query, max_results=max(1, min(max_results, 12)))

        if normalized_action in {"notebooklm_generate", "notebooklm"}:
            run_id = str(params.get("run_id") or "tool-run")
            task = str(params.get("task") or "Untitled task")
            final_summary = str(params.get("final_summary") or params.get("summary") or "")
            return await generate_notebooklm_assets(run_id=run_id, task=task, final_summary=final_summary)

        if normalized_action in {"generate_pptx", "pptx_generate"}:
            try:
                from app.services.pptx_generator import generate_pptx_from_summary
            except Exception as exc:
                return {"status": "error", "error": f"pptx dependency unavailable: {str(exc)[:200]}"}
            run_id = str(params.get("run_id") or "tool-run")
            title = str(params.get("title") or "AgentGCS Output")
            sections = params.get("sections")
            if not isinstance(sections, list):
                sections = [str(params.get("summary") or "")]
            out_dir = str(params.get("out_dir") or "./outputs")
            pptx_path = generate_pptx_from_summary(
                run_id=run_id,
                title=title,
                sections=[str(item) for item in sections if str(item).strip()],
                out_dir=out_dir,
            )
            return {"status": "generated", "pptx_path": pptx_path}

        if normalized_action in {"upload_drive_file", "google_drive_upload"}:
            if not user_id:
                return {"error": "missing_user_id"}
            file_path = str(params.get("file_path") or "")
            return await upload_to_google_drive(file_path=file_path, user_id=user_id)

        if normalized_action in {"send_gmail", "gmail_send"}:
            if not user_id:
                return {"error": "missing_user_id"}
            return await send_gmail_notification(
                user_id=user_id,
                to_email=str(params.get("to_email") or ""),
                subject=str(params.get("subject") or "AgentGCS Notification"),
                body=str(params.get("body") or ""),
            )

        if normalized_action in {"create_calendar_event", "google_calendar_create"}:
            if not user_id:
                return {"error": "missing_user_id"}
            return await create_google_calendar_event(
                user_id=user_id,
                summary=str(params.get("summary") or ""),
                start_at=str(params.get("start_at") or ""),
                end_at=str(params.get("end_at") or ""),
                description=str(params.get("description") or ""),
                calendar_id=str(params.get("calendar_id") or "primary"),
            )

        # ── 2. SCHOOL_API_ALIASES: 자연어 이름 → School API ─
        #
        # openapi_actions의 긴 이름(school_me_auth_me_get 등)에 매핑되지 않는
        # 자연어 호출(get_my_info, leaderboard, list_rooms 등)을 여기서 잡는다.
        # ReActEngine이 Claude의 판단으로 생성한 액션 이름도 여기서 처리된다.
        if normalized_action in _NORMALIZED_ALIASES:
            if not user_id:
                return {"error": "missing_user_id"}
            return await self._call_school_alias(normalized_action, params, user_id)

        # ── 3. openapi_actions: 정확한 school_{operation_id} 이름 매칭 ─
        if normalized_action in self.openapi_actions:
            if not user_id:
                return {"error": "missing_user_id"}
            op = self.openapi_actions[normalized_action]
            path = op["path"]
            # path 파라미터 치환: params["path_params"] 또는 최상위 키에서
            path_params_dict = params.get("path_params") or {}
            if not isinstance(path_params_dict, dict):
                path_params_dict = {}
            # {param_name} 패턴을 찾아 치환
            for match in re.findall(r"\{([^}]+)\}", path):
                val = path_params_dict.get(match) or params.get(match)
                if val is not None:
                    path = path.replace("{" + match + "}", str(val))
            query = params.get("query")
            body = params.get("body")
            client = await get_school_client_for_user(user_id)
            return await client.request_openapi_path(
                method=op["method"],
                path=path,
                params=query if isinstance(query, dict) else None,
                json_body=body if isinstance(body, dict) else None,
            )

        # ── 4. 제네릭 school_api_call: method + path 직접 지정 ─
        #
        # chatbot_service.py의 _execute_school_api_actions()가
        # {"method": "GET", "path": "/leaderboards", "query": {...}} 형식으로 호출한다.
        if normalized_action in {"school_api", "school_api_call"}:
            if not user_id:
                return {"error": "missing_user_id"}
            method = str(params.get("method") or "GET").upper()
            path = str(params.get("path") or "/auth/me")
            # 경로 파라미터 템플릿 치환 (path_params 또는 최상위 키)
            path_params_dict = params.get("path_params") or {}
            if not isinstance(path_params_dict, dict):
                path_params_dict = {}
            for match in re.findall(r"\{([^}]+)\}", path):
                val = path_params_dict.get(match) or params.get(match)
                if val is not None:
                    path = path.replace("{" + match + "}", str(val))
            query = params.get("query")
            body = params.get("body")
            client = await get_school_client_for_user(user_id)
            return await client.request_openapi_path(
                method=method,
                path=path,
                params=query if isinstance(query, dict) else None,
                json_body=body if isinstance(body, dict) else None,
            )

        # ── 5. 도구 목록 조회 ───────────────────────────────
        if normalized_action in {"list_tools", "tools"}:
            builtin = [
                "web_search",
                "web_search_general",
                "web_search_academic",
                "school_api_call",
                "notebooklm_generate",
                "generate_pptx",
                "upload_drive_file",
                "send_gmail",
                "create_calendar_event",
            ]
            return {
                "builtin_tools": builtin,
                "school_api_aliases": _ALIAS_DISPLAY_LIST,
                "openapi_tools": sorted(self.openapi_actions.keys()),
                "usage_hint": (
                    "school_api_aliases의 이름을 직접 action으로 사용할 수 있습니다. "
                    "예) get_auth_me, list_meeting_rooms, create_room_reservation"
                ),
            }

        return {"error": "unknown_tool", "action": action, "normalized": normalized_action, "params": params}

    async def _call_school_alias(
        self,
        normalized_action: str,
        params: dict[str, Any],
        user_id: str,
    ) -> Any:
        """
        SCHOOL_API_ALIASES 테이블을 사용해 School API를 호출한다.

        파라미터 추출 전략:
          - 경로 파라미터: params["path_params"][name] 또는 params[name]
          - 쿼리 파라미터: params["query"] 또는 평탄 params (meta 키 제외)
          - 요청 바디:     params["body"] 또는 content/purpose 등 의미 키

        openapi.json에 명시된 경로를 그대로 사용하므로 allowlist 우회 없음.
        """
        alias = SCHOOL_API_ALIASES[normalized_action]
        method = alias["method"]
        path: str = alias["path"]
        path_param_names: list[str] = alias.get("path_params") or []

        # 경로 파라미터 치환
        path_param_values = _extract_path_params(params, path_param_names)
        for pname, pval in path_param_values.items():
            path = path.replace("{" + pname + "}", pval)

        # 미해결 경로 파라미터가 남아있으면 오류 반환
        unresolved = re.findall(r"\{([^}]+)\}", path)
        if unresolved:
            return {
                "error": "missing_path_params",
                "action": normalized_action,
                "missing": unresolved,
                "hint": f"파라미터를 params에 포함하세요. 예) {{{unresolved[0]}: 123}}",
            }

        # 쿼리/바디 추출
        query = _extract_query_params(params, path_param_names)
        body = _extract_body(params, path_param_names) if method in {"POST", "PUT", "PATCH"} else None

        client = await get_school_client_for_user(user_id)
        return await client.request_openapi_path(
            method=method,
            path=path,
            params=query,
            json_body=body,
        )


tool_registry = ToolRegistry()
