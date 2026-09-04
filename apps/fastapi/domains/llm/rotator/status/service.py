"""Provider status engine — key validation → auto-disable + Secret detector.

- probe_provider_key() via discovery/service is source of truth (401/403→invalid, 429→ok)
- auto-disable: provider.enabled = registry.enabled && probe.ok
- detector: watches Secret volume at /run/secrets/llm (Helm mounts coelho-llm-rotator-secret)
  + env polling fallback. On change → reload env + re-probe + invalidate Router.
- exposed via get_status() / is_enabled() for chain filtering and API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from domains.llm.rotator.discovery import probe_provider_key, PROVIDERS
from domains.llm.rotator.discovery.service import resolve_key as _resolve_key

logger = logging.getLogger(__name__)

# in-memory cache: pid → {"ok": bool, "status": str, "enabled": bool, "probe": dict, "checked_at": float, "key_present": bool, "disabled_reason": str|None}
_status_cache: dict[str, dict[str, Any]] = {}
_last_secret_hash: str | None = None
_refresh_task: asyncio.Task | None = None
_secret_path = Path("/run/secrets/llm")
_PROBE_INTERVAL_S = 60
_SECRET_POLL_S = 10

# Cross-process provider cooldown (402/429-quota outages). Backed by the
# bundled Valkey instance (k8s/helm's `valkey` dependency, wire-compatible
# with Redis — REDIS_HOST/PORT point at it, see values.yaml) so a disable
# from one process is visible to every other process immediately, instead
# of only living in this process's _status_cache.
_COOLDOWN_KEY_PREFIX = "llmrotator:cooldown:"


def _redis_conn():
    # lazy import — chain.service imports this module too; avoids a cycle at load time.
    from domains.llm.rotator.chain.service import _redis_sync_conn
    return _redis_sync_conn()


def _read_cooldown(pid: str) -> dict[str, Any] | None:
    """Active cooldown for pid, or None. Redis/Valkey key TTL is the cooldown itself — presence == active."""
    r = _redis_conn()
    if r is None:
        return None
    try:
        raw = r.get(_COOLDOWN_KEY_PREFIX + pid)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.debug(f"[status] cooldown read failed for {pid}: {e}")
        return None
    finally:
        try:
            r.close()
        except Exception:
            pass


def _active_local_cooldown(pid: str) -> dict[str, Any] | None:
    """Same-process fallback for _read_cooldown — covers the moment before
    Valkey is reachable (pod just started) and any transient connection
    blip, so a disable this process just made can never be clobbered by
    its own next refresh_all() tick even if the Redis/Valkey call fails."""
    ent = _status_cache.get(pid)
    if not ent:
        return None
    until = ent.get("cooldown_until")
    if until and until > time.time():
        return {"reason": ent.get("disabled_reason"), "until": until}
    return None


def disable_provider(provider: str, reason: str, cooldown_s: float) -> None:
    """Provider-wide disable with a TTL cooldown, visible cluster-wide (Redis/
    Valkey) and self-healing: the disable expires on its own (key TTL), and
    the next _status_loop probe (or a fresh request once re-enabled) tests
    if the provider is back.
    """
    until = time.time() + cooldown_s
    ent = _status_cache.get(provider)
    if ent is not None:
        ent["enabled"] = False
        ent["disabled_reason"] = reason
        ent["ok"] = False
        ent["cooldown_until"] = until
    else:
        _status_cache[provider] = {
            "id": provider, "enabled": False, "disabled_reason": reason,
            "ok": False, "cooldown_until": until,
        }
    r = _redis_conn()
    if r is not None:
        try:
            r.setex(
                _COOLDOWN_KEY_PREFIX + provider,
                max(1, int(cooldown_s)),
                json.dumps({"reason": reason, "until": until}),
            )
        except Exception as e:
            logger.warning(f"[status] cooldown redis write failed for {provider}: {e}")
        finally:
            try:
                r.close()
            except Exception:
                pass
    logger.warning(
        f"[status] {provider} disabled ({reason}) for {cooldown_s:.0f}s "
        f"(until {time.strftime('%H:%M:%S', time.localtime(until))})"
    )
    try:
        from domains.llm.rotator.chain.service import reset_rotator
        reset_rotator(bump_gen=True)  # cross-process rebuild signal, not just this worker
    except Exception:
        pass


def _secret_hash() -> str:
    """Hash of Secret files + env fallback. Changes when user does upload_env_to_k3d.py."""
    parts: list[str] = []
    if _secret_path.is_dir():
        try:
            for p in sorted(_secret_path.iterdir()):
                if p.is_file():
                    try:
                        parts.append(f"{p.name}:{p.read_text().strip()[:8]}:{p.stat().st_mtime_ns}")
                    except Exception:
                        parts.append(p.name)
                # also handle ..data symlink timestamp
            return "|".join(parts)
        except Exception:
            pass
    # fallback: env present keys hash
    for pid, cfg in sorted(PROVIDERS.items()):
        v = _resolve_key(cfg.key_env) or ""
        parts.append(f"{cfg.key_env}:{1 if v else 0}:{len(v)}")
    return "|".join(parts)


async def _probe_one(pid: str) -> dict[str, Any]:
    cfg = PROVIDERS[pid]
    key_present = bool(_resolve_key(cfg.key_env))
    probe = await probe_provider_key(pid)
    ok = bool(probe.get("ok"))
    # auto-disable rule
    enabled = bool(cfg.enabled and ok)
    disabled_reason = None
    if not key_present:
        disabled_reason = "missing_key"
    elif not ok:
        disabled_reason = probe.get("status") or "probe_failed"
    elif not cfg.enabled:
        disabled_reason = "registry_disabled"
    return {
        "ok": ok,
        "probe": probe,
        "key_present": key_present,
        "enabled": enabled,
        "disabled_reason": disabled_reason,
        "status": probe.get("status"),
        "checked_at": time.time(),
        "key_env": cfg.key_env,
        "registry_enabled": cfg.enabled,
        "required": bool(getattr(cfg, "required", False)),
        "n_free_models": probe.get("n_free_models", 0),
        "n_total_models": probe.get("n_total_models", 0),
    }


async def refresh_all() -> dict[str, dict[str, Any]]:
    """Probe all providers in parallel, update cache, bump Router if any transition."""
    global _status_cache
    results = await asyncio.gather(*[_probe_one(pid) for pid in PROVIDERS], return_exceptions=True)
    changed = False
    new_cache: dict[str, dict[str, Any]] = {}
    for pid, res in zip(PROVIDERS, results):
        if isinstance(res, Exception):
            logger.warning(f"[status] probe {pid} raised: {res}")
            res = {"ok": False, "probe": {"ok": False, "status": "unreachable", "error": str(res)}, "key_present": False, "enabled": False, "disabled_reason": "unreachable", "checked_at": time.time()}
        # merge extra
        entry = {
            "id": pid,
            **res,
        }
        # Don't let a routine 60s re-probe silently clear an active outage cooldown —
        # a generic key-validity probe can't see per-model 402/429-quota exhaustion.
        # Redis first (cross-process); local _status_cache as fallback when no Redis.
        cooldown = _read_cooldown(pid) or _active_local_cooldown(pid)
        if cooldown is not None:
            entry["enabled"] = False
            entry["disabled_reason"] = cooldown.get("reason", "cooldown")
            entry["cooldown_until"] = cooldown.get("until")
        new_cache[pid] = entry
        if _status_cache.get(pid, {}).get("enabled") != entry["enabled"] or _status_cache.get(pid, {}).get("ok") != entry["ok"]:
            changed = True
            logger.info(f"[status] {pid}: enabled={entry['enabled']} ok={entry['ok']} reason={entry['disabled_reason']}")
    _status_cache = new_cache
    if changed:
        try:
            from domains.llm.rotator.chain.service import reset_rotator  # lazy to avoid cycle
            reset_rotator(bump_gen=False)
            logger.info("[status] Router reset due to status change")
        except Exception as e:
            logger.debug(f"[status] reset_rotator failed: {e}")
    return _status_cache


def get_status(pid: str | None = None) -> dict[str, dict[str, Any]] | dict[str, Any] | None:
    if pid is None:
        return dict(_status_cache)
    return _status_cache.get(pid)


def is_enabled(pid: str) -> bool:
    """Chain filter helper — disabled providers are skipped.

    Checks the Redis-backed cooldown first so a disable from another process
    (e.g. a Celery worker hitting a 402) takes effect here immediately, without
    waiting for this process's next 60s _status_loop tick. Falls back to the
    local _status_cache cooldown when Redis isn't configured.
    """
    if _read_cooldown(pid) is not None or _active_local_cooldown(pid) is not None:
        return False
    ent = _status_cache.get(pid)
    if ent is None:
        # before first probe, fall back to registry + key_present
        cfg = PROVIDERS.get(pid)
        if cfg is None:
            return False
        return bool(cfg.enabled and _resolve_key(cfg.key_env))
    return bool(ent.get("enabled"))


def _reload_env_from_secret_volume() -> bool:
    """If Secret is mounted as volume, load files into os.environ."""
    if not _secret_path.is_dir():
        return False
    loaded = False
    try:
        for p in _secret_path.iterdir():
            if not p.is_file() or p.name.startswith(".."):
                continue
            # secret keys are lower-dash (groq-api-key), env is upper-snake (GROQ_API_KEY)
            try:
                val = p.read_text().strip()
            except Exception:
                continue
            env_name = p.name.upper().replace("-", "_")
            # also set lower-dash variant for resolve_key fallback
            if val and os.getenv(env_name) != val:
                os.environ[env_name] = val
                os.environ[p.name] = val  # lower-dash
                loaded = True
                logger.info(f"[status] reloaded {env_name} from Secret volume")
    except Exception as e:
        logger.debug(f"[status] volume reload failed: {e}")
    return loaded


async def _status_loop():
    global _last_secret_hash
    _last_secret_hash = _secret_hash()
    await refresh_all()
    while True:
        try:
            await asyncio.sleep(_PROBE_INTERVAL_S)
            # secret detector: hash check + volume reload every _SECRET_POLL_S slice
            # we sleep in chunks to check secret more frequently
            for _ in range(max(1, _PROBE_INTERVAL_S // _SECRET_POLL_S)):
                await asyncio.sleep(_SECRET_POLL_S)
                h = _secret_hash()
                if h != _last_secret_hash:
                    logger.info(f"[status] Secret change detected (hash { _last_secret_hash[:40]}... → {h[:40]}...), reloading")
                    _reload_env_from_secret_volume()
                    _last_secret_hash = _secret_hash()
                    await refresh_all()
                    break
            else:
                await refresh_all()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[status] loop error: {e}")
            await asyncio.sleep(5)


def start_status_loop() -> None:
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        return
    loop = asyncio.get_event_loop()
    _refresh_task = loop.create_task(_status_loop())
    logger.info("[status] probe loop started (60s) + Secret detector (10s)")


def stop_status_loop() -> None:
    global _refresh_task
    if _refresh_task:
        _refresh_task.cancel()
        _refresh_task = None
