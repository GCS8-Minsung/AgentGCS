import asyncio
from typing import Any, Dict, Optional

from app.services.claude_service import ClaudeService


class ReActEngine:
    """A simple ReAct loop implementation.

    Each iteration:
      - Ask Claude for a Thought and optional Action
      - If Action returned, execute via provided tool_call callback
      - Provide Observation back into next loop
      - Stop when Claude emits a final_answer marker or max iterations reached
    """

    def __init__(self, claude: ClaudeService, tool_call: Any, max_iters: int = 6):
        self.claude = claude
        self.tool_call = tool_call
        self.max_iters = max_iters

    async def run(self, system_prompt: str, user_prompt: str, use_mock: bool = False) -> Dict:
        history: list[Dict] = []
        observation: Optional[str] = None
        for i in range(self.max_iters):
            prompt = self._build_prompt(system_prompt, user_prompt, history, observation)
            text = await self.claude.generate(system_prompt=system_prompt, user_prompt=prompt, use_mock=use_mock)
            # Expected structured response: either a JSON action or final_answer
            parsed = self._parse_response(text)
            history.append({"assistant": text, "parsed": parsed})
            if parsed.get("type") == "action":
                action = parsed.get("action")
                params = parsed.get("params") or {}
                try:
                    result = await self.tool_call(action, params)
                    observation = f"TOOL_RESULT:{result}"
                except Exception as exc:
                    observation = f"TOOL_ERROR:{str(exc)[:200]}"
                continue
            if parsed.get("type") == "final_answer":
                return {"status": "completed", "final": parsed.get("answer"), "history": history}
        # max iters reached
        return {"status": "max_iters_exceeded", "final": history[-1] if history else None, "history": history}

    def _build_prompt(self, system_prompt: str, user_prompt: str, history: list, observation: Optional[str]) -> str:
        parts = [user_prompt]
        if history:
            parts.append("\n\nPrevious assistant outputs:\n")
            for h in history:
                parts.append(h.get("assistant", ""))
        if observation:
            parts.append(f"\n\nObservation:\n{observation}")
        parts.append("\n\nRespond with either: {\"type\": \"action\", \"action\": \"tool_name\", \"params\": {...}} or {\"type\": \"final_answer\", \"answer\": \"...\"}")
        return "\n".join(parts)

    def _parse_response(self, text: str) -> Dict:
        # Try to extract JSON; naive but workable for now
        import re, json
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return {"type": "final_answer", "answer": text}
        try:
            data = json.loads(m.group(0))
            if data.get("type") == "action":
                return {"type": "action", "action": data.get("action"), "params": data.get("params")}
            if data.get("type") == "final_answer":
                return {"type": "final_answer", "answer": data.get("answer")}
        except Exception:
            return {"type": "final_answer", "answer": text}
        return {"type": "final_answer", "answer": text}
