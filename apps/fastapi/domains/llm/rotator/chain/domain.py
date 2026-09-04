from __future__ import annotations

import re
import time

from .keys import (
    WRITE_HEAVYWEIGHTS,
    _LITELLM_PREFIX_TO_PROVIDER,
    _NON_CHAT_MARKERS,
    _PROVIDER_KEY_ENV,
)
from .patterns import (
    MOE_RE,
    PARAM_SIZE_RE,
    _EOL_PHRASES,
)


def classify_error(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "429" in msg or "rate limit" in msg:
        return "rate_limit"
    if "timeout" in name or "timed out" in msg:
        return "timeout"
    if "auth" in name or "401" in msg or "403" in msg or "invalid api key" in msg:
        return "auth_error"
    if "content" in name and "filter" in name:
        return "content_filter"
    if "5" in msg and ("server" in msg or "internal" in msg or "bad gateway" in msg):
        return "server_error"
    return "unknown"


def is_eol_error(exc: Exception) -> bool:
    """True for EOL/deprecated: catalog must drop NOW, not wait for cooldown."""
    msg  = str(exc).lower()
    name = type(exc).__name__.lower()
    if "notfound" in name:
        return True
    if "410" in msg or " gone" in msg:
        return True
    if "404" in msg and ("model" in msg or "function" in msg):
        return True
    return any(p in msg for p in _EOL_PHRASES)


def is_insufficient_credits_error(exc: Exception) -> bool:
    """True for 402 Insufficient credits / Payment required / model_terms_required."""
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    if "402" in msg or "insufficient credits" in msg or "payment required" in msg:
        return True
    if "model_terms_required" in msg or "requires terms acceptance" in msg:
        return True
    if "invalid_request" in name and "terms" in msg:
        return True
    return False


_FREE_DAILY_QUOTA_PHRASES = ("free-models-per-day", "free_models_per_day")
_RATE_LIMIT_RESET_RE = re.compile(r'x-ratelimit-reset["\s:]+"?(\d{10,13})"?', re.IGNORECASE)
_MIN_QUOTA_COOLDOWN_S = 30.0
_DEFAULT_QUOTA_COOLDOWN_S = 900.0     # free-tier daily limit, reset header missing
_DEFAULT_CREDITS_COOLDOWN_S = 1800.0  # true 402 insufficient credits — needs a top-up, not a reset


def _rate_limit_reset_in_s(msg: str) -> float | None:
    """Seconds until OpenRouter's X-RateLimit-Reset (epoch ms or s) elapses, if present in the error text."""
    m = _RATE_LIMIT_RESET_RE.search(msg)
    if not m:
        return None
    val = int(m.group(1))
    if val > 10**12:  # epoch milliseconds
        val /= 1000
    return max(_MIN_QUOTA_COOLDOWN_S, val - time.time())


def classify_provider_outage(exc: Exception) -> tuple[str, float] | None:
    """Provider-wide (not just this model) outages that need a cooldown, distinct by cause:
    - 'insufficient_credits' (true 402/payment-required): persists until manual top-up → long cooldown.
    - 'free_quota_exhausted' (429 daily free-tier limit): resets on its own → cooldown until
      the provider's X-RateLimit-Reset if present, else a short default.
    Returns None for errors that are transient/per-model and should NOT disable the whole provider.
    """
    if is_insufficient_credits_error(exc):
        return ("insufficient_credits", _DEFAULT_CREDITS_COOLDOWN_S)
    msg = str(exc).lower()
    if "rate limit" in msg and any(p in msg for p in _FREE_DAILY_QUOTA_PHRASES):
        reset_s = _rate_limit_reset_in_s(msg)
        return ("free_quota_exhausted", reset_s if reset_s is not None else _DEFAULT_QUOTA_COOLDOWN_S)
    return None


def is_heavyweight(deployment_id: str) -> bool:
    return any(s in deployment_id for s in WRITE_HEAVYWEIGHTS)


def is_non_chat_model(model_id: str) -> bool:
    name = (model_id or "").lower()
    return any(m in name for m in _NON_CHAT_MARKERS)


def passes_capability_floor(model_id: str, min_b: float) -> bool:
    """MoE bypasses the floor; unparseable name → True (newer-named frontier models)."""
    if min_b <= 0:
        return True
    name = (model_id or "").lower()
    if MOE_RE.search(name):
        return True
    sizes = [float(x) for x in PARAM_SIZE_RE.findall(name)]
    if sizes:
        return max(sizes) >= min_b
    return True


def provider_key_env(provider: str) -> str:
    return _PROVIDER_KEY_ENV.get(provider, "NVIDIA_API_KEY")


def entry_provider_and_model(entry: dict) -> tuple[str, str]:
    m = (entry.get("litellm_params") or {}).get("model", "")
    prefix, _, model = m.partition("/")
    return _LITELLM_PREFIX_TO_PROVIDER.get(prefix, prefix), model


def provider_mode(provider_id: str, sel: dict) -> str:
    return (sel.get("mode") or {}).get(provider_id, "all")


def selection_allows(provider_id: str, model_id: str, sel: dict) -> bool:
    """Provider ids must be REGISTRY ids (groq/nim/...), not LiteLLM prefixes."""
    enabled = sel.get("enabled")
    if enabled is not None and provider_id not in enabled:
        return False
    if provider_mode(provider_id, sel) == "custom":
        return model_id in ((sel.get("selected") or {}).get(provider_id) or [])
    return True
