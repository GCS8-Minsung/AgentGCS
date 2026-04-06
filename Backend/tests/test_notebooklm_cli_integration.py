import json
from pathlib import Path

import pytest

from app.services import notebooklm


@pytest.mark.asyncio
async def test_collect_notebooklm_evidence_uses_file_upload_and_positional_query(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    async def fake_run_nlm_command(args: list[str], *, timeout_sec: int = 150):
        _ = timeout_sec
        calls.append(list(args))
        if args[:2] == ["login", "--check"]:
            return 0, "account: jsiv1124@gachon.ac.kr", ""
        if args[:2] == ["notebook", "create"]:
            return 0, "ID: 11111111-1111-1111-1111-111111111111", ""
        if args[:2] == ["source", "add"]:
            return 0, "source added", ""
        if args[:2] == ["research", "start"]:
            return 0, "started", ""
        if args[:2] == ["research", "status"]:
            return 0, "completed", ""
        if args[:2] == ["research", "import"]:
            return 0, "imported", ""
        if args[:2] == ["notebook", "query"]:
            payload = {
                "value": {
                    "answer": "1. 독립 근거 A\n2. 독립 근거 B\n3. 리스크 C / 대응 C-1",
                }
            }
            return 0, json.dumps(payload, ensure_ascii=False), ""
        return 1, "", f"unexpected:{args}"

    monkeypatch.setattr(notebooklm, "_run_nlm_command", fake_run_nlm_command)

    result = await notebooklm.collect_notebooklm_evidence(
        run_id="run-001",
        task="양육시장 기회 과제",
        source_text="# pack\n\n입력 요약",
        output_dir=str(tmp_path),
        preferred_google_account="jsiv1124@gachon.ac.kr",
        search_query="양육시장 기회",
    )

    assert result["status"] == "generated"
    assert len(result["evidence"]) > 0
    assert any(cmd[:2] == ["source", "add"] and "--file" in cmd and "--text" not in cmd for cmd in calls)
    assert not any("--prompt" in cmd for cmd in calls)
    assert any(cmd[:2] == ["notebook", "query"] and "--prompt" not in cmd for cmd in calls)


@pytest.mark.asyncio
async def test_generate_notebooklm_assets_uses_file_upload_and_downloads_slide(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    notebook_id = "22222222-2222-2222-2222-222222222222"

    async def fake_run_nlm_command(args: list[str], *, timeout_sec: int = 150):
        _ = timeout_sec
        calls.append(list(args))
        if args[:2] == ["login", "--check"]:
            return 0, "account: jsiv1124@gachon.ac.kr", ""
        if args[:2] == ["notebook", "create"]:
            return 0, f"ID: {notebook_id}", ""
        if args[:2] == ["source", "add"]:
            return 0, "source added", ""
        if args[:2] == ["notebook", "query"]:
            payload = {"value": {"answer": "발표 요약 본문"}}
            return 0, json.dumps(payload, ensure_ascii=False), ""
        if args[:2] == ["slides", "create"]:
            return 0, "started", ""
        if args[:2] == ["status", "artifacts"]:
            payload = [{"id": "artifact-1", "type": "slide_deck", "status": "completed"}]
            return 0, json.dumps(payload, ensure_ascii=False), ""
        if args[:2] == ["download", "slide-deck"]:
            output_path = None
            if "-o" in args:
                output_path = args[args.index("-o") + 1]
            elif "--output" in args:
                output_path = args[args.index("--output") + 1]
            if output_path:
                p = Path(output_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"pptx-bytes")
            return 0, "downloaded", ""
        return 1, "", f"unexpected:{args}"

    monkeypatch.setattr(notebooklm, "_run_nlm_command", fake_run_nlm_command)

    result = await notebooklm.generate_notebooklm_assets(
        run_id="run-002",
        task="양육시장 발표자료",
        final_summary="최종 결론 텍스트",
        output_dir=str(tmp_path),
        transcript_text="토론 원문",
        preferred_google_account="jsiv1124@gachon.ac.kr",
    )

    assert result["status"] == "generated"
    assert result["slides_status"] == "generated"
    assert result["ppt_path"]
    assert Path(result["ppt_path"]).exists()
    assert any(cmd[:2] == ["source", "add"] and "--file" in cmd and "--text" not in cmd for cmd in calls)
    assert not any("--prompt" in cmd for cmd in calls)


@pytest.mark.asyncio
async def test_ensure_notebooklm_slides_capability_ready_success(monkeypatch):
    calls: list[list[str]] = []

    async def fake_run_nlm_command(args: list[str], *, timeout_sec: int = 150):
        _ = timeout_sec
        calls.append(list(args))
        if args[:2] == ["login", "--check"]:
            return 0, "account: jsiv1124@gachon.ac.kr", ""
        if args[:2] == ["notebook", "create"]:
            return 0, "ID: 33333333-3333-3333-3333-333333333333", ""
        if args[:2] == ["source", "add"]:
            return 0, "source added", ""
        if args[:2] == ["slides", "create"]:
            return 0, "started", ""
        if args[:2] == ["notebook", "delete"]:
            return 0, "deleted", ""
        return 1, "", f"unexpected:{args}"

    monkeypatch.setattr(notebooklm, "_run_nlm_command", fake_run_nlm_command)
    result = await notebooklm.ensure_notebooklm_slides_capability_ready(
        preferred_google_account="jsiv1124@gachon.ac.kr",
    )
    assert result["ready"] is True
    assert any(cmd[:2] == ["slides", "create"] for cmd in calls)


@pytest.mark.asyncio
async def test_ensure_notebooklm_slides_capability_ready_failure(monkeypatch):
    async def fake_run_nlm_command(args: list[str], *, timeout_sec: int = 150):
        _ = timeout_sec
        if args[:2] == ["login", "--check"]:
            return 0, "account: jsiv1124@gachon.ac.kr", ""
        if args[:2] == ["notebook", "create"]:
            return 0, "ID: 44444444-4444-4444-4444-444444444444", ""
        if args[:2] == ["source", "add"]:
            return 0, "source added", ""
        if args[:2] == ["slides", "create"]:
            return 1, "Error: Could not create slide deck.", ""
        if args[:2] == ["notebook", "delete"]:
            return 0, "deleted", ""
        return 1, "", "unexpected"

    monkeypatch.setattr(notebooklm, "_run_nlm_command", fake_run_nlm_command)
    result = await notebooklm.ensure_notebooklm_slides_capability_ready(
        preferred_google_account="jsiv1124@gachon.ac.kr",
    )
    assert result["ready"] is False
    assert result["status"] == "slides_unavailable"


@pytest.mark.asyncio
async def test_find_slides_capable_notebooklm_profile_switches_to_next_profile(monkeypatch):
    async def fake_list_notebooklm_profiles():
        return {
            "status": "ok",
            "profiles": ["default", "personal", "team"],
            "reason": None,
            "commands": [],
        }

    async def fake_probe(*, preferred_google_account: str | None = None):
        if preferred_google_account == "default":
            return {"ready": False, "status": "slides_unavailable", "reason": "quota"}
        if preferred_google_account == "personal":
            return {"ready": True, "status": "slides_available", "reason": None}
        return {"ready": False, "status": "slides_unavailable", "reason": "unknown"}

    monkeypatch.setattr(notebooklm, "list_notebooklm_profiles", fake_list_notebooklm_profiles)
    monkeypatch.setattr(notebooklm, "ensure_notebooklm_slides_capability_ready", fake_probe)

    result = await notebooklm.find_slides_capable_notebooklm_profile(
        preferred_profile="default",
        max_candidates=6,
    )
    assert result["ready"] is True
    assert result["selected_profile"] == "personal"
    assert [row["profile"] for row in result["attempts"]] == ["default", "personal"]


@pytest.mark.asyncio
async def test_find_slides_capable_notebooklm_profile_no_profiles_uses_current(monkeypatch):
    async def fake_list_notebooklm_profiles():
        return {
            "status": "ok",
            "profiles": [],
            "reason": None,
            "commands": [],
        }

    async def fake_probe(*, preferred_google_account: str | None = None):
        assert preferred_google_account is None
        return {"ready": True, "status": "slides_available", "reason": None}

    monkeypatch.setattr(notebooklm, "list_notebooklm_profiles", fake_list_notebooklm_profiles)
    monkeypatch.setattr(notebooklm, "ensure_notebooklm_slides_capability_ready", fake_probe)

    result = await notebooklm.find_slides_capable_notebooklm_profile(
        preferred_profile=None,
    )
    assert result["ready"] is True
    assert result["selected_profile"] is None
