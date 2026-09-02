"""Env kill-switches: KD_BANDIT_MODE={ucb,ts,fgts_va} > KD_DISABLE_BANDIT_TS=1 (→ucb) > KD_DISABLE_FGTS_VA=1 (→ts) > default fgts_va."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import redis.asyncio as redis_aio

from .domain import Mode, score_cell
from .entities import CellState
from .keys import cell_key
from .params import (
    CELL_TTL_S, 
    UCB_ALPHA
)


logger = logging.getLogger(__name__)


def _resolve_mode(override: Mode | None = None) -> Mode:
    if override is not None:
        return override
    if "KD_BANDIT_MODE" in os.environ:
        explicit = os.environ["KD_BANDIT_MODE"].strip().lower()
        if explicit in ("ucb", "ts", "fgts_va"):
            return explicit  # type: ignore[return-value]
    if "KD_DISABLE_BANDIT_TS" in os.environ and os.environ["KD_DISABLE_BANDIT_TS"] == "1":
        return "ucb"
    if "KD_DISABLE_FGTS_VA" in os.environ and os.environ["KD_DISABLE_FGTS_VA"] == "1":
        return "ts"
    return "fgts_va"


# numpy.Generator draws are safe to call concurrently from asyncio coroutines.
_RNG = np.random.default_rng()

try:
    logger.info(f"[pareto] bandit scoring mode at startup: {_resolve_mode()}")
except Exception:
    pass


async def get_cell_state(
    deployment: str,
    task: str,
    *,
    redis: "redis_aio.Redis | None",
) -> CellState | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(cell_key(deployment, task))
        if raw is not None:
            if isinstance(raw, bytes):
                raw = raw.decode()
            return CellState.from_dict(json.loads(raw))
    except Exception as e:
        logger.debug(f"[pareto] cell read failed for {deployment}:{task}: {e}")
        return None
    return None


async def save_cell_state(
    state: CellState,
    *,
    redis: "redis_aio.Redis | None",
) -> bool:
    if redis is None:
        return False
    try:
        await redis.set(
            cell_key(state.deployment, state.task),
            json.dumps(state.to_dict()),
            ex = CELL_TTL_S,
        )
        return True
    except Exception as e:
        logger.debug(f"[pareto] cell write failed for {state.deployment}:{state.task}: {e}")
        return False





async def update(
    deployment: str,
    task: str,
    context: np.ndarray,
    reward: float,
    *,
    redis: "redis_aio.Redis | None",
) -> bool:
    """Posterior advance is mode-agnostic; flipping KD_BANDIT_MODE later reuses accumulated state."""
    if redis is None:
        return False
    cell = await get_cell_state(deployment, task, redis = redis)
    if cell is None:
        cell = CellState.fresh(deployment, task, benchmark_prior = 0.0)
    cell.apply_update(context, reward)
    ok = await save_cell_state(cell, redis = redis)
    if ok:
        outcome = "positive" if reward > 0.5 else ("neutral" if reward > 0 else "negative")
        _record_update(task, outcome)
        _record_sigma_sq(cell.sigma_sq_ewma)
    return ok


async def predict_top_k(
    task: str,
    context: np.ndarray,
    candidate_deployments: list[str],
    *,
    redis: "redis_aio.Redis | None",
    k: int = 3,
    alpha: float = UCB_ALPHA,
    mode: Mode | None = None,
) -> list[tuple[str, float, int]]:
    if not candidate_deployments:
        return []
    resolved_mode = _resolve_mode(mode)
    cells = await asyncio.gather(
        *[get_cell_state(d, task, redis = redis) for d in candidate_deployments]
    )
    scored: list[tuple[str, float, int]] = []
    for deployment, cell in zip(candidate_deployments, cells):
        if cell is None:
            cell = CellState.fresh(deployment, task, benchmark_prior = 0.0)
        total, _exploit, _bonus = score_cell(
            cell, 
            context, 
            resolved_mode, 
            rng = _RNG, 
            alpha = alpha)
        scored.append((deployment, total, cell.n_obs))
    scored.sort(key = lambda x: (-x[1], x[2], x[0]))
    _record_predict(task, resolved_mode)
    if scored:
        _record_score(scored[0][1], resolved_mode)
    return scored[: max(1, k)]





_metric_instruments: dict[str, Any] = {}


def _ensure_metrics() -> dict[str, Any]:
    return {}


def _record_predict(*args, **kwargs):
    return


def _track_latency_for_bandit(*args, **kwargs) -> None:
    return

def _record_update(*args, **kwargs):
    return


def _record_score(*args, **kwargs):
    return


def _record_sigma_sq(*args, **kwargs):
    return


def _record_shadow_agreement(*args, **kwargs):
    return

