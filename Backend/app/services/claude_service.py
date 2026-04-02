from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - optional dependency at runtime
    Anthropic = None


@dataclass(slots=True)
class ClaudeService:
    api_key: str | None = None
    _client: Any = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._client = Anthropic(api_key=self.api_key) if (self.api_key and Anthropic) else None

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        use_mock: bool,
        cache_hint: str = "persona-discussion",
    ) -> str:
        if use_mock or not self._client:
            return self._mock_response(system_prompt, user_prompt)

        # Prompt caching is activated via cache_control on the system prompt block.
        try:
            message = self._client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=700,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "5m"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
                metadata={"cache_hint": cache_hint},
            )
            return "".join(
                block.text for block in message.content if getattr(block, "type", "") == "text"
            ).strip()
        except Exception:
            return self._mock_response(system_prompt, user_prompt)

    def _mock_response(self, system_prompt: str, user_prompt: str) -> str:
        condensed_system = system_prompt.split(".")[0].strip()
        return (
            f"[MOCK:{condensed_system}] "
            f"{user_prompt[:220]} ... 실행 가능한 소규모 검증(시장 테스트, 제작비 추정, "
            "초기 고객 인터뷰)을 먼저 배치하고 리스크를 계량화해야 합니다."
        )
