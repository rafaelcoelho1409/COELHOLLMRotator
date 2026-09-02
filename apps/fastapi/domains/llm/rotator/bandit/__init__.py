"""Adaptive contextual bandit routing (FGTS-VA NeurIPS 2025 default; LinUCB/LinTS via env kill-switches share the same (A_a, b_a) state)."""
from __future__ import annotations

from .domain import compose_reward, make_context_vector
from .entities import CellState
from .service import (
    get_cell_state,
    predict_top_k,
    save_cell_state,
    update,
)


__all__ = [
    "CellState",
    "compose_reward",
    "get_cell_state",
    "make_context_vector",
    "predict_top_k",
    "save_cell_state",
    "update",
]
