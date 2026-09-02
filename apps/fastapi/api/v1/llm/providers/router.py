"""Providers engine — key validation → auto-disable + live models per provider.

- GET /providers          → all providers with probe (ok/status/enabled/disabled_reason/n_free_models)
- GET /providers/health   → same as probes but explicit health view
- GET /providers/{id}/models → live discovery fan-out for that provider (best script)
- GET /providers/models/live → all live models aggregated (parallel fan-out)
- POST /providers/refresh → force Secret detector + re-probe (detector also runs every 60s + Secret volume watch)
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from domains.llm.rotator.discovery import PROVIDERS, list_all_alive_models, list_provider_free_models, probe_provider_key, missing_required_keys
from domains.llm.rotator.status import get_status, refresh_all

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_providers() -> JSONResponse:
    # ensure at least one probe cached; if empty, refresh
    status = get_status()
    if not status:
        await refresh_all()
        status = get_status() or {}
    # build view merging registry + status
    views = []
    for pid, cfg in PROVIDERS.items():
        st = status.get(pid, {})
        views.append({
            "id": pid,
            "name": pid,
            "key_env": cfg.key_env,
            "registry_enabled": cfg.enabled,
            "required": bool(getattr(cfg, "required", False)),
            "enabled": st.get("enabled", cfg.enabled and bool(st.get("ok"))),
            "ok": st.get("ok"),
            "status": st.get("status"),
            "disabled_reason": st.get("disabled_reason"),
            "has_key": st.get("key_present"),
            "n_free_models": st.get("n_free_models", 0),
            "n_total_models": st.get("n_total_models", 0),
            "checked_at": st.get("checked_at"),
            "error": (st.get("probe") or {}).get("error"),
        })
    missing = missing_required_keys()
    return JSONResponse(content={"providers": views, "missing_required": missing, "ready": not missing})


@router.get("/health")
async def providers_health() -> JSONResponse:
    status = get_status()
    if not status:
        await refresh_all()
        status = get_status() or {}
    results = []
    for pid in PROVIDERS:
        st = status.get(pid, {})
        probe = st.get("probe") or {}
        results.append({"id": pid, **probe, "enabled": st.get("enabled")})
    return JSONResponse(content={"results": results})


@router.get("/models/live")
async def live_models_all() -> JSONResponse:
    """Best script: parallel fan-out per active provider, free-filtered."""
    by_provider = await list_all_alive_models()
    out = {pid: sorted(r.model_id for r in recs if r.model_id) for pid, recs in by_provider.items()}
    total = sum(len(v) for v in out.values())
    return JSONResponse(content={"providers": out, "total": total})


@router.post("/refresh")
async def refresh_providers() -> JSONResponse:
    status = await refresh_all()
    return JSONResponse(content={"refreshed": True, "providers": list(status.keys())})


@router.get("/{pid}/models")
async def provider_models(pid: str) -> JSONResponse:
    if pid not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
    # probe on demand if not cached
    probe = await probe_provider_key(pid)
    available = await list_provider_free_models(pid)
    st = get_status(pid) or {}
    return JSONResponse(content={
        "id": pid,
        "probe": probe,
        "enabled": st.get("enabled"),
        "available": available,
        "count": len(available),
    })


@router.post("/{pid}/test")
async def test_provider(pid: str) -> JSONResponse:
    if pid not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
    probe = await probe_provider_key(pid)
    return JSONResponse(content={"id": pid, **probe})
