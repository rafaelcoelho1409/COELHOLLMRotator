"""Provider discovery — live free-tier model fan-out across providers."""
from __future__ import annotations

from .config import PROVIDERS
from .entities import DiscoveryRecord, FreeFilter, ProviderConfig
from .service import (
    list_all_alive_models,
    list_provider_free_models,
    missing_required_keys,
    probe_provider_key,
)

__all__ = [
    "DiscoveryRecord",
    "FreeFilter",
    "PROVIDERS",
    "ProviderConfig",
    "list_all_alive_models",
    "list_provider_free_models",
    "missing_required_keys",
    "probe_provider_key",
]
