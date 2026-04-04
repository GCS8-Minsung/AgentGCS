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
