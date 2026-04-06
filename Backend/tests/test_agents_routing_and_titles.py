from app.routers.agents import (
    _choose_chat_provider,
    _derive_thread_title,
    _is_placeholder_thread_title,
)


def test_choose_chat_provider_prefers_openai_for_simple_chat():
    provider = _choose_chat_provider("안녕, 오늘 일정 간단히 정리해줘", "balanced")
    assert provider == "openai"


def test_choose_chat_provider_prefers_claude_for_complex_chat():
    provider = _choose_chat_provider("멀티 에이전트 기반으로 근거 비교 분석 계획을 작성해줘", "balanced")
    assert provider == "claude"


def test_placeholder_title_detection_supports_legacy_workflow_title():
    assert _is_placeholder_thread_title("새 워크플로우")
    assert _is_placeholder_thread_title("새 대화")
    assert not _is_placeholder_thread_title("실행 계획 리뷰")


def test_derive_thread_title_compacts_whitespace_and_limits_length():
    title = _derive_thread_title("   AI   토론  결과를   바탕으로   액션아이템 정리  ")
    assert title == "AI 토론 결과를 바탕으로 액션아이템 정리"
