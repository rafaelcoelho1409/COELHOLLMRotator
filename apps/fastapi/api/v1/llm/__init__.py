"""LLM rotator router — health + OpenAI compat + providers + benchmarks."""
from fastapi import APIRouter

from .benchmarks import router as _benchmarks_router
from .health import router as _health_router
from .openai import router as _openai_router
from .providers import router as _providers_router

router = APIRouter()
router.include_router(_health_router, prefix="/health")
router.include_router(_providers_router, prefix="/providers")
router.include_router(_benchmarks_router, prefix="/benchmarks")
router.include_router(_openai_router, prefix="/openai")
