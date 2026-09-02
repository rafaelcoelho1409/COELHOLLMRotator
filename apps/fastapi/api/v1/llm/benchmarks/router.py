"""Benchmarks leaderboard — unified view over OpenLM Arena / oolong / OpenEvals.

Universal Rotator: single general composite.

- GET /benchmarks?canonical=qwen/qwen3.5-397b          → scores for one model
- GET /benchmarks/leaderboard?limit=20&format=json|csv  → ranked best→worst
- GET /benchmarks/sources                               → source meta
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
from typing import Any

import redis.asyncio as redis_aio
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import logging

from domains.llm.rotator.benchmarks import get_benchmarks
from domains.llm.rotator.benchmarks.config import CACHE_TTL
from domains.llm.rotator.benchmarks.domain import merge_leaderboards
from domains.llm.rotator.benchmarks.params import STEP_WEIGHTS

logger = logging.getLogger(__name__)

router = APIRouter()


def _redis() -> redis_aio.Redis | None:
    host = os.getenv("REDIS_HOST")
    if not host or not host.strip():
        return None
    try:
        port = int(os.getenv("REDIS_PORT", "6379"))
    except ValueError:
        port = 6379
    pw = os.getenv("REDIS_PASSWORD", "")
    url = f"redis://:{pw}@{host}:{port}" if pw else f"redis://{host}:{port}"
    try:
        return redis_aio.from_url(url, socket_connect_timeout=2, socket_timeout=3)
    except Exception:
        return None


@router.get("")
async def get_model_benchmarks(
    request: Request,
    canonical: str = Query(..., description="Canonical model id, e.g. qwen/qwen3.5-397b"),
    source: str | None = Query(None, description="Filter source: openlm_arena|oolong_code|openevals"),
) -> JSONResponse:
    """Scores for a single canonical model (provenance per source)."""
    rds = _redis()
    canon = (canonical or "").strip().lower()
    if not canon:
        raise HTTPException(status_code=400, detail="canonical is required")
    scores = await get_benchmarks(canon, redis=rds)
    if rds:
        try:
            await rds.aclose()
        except Exception:
            pass
    # optional source filter
    if source:
        scores = {k: v for k, v in scores.items() if k == source}
    # ETag for client caching
    etag = hashlib.sha256(json.dumps(scores, sort_keys=True).encode()).hexdigest()[:16]
    if request.headers.get("if-none-match") == etag:
        return JSONResponse(status_code=304, content=None, headers={"ETag": etag})
    return JSONResponse(
        content={"canonical": canon, "scores": scores, "sources": list(scores.keys()), "count": len(scores)},
        headers={"ETag": etag, "Cache-Control": f"public, max-age={CACHE_TTL.scores // 2}"},
    )


@router.get("/leaderboard")
async def leaderboard(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    format: str = Query("json", pattern="^(json|csv)$"),
    source: str | None = Query(None, description="Optional source filter for CSV parity"),
) -> Any:
    """Universal: rank the union of latest benchmarks by single general composite."""
    weights = STEP_WEIGHTS["general"]
    rds = _redis()
    # 1) fetch 3 leaderboards once (uses in-mem + Redis + httpx, same as get_benchmarks)
    from domains.llm.rotator.benchmarks.service import _SOURCES, _get_cached_leaderboard
    import asyncio, httpx
    async with httpx.AsyncClient() as client:
        boards = await asyncio.gather(*[_get_cached_leaderboard(name, fn, rds, client) for name, fn in _SOURCES.items()], return_exceptions=True)
    valid_boards = [b for b in boards if isinstance(b, dict)]
    if not valid_boards:
        if rds:
            try: await rds.aclose()
            except Exception: pass
        raise HTTPException(status_code=503, detail="benchmark sources unavailable, try again in 30s")
    # 2) union canonicals across all boards
    union: set[str] = set()
    for b in valid_boards: union.update(b.keys())
    # 3) compute composite per canonical + TrueSkill μ−3σ conservative (missing≠0)
    from domains.llm.rotator.benchmarks.domain import compute_composite_score, merge_leaderboards, true_skill_adjust
    ranked = []
    for canon in union:
        scores = merge_leaderboards(canon, valid_boards)
        if source and source not in scores:
            continue
        if not scores:
            continue
        composite = compute_composite_score(scores, weights)
        adjusted = true_skill_adjust(composite, len(scores), n_expected=len(valid_boards))
        ranked.append({"canonical": canon, "composite": composite, "adjusted": adjusted, "scores": scores, "n_sources": len(scores)})
    ranked.sort(key=lambda x: (-x["adjusted"], -x["composite"], x["canonical"]))
    # ETag before limit
    etag = hashlib.sha256(json.dumps([(r["canonical"], r["adjusted"]) for r in ranked[:limit]], sort_keys=True).encode()).hexdigest()[:16]
    if request.headers.get("if-none-match") == etag:
        if rds:
            try: await rds.aclose()
            except Exception: pass
        return JSONResponse(status_code=304, content=None, headers={"ETag": etag})
    # 4) link to provider model_ids — reverse canonicalize via live discovery + normalize_model_name
    # This is the "connect names to provider pool" step you described: benchmark canonical → provider model_id
    from domains.llm.rotator.benchmarks.domain import normalize_model_name
    from domains.llm.rotator.discovery import list_all_alive_models
    provider_hits: dict[str, list[str]] = {c: [] for c in [r["canonical"] for r in ranked[:limit]]}
    try:
        live_by_provider = await list_all_alive_models()
        # live model_ids like "openai/gpt-oss-120b", "moonshotai/kimi-k2.6"
        for pid, recs in live_by_provider.items():
            for rec in recs:
                raw_id = (rec.model_id or "").strip()
                if not raw_id: continue
                # L1 normalize both sides for fuzzy family match
                norm_live = normalize_model_name(raw_id)
                for r in ranked[:limit]:
                    canon_norm = normalize_model_name(r["canonical"])
                    # exact canonical match OR live contains canonical family (e.g. kimi-k2.6 ↔ moonshotai/kimi-k2.6)
                    if canon_norm == norm_live or canon_norm in norm_live or norm_live in canon_norm:
                        label = f"{pid}/{raw_id}"
                        if label not in provider_hits[r["canonical"]]:
                            provider_hits[r["canonical"]].append(label)
    except Exception as e:
        logger.debug(f"[benchmarks] provider link failed: {e}")
    for r in ranked:
        r["providers"] = provider_hits.get(r["canonical"], [])
        # also expose if currently routable (at least one provider live)
        r["routable"] = bool(r["providers"])
        # sort providers for stable output
        r["providers"].sort()
    ranked = ranked[:limit]
    if rds:
        try: await rds.aclose()
        except Exception: pass
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["rank", "canonical", "composite", "adjusted", "n_sources", "providers", "scores_json"])
        w.writeheader()
        for i, r in enumerate(ranked, 1):
            w.writerow({"rank": i, "canonical": r["canonical"], "composite": f"{r['composite']:.4f}", "adjusted": f"{r['adjusted']:.4f}", "n_sources": r["n_sources"], "providers": ",".join(r["providers"]), "scores_json": json.dumps(r["scores"], ensure_ascii=False)})
        return PlainTextResponse(buf.getvalue(), headers={"ETag": etag, "Cache-Control": f"public, max-age={CACHE_TTL.leaderboard // 2}", "Content-Type": "text/csv; charset=utf-8"})
    return JSONResponse(
        content={"weights": weights, "count": len(ranked), "leaderboard": ranked, "sources": list(_SOURCES.keys())},
        headers={"ETag": etag, "Cache-Control": f"public, max-age={CACHE_TTL.leaderboard // 2}"},
    )


@router.get("/sources")
async def sources() -> JSONResponse:
    return JSONResponse(content={
        "sources": ["openlm_arena", "oolong_code", "openevals"],
        "steps": sorted(STEP_WEIGHTS.keys()),
        "cache_ttl": {"leaderboard": CACHE_TTL.leaderboard, "scores": CACHE_TTL.scores, "canonical": CACHE_TTL.canonical},
    })
