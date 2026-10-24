# Historical bug status

The files under `planning/bug-handoff-*` are preserved discovery snapshots with their original checksums and original observed-status labels. Do not interpret those labels as the current repository state.

Current status as of Nerve v0.2.2.dev4:

| Historical item | Current status | Resolution |
|---|---|---|
| JEV-001 repeated failure loop | Fixed | Stable failure fingerprints, provider deduplication, local third-strike REPLAN, and pre-tool control enforcement. |
| JEV-002 outcome attribution / metric scope | Fixed | Stable decision IDs, control lifecycle rows, next-action attribution, explicit scope labels, documented correction denominator. |
| JEV-003 gate telemetry ambiguity | Fixed in plugin telemetry | Gate-hook observations are recorded even with `gate_mode=off`; correlations/scopes are explicit. dev4 also serializes gate JSONL writes. |
| JEV-004 oversized `nerve_stats` | Fixed | Compact/sectioned output by default, recent data opt-in and bounded. |
| JEV-005 zero rehydration observation | Clarified + regression covered | Rehydration is demand-driven; zero without a recovery request is not itself a failure. Anchored evidence rehydration is covered. |
| JEV-006 failure-call amplification | Fixed | Repeated equivalent failures stay local after the initial provider evaluation unless state/evidence materially changes. |
| JEV-007 decision-lease churn | Fixed | Semantic fingerprints exclude monotonic counters; reuse and invalidation reasons are tracked. |
| JEV-008 startup diagnostics | Plugin-side fix complete | Plugin logs exact loaded version/path. Pre-discovery Hermes warnings, if still reproducible, are host-level/upstream behavior. |
| v0.2.1.1 BUG-001 assess schema mismatch | Fixed | Deferred schema encodes runtime choice/score constraints. |
| v0.2.1.1 BUG-002 Jev self-observation amplification | Fixed | `jev_*` events are a local-only supervision boundary and cannot recursively generate nervous provider calls. |
| v0.2.1.1 BUG-003 receipt-backed provenance | Fixed | Remote Jev claims carry receipt/request-backed provenance; local/error/stale results cannot present as verified. |
| Issue #2 >48 context-engine crash | Fixed in v0.2.1.2 | Automatic selection is bounded, apply/shadow failures are fail-open and observable, and 48/49/60/851 regressions are covered. |
| Issue #1 direct TypeSafe interoperability | Fulfilled | Two independent direct-account reports succeeded; one included smoke, full live suite, and Hermes-host integration. |

Development-line hardening added after those reports:

- v0.2.2.dev3: completed-turn eviction, missing-cost semantics, shared JSONL persistence, expanded secret redaction, generic catalog-boundary verification.
- v0.2.2.dev4: provider-cost scoping excludes local-only decisions, gate JSONL joins the shared persistence path, GitHub Actions/dependency maintenance is current, and CI includes Python 3.14.

The source of truth for current behavior is the current branch code plus its exact GitHub Actions result. Historical evidence packages remain unchanged so their recorded checksums stay meaningful.

Fork branch `deploy/robweb-20261023` additions (status as of 2026-10-23; upstream PRs open, not merged):

| Item | Current status | Resolution |
|---|---|---|
| Issue #65 terminal JSON `"error": null` read as failure | Fixed on the fork (PR #66) | Successful exit status with a null error is a success; masked exit status and whole-word failure signals are rejected. |
| Test launcher / runner misclassification (`uv run`, `uvx`, `poetry run`, virtualenv `pytest`, …) | Fixed on the fork (PRs #72, #73) | Launcher prefixes and additional runners are recognised as test invocations; previously these caused false "no tests ran" completion blocks in intervening modes. |
| Shadow mode steering the turn on completion blocks | Fixed on the fork (PR #67) | `nervous_mode=shadow` logs `progress_completion_blocked` with `shadow=true` and injects nothing. |
| Issue #60 hook-local fallback pinned as canonical Kanban task | Partially fixed on the fork (PR #68) | The fallback identifier is no longer written to the environment. Gateway processes still write one `autobind_task_missing` diagnostic per tool call; follow-up pending. |
| Provider outage silently disabling supervision | Fixed on the fork (PR #71 + `719a8a1`) | Deterministic admission risk floor keeps risky turns at `WATCH`; typed connection errors trip a circuit breaker with fixed, privacy-safe error categories. |
| Unknown-tool risk alarms for read-only commands | Fixed on the fork (`ac9cf86`) | Content-aware classification of terminal/code tools; privacy-safe replay of real shadow logs showed a large drop in `unknown` classifications and more low-risk recognition. |
| `historical_samples` stuck at 0 in shadow mode | Fixed on the fork (`aef26bd`) | Shadow interventions are auto-labelled from later turn behaviour, so the local learner receives outcome rows without hand labels. |
