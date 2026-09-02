from __future__ import annotations

from .domain import is_heavyweight
from .keys import EMBED_MODEL_NAME
from .service import (
    build_llm_fallback_chain,
    build_pinned_chain_any,
    chat_judge_async,
    chat_judge_bandit_async,
    embed_via_router_async,
    embed_via_router_sync,
    ensure_dynamic_catalog,
    get_entries_for_group,
    get_parent_group,
    init_dynamic_catalog,
    init_dynamic_catalog_sync,
    rerank_via_router_async,
    reset_rotator,
    start_catalog_refresh_loop,
    stop_catalog_refresh_loop,
)

__all__ = [
    "EMBED_MODEL_NAME",
    "build_llm_fallback_chain",
    "build_pinned_chain_any",
    "chat_judge_async",
    "chat_judge_bandit_async",
    "embed_via_router_async",
    "embed_via_router_sync",
    "ensure_dynamic_catalog",
    "get_entries_for_group",
    "get_parent_group",
    "init_dynamic_catalog",
    "init_dynamic_catalog_sync",
    "is_heavyweight",
    "rerank_via_router_async",
    "reset_rotator",
    "start_catalog_refresh_loop",
    "stop_catalog_refresh_loop",
]
