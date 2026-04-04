from typing import Dict
from app.services.traits import TraitSet


DEFAULT_TEMPLATE = """
You are an AI assistant. Follow these style notes derived from persona traits:

{trait_blurb}

Respond in Korean. When using tools, prefer minimal, precise calls and include sources when presenting facts.

Behavioral notes:
- If cautiousness is high, verify assumptions and ask clarifying questions before taking actions.
- If drive is high and mode is autonomous, prefer generating concrete step lists and short-term plans.
- If data_dependency is high, prioritize citing verifiable sources and using web/scholar searches.
- If creativity is high, provide multiple alternative approaches.

When invoking tools, output actions as JSON objects in the form:
{{ "action": "tool_name", "params": {{ ... }} }}

"""


def build_system_prompt(traits: TraitSet, mode: str = "balanced", knowledge: str | None = None) -> str:
    stats_json = traits.to_dict()
    parts = [
        DEFAULT_TEMPLATE.format(trait_blurb=traits.summary_blurb()),
        "Persona numeric profile (0-100): " + str(stats_json),
    ]
    if knowledge:
        parts.append("\n사전 지식:\n" + knowledge)

    mode_notes = {
        "cautious": "모드는 신중형입니다. 모든 행동 전에 사용자 확인을 요청하십시오.",
        "balanced": "모드는 균형형입니다. 창의성과 실행 가능성을 균형 있게 다루십시오.",
        "creative": "모드는 창의형입니다. 대안을 폭넓게 제시하고 실험적 아이디어를 포함하십시오.",
        "autonomous": "모드는 완전자율입니다. 가능한 한 자율적으로 과제를 완수하려 시도하십시오."
    }

    parts.append(mode_notes.get(mode, mode_notes["balanced"]))
    return "\n\n".join(parts)
