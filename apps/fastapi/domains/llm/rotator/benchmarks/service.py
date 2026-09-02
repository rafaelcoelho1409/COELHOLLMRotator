"""Three live benchmark sources (no-auth): OpenLM Arena (HTML), oolong-tea code.json, OpenEvals."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

import httpx
import redis.asyncio as redis_aio
from rapidfuzz import fuzz as _rf_fuzz, process as _rf_process

from .config import CACHE_TTL
from .domain import (
    compute_composite_score,
    merge_leaderboards,
    normalize_model_name,
    parse_oolong_payload,
    parse_openevals_payload,
    parse_openlm_table,
)
from .keys import canonical_key, leaderboard_key, scores_key
from .params import (
    FUZZY_THRESHOLD,
    HTTP_TIMEOUT_S,
    PROVIDER_TIER,
    STEP_WEIGHTS,
)


logger = logging.getLogger(__name__)


# Module-level by design — survive across requests within one worker.
_known_canonicals: set[str] = set()
_inmem_leaderboards: dict[str, tuple[float, dict[str, dict[str, float]]]] = {}
_metric_instruments: dict[str, Any] = {}


async def canonicalize(
    provider_id: str,
    *,
    redis: redis_aio.Redis | None = None,
    fuzzy_threshold: int = FUZZY_THRESHOLD,
) -> str:
    """Redis (1y TTL) → L1 heuristic → L2 RapidFuzz against known canonicals."""
    pid = (provider_id or "").strip()
    if not pid:
        return ""
    if redis is not None:
        try:
            cached = await redis.get(canonical_key(pid))
            if cached:
                if isinstance(cached, bytes):
                    cached = cached.decode()
                _record_canonical("cache")
                _known_canonicals.add(cached)
                return cached
        except Exception as e:
            logger.debug(f"[bench] canonical cache read failed for {pid}: {e}")
    candidate = normalize_model_name(pid)
    resolved = candidate
    layer = "heuristic"
    if _known_canonicals:
        match = _rf_process.extractOne(
            candidate,
            list(_known_canonicals),
            scorer = _rf_fuzz.token_set_ratio,
        )
        if match and match[1] >= fuzzy_threshold:
            resolved = match[0]
            layer = "fuzzy"
    _known_canonicals.add(resolved)
    _record_canonical(layer)
    if redis is not None:
        try:
            await redis.set(
                canonical_key(pid), 
                resolved, 
                ex = CACHE_TTL.canonical)
        except Exception as e:
            logger.debug(f"[bench] canonical cache write failed for {pid}: {e}")
    return resolved


async def _get_cached_leaderboard(
    source: str,
    fetcher: Callable[[httpx.AsyncClient], Awaitable[dict[str, dict[str, float]]]],
    redis: redis_aio.Redis | None,
    client: httpx.AsyncClient,
) -> dict[str, dict[str, float]]:
    """L1 in-mem → L2 Redis → fetch."""
    now = time.time()
    cached = _inmem_leaderboards.get(source)
    if cached and (now - cached[0]) < CACHE_TTL.leaderboard:
        _record_cache_hit("inmem")
        return cached[1]
    if redis is not None:
        try:
            raw = await redis.get(leaderboard_key(source))
            if raw:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                _inmem_leaderboards[source] = (now, data)
                _record_cache_hit("redis_lb")
                return data
        except Exception as e:
            logger.debug(f"[bench] L2 read failed for {source}: {e}")
    t0 = time.time()
    try:
        data = await fetcher(client)
        outcome = "ok"
        logger.info(f"[bench] {source}: fetched {len(data)} models")
    except Exception as e:
        outcome = type(e).__name__
        logger.warning(f"[bench] {source} fetch failed: {outcome}: {str(e)[:200]}")
        data = {}
    _record_fetch(source, outcome, time.time() - t0)
    _inmem_leaderboards[source] = (now, data)
    if redis is not None:
        try:
            ttl = CACHE_TTL.leaderboard if data else CACHE_TTL.empty_payload
            await redis.set(leaderboard_key(source), json.dumps(data), ex=ttl)
        except Exception as e:
            logger.debug(f"[bench] L2 write failed for {source}: {e}")
    return data


_BENCH_HEADERS = {"Accept": "application/json", "User-Agent": "coelho-llm-rotator/1.0"}


async def _fetch_openlm_arena(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    # SOTA Aug 2026: openlm.ai 301→lmarena.ai (Arena) Next.js; old HTML table gone.
    # Try JSON APIs first (HuggingFace Space + arena.ai), fallback to legacy HTML scrape.
    for url, headers, is_json in [
        ("https://huggingface.co/api/datasets/lmarena-ai/arena-leaderboard/leaderboard", _BENCH_HEADERS | {"Accept": "application/json"}, True),
        ("https://arena.ai/api/leaderboard", _BENCH_HEADERS | {"Accept": "application/json"}, True),
        ("https://lmarena.ai/api/leaderboard", _BENCH_HEADERS | {"Accept": "application/json"}, True),
        ("https://openlm.ai/chatbot-arena/", {"User-Agent": "coelho-llm-rotator/1.0 (free-tier-rotator)", "Accept": "text/html,application/xhtml+xml"}, False),
    ]:
        try:
            resp = await client.get(url, headers=headers, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
            resp.raise_for_status()
            if is_json:
                try:
                    data = resp.json()
                    # HF Space returns {"leaderboard": [{"model":..., "elo":...}]} or plain dict
                    if isinstance(data, dict) and "leaderboard" in data:
                        data = data["leaderboard"]
                    if isinstance(data, list):
                        # list of {model, arena_score} → convert to openlm table shape
                        tmp = {}
                        for row in data:
                            name = row.get("model") or row.get("id") or row.get("name") or ""
                            elo = row.get("elo") or row.get("arena_score") or row.get("score")
                            if name and elo:
                                try: tmp[name] = {"lmarena": float(elo)}
                                except: pass
                        if tmp:
                            # normalize via same path as HTML parser expects
                            from .domain import normalize_model_name
                            return {normalize_model_name(k): v for k, v in tmp.items()}
                    if isinstance(data, dict):
                        # assume already {model: {lmarena:..}}
                        from .domain import normalize_model_name
                        return {normalize_model_name(k): v for k, v in data.items() if isinstance(v, dict)}
                except Exception as e:
                    logger.debug(f"[bench] openlm JSON {url} parse failed: {e}")
                    continue
            else:
                parsed = parse_openlm_table(resp.text)
                if parsed:
                    return parsed
        except Exception as e:
            logger.debug(f"[bench] openlm {url} failed: {type(e).__name__}: {str(e)[:120]}")
            continue
    logger.warning("[bench] openlm_arena all endpoints failed → 0")
    return {}


async def _fetch_oolong_code(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    """2-step: latest.json pointer → data/{path}/code.json."""
    base = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data"
    try:
        ptr = await client.get(f"{base}/latest.json", headers=_BENCH_HEADERS, timeout=HTTP_TIMEOUT_S)
        ptr.raise_for_status()
        snapshot_path = (ptr.json() or {}).get("path") or (ptr.json() or {}).get("date")
    except Exception as e:
        logger.warning(f"[bench] oolong latest pointer failed: {e}")
        return {}
    if not snapshot_path:
        return {}
    try:
        resp = await client.get(
            f"{base}/{snapshot_path}/code.json",
            headers = _BENCH_HEADERS,
            timeout = HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        return parse_oolong_payload(resp.json())
    except Exception as e:
        logger.warning(f"[bench] oolong code.json fetch failed: {e}")
        return {}


async def _fetch_openevals(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    # SOTA Aug 2026: HF Hub docs — OpenEvals/leaderboard-data aggregates into one Parquet
    # hf://datasets/OpenEvals/leaderboard-data/data/train-00000-of-00001.parquet
    # JSON API at /api/datasets/.../leaderboard returns [] (empty) — use Parquet.
    import os, tempfile
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or ""
    # 1) Parquet via HF resolve (fastest cross-benchmark view)
    for url in [
        "https://huggingface.co/datasets/OpenEvals/leaderboard-data/resolve/main/data/train-00000-of-00001.parquet",
        "https://huggingface.co/datasets/OpenEvals/leaderboard-data/resolve/main/data/train.parquet",
    ]:
        try:
            headers = dict(_BENCH_HEADERS)
            if hf_token:
                headers["Authorization"] = f"Bearer {hf_token}"
            resp = await client.get(url, headers=headers, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
            if resp.status_code == 429:
                import asyncio as _aio; await _aio.sleep(2); continue
            resp.raise_for_status()
            # write to temp and read via pandas/pyarrow
            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
                tmp.write(resp.content); tmp_path = tmp.name
            try:
                import pandas as pd
                df = pd.read_parquet(tmp_path)
                out: dict[str, dict[str, float]] = {}
                for _, row in df.iterrows():
                    name = str(row.get("model_name") or row.get("model_id") or row.get("model") or "").strip()
                    if not name: continue
                    scores: dict[str, float] = {}
                    for col in ["aime2026_score", "mmluPro_score", "gpqa_score", "hle_score", "gsm8k_score", "math_score"]:
                        v = row.get(col)
                        if v is not None and str(v) not in ("", "nan"):
                            try: scores[col.replace("_score","").replace("mmluPro","mmlu_pro")] = float(v)
                            except: pass
                    # also map generic
                    for k in ["mmlu_pro", "gpqa", "hle", "gsm8k", "math", "aime"]:
                        if k not in scores and row.get(k) is not None:
                            try: scores[k] = float(row.get(k))
                            except: pass
                    if scores:
                        from .domain import normalize_model_name
                        out[normalize_model_name(name)] = scores
                if out:
                    logger.info(f"[bench] openevals Parquet {url} → {len(out)} models")
                    return out
            finally:
                try: import os as _os; _os.unlink(tmp_path)
                except: pass
        except Exception as e:
            logger.debug(f"[bench] openevals Parquet {url} failed: {type(e).__name__}: {str(e)[:180]}")
            continue
    # 2) fallback legacy JSON endpoints
    for url in [
        "https://huggingface.co/datasets/OpenEvals/leaderboard-data/resolve/main/leaderboard.json",
        "https://huggingface.co/spaces/OpenEvals/every-leaderboards/resolve/main/leaderboard.json",
    ]:
        for attempt in range(3):
            try:
                headers = dict(_BENCH_HEADERS)
                if hf_token:
                    headers["Authorization"] = f"Bearer {hf_token}"
                resp = await client.get(url, headers=headers, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
                if resp.status_code == 429:
                    import asyncio as _aio; await _aio.sleep(int(resp.headers.get("retry-after","2"))); continue
                resp.raise_for_status()
                return parse_openevals_payload(resp.json())
            except Exception as e:
                logger.debug(f"[bench] openevals JSON {url} {type(e).__name__}: {str(e)[:120]}")
                break
    logger.warning("[bench] openevals all endpoints failed → 0")
    return {}


_SOURCES: dict[str, Callable[[httpx.AsyncClient], Awaitable[dict[str, dict[str, float]]]]] = {
    "openlm_arena": _fetch_openlm_arena,
    "oolong_code":  _fetch_oolong_code,
    "openevals":    _fetch_openevals,
}


async def get_benchmarks(
    canonical_name: str,
    *,
    redis: redis_aio.Redis | None = None,
) -> dict[str, float]:
    """L3 Redis → fan out _SOURCES in parallel → merge → cache."""
    canonical = (canonical_name or "").strip().lower()
    if not canonical:
        return {}
    if redis is not None:
        try:
            cached = await redis.get(scores_key(canonical))
            if cached:
                _record_cache_hit("scores")
                return json.loads(cached if isinstance(cached, str) else cached.decode())
        except Exception as e:
            logger.debug(f"[bench] L3 read failed for {canonical}: {e}")
    async with httpx.AsyncClient() as client:
        boards = await asyncio.gather(
            *[_get_cached_leaderboard(name, fetcher, redis, client)
              for name, fetcher in _SOURCES.items()],
            return_exceptions = True,
        )
    valid = [b for b in boards if isinstance(b, dict)]
    merged = merge_leaderboards(canonical, valid)
    if redis is not None:
        try:
            ttl = CACHE_TTL.scores if merged else CACHE_TTL.empty_payload
            await redis.set(
                scores_key(canonical), 
                json.dumps(merged), 
                ex = ttl)
        except Exception as e:
            logger.debug(f"[bench] L3 write failed for {canonical}: {e}")
    return merged


async def rank_for_step(
    step: str,
    alive_models: list,
    *,
    redis: redis_aio.Redis | None = None,
) -> list[tuple[Any, float]]:
    weights = STEP_WEIGHTS.get(step, STEP_WEIGHTS["dd-all"])
    if not alive_models:
        return []
    canonicals = await asyncio.gather(
        *[canonicalize(getattr(m, "model_id", ""), redis = redis) for m in alive_models]
    )
    async with httpx.AsyncClient() as client:
        board_results = await asyncio.gather(
            *[_get_cached_leaderboard(name, fetcher, redis, client)
              for name, fetcher in _SOURCES.items()],
            return_exceptions = True,
        )
    valid_boards = [b for b in board_results if isinstance(b, dict)]
    ranked: list[tuple[Any, float]] = []
    for record, canonical in zip(alive_models, canonicals):
        scores = merge_leaderboards(canonical, valid_boards)
        composite = compute_composite_score(scores, weights)
        ranked.append((record, composite))
    ranked.sort(
        key = lambda x: (
            -x[1],
            PROVIDER_TIER.get(getattr(x[0], "provider", ""), 99),
            getattr(x[0], "model_id", ""),
        )
    )
    return ranked


def get_composite_cached(canonical: str, weights: dict[str, float] | None = None) -> float:
    """Sync composite from in-mem leaderboards (no network) — for Router cold-start sort."""
    if not canonical:
        return 0.0
    boards = [v[1] for v in _inmem_leaderboards.values() if isinstance(v, tuple) and len(v)==2 and isinstance(v[1], dict) and v[1]]
    if not boards:
        return 0.0
    from .domain import merge_leaderboards, compute_composite_score, true_skill_adjust, normalize_model_name
    from .params import STEP_WEIGHTS
    w = weights or STEP_WEIGHTS["general"]
    canon_norm = normalize_model_name(canonical)
    scores = merge_leaderboards(canon_norm, boards)
    if not scores:
        return 0.0
    comp = compute_composite_score(scores, w)
    # apply same TrueSkill conservative as leaderboard
    return true_skill_adjust(comp, len(scores), n_expected=3)


def _ensure_metrics() -> dict[str, Any]:
    return {}


def _record_fetch(*args, **kwargs):
    return


def _record_cache_hit(*args, **kwargs):
    return


def _record_canonical(*args, **kwargs):
    return

