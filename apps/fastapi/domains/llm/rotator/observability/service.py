"""No-op observability — OTel/LangFuse removed for demo. All spans are no-ops."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator


class _NoOpSpan:
    def attach_attrs(self, *a, **kw): return None
    def attach_chat_response(self, *a, **kw): return None
    def attach_embedding_response(self, *a, **kw): return None
    def attach_rerank_response(self, *a, **kw): return None
    def set_total_attempts(self, *a, **kw): return None
    def set_fallback(self, *a, **kw): return None


@asynccontextmanager
async def genai_completion_span(*a, **kw) -> AsyncIterator[_NoOpSpan]:
    yield _NoOpSpan()

@asynccontextmanager
async def genai_embedding_span(*a, **kw) -> AsyncIterator[_NoOpSpan]:
    yield _NoOpSpan()

@contextmanager
def genai_embedding_span_sync(*a, **kw) -> Iterator[_NoOpSpan]:
    yield _NoOpSpan()

@asynccontextmanager
async def genai_rerank_span(*a, **kw) -> AsyncIterator[_NoOpSpan]:
    yield _NoOpSpan()

@asynccontextmanager
async def genai_bandit_cascade_span(*a, **kw) -> AsyncIterator[_NoOpSpan]:
    yield _NoOpSpan()

@asynccontextmanager
async def genai_bandit_attempt_span(*a, **kw) -> AsyncIterator[_NoOpSpan]:
    yield _NoOpSpan()

def update_bandit_outcome(*a, **kw) -> None:
    return None
