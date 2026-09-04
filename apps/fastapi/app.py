"""COELHO LLM Rotator — minimal FastAPI shell.

Providers engine: key validation → auto-disable + Secret detector + live models fan-out.
SOTA Sept 2026: lifespan active probe 1x per provider for 402/Payment required/model_terms_required
→ cross-process TTL cooldown via domains.llm.rotator.status.disable_provider (self re-enables on expiry).
"""
import asyncio
import logging
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.router import api_v1
from domains.llm.rotator.chain import (
    init_dynamic_catalog,
    start_catalog_refresh_loop,
    stop_catalog_refresh_loop,
)
from domains.llm.rotator.status import start_status_loop, stop_status_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_dynamic_catalog()
    except Exception as e:
        logger.warning(f"[lifespan] dynamic catalog init failed: {type(e).__name__}: {e}. Using static catalog.")
    try:
        start_catalog_refresh_loop()
    except Exception as e:
        logger.warning(f"[lifespan] catalog refresh loop start failed: {type(e).__name__}: {e}.")
    try:
        start_status_loop()
    except Exception as e:
        logger.warning(f"[lifespan] status loop start failed: {type(e).__name__}: {e}.")

    # SOTA active probe: 1× tiny ping per provider's first free model.
    # 402/Payment-required/model_terms_required and 429 free-daily-quota-exhausted →
    # provider-wide TTL cooldown (see classify_provider_outage); self re-enables on expiry
    # via the next request or _status_loop probe. Prevents general fallback from routing
    # to dead arms (Truefoundry/Portkey-class outages).
    try:
        from domains.llm.rotator.chain.domain import classify_provider_outage
        from domains.llm.rotator.chain.service import disable_provider_for_outage, mark_inaccessible, resolve_key
        from domains.llm.rotator.chain.keys import _PROVIDER_KEY_ENV

        # SOTA: benchmark-top per provider (not hardcoded) — uses leaderboard composite
        # `/benchmarks` ranks general top-K 50 by TrueSkill, so probe hits the
        # model the planner will actually route to, not arbitrary first free.
        _PROBE_MODELS: dict[str, str] = {}
        try:
            from domains.llm.rotator.discovery.service import get_live_models  # type: ignore
            from domains.llm.rotator.benchmarks.service import rank_for_step  # type: ignore

            live = await get_live_models()  # type: ignore
            # live: {provider: [ModelRecord]} already free-filtered
            for prov, recs in (live or {}).items():
                if not recs:
                    continue
                try:
                    ranked = await rank_for_step("general", recs)  # type: ignore
                    top = ranked[0][0] if ranked else recs[0]
                    mid = getattr(top, "model_id", None) or getattr(top, "model", None) or str(top)
                    if mid:
                        _PROBE_MODELS[prov] = mid
                except Exception:
                    try:
                        _PROBE_MODELS[prov] = getattr(recs[0], "model_id", str(recs[0]))
                    except Exception:
                        pass
            if not _PROBE_MODELS:
                raise RuntimeError("no live models for probe")
        except Exception as e:
            logger.debug(f"[lifespan-probe] benchmark-top fallback {type(e).__name__}: {e}")
            _PROBE_MODELS = {
                "groq": "llama-3.3-70b-versatile",
                "nim": "openai/gpt-oss-120b",
                "cerebras": "qwen-3.8-27b",
                "mistral": "mistral-small-latest",
                "gemini": "gemini-2.5-flash",
                "openrouter": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            }

        async def _probe_one(provider: str, model_id: str):
            try:
                import litellm  # type: ignore

                # Build litellm model string with provider prefix
                if provider == "nim":
                    litellm_model = f"nvidia_nim/{model_id}"
                elif provider == "gemini":
                    litellm_model = f"gemini/{model_id}"
                else:
                    litellm_model = f"{provider}/{model_id}"
                await litellm.acompletion(
                    model = litellm_model,
                    messages = [{"role": "user", "content": "ping"}],
                    max_tokens = 1,
                    timeout = 5,
                    api_key = resolve_key(_PROVIDER_KEY_ENV.get(provider, "NVIDIA_API_KEY")),
                )
            except Exception as e:
                outage = classify_provider_outage(e)
                if outage is not None:
                    reason, cooldown_s = outage
                    try:
                        mark_inaccessible(model_id)
                    except Exception:
                        pass
                    # Provider-wide disable (cross-process, TTL cooldown) — the boot probe only
                    # hit one model, but 402/429-quota means every model on this provider is out.
                    disable_provider_for_outage(f"{provider}/{model_id}", e)
                    # Also mark all live models for this provider as inaccessible so general rebuild drops them
                    try:
                        from domains.llm.rotator.discovery.service import get_live_models  # type: ignore

                        live_all = await get_live_models()  # type: ignore
                        for rec in (live_all.get(provider) or []):
                            mid = getattr(rec, "model_id", None) or str(rec)
                            try:
                                mark_inaccessible(mid)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    logger.warning(f"[lifespan-probe] {provider}/{model_id} → {reason} → provider disabled for {cooldown_s:.0f}s (all {provider} models)")
                else:
                    logger.debug(f"[lifespan-probe] {provider}/{model_id} transient {type(e).__name__}: {str(e)[:120]}")

        probe_tasks = []
        for prov, model_id in _PROBE_MODELS.items():
            try:
                if not resolve_key(_PROVIDER_KEY_ENV.get(prov, "")):
                    continue
            except Exception:
                pass
            probe_tasks.append(_probe_one(prov, model_id))
        if probe_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*probe_tasks), timeout=12.0)
                logger.info(f"[lifespan-probe] probed {len(probe_tasks)} providers for 402/terms")
            except asyncio.TimeoutError:
                logger.warning("[lifespan-probe] overall probe timed out after 12s")
    except Exception as e:
        logger.warning(f"[lifespan-probe] failed {type(e).__name__}: {e}")

    yield
    try:
        await stop_catalog_refresh_loop()
    except Exception as e:
        logger.warning(f"[lifespan] catalog refresh loop stop failed: {e}")
    try:
        stop_status_loop()
    except Exception as e:
        logger.warning(f"[lifespan] status loop stop failed: {e}")


app = FastAPI(
    title = "COELHO LLM Rotator",
    description = "OpenAI-compatible LLM rotator over free-tier providers (bandit + discovery)",
    version = "1.0.0",
    lifespan = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"],
)

app.include_router(api_v1, prefix = "/api")


@app.get("/")
async def root():
    return {
        "service": "COELHO LLM Rotator",
        "version": "1.0.0",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "openai_models": "/api/v1/llm/openai/v1/models",
            "openai_chat": "/api/v1/llm/openai/v1/chat/completions",
            "llm_health": "/api/v1/llm/health",
            "providers": "/api/v1/llm/providers",
            "providers_models_live": "/api/v1/llm/providers/models/live",
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "COELHO LLM Rotator"}
