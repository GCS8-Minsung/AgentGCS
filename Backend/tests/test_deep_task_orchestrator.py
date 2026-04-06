import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.models.schemas import DeepTaskRequest, PersonaStats
from app.services.multi_agent_graph import DeepTaskOrchestrator


class DummyWSManager:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, user_id: str, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append(
            {"user_id": user_id, "event_type": event_type, "payload": payload, "run_id": run_id}
        )


class FakeClaude:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        use_mock: bool,
        cache_hint: str = "default",
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "use_mock": use_mock,
                "cache_hint": cache_hint,
            }
        )
        if "최종 결론 작성자" in system_prompt:
            if "정체 대응 강제 결정" in system_prompt:
                return "default-balanced final decision"
            return "regular final decision"
        return "동일 주장 [S1]"


def _persona(name: str, pid: str) -> dict:
    return {
        "id": pid,
        "name": name,
        "stats": {
            "creativity": 70,
            "logic": 70,
            "critical_thinking": 70,
            "data_dependency": 70,
            "cautiousness": 70,
            "drive": 70,
        },
    }


def _user_settings(rounds: int) -> dict:
    return {
        "discussion_rounds": rounds,
        "personas": [
            _persona("기본 균형형", "default-balanced"),
            _persona("분석가", "analyst"),
            _persona("전략가", "strategist"),
        ],
    }


def _request(task: str = "시장 진입 전략 수립") -> DeepTaskRequest:
    return DeepTaskRequest(
        task=task,
        persona_stats=PersonaStats(),
        worker_count=3,
        trigger_source="console",
        use_mock=False,
    )


def _install_test_monkeypatches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_general_sources(_query: str, max_results: int = 8):
        return [
            {"title": "General Source", "url": "https://example.com/general", "snippet": "general"}
        ][:max_results]

    async def fake_academic_sources(_query: str, max_results: int = 6):
        return [
            {"title": "Academic Source", "url": "https://example.com/academic", "snippet": "academic"}
        ][:max_results]

    async def fake_notebooklm_assets(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path = out_dir / f"{kwargs['run_id']}-notebooklm-summary.md"
        transcript_path = out_dir / f"{kwargs['run_id']}-discussion.txt"
        slide_path = out_dir / f"{kwargs['run_id']}-notebooklm.pptx"
        summary_path.write_text("notebook summary", encoding="utf-8")
        transcript_path.write_text("notebook transcript", encoding="utf-8")
        slide_path.write_bytes(b"notebook-pptx")
        return {
            "status": "generated",
            "summary_path": str(summary_path),
            "transcript_path": str(transcript_path),
            "slides_status": "generated",
            "ppt_path": str(slide_path),
        }

    async def fake_collect_notebook_evidence(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        source_pack_path = out_dir / "notebook_source_pack.md"
        evidence_json_path = out_dir / "notebook_evidence.json"
        evidence_md_path = out_dir / "notebook_evidence.md"
        source_pack_path.write_text("source pack", encoding="utf-8")
        evidence_payload = {
            "run_id": kwargs["run_id"],
            "task": kwargs["task"],
            "evidence": [
                {
                    "title": "Notebook Evidence 1",
                    "url": "",
                    "snippet": "NotebookLM-derived evidence",
                    "source": "notebooklm_query",
                    "query_label": "핵심 사실",
                }
            ],
        }
        evidence_json_path.write_text(json.dumps(evidence_payload, ensure_ascii=False), encoding="utf-8")
        evidence_md_path.write_text("# Notebook Evidence", encoding="utf-8")
        return {
            "status": "generated",
            "run_id": kwargs["run_id"],
            "source_pack_path": str(source_pack_path),
            "evidence_json_path": str(evidence_json_path),
            "evidence_md_path": str(evidence_md_path),
            "evidence": evidence_payload["evidence"],
            "query_outputs": [{"label": "핵심 사실", "status": "ok", "text": "..."},],
        }

    async def fake_upload_artifacts(*, user_id: str, run_id: str, file_paths: list[str]) -> dict:
        _ = user_id
        _ = file_paths
        return {
            "status": "live",
            "run_id": run_id,
            "output_root_folder_id": "root-folder",
            "run_folder_id": "run-folder",
            "folder_url": "https://drive.google.com/drive/folders/run-folder",
            "files": [{"status": "live", "file_name": "artifact"}],
        }

    async def fake_identity(_user_id: str) -> dict:
        return {"status": "live", "email": "user@example.com"}

    async def fake_drive_input_summary(*, user_id: str, max_files: int = 8, max_chars_per_file: int = 1200):
        _ = user_id
        _ = max_files
        _ = max_chars_per_file
        return {
            "status": "live",
            "folder_id": "input-folder",
            "folder_name": "input",
            "folder_url": "https://drive.google.com/drive/folders/input-folder",
            "files": [
                {
                    "file_id": "file-1",
                    "name": "brief.md",
                    "mime_type": "text/markdown",
                    "modified_time": "2026-04-06T09:00:00Z",
                    "drive_url": "https://drive.google.com/file/d/file-1/view",
                    "snippet": "input summary content",
                    "has_text": True,
                }
            ],
            "summary_markdown": "# Google Drive Input Folder Summary\n\n- [D1] brief.md\n",
            "error": None,
        }

    async def fake_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.multi_agent_graph.search_general_sources", fake_general_sources)
    monkeypatch.setattr("app.services.multi_agent_graph.search_academic_sources", fake_academic_sources)
    monkeypatch.setattr("app.services.multi_agent_graph.collect_notebooklm_evidence", fake_collect_notebook_evidence)
    monkeypatch.setattr("app.services.multi_agent_graph.generate_notebooklm_assets", fake_notebooklm_assets)
    monkeypatch.setattr(
        "app.services.multi_agent_graph.upload_artifacts_to_google_drive", fake_upload_artifacts
    )
    monkeypatch.setattr("app.services.multi_agent_graph.get_connected_google_oauth_identity", fake_identity)
    monkeypatch.setattr("app.services.multi_agent_graph.collect_google_drive_input_summary", fake_drive_input_summary)
    monkeypatch.setattr("app.services.multi_agent_graph.save_session", fake_noop)
    monkeypatch.setattr("app.services.multi_agent_graph.append_session_message", fake_noop)
    monkeypatch.setattr("app.services.multi_agent_graph.save_agent_log", fake_noop)
    monkeypatch.setattr(settings, "notebooklm_output_dir", str(tmp_path))


@pytest.mark.asyncio
async def test_orchestrator_requires_at_least_three_personas():
    ws = DummyWSManager()
    claude = FakeClaude()
    orchestrator = DeepTaskOrchestrator(ws, claude)
    with pytest.raises(ValueError):
        await orchestrator.run(
            user_id="user-a",
            run_id="run-a",
            request=_request(),
            user_settings={
                "discussion_rounds": 3,
                "personas": [
                    _persona("기본 균형형", "default-balanced"),
                    _persona("분석가", "analyst"),
                ],
            },
        )


@pytest.mark.asyncio
async def test_orchestrator_stagnation_switches_to_default_balanced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _install_test_monkeypatches(monkeypatch, tmp_path)

    ws = DummyWSManager()
    claude = FakeClaude()
    orchestrator = DeepTaskOrchestrator(ws, claude)

    result = await orchestrator.run(
        user_id="user-b",
        run_id="run-b",
        request=_request(),
        user_settings=_user_settings(rounds=5),
    )

    assert result["final_summary"] == "default-balanced final decision"
    assert any(
        "정체 대응 강제 결정" in call["system_prompt"] for call in claude.calls if "최종 결론 작성자" in call["system_prompt"]
    )

    full_log_path = Path(result["artifacts"]["logs"]["json"])
    assert full_log_path.exists()
    payload = json.loads(full_log_path.read_text(encoding="utf-8"))
    assert payload["stagnation_detected"] is True
    assert payload["executed_rounds"] == 3
    assert len(payload["turns"]) == 9

    drive = result["artifacts"]["drive"]
    assert drive["folder_url"].startswith("https://drive.google.com/drive/folders/")
    assert isinstance(drive["files"], list) and drive["files"]


@pytest.mark.asyncio
async def test_run_and_stream_emits_artifacts_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _install_test_monkeypatches(monkeypatch, tmp_path)

    ws = DummyWSManager()
    claude = FakeClaude()
    orchestrator = DeepTaskOrchestrator(ws, claude)

    await orchestrator.run_and_stream(
        user_id="user-c",
        run_id="run-c",
        request=_request("운영 자동화 개선안 작성"),
        claude_override=claude,
        user_settings=_user_settings(rounds=2),
    )

    event_types = [row["event_type"] for row in ws.events]
    assert "deep_task.artifacts_ready" in event_types
    assert "deep_task.completed" in event_types
    completed = next(row for row in ws.events if row["event_type"] == "deep_task.completed")
    artifacts = completed["payload"]["artifacts"]
    logs = artifacts["logs"]
    assert logs["drive_input_json"]
    assert logs["drive_input_markdown"]
    assert logs["notebook_source_pack"]
    assert logs["notebook_evidence_json"]
    assert logs["notebook_evidence_markdown"]


@pytest.mark.asyncio
async def test_orchestrator_falls_back_to_web_evidence_when_notebook_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _install_test_monkeypatches(monkeypatch, tmp_path)

    async def fake_collect_notebook_evidence_fail(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        source_pack_path = out_dir / "notebook_source_pack.md"
        evidence_json_path = out_dir / "notebook_evidence.json"
        evidence_md_path = out_dir / "notebook_evidence.md"
        source_pack_path.write_text("source pack", encoding="utf-8")
        evidence_json_path.write_text("{}", encoding="utf-8")
        evidence_md_path.write_text("# fallback", encoding="utf-8")
        return {
            "status": "mocked",
            "run_id": kwargs["run_id"],
            "source_pack_path": str(source_pack_path),
            "evidence_json_path": str(evidence_json_path),
            "evidence_md_path": str(evidence_md_path),
            "evidence": [],
            "reason": "query_failed",
        }

    monkeypatch.setattr(
        "app.services.multi_agent_graph.collect_notebooklm_evidence",
        fake_collect_notebook_evidence_fail,
    )

    ws = DummyWSManager()
    claude = FakeClaude()
    orchestrator = DeepTaskOrchestrator(ws, claude)

    result = await orchestrator.run(
        user_id="user-d",
        run_id="run-d",
        request=_request("NotebookLM 실패 폴백 검증"),
        user_settings=_user_settings(rounds=2),
    )

    assert result["artifacts"]["evidence_source"] == "web_fallback"
    assert result["evidence"]


@pytest.mark.asyncio
async def test_orchestrator_prefers_notebooklm_slide_when_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _install_test_monkeypatches(monkeypatch, tmp_path)

    async def fake_notebooklm_assets_with_slide(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path = out_dir / f"{kwargs['run_id']}-notebooklm-summary.md"
        transcript_path = out_dir / f"{kwargs['run_id']}-discussion.txt"
        slide_path = out_dir / f"{kwargs['run_id']}-notebooklm.pptx"
        summary_path.write_text("notebook summary", encoding="utf-8")
        transcript_path.write_text("notebook transcript", encoding="utf-8")
        slide_path.write_bytes(b"notebook-pptx")
        return {
            "status": "generated",
            "summary_path": str(summary_path),
            "transcript_path": str(transcript_path),
            "ppt_path": str(slide_path),
            "slides_status": "generated",
        }

    monkeypatch.setattr(
        "app.services.multi_agent_graph.generate_notebooklm_assets",
        fake_notebooklm_assets_with_slide,
    )

    ws = DummyWSManager()
    claude = FakeClaude()
    orchestrator = DeepTaskOrchestrator(ws, claude)

    result = await orchestrator.run(
        user_id="user-e",
        run_id="run-e",
        request=_request("NotebookLM 슬라이드 우선 경로 검증"),
        user_settings=_user_settings(rounds=2),
    )

    artifacts = result["artifacts"]
    assert artifacts["presentation_source"] == "notebooklm_cli"
    assert artifacts["pptx_path"].endswith("-notebooklm.pptx")


@pytest.mark.asyncio
async def test_orchestrator_fails_when_notebooklm_slide_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _install_test_monkeypatches(monkeypatch, tmp_path)

    async def fake_notebooklm_assets_without_slide(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path = out_dir / f"{kwargs['run_id']}-notebooklm-summary.md"
        transcript_path = out_dir / f"{kwargs['run_id']}-discussion.txt"
        summary_path.write_text("notebook summary", encoding="utf-8")
        transcript_path.write_text("notebook transcript", encoding="utf-8")
        return {
            "status": "partial",
            "summary_path": str(summary_path),
            "transcript_path": str(transcript_path),
            "slides_status": "mocked",
            "slides_reason": "slide_not_ready",
            "ppt_path": None,
        }

    monkeypatch.setattr(
        "app.services.multi_agent_graph.generate_notebooklm_assets",
        fake_notebooklm_assets_without_slide,
    )

    ws = DummyWSManager()
    claude = FakeClaude()
    orchestrator = DeepTaskOrchestrator(ws, claude)

    with pytest.raises(RuntimeError):
        await orchestrator.run(
            user_id="user-f",
            run_id="run-f",
            request=_request("NotebookLM 슬라이드 필수 경로 검증"),
            user_settings=_user_settings(rounds=2),
        )
