from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.core.supabase_client import append_session_message, save_agent_log, save_session
from app.models.schemas import DeepTaskRequest
from app.services.claude_service import ClaudeService
from app.services.integrations import send_gmail_notification, upload_to_google_drive
from app.services.notebooklm import generate_notebooklm_assets
from app.services.pptx_generator import generate_pptx_from_summary
from app.services.websocket_manager import WebSocketManager
from app.tools.web_search import search_academic_sources, search_general_sources


@dataclass(slots=True)
class WorkerPersona:
    worker_id: str
    label: str
    perspective: str
    stat_weights: dict[str, float]


WORKER_TEMPLATES: list[WorkerPersona] = [
    WorkerPersona(
        worker_id="logic_critic",
        label="Logic Critic",
        perspective="핵심 가정/논리 오류/실행 리스크를 비판적으로 검증",
        stat_weights={"logic": 0.5, "critical_thinking": 0.4, "cautiousness": 0.1},
    ),
    WorkerPersona(
        worker_id="creative_builder",
        label="Creative Builder",
        perspective="새로운 제품/수익 모델/차별화된 시장 진입 전략 발굴",
        stat_weights={"creativity": 0.6, "drive": 0.2, "data_dependency": 0.2},
    ),
    WorkerPersona(
        worker_id="data_validator",
        label="Data Validator",
        perspective="출처 신뢰도, 수치 검증, 시장/학술 근거 재확인",
        stat_weights={"data_dependency": 0.65, "logic": 0.2, "critical_thinking": 0.15},
    ),
    WorkerPersona(
        worker_id="execution_operator",
        label="Execution Operator",
        perspective="로드맵, MVP 범위, 일정/리소스 계획, 우선순위 설정",
        stat_weights={"drive": 0.45, "logic": 0.25, "cautiousness": 0.3},
    ),
    WorkerPersona(
        worker_id="risk_controller",
        label="Risk Controller",
        perspective="법/보안/규제/운영 리스크와 실패 시나리오 관리",
        stat_weights={"critical_thinking": 0.45, "cautiousness": 0.4, "logic": 0.15},
    ),
    WorkerPersona(
        worker_id="market_strategist",
        label="Market Strategist",
        perspective="고객 세그먼트/채널/GTM/가격 전략 최적화",
        stat_weights={"creativity": 0.25, "logic": 0.25, "drive": 0.25, "data_dependency": 0.25},
    ),
]


class DeepTaskOrchestrator:
    def __init__(self, ws_manager: WebSocketManager, claude_service: ClaudeService) -> None:
        self.ws_manager = ws_manager
        self.claude_service = claude_service

    async def run_and_stream(
        self,
        *,
        user_id: str,
        run_id: str,
        request: DeepTaskRequest,
        claude_override: ClaudeService | None = None,
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
        except Exception as exc:
            await stream_hook(
                "deep_task.failed",
                {
                    "message": "멀티 에이전트 파이프라인 실행 중 오류가 발생했습니다.",
                    "error": str(exc),
                },
            )

    async def run(
        self,
        *,
        user_id: str,
        run_id: str,
        request: DeepTaskRequest,
        claude_override: ClaudeService | None = None,
    ) -> dict[str, Any]:
        async def noop(_event_type: str, _payload: dict[str, Any]) -> None:
            return

        return await self._run_pipeline(
            user_id=user_id,
            run_id=run_id,
            request=request,
            claude=claude_override or self.claude_service,
            stream_hook=noop,
        )

    async def _run_pipeline(
        self,
        *,
        user_id: str,
        run_id: str,
        request: DeepTaskRequest,
        claude: ClaudeService,
        stream_hook: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
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
                content=f"[system] 완전자율 실행 시작: {request.task}",
                metadata={"run_id": run_id},
            )
        except Exception:
            # local/dev fallback: continue without DB persistence
            pass

        await stream_hook(
            "deep_task.started",
            {
                "message": "완전자율 멀티 에이전트(Worker + Moderator) 실행을 시작합니다.",
                "task": request.task,
                "worker_count": request.worker_count,
                "persona_stats": request.persona_stats.model_dump(),
            },
        )

        general_task = asyncio.create_task(search_general_sources(request.task, max_results=6))
        academic_task = asyncio.create_task(search_academic_sources(request.task, max_results=4))
        general_sources, academic_sources = await asyncio.gather(general_task, academic_task)
        evidence = [*general_sources, *academic_sources]

        await stream_hook(
            "deep_task.discovery",
            {
                "message": "일반 웹 검색 + 학술 검색 결과를 수집했습니다.",
                "general_count": len(general_sources),
                "academic_count": len(academic_sources),
                "sources": evidence[:8],
            },
        )

        workers = self._select_workers(request.worker_count)
        arguments: dict[str, str] = {}
        rolling_context: list[str] = []
        stat_snapshot = request.persona_stats.as_dict()

        for worker in workers:
            weight_score = self._weight_score(stat_snapshot, worker.stat_weights)
            worker_stats = self._derive_worker_stats(stat_snapshot, worker.stat_weights)
            evidence_lines = "\n".join(
                f"- {doc.get('title')} ({doc.get('url')})" for doc in evidence[:8]
            )
            debate_context = "\n".join(rolling_context[-3:])
            system_prompt = (
                f"당신은 멀티 에이전트 워커 {worker.label} 입니다.\n"
                f"관점: {worker.perspective}\n"
                f"성향 가중 점수: {weight_score:.3f}\n"
                f"워커 성향 지표(0-100): {worker_stats}\n"
                "목표: 다른 워커와 중복을 줄이고, 반박 가능한 근거 중심으로 주장하라."
            )
            user_prompt = (
                f"과제:\n{request.task}\n\n"
                f"검색 근거:\n{evidence_lines}\n\n"
                f"이전 워커 발언:\n{debate_context or '(없음)'}\n\n"
                "출력 형식:\n"
                "1) 핵심 주장 2개\n"
                "2) 근거 출처 1~2개\n"
                "3) 반대 의견/리스크 1개\n"
                "총 8줄 이내."
            )
            message = await claude.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                use_mock=request.use_mock,
                cache_hint=f"worker-{worker.worker_id}",
            )
            arguments[worker.worker_id] = message
            rolling_context.append(f"[{worker.label}] {message}")

            try:
                await append_session_message(
                    session_id=run_id,
                    user_id=user_id,
                    role="assistant",
                    content=f"[{worker.label}] {message}",
                    metadata={"type": "worker_argument", "worker_id": worker.worker_id},
                )
            except Exception:
                pass
            await stream_hook(
                "deep_task.debate_turn",
                {
                    "persona_id": worker.worker_id,
                    "persona_label": worker.label,
                    "focus": worker.perspective,
                    "weight_score": round(weight_score, 4),
                    "worker_stats": worker_stats,
                    "message": message,
                },
            )

        discussion_text = "\n\n".join(f"[{k}] {v}" for k, v in arguments.items())
        moderator_prompt = (
            "당신은 Moderator(중재자)다. 워커들의 충돌되는 의견을 조율해 최종 결론을 확정한다.\n"
            "반드시 다음 순서로 답변하라:\n"
            "1) 충돌된 주장 요약\n"
            "2) 충돌 조정 근거(출처/논리)\n"
            "3) 최종 결론\n"
            "4) 즉시 실행 TODO 5개"
        )
        moderator_user = (
            f"과제:\n{request.task}\n\n"
            f"워커 토론:\n{discussion_text}\n\n"
            "검색 증거 요약:\n"
            + "\n".join(f"- {doc.get('title')} ({doc.get('url')})" for doc in evidence[:10])
        )
        final_summary = await claude.generate(
            system_prompt=moderator_prompt,
            user_prompt=moderator_user,
            use_mock=request.use_mock,
            cache_hint="moderator-final",
        )

        try:
            await append_session_message(
                session_id=run_id,
                user_id=user_id,
                role="assistant",
                content=f"[Moderator] {final_summary}",
                metadata={"type": "moderator_summary"},
            )
        except Exception:
            pass
        await stream_hook(
            "deep_task.moderator_decision",
            {"message": "중재자가 최종 결론을 확정했습니다.", "summary": final_summary},
        )

        notebooklm = await generate_notebooklm_assets(
            run_id=run_id,
            task=request.task,
            final_summary=final_summary,
        )
        pptx_path = generate_pptx_from_summary(
            run_id=run_id,
            title=request.task[:80],
            sections=[final_summary],
            out_dir="./outputs",
        )
        drive_result = await upload_to_google_drive(file_path=pptx_path, user_id=user_id)

        await stream_hook(
            "deep_task.post_processing",
            {
                "message": "NotebookLM 요약/PPT 생성/Drive 업로드가 완료되었습니다.",
                "notebooklm": notebooklm,
                "pptx_path": pptx_path,
                "drive": drive_result,
            },
        )

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
                "notebooklm": notebooklm,
                "pptx_path": pptx_path,
                "drive": drive_result,
            },
        }

    def _select_workers(self, count: int) -> list[WorkerPersona]:
        count = max(3, min(6, count))
        return WORKER_TEMPLATES[:count]

    def _weight_score(self, stats: dict[str, int], weights: dict[str, float]) -> float:
        score = 0.0
        for key, ratio in weights.items():
            score += (float(stats.get(key, 50)) / 100.0) * ratio
        return min(1.0, max(0.0, score))

    def _derive_worker_stats(
        self,
        base_stats: dict[str, int],
        weights: dict[str, float],
    ) -> dict[str, int]:
        adjusted: dict[str, int] = {}
        for key, value in base_stats.items():
            ratio = float(weights.get(key, 0.0))
            delta = 14 if ratio >= 0.45 else (8 if ratio >= 0.25 else (3 if ratio > 0 else 0))
            adjusted[key] = max(0, min(100, int(value) + delta))
        return adjusted
