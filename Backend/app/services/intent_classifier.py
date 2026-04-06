from __future__ import annotations

# ============================================================
# intent_classifier.py  –  AgentGCS 의도 분류기
#
# 듀얼 모델 라우팅 전략:
#   1단계 (빠른 휴리스틱)  : 규칙 기반 → 즉시 반환 (LLM 호출 없음)
#   2단계 (경량 LLM)       : gpt-5-mini 우선 사용 → 빠르고 저렴
#   3단계 (폴백)           : gpt-5-mini 실패 시 Claude Sonnet으로 후퇴
#
# 의도 종류:
#   INTENT_GENERAL_CHAT  – 일반 대화 → gpt-5-mini로 최종 생성
#   INTENT_TOOL_REQUIRED – 도구 필요 → Claude Sonnet으로 최종 생성
#   INTENT_DEEP_TASK     – 심층 복합 과제 → DeepTaskOrchestrator로 위임 (미래 확장)
# ============================================================

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from app.services.claude_service import ClaudeService


# ── 의도 상수 ──────────────────────────────────────────────
INTENT_GENERAL_CHAT = "general_chat"
INTENT_TOOL_REQUIRED = "tool_required"
INTENT_DEEP_TASK = "deep_task"          # [신규] 심층 복합 과제: 다중 단계/에이전트 필요

# ── 도구 이름 상수 ─────────────────────────────────────────
TOOL_WEB_SEARCH = "web_search"
TOOL_SCHOOL_API = "school_api_call"
TOOL_GMAIL_SEND = "send_gmail"
TOOL_CALENDAR_CREATE = "create_calendar_event"
TOOL_DRIVE_UPLOAD = "upload_drive_file"

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi_1000school.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
METHOD_HINTS: dict[str, tuple[str, ...]] = {
    "GET": ("조회", "보기", "보여", "목록", "리스트", "검색", "확인", "가져와", "list", "get", "show", "fetch", "search"),
    "POST": ("생성", "등록", "추가", "예약", "제출", "참여", "만들", "create", "add", "submit", "join", "book", "reserve"),
    "PUT": ("수정", "업데이트", "갱신", "변경", "update", "replace", "edit"),
    "PATCH": ("수정", "업데이트", "갱신", "변경", "rename", "mark", "patch"),
    "DELETE": ("삭제", "취소", "제거", "해지", "revoke", "delete", "remove", "cancel"),
}
API_MARKERS = (
    "api.1000.school",
    "gcs pulse",
    "openapi",
    "엔드포인트",
    "endpoint",
    "파라미터",
    "meeting-rooms",
    "snippets",
    "leaderboards",
    "peer-reviews",
    "tournaments",
    "/auth/",
    "/teams/",
    "/users/",
)
KOREAN_METHOD_MARKERS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}
STOP_TOKENS = {
    "api", "school", "1000", "gcs", "pulse",
    "the", "and", "for", "with", "from", "this", "that", "please",
}

# ── 심층 과제 감지 마커 ────────────────────────────────────
# 이 키워드들이 복수 등장하거나 메시지가 길면 INTENT_DEEP_TASK로 분류
DEEP_TASK_MARKERS = (
    "보고서", "리포트", "종합", "종합적", "심층", "상세히",
    "전략", "로드맵", "마스터플랜", "전체적으로",
    "분석해줘", "비교해줘", "정리해줘", "요약해줘",
    "단계별", "단계적", "순차적",
    "research", "analyze", "comprehensive", "detailed report",
    "compare and contrast", "step by step plan",
)
# 단독으로도 deep_task 확정 시그널이 되는 강력한 표현
DEEP_TASK_STRONG_MARKERS = (
    "전략 보고서", "종합 분석", "심층 분석", "detailed analysis",
    "comprehensive report", "멀티 에이전트", "multi-agent",
    "과제 수행할게", "과제 수행할께", "과제를 수행", "과제를 진행",
    "drive를 참고해서 진행", "구글 드라이브를 참고해서 진행",
)


# ── 데이터 클래스 ──────────────────────────────────────────

@dataclass(slots=True)
class IntentDecision:
    intent: str
    confidence: float = 0.0
    reason: str = ""
    tools: list[str] = field(default_factory=list)
    tool_query: str | None = None
    school_api_actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class OpenApiAction:
    action_name: str
    method: str
    path: str
    summary: str
    operation_id: str
    tags: list[str]
    path_params: list[str]
    query_params: list[str]
    required_query_params: list[str]
    body_required: bool
    tokens: set[str] = field(default_factory=set)


# ── 내부 유틸리티 ──────────────────────────────────────────

def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("{") and raw.endswith("}"):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _tokenize(text: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_/:-]+|[가-힣]{2,}", (text or "").lower())
    tokens: list[str] = []
    for token in raw_tokens:
        normalized = token.strip("_-/:")
        if not normalized or normalized in STOP_TOKENS:
            continue
        tokens.append(normalized)
    return tokens


def _split_path_tokens(path: str) -> list[str]:
    parts = [part for part in re.split(r"[/{}_-]+", path.lower()) if part]
    return [part for part in parts if part not in STOP_TOKENS]


def _normalize_action_name(name: str) -> str:
    lowered = name.strip().lower()
    lowered = re.sub(r"[^a-z0-9_]+", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered


def _operation_tokens(
    *,
    method: str,
    path: str,
    summary: str,
    operation_id: str,
    tags: list[str],
    path_params: list[str],
    query_params: list[str],
) -> set[str]:
    tokens: set[str] = set()
    for source in (
        " ".join([summary, operation_id, path, " ".join(tags), " ".join(path_params), " ".join(query_params)]),
    ):
        tokens.update(_tokenize(source))
    tokens.update(_split_path_tokens(path))
    for hint in METHOD_HINTS.get(method.upper(), ()):
        tokens.update(_tokenize(hint))
    return tokens


@lru_cache(maxsize=1)
def _load_openapi_actions() -> list[OpenApiAction]:
    if not OPENAPI_PATH.exists():
        return []
    try:
        spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []

    actions: list[OpenApiAction] = []
    for path, methods in paths.items():
        if not isinstance(path, str) or not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "").strip()
            summary = str(operation.get("summary") or operation.get("description") or "").strip()
            tags_raw = operation.get("tags")
            tags = [str(tag).strip() for tag in tags_raw if isinstance(tag, str)] if isinstance(tags_raw, list) else []
            params_raw = operation.get("parameters")
            path_params: list[str] = []
            query_params: list[str] = []
            required_query_params: list[str] = []
            if isinstance(params_raw, list):
                for prm in params_raw:
                    if not isinstance(prm, dict):
                        continue
                    prm_name = str(prm.get("name") or "").strip()
                    prm_in = str(prm.get("in") or "").strip().lower()
                    if not prm_name:
                        continue
                    if prm_in == "path":
                        path_params.append(prm_name)
                    elif prm_in == "query":
                        query_params.append(prm_name)
                        if bool(prm.get("required")):
                            required_query_params.append(prm_name)
            body_required = False
            request_body = operation.get("requestBody")
            if isinstance(request_body, dict):
                body_required = bool(request_body.get("required"))
            base_name = operation_id if operation_id else f"{method}_{path.replace('/', '_')}"
            action_name = f"school_{_normalize_action_name(base_name)}"
            actions.append(
                OpenApiAction(
                    action_name=action_name,
                    method=method.upper(),
                    path=path,
                    summary=summary,
                    operation_id=operation_id,
                    tags=tags,
                    path_params=path_params,
                    query_params=query_params,
                    required_query_params=required_query_params,
                    body_required=body_required,
                    tokens=_operation_tokens(
                        method=method.upper(),
                        path=path,
                        summary=summary,
                        operation_id=operation_id,
                        tags=tags,
                        path_params=path_params,
                        query_params=query_params,
                    ),
                )
            )
    return actions


def _detect_method_hint(message: str) -> str | None:
    lowered = (message or "").lower()
    for method_word, method in KOREAN_METHOD_MARKERS.items():
        if re.search(rf"\b{method_word}\b", lowered):
            return method
    for method, hints in METHOD_HINTS.items():
        if any(hint in lowered for hint in hints):
            return method
    return None


def _action_score(message: str, message_tokens: set[str], action: OpenApiAction, method_hint: str | None) -> float:
    lowered = message.lower()
    score = 0.0

    if action.path.lower() in lowered:
        score += 10.0
    path_alias = action.path.lower().lstrip("/")
    if path_alias and path_alias in lowered:
        score += 7.0

    explicit_paths = re.findall(r"/[a-zA-Z0-9][a-zA-Z0-9/_-]*", lowered)
    for candidate in explicit_paths:
        if action.path.lower().startswith(candidate):
            score += 7.5
            break
    explicit_paths_no_slash = re.findall(r"\b[a-zA-Z0-9_-]+/[a-zA-Z0-9_/-]+\b", lowered)
    for candidate in explicit_paths_no_slash:
        normalized_candidate = candidate.strip("/")
        if normalized_candidate and action.path.lower().lstrip("/").startswith(normalized_candidate):
            score += 6.5
            break

    overlaps = len(message_tokens.intersection(action.tokens))
    if overlaps:
        score += min(7.0, overlaps * 1.15)

    if action.summary and action.summary.lower() in lowered:
        score += 4.0

    if method_hint and method_hint == action.method:
        score += 2.2
    elif method_hint and method_hint != action.method:
        score -= 1.0

    for required in action.path_params + action.required_query_params:
        if required.lower() in lowered:
            score += 0.8

    if "내 정보" in message and action.path == "/auth/me":
        score += 4.0
    if "auth/me" in lowered and action.path == "/auth/me":
        score += 6.0
    if "회의실" in message and "meeting-rooms" in action.path:
        score += 3.0
    if "예약" in message and "reservations" in action.path:
        score += 3.0
    if "토큰" in message and "/auth/tokens" in action.path:
        score += 3.0
    if "스니펫" in message and "snippets" in action.path:
        score += 2.5
    if ("리더보드" in message or "순위" in message) and action.path == "/leaderboards":
        score += 2.8
    return score


def _detect_school_api_actions(message: str) -> list[dict[str, Any]]:
    actions = _load_openapi_actions()
    if not actions:
        return []
    msg = (message or "").strip()
    if not msg:
        return []
    message_tokens = set(_tokenize(msg))
    method_hint = _detect_method_hint(msg)

    scored: list[tuple[float, OpenApiAction]] = []
    for action in actions:
        score = _action_score(msg, message_tokens, action, method_hint)
        if score >= 3.2:
            scored.append((score, action))

    scored.sort(key=lambda row: row[0], reverse=True)
    selected: list[dict[str, Any]] = []
    for score, action in scored[:5]:
        selected.append(
            {
                "action": action.action_name,
                "method": action.method,
                "path": action.path,
                "summary": action.summary or action.operation_id or action.path,
                "operation_id": action.operation_id,
                "path_params": action.path_params,
                "query_params": action.query_params,
                "required_query_params": action.required_query_params,
                "body_required": action.body_required,
                "confidence": round(min(0.99, 0.5 + score / 20), 3),
            }
        )
    return selected


def _looks_like_api_request(message: str) -> bool:
    lowered = (message or "").lower()
    if any(marker in lowered for marker in API_MARKERS):
        return True
    if re.search(r"/[a-zA-Z0-9][a-zA-Z0-9/_-]*", message):
        return True
    return False


def _is_deep_task(text: str, lowered: str) -> bool:
    """
    메시지가 '심층 복합 과제(INTENT_DEEP_TASK)'에 해당하는지 판별한다.

    판별 기준:
    - 강력한 단일 마커가 있으면 즉시 True
    - 일반 마커가 2개 이상 & 메시지가 150자 이상이면 True
    - 단순 도구 요청(API/검색/메일/캘린더)과 겹치지 않도록 음성 필터 적용
    """
    normalized = re.sub(r"\s+", "", lowered)

    # 강력 단독 마커 체크 (공백 변형 포함)
    for marker in DEEP_TASK_STRONG_MARKERS:
        marker_norm = re.sub(r"\s+", "", marker.lower())
        if marker.lower() in lowered or (marker_norm and marker_norm in normalized):
            return True

    # 트리거 패턴: 과제 + (진행/수행/시작) + (구글 드라이브/drive/input)
    task_tokens = ("과제", "assignment", "task")
    action_tokens = ("진행", "수행", "시작", "해결", "토론")
    drive_tokens = (
        "드라이브",
        "구글드라이브",
        "googledrive",
        "google drive",
        "drive",
        "input폴더",
        "inputfolder",
        "input folder",
    )
    has_task = any(token in lowered or token in normalized for token in task_tokens)
    has_action = any(token in lowered or token in normalized for token in action_tokens)
    has_drive = any(token in lowered or token in normalized for token in drive_tokens)
    if has_task and has_action and has_drive:
        return True

    # 사용자 트리거 문장(짧은 명령형)도 Deep Task로 본다.
    trigger_pairs = [
        ("과제", "진행"),
        ("과제", "수행"),
        ("drive", "참고"),
        ("드라이브", "참고"),
    ]
    hit_pairs = sum(
        1
        for a, b in trigger_pairs
        if (a in lowered and b in lowered)
        or (a in normalized and b in normalized)
    )
    if hit_pairs >= 2 and len(text) >= 12:
        return True

    # 일반 마커 카운트
    marker_count = sum(1 for m in DEEP_TASK_MARKERS if m in lowered)
    if marker_count >= 2 and len(text) >= 150:
        return True

    return False


def heuristic_intent(message: str) -> IntentDecision:
    """
    LLM 없이 규칙 기반으로 의도를 빠르게 분류한다.
    신뢰도가 충분하면 이 결과를 그대로 사용하여 API 비용을 절약한다.

    우선순위:
    1. INTENT_DEEP_TASK  – 심층 분석/보고서/전략 등
    2. INTENT_TOOL_REQUIRED – API/검색/Gmail/캘린더/Drive
    3. INTENT_GENERAL_CHAT – 그 외 일반 대화
    """
    text = (message or "").strip()
    lowered = text.lower()

    # ── 1. 심층 과제 감지 ───────────────────────────────────
    # 단순 도구 요청과 겹칠 경우 tool_required를 우선시한다
    if _is_deep_task(text, lowered):
        return IntentDecision(
            intent=INTENT_DEEP_TASK,
            confidence=0.70,
            reason="heuristic_deep_task",
            tools=[TOOL_WEB_SEARCH],     # 심층 과제는 대개 웹 검색 포함
            tool_query=text[:600],
            school_api_actions=[],
        )

    # ── 2. 도구 필요 감지 ──────────────────────────────────
    tool_markers = [
        "검색", "웹", "인터넷", "api", "endpoint", "엔드포인트",
        "호출", "조회", "가져와", "request", "auth/me", "openapi",
        "gcs pulse", "api.1000.school",
        "gmail", "메일", "calendar", "캘린더", "일정", "drive", "드라이브", "업로드",
    ]
    school_api_actions = _detect_school_api_actions(text)
    wants_tool = any(marker in lowered for marker in tool_markers) or bool(school_api_actions)

    if not wants_tool:
        return IntentDecision(
            intent=INTENT_GENERAL_CHAT,
            confidence=0.62,
            reason="heuristic_general",
            tools=[],
            tool_query=None,
            school_api_actions=[],
        )

    tools: list[str] = []
    if school_api_actions or _looks_like_api_request(text):
        tools.append(TOOL_SCHOOL_API)
    if any(marker in lowered for marker in ["검색", "웹", "인터넷", "latest", "최신"]):
        tools.append(TOOL_WEB_SEARCH)
    if any(marker in lowered for marker in ["gmail", "메일", "email", "이메일"]):
        tools.append(TOOL_GMAIL_SEND)
    if any(marker in lowered for marker in ["calendar", "캘린더", "일정", "schedule"]):
        tools.append(TOOL_CALENDAR_CREATE)
    if any(marker in lowered for marker in ["drive", "드라이브", "업로드", "upload"]):
        tools.append(TOOL_DRIVE_UPLOAD)
    if not tools:
        tools = [TOOL_WEB_SEARCH] if not school_api_actions else [TOOL_SCHOOL_API]

    confidence = 0.68
    if school_api_actions:
        confidence = min(0.96, 0.74 + (0.03 * min(4, len(school_api_actions))))

    return IntentDecision(
        intent=INTENT_TOOL_REQUIRED,
        confidence=confidence,
        reason="heuristic_tool_required_with_openapi",
        tools=list(dict.fromkeys(tools)),
        tool_query=text[:600],
        school_api_actions=school_api_actions,
    )


# ── 의도 분류기 ────────────────────────────────────────────

class IntentClassifier:
    """
    듀얼 모델 라우팅 의도 분류기.

    분류 흐름:
      1. heuristic_intent() — LLM 없이 규칙 기반 빠른 판단 (신뢰도 충분 시 즉시 반환)
      2. _classify_with_fast_model() — gpt-5-mini로 정밀 분류 (저비용)
      3. claude.generate() — gpt-5-mini 실패 시 Claude Sonnet 폴백
    """

    def __init__(self, claude: ClaudeService) -> None:
        self.claude = claude

    async def classify(self, *, message: str, use_mock: bool) -> IntentDecision:
        """
        메시지의 의도를 분류한다.

        fast-path: 휴리스틱이 TOOL_REQUIRED + school_api_actions 또는 confidence >= 0.72 이면
                   LLM 호출 없이 즉시 반환 → 토큰 낭비 없음.
        """
        heuristic = heuristic_intent(message)

        # 심층 과제는 휴리스틱만으로 충분 (LLM 분류 생략)
        if heuristic.intent == INTENT_DEEP_TASK:
            return heuristic

        # 도구 필요 + 고신뢰도 → 즉시 반환
        if heuristic.intent == INTENT_TOOL_REQUIRED and (
            heuristic.school_api_actions or heuristic.confidence >= 0.72
        ):
            return heuristic

        # ── LLM 분류 단계: gpt-5-mini 우선 사용 ────────────
        action_preview = _detect_school_api_actions(message)[:12]
        action_lines = [
            f"- {item['method']} {item['path']} :: {item.get('summary', '')} "
            f"(path_params={item.get('path_params', [])}, query_params={item.get('query_params', [])})"
            for item in action_preview
        ]
        action_catalog_text = "\n".join(action_lines) if action_lines else "- (no strong API action candidate)"
        system_prompt = (
            "You are an intent classifier for a chatbot.\n"
            "Return STRICT JSON only with keys:\n"
            f"intent: one of [{INTENT_GENERAL_CHAT}, {INTENT_TOOL_REQUIRED}, {INTENT_DEEP_TASK}]\n"
            "confidence: number 0~1\n"
            "reason: short string\n"
            "tools: array of tool names from [web_search, school_api_call, send_gmail, create_calendar_event, upload_drive_file]\n"
            "tool_query: string or null\n"
            "school_api_actions: array of objects with keys "
            "[method, path, summary, path_params, query_params, required_query_params, body_required]\n"
            f"Use '{INTENT_DEEP_TASK}' when the request requires multi-step research, "
            "comprehensive reports, or deep analysis that cannot be answered in one tool call.\n"
            "No markdown, no extra text."
        )
        user_prompt = (
            f"User message:\n{message}\n\n"
            "Classify intent and suggest tools only when needed.\n"
            "OpenAPI candidate actions (derived from path/method/summary/parameters):\n"
            f"{action_catalog_text}"
        )

        # gpt-5-mini 우선 시도 (빠르고 저렴)
        response: str | None = None
        if not use_mock:
            response = await self._classify_with_fast_model(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

        # gpt-5-mini 실패 시 Claude Sonnet 폴백
        if response is None:
            try:
                response = await self.claude.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    use_mock=use_mock,
                    cache_hint="intent-classifier",
                )
            except Exception:
                return heuristic

        return self._parse_classify_response(response, heuristic)

    async def _classify_with_fast_model(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str | None:
        """
        gpt-5-mini를 사용하여 의도를 분류한다.
        ClaudeService에 저장된 OpenAI 자격증명을 재사용하여 별도 설정 불필요.

        실패(키 없음/타임아웃/오류) 시 None을 반환하여 호출자가 폴백하도록 한다.
        """
        openai_key = (self.claude.openai_api_key or "").strip()
        if not openai_key:
            return None

        model = self.claude.openai_fallback_model or "gpt-5-mini"
        fallback_url = self.claude.openai_fallback_url or "https://api.openai.com/v1/chat/completions"

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,        # 의도 분류는 결정론적으로
            "max_tokens": 400,         # JSON 응답은 짧다
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(fallback_url, headers=headers, json=payload)
            if resp.status_code == 400:
                # json_object 미지원 모델 대비 재시도
                payload.pop("response_format", None)
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(fallback_url, headers=headers, json=payload)
            if resp.status_code >= 400:
                return None
            data = resp.json() if resp.text else {}
            choice = (data.get("choices") or [])[0]
            message = choice.get("message") if isinstance(choice, dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception:
            pass
        return None

    def _parse_classify_response(
        self,
        response: str,
        heuristic: IntentDecision,
    ) -> IntentDecision:
        """LLM 응답 JSON을 파싱하여 IntentDecision으로 변환한다."""
        try:
            parsed = _extract_json_object(response)
            if not isinstance(parsed, dict):
                return heuristic

            intent = str(parsed.get("intent") or "").strip()
            confidence = float(parsed.get("confidence") or 0.0)
            reason = str(parsed.get("reason") or "").strip()
            raw_tools = parsed.get("tools")
            tools = (
                [str(item) for item in raw_tools if isinstance(item, str)]
                if isinstance(raw_tools, list)
                else []
            )
            tool_query = parsed.get("tool_query")
            tool_query_text = str(tool_query).strip() if isinstance(tool_query, str) else None

            raw_actions = parsed.get("school_api_actions")
            school_actions: list[dict[str, Any]] = []
            if isinstance(raw_actions, list):
                for action in raw_actions[:5]:
                    if not isinstance(action, dict):
                        continue
                    method = str(action.get("method") or "").upper().strip()
                    path = str(action.get("path") or "").strip()
                    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not path.startswith("/"):
                        continue
                    school_actions.append(
                        {
                            "method": method,
                            "path": path,
                            "summary": str(action.get("summary") or ""),
                            "path_params": [str(v) for v in action.get("path_params", []) if isinstance(v, str)]
                            if isinstance(action.get("path_params"), list)
                            else [],
                            "query_params": [str(v) for v in action.get("query_params", []) if isinstance(v, str)]
                            if isinstance(action.get("query_params"), list)
                            else [],
                            "required_query_params": [
                                str(v) for v in action.get("required_query_params", []) if isinstance(v, str)
                            ]
                            if isinstance(action.get("required_query_params"), list)
                            else [],
                            "body_required": bool(action.get("body_required", False)),
                        }
                    )

            valid_intents = {INTENT_GENERAL_CHAT, INTENT_TOOL_REQUIRED, INTENT_DEEP_TASK}
            if intent not in valid_intents:
                return heuristic

            merged_actions = school_actions or heuristic.school_api_actions
            merged_tools = tools[:3]
            if merged_actions and TOOL_SCHOOL_API not in merged_tools:
                merged_tools.append(TOOL_SCHOOL_API)

            return IntentDecision(
                intent=intent,
                confidence=max(0.0, min(1.0, confidence)),
                reason=reason[:180],
                tools=merged_tools,
                tool_query=(tool_query_text[:600] if tool_query_text else None),
                school_api_actions=merged_actions,
            )
        except Exception:
            return heuristic
