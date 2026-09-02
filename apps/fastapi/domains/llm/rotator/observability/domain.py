"""Pure attribute helpers for observability — minimal for rotator."""
from __future__ import annotations

from .keys import SYSTEM_LITELLM_ROTATOR


def system_for_deployment(deployment_id: str | None) -> str:
    """LiteLLM deployment_id prefix → `gen_ai.system`; unprefixed → `litellm-rotator`."""
    if not deployment_id:
        return SYSTEM_LITELLM_ROTATOR
    prefix, sep, _ = deployment_id.partition("/")
    return prefix if sep else SYSTEM_LITELLM_ROTATOR
