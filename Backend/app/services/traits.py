from dataclasses import dataclass
from typing import Dict


def _clamp(val: int) -> int:
    return max(0, min(100, int(val)))


@dataclass
class TraitSet:
    creativity: int = 50
    logic: int = 50
    critical_thinking: int = 50
    data_dependency: int = 50
    cautiousness: int = 50
    drive: int = 50

    @classmethod
    def from_dict(cls, d: Dict) -> "TraitSet":
        return cls(
            creativity=_clamp(d.get("creativity", 50)),
            logic=_clamp(d.get("logic", 50)),
            critical_thinking=_clamp(d.get("critical_thinking", d.get("critical", 50))),
            data_dependency=_clamp(d.get("data_dependency", d.get("data_dependence", 50))),
            cautiousness=_clamp(d.get("cautiousness", 50)),
            drive=_clamp(d.get("drive", 50)),
        )

    def to_dict(self) -> Dict:
        return {
            "creativity": self.creativity,
            "logic": self.logic,
            "critical_thinking": self.critical_thinking,
            "data_dependency": self.data_dependency,
            "cautiousness": self.cautiousness,
            "drive": self.drive,
        }

    def summary_blurb(self) -> str:
        """Return a short instruction blurb describing how the assistant should behave."""
        parts = []
        if self.creativity >= 66:
            parts.append("Prioritize creative, novel suggestions.")
        elif self.creativity <= 33:
            parts.append("Avoid speculative or overly imaginative proposals.")
        else:
            parts.append("Balance creativity with practicality.")

        if self.logic >= 66:
            parts.append("Favor step-by-step logical reasoning and explicit justification.")
        if self.critical_thinking >= 66:
            parts.append("Apply critical scrutiny to claims and surface weaknesses.")
        if self.data_dependency >= 66:
            parts.append("Prefer data-backed evidence and cite sources when possible.")
        if self.cautiousness >= 66:
            parts.append("When uncertain, ask for clarification before executing.")
        if self.drive >= 66:
            parts.append("If applicable, propose concrete actions and follow-through steps.")

        return " ".join(parts)

    def get_llm_params(self) -> Dict:
        """
        6개 슬라이더(0~100) 값을 기반으로 LLM 생성 파라미터를 동적으로 계산한다.

        매핑 로직:
        ┌─────────────────┬──────────────────────────────────────────────────────┐
        │ 파라미터         │ 결정 요인                                             │
        ├─────────────────┼──────────────────────────────────────────────────────┤
        │ temperature     │ creativity ↑ → 높음 / cautiousness ↑ → 낮음          │
        │ top_p           │ logic ↑ → 낮음(결정론적) / creativity ↑ → 높음       │
        │ max_tokens      │ drive ↑ → 길게 / cautiousness ↑ → 짧게               │
        └─────────────────┴──────────────────────────────────────────────────────┘

        반환 예시: {"temperature": 0.72, "top_p": 0.88, "max_tokens": 1600}
        이 값은 gpt-5-mini 직접 호출 시 payload에 실제 적용된다.
        Claude Sonnet 호출 시에는 시스템 프롬프트 스타일 힌트로 반영된다.
        """
        creativity_f = self.creativity / 100.0
        cautiousness_f = self.cautiousness / 100.0
        logic_f = self.logic / 100.0
        drive_f = self.drive / 100.0

        # temperature: 기본 0.50, creativity(+0.45) / cautiousness(-0.30)로 조정
        # 범위: [0.10, 1.00]
        temperature = 0.50 + (creativity_f * 0.45) - (cautiousness_f * 0.30)
        temperature = round(max(0.10, min(1.00, temperature)), 2)

        # top_p: 기본 0.98, logic(-0.25) / creativity(+0.05)로 조정
        # logic이 높을수록 greedy에 가까워지며 일관된 답변 생성
        # 범위: [0.70, 0.99]
        top_p = 0.98 - (logic_f * 0.25) + (creativity_f * 0.05)
        top_p = round(max(0.70, min(0.99, top_p)), 2)

        # max_tokens: 기본 900, drive(+1300) / cautiousness(-400)로 조정
        # drive가 높으면 상세한 행동 계획, cautiousness가 높으면 핵심만
        # 범위: [600, 2400]
        max_tokens = int(900 + drive_f * 1300 - cautiousness_f * 400)
        max_tokens = max(600, min(2400, max_tokens))

        return {
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
