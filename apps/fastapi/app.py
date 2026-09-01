"""COELHO LLM Rotator — minimal FastAPI shell.

"""
import logging
from contextlib import asynccontextmanager

logging.basicConfig(
    level = logging.INFO, 
    format = "%(asctime)s %(levelname)s %(name)s %(message)s")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.router import api_v1
from domains.llm.rotator.chain import (
    init_dynamic_catalog,
    start_catalog_refresh_loop,
    stop_catalog_refresh_loop,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm dynamic catalog (discovery fan-out) — falls back to static catalog on failure.
    try:
        await init_dynamic_catalog()
    except Exception as e:
        logger.warning(f"[lifespan] dynamic catalog init failed: {type(e).__name__}: {e}. Using static catalog.")
    # Periodic re-discovery drops EOL'd models without a redeploy.
    try:
        start_catalog_refresh_loop()
    except Exception as e:
        logger.warning(f"[lifespan] catalog refresh loop start failed: {type(e).__name__}: {e}.")
    
    yield
    try:
        await stop_catalog_refresh_loop()
    except Exception as e:
        logger.warning(f"[lifespan] catalog refresh loop stop failed: {e}")


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
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "COELHO LLM Rotator"}
