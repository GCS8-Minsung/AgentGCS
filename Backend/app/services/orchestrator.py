import asyncio
from typing import Any
from uuid import uuid4

from app.services.traits import TraitSet
from app.services.react_engine import ReActEngine
from app.services.tool_registry import tool_registry
from app.services.pptx_generator import generate_pptx_from_summary
from app.services.notebooklm import generate_notebooklm_assets
from app.services.integrations import upload_to_google_drive
from app.core.supabase_client import save_session, save_agent_log


class AgentWorker:
    def __init__(self, agent_id: str, traits: TraitSet, claude, persistence_cb=None, event_cb=None):
        self.agent_id = agent_id
        self.traits = traits
        self.claude = claude
        self.persistence_cb = persistence_cb
        self.event_cb = event_cb

    async def run(self, session_id: str, task: str, use_mock: bool = True) -> dict:
        system_prompt = f"Agent {self.agent_id} persona: {self.traits.summary_blurb()}"
        react = ReActEngine(self.claude, tool_registry.call, max_iters=5, persistence_callback=self.persistence_cb, event_callback=self.event_cb)
        result = await react.run(system_prompt=system_prompt, user_prompt=task, use_mock=use_mock, session_id=session_id, agent_id=self.agent_id)
        return result


class Orchestrator:
    def __init__(self, claude, ws_event_cb=None):
        self.claude = claude
        self.ws_event_cb = ws_event_cb

    async def run(self, user_id: str, task: str, persona_count: int = 4, use_mock: bool = True) -> dict:
        run_id = str(uuid4())
        session = {
            "id": run_id,
            "user_id": user_id,
            "status": "running",
            "autonomy_mode": "autonomous",
        }
        await save_session(session)

        workers = []
        for i in range(persona_count):
            stats = TraitSet().to_dict()
            stats["creativity"] = 40 + i * 10
            agent = AgentWorker(agent_id=f"agent-{i}", traits=TraitSet.from_dict(stats), claude=self.claude, persistence_cb=save_agent_log, event_cb=self.ws_event_cb)
            workers.append(agent)

        results = await asyncio.gather(*[w.run(session_id=run_id, task=task, use_mock=use_mock) for w in workers])

        # synthesize final summary using Claude
        discussion_text = "\n\n".join([str(r.get("final") or "") for r in results])
        summary = await self.claude.generate(system_prompt="Consolidate", user_prompt=discussion_text, use_mock=use_mock)

        # generate notebooklm assets and pptx
        notebook = await generate_notebooklm_assets(run_id=run_id, task=task, final_summary=summary)
        pptx_path = generate_pptx_from_summary(run_id=run_id, title=task, sections=[summary], out_dir="./outputs")

        # attempt drive upload
        drive_res = await upload_to_google_drive(file_path=pptx_path, user_id=user_id)

        # persist orchestrator log
        await save_agent_log({
            "session_id": run_id,
            "agent_id": "orchestrator",
            "step_index": -1,
            "role": "final",
            "content": summary,
            "meta": {"notebook": notebook, "pptx": pptx_path, "drive": drive_res},
        })

        return {"run_id": run_id, "summary": summary, "notebook": notebook, "pptx": pptx_path, "drive": drive_res}
