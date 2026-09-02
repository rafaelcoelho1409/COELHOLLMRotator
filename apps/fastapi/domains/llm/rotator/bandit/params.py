from __future__ import annotations


CONTEXT_DIM = 32
CELL_TTL_S = 90 * 24 * 3600
# 32-dim for latency-aware: 0 bias, 1-3 chapter/hash/thinking, 4-6 vault, 7-14 task (sparse), 15 reserved, 16-18 time, 19-23 provider error, 24 input_len, 25 inflight/cap, 26 p50 latency, 27 prefix bucket, 28-31 reserved

UCB_ALPHA = 0.5
TS_SCALE = 0.5
# Ridge on A_a + weak prior θ̂_a → 0.
RIDGE_LAMBDA = 1.0
# γ=0.01: old observations decay to e^-1 after ~100 updates.
FORGETTING_GAMMA = 0.01


# Penalty must be strong enough to flip ranking after concurrent 8-14 picks land.
ERROR_CLASS_PENALTIES: dict[str, float] = {
    "rate_limit":     -0.10,
    "timeout":        -0.60,
    "server_error":   -0.50,
    "auth_error":     -0.80,
    "schema_invalid": -0.40,
    "content_filter": -0.20,
    "unknown":        -0.40,
}
