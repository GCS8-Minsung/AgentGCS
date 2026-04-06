from app.services.intent_classifier import (
    INTENT_DEEP_TASK,
    INTENT_GENERAL_CHAT,
    INTENT_TOOL_REQUIRED,
    heuristic_intent,
)


def test_heuristic_general_chat():
    decision = heuristic_intent("안녕, 오늘 기분 어때?")
    assert decision.intent == INTENT_GENERAL_CHAT
    assert decision.tools == []


def test_heuristic_tool_required_for_search():
    decision = heuristic_intent("인터넷 검색으로 성남 날씨 알려줘")
    assert decision.intent == INTENT_TOOL_REQUIRED
    assert "web_search" in decision.tools


def test_heuristic_tool_required_for_school_api():
    decision = heuristic_intent("GCS Pulse API로 내 정보(auth/me) 조회해줘")
    assert decision.intent == INTENT_TOOL_REQUIRED
    assert "school_api_call" in decision.tools
    assert decision.school_api_actions
    assert any(action["path"] == "/auth/me" and action["method"] == "GET" for action in decision.school_api_actions)


def test_openapi_based_intent_for_meeting_rooms_list():
    decision = heuristic_intent("/meeting-rooms GET 호출해서 회의실 목록 보여줘")
    assert decision.intent == INTENT_TOOL_REQUIRED
    assert "school_api_call" in decision.tools
    assert any(
        action["path"] == "/meeting-rooms" and action["method"] == "GET"
        for action in decision.school_api_actions
    )


def test_openapi_based_intent_for_meeting_room_reservation_create():
    decision = heuristic_intent("회의실 room_id=3 예약 생성해줘 start_at=2026-04-06T10:00:00+09:00")
    assert decision.intent == INTENT_TOOL_REQUIRED
    assert "school_api_call" in decision.tools
    assert any(
        action["path"] == "/meeting-rooms/{room_id}/reservations" and action["method"] == "POST"
        for action in decision.school_api_actions
    )


def test_heuristic_deep_task_detected():
    decision = heuristic_intent("멀티 에이전트로 종합 분석 보고서를 작성하고 단계별 실행 전략까지 정리해줘")
    assert decision.intent == INTENT_DEEP_TASK


def test_heuristic_deep_task_detected_with_drive_trigger_phrase():
    decision = heuristic_intent("과제를 진행할께. 연결된 구글 드라이브를 참고해서 진행해줘")
    assert decision.intent == INTENT_DEEP_TASK


def test_heuristic_deep_task_detected_with_compact_drive_trigger_phrase():
    decision = heuristic_intent("과제진행할께 연결된구글드라이브참고해서진행")
    assert decision.intent == INTENT_DEEP_TASK
