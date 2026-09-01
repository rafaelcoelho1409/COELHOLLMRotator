"""LLM rotator router — health probe + OpenAI compat (BYOK/settings removed — keys via K8s Secret)."""
from fastapi import APIRouter

from .health import router as _health_router
from .openai import router as _openai_router

router = APIRouter()
router.include_router(_health_router, prefix="/health")
router.include_router(_openai_router, prefix="/openai")
