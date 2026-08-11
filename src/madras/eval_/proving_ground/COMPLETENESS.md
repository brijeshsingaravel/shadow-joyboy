# Proving Ground — Completeness Ledger

> **Source of truth for "is the Proving Ground done?"** — checked against CODE, not memory.
> Every scoped item (from the v2 spec, the phase goal, research/industry-radar.md, IDEA_REGISTRY eval
> entries) is listed with its REAL status + file evidence. Update this whenever an item lands. A session
> that has lost context to compaction must reconcile "done" against THIS file + the code, never against a
> summary. Audited 2026-06-16 (session 9).

Status key: **BUILT** (real, tested code) · **PARTIAL** (works but missing a scoped part) · **MISSING** (zero/near-zero code) · **NOT-SCOPED** (named in industry research but never adopted into Madras scope).

---

## A. Benchmark suites

| Suite | Scoped at | Status | Evidence / gap |
|---|---|---|---|
| τ²-bench (`tau2`) | spec §1 PRIMARY | BUILT | `suites/tau2.py` + registry |
| BFCL (`bfcl`) | spec §1 | BUILT | `suites/bfcl.py` + committed slice |
| AgentHarm (`agentharm`) | spec §1 | BUILT | `suites/agentharm.py` |
| GAIA (`gaia`) | spec §1 | BUILT | `suites/gaia.py` (token-gated) |
| native bank (`madras_features`) | spec §1 | BUILT | `NativeSuite` over `scenarios/` |
| terminal-bench | spec expanded roster | BUILT | `suites/terminal_bench.py` (WSL+Docker) |
| GPQA (`gpqa`) | spec expanded roster | BUILT | `suites/gpqa.py` (+ option shuffle) |
| GSM8K (`gsm8k`) | spec expanded roster | BUILT | `suites/gsm8k.py` |
| MMLU-Pro (`mmlu_pro`) | spec expanded roster | BUILT | `suites/mmlu_pro.py` |
| SWE-bench Verified (`swebench`) | spec expanded roster | BUILT | `suites/swebench.py` (Docker eval) |
| trace-grounding track | spec §47-50 | BUILT | `trace_grounding.py` (judge calibration, not a scored suite) |
| **LongMemEval** | **phase goal `STATUS.json:23`** + radar + IDEA_REGISTRY (planned) | **BUILT** | `suites/longmemeval.py` (token-gated, oracle variant) + committed slice `suites/longmemeval/data/longmemeval_slice.json` (16 rows, all 6 question types) + `tests/test_proving_ground/test_longmemeval_suite.py`. Memory benchmark — Madras's headline differentiator. |
| WebArena / VisualWebArena | radar; IDEA_REGISTRY `future` | BUILT (adapter) · live-infra-gated | `suites/webarena.py` external adapter + `scripts/webarena_runner.py` (browsergym agent → our proxy). `run()` gates on the `WA_*` hosted-site env vars + the isolated venv (multi-GB Docker sites + Playwright are operator-provisioned), raising a clear error otherwise. Registered; `targets.py` 0.15; hermetic test `test_webarena_suite.py` pins the parse + the infra gate. Live run needs the WebArena Docker stack. |
| AgentBench | radar; IDEA_REGISTRY `future` | BUILT (adapter) · live-infra-gated | `suites/agentbench.py` external adapter — clones `THUDM/AgentBench` into `.benchmarks/agentbench`, writes a routed `madras-routed` HTTPAgent config (imports the repo's `openai-chat.yaml`, overrides url/headers/body.model → our LiteLLM proxy) + a self-contained assignment config (fixed `outputs/madras`), shells `python -m src.assigner`, reads each env's `overall.json` and normalizes the headline score (`overall.acc`/`success_rate`/`reward`) to one row per environment. `run()` gates on the cloned repo + isolated venv + a reachable Docker daemon (8 Docker task servers + controller are operator-provisioned), raising clear errors otherwise. Registered; `targets.py` 0.30; hermetic test `test_agentbench_suite.py` pins the parse + both config writers + the infra gate. Live run needs the AgentBench task controller/workers. |
| AppWorld | IDEA_REGISTRY `future` | BUILT (adapter) · live-unvalidated | `suites/appworld.py` external adapter — installs `appworld` + clones the repo into `.benchmarks/`, writes a routed config (baseline simplified_function_calling agent → our LiteLLM proxy via OPENAI_BASE_URL), shells `appworld run` + scores via `evaluate_dataset`, normalizes per-task TGC. Registered; `targets.py` 0.30; hermetic test `test_appworld_suite.py` pins the parse. **NOT yet run live** (full split = long code-gen sweep, rate-limit-bound — same status as SWE-bench's Docker note). |
| AssistantBench · Agent-SafetyBench/ASB · MLE-bench · TheAgentCompany · HAL | (industry) | NOT-SCOPED | not named in any Madras canon/spec/research file — do not claim as gaps unless we choose to adopt |

## B. Engine + methodology capabilities

| Capability | Scoped at | Status | Evidence / gap |
|---|---|---|---|
| Sweep engine (`run_case`/`run_sweep`, bounded concurrency, per-resample isolation) | spec §2, v2-C | BUILT | `sweep.py:158,413` |
| Agent-model self-exclusion from judge panel | spec §3 | BUILT | `sweep.py:458` |
| 5-judge independent panel, no debate, supermajority ≥4/5 | spec §3 | BUILT | `judge_panel.py:36-59` |
| Rubric-anchored pointwise judge, fail-closed | spec §3 | BUILT | `judge_runner.py` |
| Perspective-diverse cross-family judges | spec §3 | BUILT | `judge_panel.py:23-26` (one per model family) |
| **Meta-judge on disagreement splits** | spec §3; STATUS:1066/1082 | **BUILT** | `judge_panel.py` runs ONE injected `meta_call` only when `n_pass in {threshold-1, threshold}` (one vote from flipping); meta verdict authoritative for the split, clear consensus untouched. `PanelVerdict.meta_used/meta_reason`. Real builder `judge_runner.make_meta_judge_call` (different model, told to ignore verbosity, shown the dissent). Tests `test_judge_meta.py`. |
| **Randomized option order / position-bias mitigation** | spec §3 | **N/A (docstring corrected)** | Judging is pointwise over a single trajectory → no option-order surface, nothing to shuffle. False claims in `judge_panel.py` + `judge_runner.py` docstrings CORRECTED to state this; position-bias would apply only to a future pairwise mode. No fake shuffle added. |
| Verbosity-bias mitigation | spec §3 | BUILT (signal) | soft prompt instruction (`judge_runner.py`) PLUS `judge_panel` records `answer_len` + `length_warn` (`VERBOSITY_LEN_WARN=4000`) and the meta-judge is told to ignore verbosity. No score-distorting normalization (kept simple/defensible). |
| Pairwise judging option | spec §3 (pointwise chosen) | NOT-SCOPED | by design pointwise; note only |
| Metric taxonomy (trajectory+governance+safety+8-dim+cost) | spec §4 | BUILT | `metrics.py`, `metrics_v2.py` |
| pass^k consistency metric | spec §4 | BUILT | `sweep.py:_aggregate_model_run` now computes STRICT pass^k: a scenario counts only if all k resamples passed (`passes >= k`), then mean across scenarios. Distinct from `overall` (mean pass_rate); equal only at k=1. Docstring corrected; `test_sweep_run_sweep.py` green. |
| Composite + leaderboard | spec §4 | BUILT | `sweep.py:348-369` |
| Normalized 8-table store | spec §5, v2-AB | BUILT | `store_v2.py` + migration 0007 |
| **Agent dimension (agent × model unit-under-test)** | session-10 (multi-agent eval) | **BUILT** | `agents.py` registry (`AgentSpec`: rank/toolsets/persona/agent_name/default_models; Shadow first entry) + migration `0009_pg_agent_dimension.sql` (adds `agent` to model_runs/scenario_results/tool_calls/judge_votes/metrics/coverage + extends PKs; coverage also gains `model`; pg_runs gains `agents`). `runner.run_scenario(agent=...)` binds rank/agent_name/persona (Shadow defaults preserved). `sweep.run_sweep(agents=, models=)` runs the full `agents x models x cases` cross-product; leaderboard/composite keyed by `agent::model`; regression gate per (agent, model). `store_v2` carries `agent` on every insert/read (`model_run(run_id, model, agent)`, `cost_rows` grouped by agent). Endpoint `POST /proving-ground/run` accepts `agents`; `GET /proving-ground/agents` lists them. Hermetic tests: `test_agents.py` + multi-agent×model sweep test. Live store validation blocked only by the pre-existing local-Postgres SSL reset. |
| **Coverage matrix reflects agent + model + use-case** | session-10 | **BUILT** | `coverage.build_coverage` now emits one feature/tool grid PER (agent, model) unit that ran (cells carry `agent`+`model`; `benchmark`/`feature` remain the use-case axes). UI `proving_ground.html` adds an agent·model slicer over the matrix. |
| **Multi-model sweep (both profiles, no friction)** | session-10 | **BUILT** | engine always supported a model list; the UI now exposes a free-text model field (comma-separated) + agent picker feeding both Run Sweep and Deep Sweep. |
| Coverage matrix (features×tools) | spec §7 | BUILT | `coverage.py:64` |
| Regression gate (drop vs prev run → backlog) | spec §7 | BUILT | `coverage.py:152` |
| **Beat-ladder / target scores as engine constants** | spec §1/§6 | **BUILT** | `targets.py` is the single source: `BENCHMARK_TARGETS` (one per registered non-native benchmark_family) + `TARGET_SOURCE` (spec vs default provenance) + `target_for`/`beats`. Exposed via `GET /proving-ground/targets` (degrades, never 500); UI `proving_ground.html` fetches it into `TARGETS` before `renderBench` (inline fallback). Tests `test_targets.py` (incl. registry-coverage guard) + `test_targets_endpoint.py`. tau2=0.50 & bfcl=0.70 spec-stated; rest defensible defaults. |
| Strategist | spec §10 (v1) | BUILT | `strategist.py` |
| scope_probe | spec §10 (v1) | BUILT | `scope_probe.py` |
| **Analyst v2 (over the normalized store)** | spec §8 v2-E | **BUILT** | `analyst.py:126 analyze_store(store, *, limit, regression_threshold)` reads the last N runs via `store.recent_runs`, assembles each model's per-feature/per-benchmark history via `store.model_run`, and mines **regressions** (drop > threshold vs prev run, reusing `coverage.detect_regressions`) + **recurring-fails** (per-axis window mean < floor). Emits the existing `madras_pg_backlog` shape (track via `FEATURE_TRACK`); persists via `store.write_backlog` if exposed (hasattr-guarded), returns regardless. Pure `analyze(runs)` kept intact. Tests `test_analyst_store.py` (hermetic fake store). |
| UI (v2-D): leaderboard·coverage·drill·economics | v2-D | BUILT | `static/proving_ground.html` (live-verified) |
| SP3 economics engine (adjacent) | session-9 | BUILT | `eval_/economics/` (consumes the PG cost spine) |

## C. Omnigent layers in the Proving Ground

The session-9 Omnigent gap-analysis was decomposed into **separate** sub-projects SP1–SP8 (`STATUS.json:1082`); **none of the Omnigent architectural layers are inside the Proving Ground.** SP1 PolicyEngine, SP2 Executor/tiering + blast-radius gate + typed-purpose guard + multi-provider SandboxLauncher = planned, not built, not PG. SP3 economics = built but adjacent. The only Omnigent-*flavored* ideas reflected in the PG (and they pre-date the analysis): perspective-diverse cross-family judging, no-debate independence, trace-grounding calibration. **If we want any Omnigent eval idea actually folded into the PG, it must be a new ledger item — today there are none.**

---

## Close-out priority (to legitimately call the Proving Ground "built")
> Items 1-4 closed 2026-06-16; item 5 (web/multi-env benchmarks) — adapters now BUILT (AppWorld, WebArena, AgentBench), live runs infra-gated.
1. ~~**LongMemEval suite** — the phase-goal benchmark + memory differentiator. (token-gated dataset adapter, like GAIA.)~~ **DONE** — `suites/longmemeval.py` + committed slice + tests.
2. ~~**Judge-methodology integrity** — either implement randomized option order + meta-judge-on-splits, or correct the docstrings that claim them.~~ **DONE** — meta-judge-on-splits implemented (`judge_panel.py` + `make_meta_judge_call`), verbosity length signal added, randomized-option-order false claim corrected (N/A for pointwise). Tests `test_judge_meta.py`.
3. ~~**Analyst v2 wired to `store_v2`** — longitudinal regression/feature/tool mining (v2-E).~~ **DONE** — `analyst.analyze_store` over `store_v2` (regression + recurring-fail), backlog-shape items, hermetic tests.
4. ~~**Beat-ladder targets as engine constants** — single source for "did we beat it", consumed by the UI ticks + the regression gate.~~ **DONE** — `targets.py` (`BENCHMARK_TARGETS`/`TARGET_SOURCE`/`target_for`/`beats`) + `GET /proving-ground/targets` + UI fetch into `TARGETS`. Registry-coverage test guards new suites. Tests `test_targets.py` + `test_targets_endpoint.py`.
5. (Optional/deferred) WebArena/AgentBench/AppWorld — keep `future` unless a web-sandbox decision is made.
