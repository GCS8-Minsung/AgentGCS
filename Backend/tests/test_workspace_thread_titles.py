from app.routers.workspace import _derive_thread_title, _is_placeholder_thread_title


def test_workspace_placeholder_title_detection():
    assert _is_placeholder_thread_title("새 대화")
    assert _is_placeholder_thread_title("새 워크플로우")
    assert not _is_placeholder_thread_title("프로젝트 킥오프 논의")


def test_workspace_derive_thread_title_trim_and_limit():
    title = _derive_thread_title("   다음 분기  시장 진입  전략   논의  ")
    assert title == "다음 분기 시장 진입 전략 논의"
