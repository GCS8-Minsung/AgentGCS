from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypedDict

from app.core.supabase_client import get_supabase_admin
from app.models.schemas import DeepTaskRequest
from app.services.claude_service import ClaudeService
from app.services.integrations import send_gmail_notification, upload_to_google_drive
from app.services.notebooklm import generate_notebooklm_assets
from app.services.websocket_manager import WebSocketManager
from app.tools.web_search import search_trusted_sources

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - optional at runtime
    LANGGRAPH_AVAILABLE = False


class DebateState(TypedDict, total=False):
    run_id: str
    user_id: str
    task: str
    use_mock: bool
    persona_stats: dict[str, int]
    evidence: list[dict[str, Any]]
    arguments: dict[str, str]
    final_summary: str
    stream_hook: Callable[[str, dict[str, Any]], Awaitable[None]]


PERSONAS: list[dict[str, Any]] = [
    {
        "id": "innovator",
        "label": "Innovator",
        "focus": "새로운 수익 모델과 파격적인 제품 경험",
        "weights": {"creativity": 0.55, "drive": 0.25, "empathy": 0.2},
    },
    {
        "id": "analyst",
        "label": "Analyst",
        "focus": "원가 구조, 유닛 이코노믹스, 실행 가능성",
        "weights": {"logic": 0.5, "data_dependency": 0.35, "critical_thinking": 0.15},
    },
    {
        "id": "skeptic",
        "label": "Skeptic",
        "focus": "핵심 리스크, 잘못된 가정, 실패 시나리오",
        "weights": {"critical_thinking": 0.6, "logic": 0.25, "data_dependency": 0.15},
    },
    {
        "id": "researcher",
        "label": "Researcher",
        "focus": "출처 기반 사실 검증과 근거 우선 제안",
        "weights": {"data_dependency": 0.65, "logic": 0.2, "critical_thinking": 0.15},
    },
    {
        "id": "operator",
        "label": "Operator",
        "focus": "로드맵, 마일스톤, 파트너십, 즉시 실행 계획",
        "weights": {"drive": 0.45, "logic": 0.3, "empathy": 0.25},
    },
]


class DeepTaskOrchestrator:
    def __init__(self, ws_manager: WebSocketManager, claude_service: ClaudeService) -> None:
        self.ws_manager = ws_manager
        self.claude_service = claude_service
        self._graph = self._build_graph() if LANGGRAPH_AVAILABLE else None

    def _build_graph(self):
        graph = StateGraph(DebateState)
        graph.add_node("discover", self._discover_node)

        for persona in PERSONAS:
            graph.add_node(persona["id"], self._make_persona_node(persona))

        graph.add_node("synthesize", self._synthesize_node)

        graph.add_edge(START, "discover")
        previous = "discover"
        for persona in PERSONAS:
            graph.add_edge(previous, persona["id"])
            previous = persona["id"]
        graph.add_edge(previous, "synthesize")
        graph.add_edge("synthesize", END)
        return graph.compile()

    async def run_and_stream(self, *, user_id: str, run_id: str, request: DeepTaskRequest) -> None:
        async def stream_hook(event_type: str, payload: dict[str, Any]) -> None:
            await self.ws_manager.emit(user_id, event_type, payload, run_id=run_id)

        initial_state: DebateState = {
            "run_id": run_id,
            "user_id": user_id,
            "task": request.task,
            "use_mock": request.use_mock,
            "persona_stats": request.persona_stats.as_dict(),
            "arguments": {},
            "stream_hook": stream_hook,
        }

        await stream_hook(
            "deep_task.started",
            {
                "message": "정보 탐색 및 5인 페르소나 토론을 시작합니다.",
                "task": request.task,
                "persona_stats": request.persona_stats.model_dump(),
            },
        )

        try:
            if self._graph:
                result = await self._graph.ainvoke(initial_state)
            else:
                result = await self._run_without_langgraph(initial_state)

            final_summary = result.get("final_summary", "")
            notebooklm_result = await generate_notebooklm_assets(
                run_id=run_id,
                task=request.task,
                final_summary=final_summary,
            )
            await stream_hook(
                "deep_task.post_processing",
                {
                    "message": "NotebookLM 후처리가 완료되었습니다.",
                    "notebooklm": notebooklm_result,
                },
            )

            drive_result = await upload_to_google_drive(
                file_path=notebooklm_result.get("script_path", ""),
                user_id=user_id,
            )
            await stream_hook(
                "deep_task.drive_uploaded",
                {
                    "message": "결과물을 Google Drive로 업로드했습니다.",
                    "drive": drive_result,
                },
            )

            await self._persist_log(user_id=user_id, run_id=run_id, request=request, result=result)

            await stream_hook(
                "deep_task.completed",
                {
                    "final_summary": final_summary,
                    "arguments": result.get("arguments", {}),
                    "sources": result.get("evidence", []),
                },
            )

            if request.notify_email:
                mail_result = await send_gmail_notification(
                    user_id=user_id,
                    to_email=request.notify_email,
                    subject=f"[AgentGCS] 과제 분석 완료 - {request.task[:40]}",
                    body=final_summary,
                )
                await stream_hook(
                    "toast.notification",
                    {
                        "title": "결과 발송 예정",
                        "description": f"{request.notify_email} 로 결과 요약 메일을 처리했습니다.",
                        "action": {"label": "메일함 열기", "href": "https://mail.google.com"},
                        "meta": mail_result,
                    },
                )
        except Exception as exc:
            await stream_hook(
                "deep_task.failed",
                {"message": "에이전트 파이프라인 실행 중 오류가 발생했습니다.", "error": str(exc)},
            )

    async def _run_without_langgraph(self, state: DebateState) -> DebateState:
        running = dict(state)
        running.update(await self._discover_node(running))
        for persona in PERSONAS:
            node = self._make_persona_node(persona)
            running.update(await node(running))
        running.update(await self._synthesize_node(running))
        return running

    async def _discover_node(self, state: DebateState) -> dict[str, Any]:
        evidence = await search_trusted_sources(state["task"], max_results=5)
        hook = state.get("stream_hook")
        if hook:
            await hook(
                "deep_task.discovery",
                {
                    "message": "신뢰 가능한 출처 기반 정보 탐색이 완료되었습니다.",
                    "sources": evidence,
                },
            )
        return {"evidence": evidence}

    def _make_persona_node(self, persona: dict[str, Any]):
        async def persona_node(state: DebateState) -> dict[str, Any]:
            stats = state["persona_stats"]
            weight = self._weight_score(stats, persona["weights"])
            source_lines = "\n".join(
                f"- {doc['title']} ({doc['url']})" for doc in state.get("evidence", [])
            )
            system_prompt = (
                f"당신은 멀티 에이전트 토론의 {persona['label']} 역할입니다. "
                f"핵심 관점: {persona['focus']}. "
                f"현재 성향 가중치 점수는 {weight:.2f}/1.00 입니다."
            )
            user_prompt = (
                f"과제: {state['task']}\n"
                f"자료:\n{source_lines}\n\n"
                "요구사항:\n"
                "1) 비즈니스 모델 핵심 제안 2개\n"
                "2) 90일 MVP 실행안 1개\n"
                "3) 리스크 1개와 완화책 1개\n"
                "답변은 한국어로 220자 이내 핵심 bullet 스타일."
            )
            argument = await self.claude_service.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                use_mock=state.get("use_mock", True),
                cache_hint=f"persona-{persona['id']}",
            )
            merged_args = dict(state.get("arguments", {}))
            merged_args[persona["id"]] = argument

            hook = state.get("stream_hook")
            if hook:
                await hook(
                    "deep_task.debate_turn",
                    {
                        "persona_id": persona["id"],
                        "persona_label": persona["label"],
                        "focus": persona["focus"],
                        "weight_score": round(weight, 3),
                        "message": argument,
                    },
                )
            return {"arguments": merged_args}

        return persona_node

    async def _synthesize_node(self, state: DebateState) -> dict[str, Any]:
        discussion_text = "\n".join(
            f"[{persona}] {text}" for persona, text in state.get("arguments", {}).items()
        )
        synthesis = await self.claude_service.generate(
            system_prompt=(
                "당신은 토론 정리자입니다. 상충되는 주장도 살리면서 실행 로드맵 중심으로 결론을 만드세요."
            ),
            user_prompt=(
                f"과제: {state['task']}\n\n토론 내용:\n{discussion_text}\n\n"
                "출력 포맷:\n"
                "1) 핵심 비즈니스 모델 3줄\n"
                "2) 0~90일 실행계획 4줄\n"
                "3) 예상 KPI 3개\n"
                "4) 즉시 해야 할 액션 2개"
            ),
            use_mock=state.get("use_mock", True),
            cache_hint="synthesis",
        )
        hook = state.get("stream_hook")
        if hook:
            await hook(
                "deep_task.synthesized",
                {"message": "5인 토론 결과를 종합했습니다.", "summary": synthesis},
            )
        return {"final_summary": synthesis}

    def _weight_score(self, stats: dict[str, int], weight_map: dict[str, float]) -> float:
        score = 0.0
        for axis, ratio in weight_map.items():
            score += (stats.get(axis, 50) / 100.0) * ratio
        return min(1.0, max(0.0, score))

    async def _persist_log(
        self, *, user_id: str, run_id: str, request: DeepTaskRequest, result: dict[str, Any]
    ) -> None:
        payload = {
            "run_id": run_id,
            "user_id": user_id,
            "task": request.task,
            "persona_stats": request.persona_stats.model_dump(),
            "arguments": result.get("arguments", {}),
            "final_summary": result.get("final_summary", ""),
            "sources": result.get("evidence", []),
            "feedback_score": None,
        }

        def _insert() -> None:
            client = get_supabase_admin()
            client.table("agent_logs").insert(payload).execute()

        await asyncio.to_thread(_insert)
