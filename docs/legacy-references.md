# Legacy References — dd-*, ycs-*, rr-* (COELHO Nexus) → Universal Rotator

> Snapshot 2026-09-02 before removal. Keep for traceability when collapsing `3` Nexus domains into `1` universal `auto` pool.

## 1. dd-* (Docs Distiller)

**Origin:** `COELHO Nexus` `domains/dd` `6-phase` `docs-distiller` orchestration.

| File:Line | Constant / Usage | Nexus Meaning | Rotator Current Use | Replan for Universal |
|---|---|---|---|---|
| `chain/keys.py:GROUP="dd-all"` | `Router` `model_name` `general` pool | DD `all` phases | `Router` `dd-all` `30` `models` `general` chat | **Keep as `general` alias** `GROUP="general"` `+` `compat alias dd-all="general"` |
| `chain/keys.py:KEYLM_GROUP="dd-keylm"` | Tiny-LM `Llama-3.2-1B` cluster labels | DD `KeyLLM` | `build_keylm_chain()` `1B→3B` fallback | **Collapse → general** (no separate `keylm` pool for universal) |
| `chain/keys.py:SYNTH_GROUP="dd-synth"` | Reasoning `120b` `kimi-k2.6` | DD `synth` `T=0.7` | `build_synth_chain()` `reasoning` | **Collapse → general** `T` via `request` `temperature` not `group` |
| `chain/keys.py:REDUCE_LABEL_GROUP="dd-reduce-label"` | Fast `non-reasoning` `70b` `flash-lite` | DD `reduce` `T=1.0` | `build_reduce_label_chain()` | **Collapse → general** |
| `chain/keys.py:DD_EMBED_GROUP="dd-embed"` | Embed `nvidia/llama-nemotron-embed-1b-v2` `float` | DD `embed` | `embed_via_router_sync/async` `single-entry` `cosine` | **Keep** `embed` `separate` (different `litellm` `encoding_format`) |
| `chain/keys.py:RR_STRONG_GROUP="rr-strong"` | `RR` `strong` `non-small` `phantom` filter | RR `Research Radar` | `_rr_strong_entries_current()` `no` `small` `arms` | **Collapse → general** (RR not in Rotator) |
| `chain/keys.py:_JUDGE_KD_PROCESS="dd-grader"` | `bandit` `cell` `separate` `binary` `vs` `continuous` | DD `grader` `judge` | `chat_judge_bandit_async` `FGTS-VA` `cell` | **Rename → `general-grader`** or `keep` isolated `grader` cell |
| `chain/config.py:dd-all/synth/reduce-label` | `DynamicStepConfig` `top_k 30|12|10` `timeout 120|180|90` | DD `dynamic` `catalog` `per step` | `ensure_dynamic_catalog` `30` | **Collapse → single `general` `top_k 50` `timeout 120`** |
| `chain/service.py:2283` `_all_entries_current` etc. | `4` `funcs` `dd-all|dd-synth|dd-reduce-label|rr-strong` | DD/RR `pools` | `Router` `cold-start` `sort` | **Single `general_entries_current()`** `→` `_sort_by_benchmark` `best→worst` |
| `chain/service.py:1254` `_RR_DD_PROCESS=RR_STRONG_GROUP` | `bandit` `FGTS-VA` `separate` `cell` `RR` vs `DD` | RR `vs` DD `reward` leak guard | `RR` `bandit` | **Remove RR, keep `general`** |
| `chain/service.py:872` `_GROUP_NAMES` `frozenset dd-all, rr-strong...` | `is_eol_error` `filter` | DD/RR `group` `names` | `Router` `group` `check` | **Reduce to `{"general","embed"}`** |
| `benchmarks/params.py:STEP_WEIGHTS` `dd-*` `8` keys | `general` already collapsed `2026-09-02` `leaderboard` `?step` removed | DD `step` `weights` | `leaderboard` `general` | **Done** |
| `benchmarks/service.py:330` `weights.get(step)` | `dd-all` fallback | DD | `rank_for_step` | **Keep `general` fallback** |
| `bandit/keys.py` `dd-all|dd-synth|...|dd-critic` `ycs-neo4j` | `per-step` `CellState` `prior` | DD `8` steps `+` YCS `Neo4j` | `bandit` `prior` `μ₀` | **Single `general` `prior` `+` `grader` isolated** |
| `chain/service.py:414` `_bump_dd_llm_counter` `domains/dd/runtime` | `DD` `llm_counter` `attribution` | DD `planner` | `no-op` `try/except` | **Safe to delete import** |

## 2. ycs-* (YouTube Content Search)

| File:Line | Usage | Nexus Meaning | Rotator | Replan |
|---|---|---|---|---|
| `chain/service.py:_YCS_NEO4J_PROCESS="ycs-neo4j"` | `bandit` `cell` `YCS` `Neo4j` `graph` | YCS `entity extraction` | `unused` `in` `Rotator` | **Delete constant** |
| `chain/service.py:ycs-bandit-pin` `8` `hits` | `YCS` `pinning` `provider-slot` `reservation` | YCS `Neo4j` `pin` | `YCS` `pin` `functions` `~200` `lines` `never` `called` `by` `Rotator` `openai` `router` | **Delete `ycs-bandit-pin` block** `(` `~180` `lines` `)` |
| `chain/service.py:1839` `YCS Phase 3` `comment` | `LLMGraphTransformer` `shares` `dd-synth` `POOL` | YCS | `comment` `only` | **Delete comment** |

## 3. rr-* (Research Radar)

| File:Line | Usage | Nexus Meaning | Rotator | Replan |
|---|---|---|---|---|
| `keys.py:RR_STRONG_GROUP="rr-strong"` `chain/service.py:_rr_strong_entries` `+` `_RR_DD_PROCESS` | `RR` `strong` `arms` `no` `small` `phantom` | RR `agent` `strong` `reasoning` | `rr-strong` `pool` `never` `called` `by` `openai` `auto` `→` `dd-all` `covers` | **Collapse to `general`** |

## Replan Order (no crash)

1. **Keep `embed` separate** (`DD_EMBED_GROUP` `encoding_format float`) — different `litellm` `model` type.
2. **Alias `GROUP="general"` + `compat` `dd-all="general"`** (`_GROUP_NAMES`, `config` `general` `top_k 50`) → `Router` `model_name` `general` `not` `found` avoided.
3. **Collapse `4` `current() funcs → 1` `general_entries_current()`** `+` `bandit` `keys` `single` `general` `prior` `→` `_sort_by_benchmark` `best→worst` preserved.
4. **Delete `ycs-bandit-pin` `+` `YCS_NEO4J_PROCESS` + `RR_STRONG_GROUP` block** after `general` alias — `openai` `router` `auto` `unchanged`.
5. **Update `docs/features.md`** `filter→rank→route` `to` `general` `only`.
