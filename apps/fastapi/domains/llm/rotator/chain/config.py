from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class DynamicStepConfig:
    group:     str
    top_k:     int
    timeout_s: int


DYNAMIC_STEPS: dict[str, DynamicStepConfig] = {
    "general": DynamicStepConfig(group = "general", top_k = 50, timeout_s = 120),
}


@dataclass(frozen = True, slots = True)
class JudgeConfig:
    """general-grader cells kept separate from synthesizer cells — binary vs continuous reward shape."""
    dd_process:         str   = "general-grader"
    expected_latency_s: float = 4.0
    bandit_top_k:       int   = 10


JUDGE = JudgeConfig()
