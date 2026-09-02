# COELHO LLM Rotator — Features Order (Free-Quota Max, SOTA Aug 2026)

> Universal `OpenAI` `v1` gateway over `groq|nim|cerebras|mistral|gemini` free tiers.
> Goal: **best `benchmark` quality at lowest `latency` within free `RPM`**, `FGTS-VA` re-ranks live.

## Filter → Rank → Route

1. **Availability — hard gate**
   - `probe_provider_key` `ok` (`200/429`) `has_key` `enabled` (`status/service.py:48` `Secret` `/run/secrets/llm` `10s` watch `60s` re-probe)
   - `EOL` `410 Gone` `auto-blocklist` (`chain/service.py:773` `mark_inaccessible` `+` `reset_rotator`) + `in-flight caps` (`_RR_PROVIDER_CAPS` `nvidia 4|groq 2`) `429` `cooldown 60s`
   - Not available → never picked

2. **Benchmark Quality — cold-start prior `general` `μ−3σ`**
   - `3` sources `openlm_arena` `298` (`arena.ai` `JSON`) + `oolong_code` `49` + `OpenEvals` `Parquet` `58` (`benchmarks/service.py:132` `7d` `Redis` `in-mem`)
   - `L1` `normalize_model_name` `ModelGraveyard` (`PROVIDER_ALIASES` `groq→meta-llama`, `TRAILING_PATTERNS` `-20240620:-v1:0`) + `L2` `LLM DB` `alias` `dated canonical` + `L3` `RapidFuzz 95` + `L4` `LLM judge`
   - `merge_leaderboards` → `compute_composite_score` `STEP_WEIGHTS["general"]` `aaii 0.30|lmarena 0.25|…` → `true_skill_adjust` `μ−3σ` `composite*(n/3)**0.3` `missing≠0` (`LLM Stats Score` `2026-07-17`)
   - `GET /benchmarks/leaderboard?limit=200` `best→worst` (`kimi-k3` `0.839` `5` `sources` `nim/kimi-k3` `routable`)

3. **Live Latency — `FGTS-VA` `variance-aware`**
   - `reward = success × schema_valid × exp(-latency/30s) − error_class` (`chain/service.py:452` `bandit` `redis` `context dd_process`)
   - `KV-cache affinity` `57×` `warm` (`Workload–Router–Pool` `2603.21354`) + `TTFT` `prefill|decode` (`Artificial Analysis` `7d`)
   - `σ₀²=1/(n_sources·5)` `±` `true_skill_adjust` → `fast` `mistral 15ms` outranks `slow` `kimi 1.2s` after `~3` calls

4. **Reliability**
   - `success` `error_class` `429|403|410` `AllowedFails` `Auth 0` `Timeout 2` `+` `cooldown` `→` demote, `SWE-bench` `HLE` `OSWorld` saturated (`>90%` `MMLU` ignored per `CodeSOTA` `2026-04`)

5. **Context | Tools**
   - `max_input_tokens` `8192` vs `1M` (`deepseek-v4`) + `supports_tool_choice` (`compound` `agentic` skipped for `chat`)
   - `freshness` `dated canonical` `20251001` vs `latest` `alias` (`OpenRouter ~author/family-latest`)

## Endpoints

```bash
curl -s http://localhost:23030/api/v1/llm/providers | jq '.providers[] | {id, n_free_models, enabled, ok}'
curl -s "http://localhost:23030/api/v1/llm/benchmarks/leaderboard?limit=10" | jq '.leaderboard[] | select(.routable) | {canonical, adjusted, providers}'
curl -s http://localhost:23030/api/v1/llm/openai/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}' | jq .model
```

*Consumer weights (`coding|reasoning`) belong in **consumer** (`Nexus` `RR`), `Rotator` stays `universal` `general` `composite`.*
