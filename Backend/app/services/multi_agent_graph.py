from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.config import settings
from app.core.supabase_client import append_session_message, save_agent_log, save_session
from app.models.schemas import DeepTaskRequest, PersonaStats
from app.services.claude_service import ClaudeService
from app.services.dev_store import DEFAULT_PERSONA
from app.services.integrations import (
    collect_google_drive_input_summary,
    get_connected_google_oauth_identity,
    send_gmail_notification,
    upload_artifacts_to_google_drive,
)
from app.services.notebooklm import collect_notebooklm_evidence, generate_notebooklm_assets
from app.services.websocket_manager import WebSocketManager
from app.tools.web_search import search_academic_sources, search_general_sources


@dataclass(slots=True)
class DebatePersona:
    persona_id: str
    name: str
    stats: dict[str, int]
    focus: str


@dataclass(slots=True)
class DebateTurn:
    round_number: int
    turn_index: int
    persona_id: str
    persona_name: str
    focus: str
    message: str
    created_at: str


class RunCancelledError(RuntimeError):
    pass


class DeepTaskOrchestrator:
    def __init__(self, ws_manager: WebSocketManager, claude_service: ClaudeService) -> None:
        self.ws_manager = ws_manager
        self.claude_service = claude_service
        self._cancel_lock = asyncio.Lock()
        self._cancelled_runs: set[str] = set()

    async def request_cancel(self, *, run_id: str) -> bool:
        key = str(run_id or "").strip()
        if not key:
            return False
        async with self._cancel_lock:
            self._cancelled_runs.add(key)
        return True

    async def run_and_stream(
        self,
        *,
        user_id: str,
        run_id: str,
        request: DeepTaskRequest,
        claude_override: ClaudeService | None = None,
        user_settings: dict[str, Any] | None = None,
    ) -> None:
        active_claude = claude_override or self.claude_service

        async def stream_hook(event_type: str, payload: dict[str, Any]) -> None:
            await self.ws_manager.emit(user_id, event_type, payload, run_id=run_id)

        try:
            result = await self._run_pipeline(
                user_id=user_id,
                run_id=run_id,
                request=request,
                claude=active_claude,
                stream_hook=stream_hook,
                user_settings=user_settings or {},
            )
            await stream_hook(
                "deep_task.completed",
                {
                    "final_summary": result["final_summary"],
                    "arguments": result["arguments"],
                    "sources": result["evidence"],
                    "artifacts": result["artifacts"],
                },
            )
        except RunCancelledError as exc:
            await stream_hook(
                "deep_task.cancelled",
                {"message": str(exc) or "사용자 중단 요청으로 작업이 취소되었습니다."},
            )
        except Exception as exc:
            await stream_hook(
                "deep_task.failed",
                {
                    "message": "멀티 에이전트 파이프라인 실행 중 오류가 발생했습니다.",
                    "error": str(exc),
                },
            )
        finally:
            await self._clear_cancel(run_id)

    async def run(
        self,
        *,
        user_id: str,
        run_id: str,
        request: DeepTaskRequest,
        claude_override: ClaudeService | None = None,
        user_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async def noop(_event_type: str, _payload: dict[str, Any]) -> None:
            return

        try:
            return await self._run_pipeline(
                user_id=user_id,
                run_id=run_id,
                request=request,
                claude=claude_override or self.claude_service,
                stream_hook=noop,
                user_settings=user_settings or {},
            )
        finally:
            await self._clear_cancel(run_id)

    async def _run_pipeline(
        self,
        *,
        user_id: str,
        run_id: str,
        request: DeepTaskRequest,
        claude: ClaudeService,
        stream_hook: Callable[[str, dict[str, Any]], Awaitable[None]],
        user_settings: dict[str, Any],
    ) -> dict[str, Any]:
        await self._raise_if_cancelled(run_id)
        debate_personas, default_balanced = self._resolve_personas(user_settings)
        if len(debate_personas) < 3:
            raise ValueError(
                "과제 자동화를 시작하려면 설정에서 과제 해결용 페르소나를 최소 3개 준비해야 합니다."
            )

        discussion_rounds = self._resolve_discussion_rounds(user_settings)
        run_output_dir = Path(settings.notebooklm_output_dir).resolve() / run_id
        run_output_dir.mkdir(parents=True, exist_ok=True)

        try:
            await save_session(
                {
                    "id": run_id,
                    "user_id": user_id,
                    "title": f"완전자율 실행: {request.task[:60]}",
                    "task": request.task,
                    "autonomy_mode": "autonomous",
                }
            )
            await append_session_message(
                session_id=run_id,
                user_id=user_id,
                role="system",
                content=f"[system] 완전자율 실행 시작 ({request.trigger_source}): {request.task}",
                metadata={"run_id": run_id, "trigger_source": request.trigger_source},
            )
        except Exception:
            pass

        await stream_hook(
            "deep_task.started",
            {
                "message": "멀티 에이전트 실행을 시작합니다. 계정/입력 폴더 점검 후 토론을 시작합니다.",
                "task": request.task,
                "trigger_source": request.trigger_source,
                "worker_count": len(debate_personas),
                "discussion_rounds": discussion_rounds,
                "notebooklm_profile": str(user_settings.get("notebooklm_profile") or "").strip() or None,
                "personas": [
                    {"id": p.persona_id, "name": p.name, "focus": p.focus} for p in debate_personas
                ],
            },
        )

        await self._raise_if_cancelled(run_id)
        oauth_identity = await get_connected_google_oauth_identity(user_id)
        oauth_email = (
            str(oauth_identity.get("email") or "").strip()
            if oauth_identity.get("status") == "live"
            else None
        )
        configured_notebook_profile = str(user_settings.get("notebooklm_profile") or "").strip() or None
        preferred_notebook_profile = configured_notebook_profile or oauth_email
        await stream_hook(
            "deep_task.account_verified",
            {
                "status": oauth_identity.get("status"),
                "email": oauth_identity.get("email"),
                "name": oauth_identity.get("name"),
                "verified_email": oauth_identity.get("verified_email"),
                "notebooklm_profile": preferred_notebook_profile,
            },
        )

        await self._raise_if_cancelled(run_id)
        drive_input = await collect_google_drive_input_summary(
            user_id=user_id,
            max_files=8,
            max_chars_per_file=1200,
        )
        drive_input_json_path = run_output_dir / "drive_input_summary.json"
        drive_input_md_path = run_output_dir / "drive_input_summary.md"
        drive_input_json_path.write_text(
            json.dumps(drive_input, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        drive_input_md_path.write_text(str(drive_input.get("summary_markdown") or ""), encoding="utf-8")

        await stream_hook(
            "deep_task.input_folder_checked",
            {
                "status": drive_input.get("status"),
                "folder_name": drive_input.get("folder_name"),
                "folder_url": drive_input.get("folder_url"),
                "file_count": len(drive_input.get("files") or []),
                "error": drive_input.get("error"),
            },
        )
        if str(drive_input.get("status") or "") != "live" or not list(drive_input.get("files") or []):
            raise ValueError(
                "연결된 Google Drive input 폴더를 확인할 수 없거나 파일이 비어 있습니다. "
                "input 폴더에 자료를 업로드한 뒤 다시 실행해주세요."
            )

        drive_evidence = self._compress_sources(
            self._evidence_from_drive_input(list(drive_input.get("files") or [])),
            max_items=8,
        )
        research_query = self._build_research_query(
            task=request.task,
            drive_files=list(drive_input.get("files") or []),
        )
        await stream_hook(
            "deep_task.search_query_built",
            {
                "query": research_query,
                "message": "Drive 자료 기반 검색 질의를 생성했습니다.",
            },
        )

        await self._raise_if_cancelled(run_id)
        general_task = asyncio.create_task(search_general_sources(research_query, max_results=8))
        academic_task = asyncio.create_task(search_academic_sources(research_query, max_results=6))
        general_sources, academic_sources = await asyncio.gather(general_task, academic_task)
        general_sources = self._filter_sources_by_focus(general_sources, research_query, max_items=8)
        academic_sources = self._filter_sources_by_focus(academic_sources, research_query, max_items=6)
        await stream_hook(
            "deep_task.web_enrichment_completed",
            {
                "query": research_query,
                "general_count": len(general_sources),
                "academic_count": len(academic_sources),
            },
        )

        web_evidence = self._compress_sources([*general_sources, *academic_sources], max_items=12)
        source_pack_text = self._build_notebook_source_pack(
            task=request.task,
            research_query=research_query,
            drive_input_summary=str(drive_input.get("summary_markdown") or ""),
            general_sources=general_sources,
            academic_sources=academic_sources,
        )

        await self._raise_if_cancelled(run_id)
        notebook_evidence_result = await collect_notebooklm_evidence(
            run_id=run_id,
            task=request.task,
            source_text=source_pack_text,
            output_dir=str(run_output_dir),
            preferred_google_account=preferred_notebook_profile,
            search_query=research_query,
        )
        notebook_evidence = self._compress_sources(
            list(notebook_evidence_result.get("evidence") or []),
            max_items=12,
        )
        notebook_evidence_ready = str(notebook_evidence_result.get("status") or "") == "generated"
        fallback_evidence = self._compress_sources([*drive_evidence, *web_evidence], max_items=12)
        evidence = notebook_evidence if notebook_evidence_ready and notebook_evidence else fallback_evidence
        evidence_source = "notebooklm" if notebook_evidence_ready and notebook_evidence else "web_fallback"
        evidence_digest = self._format_evidence_digest(evidence, max_items=8)

        await stream_hook(
            "deep_task.discovery",
            {
                "message": "NotebookLM 기반 근거 수집을 완료했습니다."
                if evidence_source == "notebooklm"
                else "NotebookLM 근거 수집 실패로 웹 근거로 진행합니다.",
                "drive_file_count": len(drive_input.get("files") or []),
                "query": research_query,
                "general_count": len(general_sources),
                "academic_count": len(academic_sources),
                "notebook_evidence_count": len(notebook_evidence),
                "notebook_status": notebook_evidence_result.get("status"),
                "evidence_source": evidence_source,
                "sources": evidence[:8],
            },
        )

        transcript: list[DebateTurn] = []
        round_summaries: list[str] = []
        stagnation_streak = 0
        stagnation_detected = False
        executed_rounds = discussion_rounds

        for round_number in range(1, discussion_rounds + 1):
            await self._raise_if_cancelled(run_id)
            await stream_hook(
                "deep_task.round_started",
                {
                    "round": round_number,
                    "total_rounds": discussion_rounds,
                    "persona_count": len(debate_personas),
                },
            )

            round_turns: list[DebateTurn] = []
            for turn_index, persona in enumerate(debate_personas, start=1):
                await self._raise_if_cancelled(run_id)
                message = await self._generate_debate_turn(
                    claude=claude,
                    request=request,
                    persona=persona,
                    round_number=round_number,
                    evidence_digest=evidence_digest,
                    round_summaries=round_summaries,
                    transcript=transcript,
                )
                turn = DebateTurn(
                    round_number=round_number,
                    turn_index=turn_index,
                    persona_id=persona.persona_id,
                    persona_name=persona.name,
                    focus=persona.focus,
                    message=message,
                    created_at=self._now_iso(),
                )
                transcript.append(turn)
                round_turns.append(turn)

                try:
                    await append_session_message(
                        session_id=run_id,
                        user_id=user_id,
                        role="assistant",
                        content=f"[R{round_number}][{persona.name}] {message}",
                        metadata={
                            "type": "debate_turn",
                            "round": round_number,
                            "turn": turn_index,
                            "persona_id": persona.persona_id,
                        },
                    )
                except Exception:
                    pass

                await stream_hook(
                    "deep_task.debate_turn",
                    {
                        "round": round_number,
                        "turn": turn_index,
                        "persona_id": persona.persona_id,
                        "persona_label": persona.name,
                        "focus": persona.focus,
                        "message": message,
                    },
                )

            round_summary = self._summarize_round(round_turns)
            previous_summary = round_summaries[-1] if round_summaries else ""
            is_stagnated = self._is_stagnated(previous_summary, round_summary)
            round_summaries.append(round_summary)
            if is_stagnated:
                stagnation_streak += 1
                await stream_hook(
                    "deep_task.stagnation_detected",
                    {
                        "round": round_number,
                        "streak": stagnation_streak,
                        "threshold": 2,
                        "summary": round_summary,
                    },
                )
            else:
                stagnation_streak = 0

            await stream_hook(
                "deep_task.round_completed",
                {
                    "round": round_number,
                    "total_rounds": discussion_rounds,
                    "stagnated": is_stagnated,
                    "streak": stagnation_streak,
                    "summary": round_summary,
                },
            )

            if stagnation_streak >= 2:
                stagnation_detected = True
                executed_rounds = round_number
                break

        final_summary = ""
        await self._raise_if_cancelled(run_id)
        if stagnation_detected:
            await stream_hook(
                "deep_task.decision_by_default_balanced",
                {
                    "message": "토론 정체가 2라운드 연속 감지되어 default-balanced가 최종 결정을 수행합니다.",
                    "decider": default_balanced.persona_id,
                    "stagnation_rounds": 2,
                },
            )
            final_summary = await self._generate_final_decision(
                claude=claude,
                request=request,
                evidence=evidence,
                round_summaries=round_summaries,
                transcript=transcript,
                decider=default_balanced,
                by_default_balanced=True,
            )
        else:
            final_summary = await self._generate_final_decision(
                claude=claude,
                request=request,
                evidence=evidence,
                round_summaries=round_summaries,
                transcript=transcript,
                decider=default_balanced,
                by_default_balanced=False,
            )

        try:
            await append_session_message(
                session_id=run_id,
                user_id=user_id,
                role="assistant",
                content=f"[Final] {final_summary}",
                metadata={"type": "final_summary"},
            )
        except Exception:
            pass

        await stream_hook(
            "deep_task.moderator_decision",
            {"message": "최종 결론이 확정되었습니다.", "summary": final_summary},
        )

        transcript_md = self._render_full_debate_markdown(
            task=request.task,
            evidence=evidence,
            personas=debate_personas,
            transcript=transcript,
            round_summaries=round_summaries,
            final_summary=final_summary,
            executed_rounds=executed_rounds,
            discussion_rounds=discussion_rounds,
            stagnation_detected=stagnation_detected,
        )
        debate_json = self._render_full_debate_json(
            run_id=run_id,
            task=request.task,
            trigger_source=request.trigger_source,
            personas=debate_personas,
            discussion_rounds=discussion_rounds,
            executed_rounds=executed_rounds,
            stagnation_detected=stagnation_detected,
            round_summaries=round_summaries,
            transcript=transcript,
            evidence=evidence,
            final_summary=final_summary,
        )

        full_log_json_path = run_output_dir / "full_debate_log.json"
        full_log_md_path = run_output_dir / "full_debate_log.md"
        final_summary_path = run_output_dir / "final_summary.md"
        brief_path = run_output_dir / "brief.txt"
        full_log_json_path.write_text(
            json.dumps(debate_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        full_log_md_path.write_text(transcript_md, encoding="utf-8")
        final_summary_path.write_text(final_summary, encoding="utf-8")
        brief_text = self._trim_text(final_summary, 520)
        brief_path.write_text(brief_text, encoding="utf-8")

        await self._raise_if_cancelled(run_id)
        await stream_hook(
            "deep_task.post_processing_started",
            {
                "message": "최종 결론 확정 후 NotebookLM 산출물 생성을 시작합니다.",
                "stage": "notebooklm_assets",
            },
        )

        notebook_attempt_plans: list[tuple[str, str]] = [
            ("full_transcript", transcript_md),
            ("summary_fallback", final_summary),
        ]
        notebook_attempt_logs: list[dict[str, Any]] = []
        notebooklm: dict[str, Any] | None = None
        for attempt_index, (attempt_label, transcript_candidate) in enumerate(
            notebook_attempt_plans,
            start=1,
        ):
            await self._raise_if_cancelled(run_id)
            await stream_hook(
                "deep_task.post_processing_progress",
                {
                    "stage": "notebooklm_assets",
                    "attempt": attempt_index,
                    "total_attempts": len(notebook_attempt_plans),
                    "mode": attempt_label,
                    "message": "NotebookLM 슬라이드 생성 시도 중입니다.",
                },
            )
            candidate = await generate_notebooklm_assets(
                run_id=run_id,
                task=request.task,
                final_summary=final_summary,
                output_dir=str(run_output_dir),
                transcript_text=transcript_candidate,
                preferred_google_account=preferred_notebook_profile,
            )
            candidate_status = str(candidate.get("status") or "").strip().lower()
            candidate_slides_status = str(candidate.get("slides_status") or "").strip().lower()
            candidate_slide_path = str(
                candidate.get("ppt_path")
                or candidate.get("slide_path")
                or ""
            ).strip()
            notebook_attempt_logs.append(
                {
                    "attempt": attempt_index,
                    "mode": attempt_label,
                    "status": candidate_status,
                    "slides_status": candidate_slides_status,
                    "slides_reason": candidate.get("slides_reason"),
                    "reason": candidate.get("reason"),
                    "ppt_path": candidate_slide_path or None,
                }
            )
            if (
                candidate_status in {"generated", "partial"}
                and candidate_slides_status == "generated"
                and candidate_slide_path
            ):
                notebooklm = candidate
                break

        notebook_attempts_path = run_output_dir / "notebooklm_attempts.json"
        notebook_attempts_path.write_text(
            json.dumps(notebook_attempt_logs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if notebooklm is None:
            latest = notebook_attempt_logs[-1] if notebook_attempt_logs else {}
            raise RuntimeError(
                "NotebookLM slide deck generation failed after retries. "
                f"status={latest.get('status') or 'unknown'} "
                f"slides_status={latest.get('slides_status') or 'unknown'} "
                f"reason={latest.get('slides_reason') or latest.get('reason') or '-'}"
            )

        notebook_status = str(notebooklm.get("status") or "").strip().lower()
        notebook_slides_status = str(notebooklm.get("slides_status") or "").strip().lower()
        notebook_slide_path = str(notebooklm.get("ppt_path") or notebooklm.get("slide_path") or "").strip()
        if notebook_status not in {"generated", "partial"}:
            raise RuntimeError(
                f"NotebookLM assets generation failed: {notebooklm.get('reason') or notebook_status or 'unknown'}"
            )
        if notebook_slides_status != "generated" or not notebook_slide_path:
            raise RuntimeError(
                "NotebookLM slide deck generation is required but not completed. "
                f"status={notebook_slides_status or 'unknown'} reason={notebooklm.get('slides_reason') or '-'}"
            )
        presentation_path = notebook_slide_path
        presentation_source = "notebooklm_cli"

        artifact_files: list[str] = [
            str(full_log_json_path),
            str(full_log_md_path),
            str(final_summary_path),
            str(brief_path),
            str(drive_input_json_path),
            str(drive_input_md_path),
            str(presentation_path),
        ]
        notebook_source_pack_path = str(notebook_evidence_result.get("source_pack_path") or "").strip()
        notebook_evidence_json_path = str(notebook_evidence_result.get("evidence_json_path") or "").strip()
        notebook_evidence_md_path = str(notebook_evidence_result.get("evidence_md_path") or "").strip()
        if notebook_source_pack_path:
            artifact_files.append(notebook_source_pack_path)
        if notebook_evidence_json_path:
            artifact_files.append(notebook_evidence_json_path)
        if notebook_evidence_md_path:
            artifact_files.append(notebook_evidence_md_path)
        notebook_summary_path = str(notebooklm.get("summary_path") or "").strip()
        notebook_transcript_path = str(notebooklm.get("transcript_path") or "").strip()
        if notebook_summary_path:
            artifact_files.append(notebook_summary_path)
        if notebook_transcript_path:
            artifact_files.append(notebook_transcript_path)
        artifact_files.append(str(notebook_attempts_path))

        await self._raise_if_cancelled(run_id)
        drive_result = await upload_artifacts_to_google_drive(
            user_id=user_id,
            run_id=run_id,
            file_paths=artifact_files,
        )

        await stream_hook(
            "deep_task.artifacts_ready",
            {
                "folder_url": drive_result.get("folder_url"),
                "files": drive_result.get("files", []),
                "brief": brief_text,
            },
        )
        await stream_hook(
            "deep_task.post_processing",
            {
                "message": "NotebookLM/아티팩트 생성 및 Drive 업로드가 완료되었습니다.",
                "notebook_evidence": notebook_evidence_result,
                "notebooklm": notebooklm,
                "presentation_path": str(presentation_path),
                "presentation_source": presentation_source,
                "drive": drive_result,
                "oauth_identity": oauth_identity,
            },
        )

        arguments = self._build_argument_map(transcript)
        try:
            await save_agent_log(
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "task": request.task,
                    "persona_stats": request.persona_stats.model_dump(),
                    "arguments": arguments,
                    "final_summary": final_summary,
                    "sources": evidence,
                    "feedback_score": None,
                }
            )
        except Exception:
            pass

        if request.notify_email:
            mail_result = await send_gmail_notification(
                user_id=user_id,
                to_email=request.notify_email,
                subject=f"[AgentGCS] 완전자율 과제 완료 - {request.task[:48]}",
                body=final_summary,
            )
            await stream_hook(
                "toast.notification",
                {
                    "title": "결과 메일 전송",
                    "description": f"{request.notify_email} 로 결과를 전송했습니다.",
                    "meta": mail_result,
                },
            )

        return {
            "run_id": run_id,
            "task": request.task,
            "evidence": evidence,
            "arguments": arguments,
            "final_summary": final_summary,
            "artifacts": {
                "output_dir": str(run_output_dir),
                "logs": {
                    "json": str(full_log_json_path),
                    "markdown": str(full_log_md_path),
                    "summary": str(final_summary_path),
                    "brief": str(brief_path),
                    "drive_input_json": str(drive_input_json_path),
                    "drive_input_markdown": str(drive_input_md_path),
                    "notebook_source_pack": notebook_source_pack_path or None,
                    "notebook_evidence_json": notebook_evidence_json_path or None,
                    "notebook_evidence_markdown": notebook_evidence_md_path or None,
                },
                "drive_input": drive_input,
                "evidence_source": evidence_source,
                "notebook_evidence": notebook_evidence_result,
                "notebooklm": notebooklm,
                "presentation_path": str(presentation_path),
                "presentation_source": presentation_source,
                "pptx_path": str(presentation_path),
                "drive": drive_result,
            },
        }

    async def _is_cancelled(self, run_id: str) -> bool:
        async with self._cancel_lock:
            return run_id in self._cancelled_runs

    async def _clear_cancel(self, run_id: str) -> None:
        async with self._cancel_lock:
            self._cancelled_runs.discard(run_id)

    async def _raise_if_cancelled(self, run_id: str) -> None:
        if await self._is_cancelled(run_id):
            raise RunCancelledError("사용자 중단 요청으로 실행이 취소되었습니다.")

    def _resolve_personas(self, user_settings: dict[str, Any]) -> tuple[list[DebatePersona], DebatePersona]:
        default_stats = PersonaStats(**deepcopy_dict(DEFAULT_PERSONA.get("stats") or {})).as_dict()
        default_balanced = DebatePersona(
            persona_id="default-balanced",
            name=str(DEFAULT_PERSONA.get("name") or "기본 균형형"),
            stats=default_stats,
            focus=self._focus_from_stats(default_stats),
        )

        raw_personas = user_settings.get("personas")
        personas: list[DebatePersona] = [default_balanced]
        seen_ids = {default_balanced.persona_id}
        if isinstance(raw_personas, list):
            for row in raw_personas:
                if len(personas) >= 6:
                    break
                if not isinstance(row, dict):
                    continue
                persona_id = str(row.get("id") or "").strip()
                if not persona_id or persona_id in seen_ids or persona_id == default_balanced.persona_id:
                    continue
                persona_name = str(row.get("name") or "").strip() or persona_id
                try:
                    stats = PersonaStats(**(row.get("stats") or {})).as_dict()
                except Exception:
                    continue
                personas.append(
                    DebatePersona(
                        persona_id=persona_id,
                        name=persona_name,
                        stats=stats,
                        focus=self._focus_from_stats(stats),
                    )
                )
                seen_ids.add(persona_id)

        return personas[:6], default_balanced

    def _resolve_discussion_rounds(self, user_settings: dict[str, Any]) -> int:
        try:
            value = int(user_settings.get("discussion_rounds"))
        except Exception:
            value = 3
        return max(2, min(5, value))

    async def _generate_debate_turn(
        self,
        *,
        claude: ClaudeService,
        request: DeepTaskRequest,
        persona: DebatePersona,
        round_number: int,
        evidence_digest: str,
        round_summaries: list[str],
        transcript: list[DebateTurn],
    ) -> str:
        summary_context = "\n".join(
            f"[R{idx + 1}] {self._trim_text(summary, 240)}"
            for idx, summary in enumerate(round_summaries[-2:])
        )
        transcript_context = "\n".join(
            f"- {turn.persona_name}: {self._trim_text(turn.message, 220)}"
            for turn in transcript[-8:]
        )
        system_prompt = (
            f"당신은 과제 토론 에이전트 '{persona.name}'다.\n"
            f"페르소나 ID: {persona.persona_id}\n"
            f"관점: {persona.focus}\n"
            f"성향 지표(0~100): {persona.stats}\n"
            "규칙:\n"
            "1) 근거를 반드시 인용한다.\n"
            "2) 중복 주장을 피하고 이전 주장과 연결한다.\n"
            "3) 출력은 한국어 6~10줄 이내로 간결하게 작성한다.\n"
        )
        user_prompt = (
            f"과제:\n{request.task}\n\n"
            f"근거 인덱스:\n{evidence_digest}\n\n"
            f"이전 라운드 요약:\n{summary_context or '(없음)'}\n\n"
            f"최근 발언(슬라이딩 윈도우):\n{transcript_context or '(없음)'}\n\n"
            f"현재 라운드: {round_number}\n"
            "출력 형식:\n"
            "- 주장 2개\n"
            "- 근거 출처 인덱스 [S#] 1~2개\n"
            "- 반박/보완 1개"
        )
        message = await claude.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            use_mock=request.use_mock,
            cache_hint=f"debate-{persona.persona_id}-r{round_number}",
        )
        return self._trim_text(message, 1200)

    async def _generate_final_decision(
        self,
        *,
        claude: ClaudeService,
        request: DeepTaskRequest,
        evidence: list[dict[str, Any]],
        round_summaries: list[str],
        transcript: list[DebateTurn],
        decider: DebatePersona,
        by_default_balanced: bool,
    ) -> str:
        evidence_digest = self._format_evidence_digest(evidence, max_items=10)
        summary_digest = "\n".join(
            f"[R{idx + 1}] {self._trim_text(summary, 260)}"
            for idx, summary in enumerate(round_summaries)
        )
        recent_turns_digest = "\n".join(
            f"[T{idx + 1}] {turn.persona_name}: {self._trim_text(turn.message, 180)}"
            for idx, turn in enumerate(transcript[-10:])
        )
        system_prompt = (
            "당신은 멀티 에이전트 토론의 최종 결론 작성자다.\n"
            f"결정자: {decider.name} ({decider.persona_id})\n"
            f"결정 방식: {'정체 대응 강제 결정' if by_default_balanced else '일반 종합 결정'}\n"
            "지침:\n"
            "1) 근거 인덱스 [S#]를 포함해 결론을 작성한다.\n"
            "2) 실행 항목은 구체적으로 작성한다.\n"
            "3) 원문 전체를 복사하지 말고 요약 인덱스를 활용한다.\n"
            "4) 출력 섹션: 핵심 결론 / 근거 / 실행 TODO 5개 / 리스크 대응.\n"
        )
        user_prompt = (
            f"과제:\n{request.task}\n\n"
            f"라운드 요약 인덱스:\n{summary_digest}\n\n"
            f"최근 발언 인덱스:\n{recent_turns_digest}\n\n"
            f"근거 인덱스:\n{evidence_digest}"
        )
        return await claude.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            use_mock=request.use_mock,
            cache_hint="final-decision",
        )

    def _compress_sources(self, rows: list[dict[str, Any]], *, max_items: int) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if len(deduped) >= max_items:
                break
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            title = self._trim_text(str(row.get("title") or "제목 없음"), 120)
            if not title:
                continue
            key = (url or title).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": self._trim_text(
                        str(row.get("snippet") or row.get("abstract") or row.get("body") or ""),
                        220,
                    ),
                    "source": str(row.get("source") or "unknown"),
                }
            )
        return deduped

    def _build_research_query(self, *, task: str, drive_files: list[dict[str, Any]]) -> str:
        task_text = re.sub(r"\s+", " ", (task or "").strip())
        boilerplate_patterns = [
            r"과제를?\s*진행할?께?",
            r"연결된\s*구글\s*드라이브를?\s*참고해서?\s*진행해줘?",
            r"구글\s*드라이브를?\s*참고해줘?",
            r"드라이브를?\s*참고해줘?",
        ]
        normalized_task = task_text
        for pattern in boilerplate_patterns:
            normalized_task = re.sub(pattern, " ", normalized_task, flags=re.IGNORECASE)
        normalized_task = re.sub(r"\s+", " ", normalized_task).strip()

        token_pool: list[str] = []
        stop_tokens = {
            "과제", "진행", "수행", "연결", "구글", "드라이브", "input", "output",
            "자료", "토론", "준비", "해결", "참고", "있는가", "pdf",
        }
        candidate_texts = [normalized_task]
        for row in drive_files:
            if not isinstance(row, dict):
                continue
            candidate_texts.append(str(row.get("name") or ""))
            candidate_texts.append(str(row.get("snippet") or ""))

        for text in candidate_texts:
            for token in re.findall(r"[A-Za-z0-9가-힣]{2,}", (text or "").lower()):
                if token in stop_tokens:
                    continue
                token_pool.append(token)

        freq: dict[str, int] = {}
        for token in token_pool:
            freq[token] = freq.get(token, 0) + 1
        ranked_tokens = [token for token, _ in sorted(freq.items(), key=lambda item: item[1], reverse=True)]
        top_tokens = ranked_tokens[:8]

        if normalized_task and len(normalized_task) >= 8:
            base = normalized_task
        elif top_tokens:
            base = " ".join(top_tokens[:5])
        else:
            base = task_text or "시장 분석 실행 전략"

        query_parts: list[str] = [base]
        if top_tokens:
            query_parts.append(" ".join(top_tokens[:6]))
        query_parts.append("시장 기회 리스크 대응 근거")
        query = " ".join(part for part in query_parts if part).strip()
        return self._trim_text(query, 240)

    def _source_focus_score(self, row: dict[str, Any], focus_query: str) -> float:
        query_tokens = self._tokenize(focus_query)
        if not query_tokens:
            return 0.0
        haystack = " ".join(
            [
                str(row.get("title") or ""),
                str(row.get("snippet") or ""),
                str(row.get("url") or ""),
            ]
        ).lower()
        hits = sum(1 for token in query_tokens if token in haystack)
        return hits / max(1, len(query_tokens))

    def _filter_sources_by_focus(
        self,
        rows: list[dict[str, Any]],
        focus_query: str,
        *,
        max_items: int,
    ) -> list[dict[str, Any]]:
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            score = self._source_focus_score(row, focus_query)
            if score < 0.08:
                continue
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:max_items]]

    def _build_notebook_source_pack(
        self,
        *,
        task: str,
        research_query: str,
        drive_input_summary: str,
        general_sources: list[dict[str, Any]],
        academic_sources: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = [
            "# AgentGCS Discovery Source Pack",
            "",
            f"## Task",
            task,
            "",
            "## Search Query",
            research_query,
            "",
            "## Drive Input Folder Summary",
            drive_input_summary.strip() or "(no drive input summary)",
            "",
            "## General Sources",
        ]
        compressed_general = self._compress_sources(general_sources, max_items=10)
        if compressed_general:
            for idx, row in enumerate(compressed_general, start=1):
                lines.append(f"- [G{idx}] {row.get('title') or '제목 없음'}")
                snippet = str(row.get("snippet") or "").strip()
                if snippet:
                    lines.append(f"  - 요약: {snippet}")
                url = str(row.get("url") or "").strip()
                if url:
                    lines.append(f"  - URL: {url}")
        else:
            lines.append("- (no general sources)")

        lines.extend(["", "## Academic Sources"])
        compressed_academic = self._compress_sources(academic_sources, max_items=8)
        if compressed_academic:
            for idx, row in enumerate(compressed_academic, start=1):
                lines.append(f"- [A{idx}] {row.get('title') or '제목 없음'}")
                snippet = str(row.get("snippet") or "").strip()
                if snippet:
                    lines.append(f"  - 요약: {snippet}")
                url = str(row.get("url") or "").strip()
                if url:
                    lines.append(f"  - URL: {url}")
        else:
            lines.append("- (no academic sources)")

        lines.extend(
            [
                "",
                "## Instructions",
                "- 위 자료를 기반으로 실무 토론에 활용 가능한 근거를 정리한다.",
                "- 중복된 주장보다 서로 독립적인 사실/근거를 우선한다.",
                "- 리스크/대응 관점 근거도 반드시 포함한다.",
                "",
            ]
        )
        return "\n".join(lines)

    def _evidence_from_drive_input(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in files:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "title": str(row.get("name") or "Drive Input"),
                    "url": str(row.get("drive_url") or ""),
                    "snippet": str(row.get("snippet") or "(텍스트 요약 없음)"),
                    "source": "google_drive_input",
                }
            )
        return rows

    def _format_evidence_digest(self, evidence: list[dict[str, Any]], *, max_items: int) -> str:
        lines: list[str] = []
        for idx, row in enumerate(evidence[:max_items], start=1):
            title = self._trim_text(str(row.get("title") or "제목 없음"), 90)
            snippet = self._trim_text(str(row.get("snippet") or ""), 140)
            url = self._trim_text(str(row.get("url") or ""), 180)
            lines.append(f"[S{idx}] {title}")
            if snippet:
                lines.append(f" - 요약: {snippet}")
            if url:
                lines.append(f" - URL: {url}")
        return "\n".join(lines) if lines else "[S0] 근거 없음"

    def _summarize_round(self, round_turns: list[DebateTurn]) -> str:
        lines = [
            f"{turn.persona_name}: {self._trim_text(turn.message, 170)}"
            for turn in round_turns
        ]
        return " | ".join(lines)

    def _is_stagnated(self, previous_summary: str, current_summary: str) -> bool:
        previous_tokens = self._tokenize(previous_summary)
        current_tokens = self._tokenize(current_summary)
        if not previous_tokens or not current_tokens:
            return False
        overlap = previous_tokens.intersection(current_tokens)
        union = previous_tokens.union(current_tokens)
        ratio = len(overlap) / max(1, len(union))
        return ratio >= 0.78

    def _tokenize(self, text: str) -> set[str]:
        tokens = re.findall(r"[A-Za-z0-9가-힣]{2,}", (text or "").lower())
        stop = {"그리고", "하지만", "입니다", "합니다", "the", "and", "for", "with"}
        return {token for token in tokens if token not in stop}

    def _focus_from_stats(self, stats: dict[str, int]) -> str:
        if not stats:
            return "균형형 관점으로 실행 가능한 결론 제시"
        ranked = sorted(stats.items(), key=lambda item: item[1], reverse=True)
        top_keys = [item[0] for item in ranked[:2]]
        labels = {
            "creativity": "창의적 대안",
            "logic": "논리 검증",
            "critical_thinking": "비판적 리스크 점검",
            "data_dependency": "데이터 기반 근거",
            "cautiousness": "안정성 우선",
            "drive": "실행 추진력",
        }
        return ", ".join(labels.get(key, key) for key in top_keys)

    def _build_argument_map(self, transcript: list[DebateTurn]) -> dict[str, str]:
        grouped: dict[str, list[str]] = {}
        for turn in transcript:
            grouped.setdefault(turn.persona_id, []).append(
                f"[R{turn.round_number}-T{turn.turn_index}] {turn.message}"
            )
        return {persona_id: "\n\n".join(rows) for persona_id, rows in grouped.items()}

    def _render_full_debate_json(
        self,
        *,
        run_id: str,
        task: str,
        trigger_source: str,
        personas: list[DebatePersona],
        discussion_rounds: int,
        executed_rounds: int,
        stagnation_detected: bool,
        round_summaries: list[str],
        transcript: list[DebateTurn],
        evidence: list[dict[str, Any]],
        final_summary: str,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "task": task,
            "trigger_source": trigger_source,
            "created_at": self._now_iso(),
            "discussion_rounds": discussion_rounds,
            "executed_rounds": executed_rounds,
            "stagnation_detected": stagnation_detected,
            "personas": [
                {
                    "id": persona.persona_id,
                    "name": persona.name,
                    "focus": persona.focus,
                    "stats": persona.stats,
                }
                for persona in personas
            ],
            "round_summaries": round_summaries,
            "turns": [
                {
                    "round": turn.round_number,
                    "turn": turn.turn_index,
                    "persona_id": turn.persona_id,
                    "persona_name": turn.persona_name,
                    "focus": turn.focus,
                    "message": turn.message,
                    "created_at": turn.created_at,
                }
                for turn in transcript
            ],
            "evidence": evidence,
            "final_summary": final_summary,
        }

    def _render_full_debate_markdown(
        self,
        *,
        task: str,
        evidence: list[dict[str, Any]],
        personas: list[DebatePersona],
        transcript: list[DebateTurn],
        round_summaries: list[str],
        final_summary: str,
        executed_rounds: int,
        discussion_rounds: int,
        stagnation_detected: bool,
    ) -> str:
        lines: list[str] = [
            "# AgentGCS Full Debate Log",
            "",
            f"- Task: {task}",
            f"- Rounds: {executed_rounds}/{discussion_rounds}",
            f"- Stagnation Detected: {'yes' if stagnation_detected else 'no'}",
            "",
            "## Personas",
        ]
        for persona in personas:
            lines.append(f"- {persona.name} ({persona.persona_id}) / {persona.focus}")

        lines.extend(["", "## Evidence Index"])
        if evidence:
            for idx, row in enumerate(evidence, start=1):
                lines.append(
                    f"- [S{idx}] {row.get('title') or '제목 없음'} | {row.get('url') or 'no-url'}"
                )
        else:
            lines.append("- (no evidence)")

        lines.extend(["", "## Round Summaries"])
        for idx, summary in enumerate(round_summaries, start=1):
            lines.append(f"- R{idx}: {summary}")

        lines.extend(["", "## Full Transcript"])
        for turn in transcript:
            lines.append(f"### R{turn.round_number}-T{turn.turn_index} {turn.persona_name}")
            lines.append("")
            lines.append(turn.message)
            lines.append("")

        lines.extend(["## Final Summary", "", final_summary.strip(), ""])
        return "\n".join(lines)

    def _trim_text(self, text: str, max_len: int) -> str:
        compact = re.sub(r"\s+", " ", (text or "").strip())
        if len(compact) <= max_len:
            return compact
        return compact[: max(0, max_len - 3)] + "..."

    def _now_iso(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat()


def deepcopy_dict(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))
