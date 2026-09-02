from __future__ import annotations


CACHE_PREFIX = "rotator:bandit:cell:"
RESERVATION_PREFIX = "rotator:bandit:reserved:"
PROVIDER_SLOT_PREFIX = "rotator:bandit:provider_slot:"

TASKS: tuple[str, ...] = (
    "general",
    "general-grader",
    "embed",
)
# Preserve old indices to avoid bandit corruption across migration (general=0, embed=4, general-grader=7).
_TASK_IDX: dict[str, int] = {
    "general": 0,
    "general-grader": 7,
    "embed": 4,
}

CONTEXT_PROVIDERS: tuple[str, ...] = (
    "groq",
    "nim",
    "cerebras",
    "mistral",
    "gemini",
)
_PROVIDER_IDX = {p: i for i, p in enumerate(CONTEXT_PROVIDERS)}


def cell_key(deployment: str, task: str) -> str:
    return f"{CACHE_PREFIX}{deployment}:{task}"


def reservation_key(deployment: str, task: str) -> str:
    return f"{RESERVATION_PREFIX}{task}:{deployment}"


def provider_slot_key(provider: str, slot_idx: int) -> str:
    return f"{PROVIDER_SLOT_PREFIX}{provider}:{slot_idx}"
