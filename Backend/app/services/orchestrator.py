from __future__ import annotations

from uuid import uuid4

from app.models.schemas import DeepTaskRequest, PersonaStats
from app.services.multi_agent_graph import DeepTaskOrchestrator


class Orchestrator:
    """
    Backward-compatible wrapper used by /api/orchestrator/run.
    Internally delegates to DeepTaskOrchestrator pipeline.
    """

    def __init__(self, orchestrator: DeepTaskOrchestrator):
        self._orchestrator = orchestrator

    async def run(
        self,
        *,
        user_id: str,
        task: str,
        persona_count: int = 5,
        use_mock: bool = True,
    ) -> dict:
        run_id = str(uuid4())
        count = max(3, min(6, persona_count))
        personas = [
            {
                "id": "default-balanced",
                "name": "기본 균형형",
                "stats": PersonaStats().model_dump(),
            }
        ]
        for idx in range(2, count + 1):
            personas.append(
                {
                    "id": f"auto-persona-{idx}",
                    "name": f"Auto Persona {idx}",
                    "stats": PersonaStats().model_dump(),
                }
            )
        request = DeepTaskRequest(
            task=task,
            persona_stats=PersonaStats(),
            worker_count=count,
            trigger_source="console",
            use_mock=use_mock,
        )
        result = await self._orchestrator.run(
            user_id=user_id,
            run_id=run_id,
            request=request,
            user_settings={"personas": personas, "discussion_rounds": 3},
        )
        return {
            "run_id": run_id,
            "summary": result.get("final_summary"),
            "sources": result.get("evidence", []),
            "arguments": result.get("arguments", {}),
            "artifacts": result.get("artifacts", {}),
        }
