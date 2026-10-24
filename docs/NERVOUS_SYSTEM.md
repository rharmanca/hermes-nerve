# Jev Nervous System (fork branch `deploy/robweb-20261023`; manifest `0.3.2.dev0` on the `v0.3.1` tree)

Nerve on this branch keeps Jev off Hermes' normal critical path. Hermes remains the reasoning and execution engine; Jev is an asynchronous decision supervisor. The branch is a deployment snapshot of the `v0.3.1` release tree with fork changes, not a rebase of upstream `main`'s post-0.3.1 development line.

## Turn lifecycle

1. `pre_llm_call` creates/resolves a `turn_id` and starts Jev admission in a background worker.
2. Admission returns `OFF`, `WATCH`, or `ON` using contract `hermes/jev-turn-admission/v1`.
3. Hermes continues immediately; admission never waits in the user-turn critical path.
4. Runtime/tool events are normalized into compact JSON events and processed locally.
5. The adaptive router forwards only state changes where a remote Jev opinion could matter.
6. Jev agreement and low-confidence disagreement are logged silently.
7. High-confidence disagreement becomes a `Challenge`.
8. `transform_tool_result` attaches a still-current challenge to the next model-bound tool result. Stale challenges are discarded from delivery and retained as telemetry.
9. `post_llm_call`/`on_session_end` closes turn-scoped supervision.

## Admission

- `OFF`: ordinary conversation/creative output with no accountable decision plane expected.
- `WATCH`: a decision plane may emerge; events stay local until material work appears.
- `ON`: active asynchronous supervision for the turn.

Pure roleplay is normally OFF. Roleplay with an accountable director/training objective can be WATCH/ON at the director plane; actor dialogue itself is not a decision event.

## Local adaptive router

The router does not use "every N events" as its primary trigger. It scores semantic change from structured fields including:

- materiality and consequence
- uncertainty
- novelty
- contradiction
- strategy change
- repeated failure/recovery
- completion pressure
- risk/reversibility
- freshness of the last remote assessment

Equivalent state is suppressed by semantic hysteresis. Significant events arriving while a Jev call is already in flight are retained in a local pending batch and summarized into a later assessment.

A hard per-turn provider-call budget exists only as a safety valve.

## Decision events

For the strongest supervision, emit `nerve_nervous_event` with bounded alternatives and Hermes' proposed answer in the original event:

```json
{
  "type": "DECISION",
  "goal": "repair failing service",
  "choices": ["PATCH", "ROLLBACK", "GATHER_MORE"],
  "hermes_decision": "PATCH",
  "materiality": 0.9,
  "uncertainty": 0.6,
  "state_version": "git:abc123",
  "decision_version": "repair-4"
}
```

This avoids a second round trip asking what Hermes decided.

## Confidence-gated challenges

`nervous_challenge_confidence` controls when disagreement becomes actionable. Agreement and lower-confidence disagreement remain telemetry. `correct_next` is the recommended default: Jev can challenge the next still-actionable decision without attempting to rewind already-committed actions.

Modes:

- `shadow`: observe only; never inject a challenge.
- `correct_next`: inject current high-confidence disagreement into the next tool-result turn context.
- `precommit`: retains asynchronous operation but lets `pre_verify` consume an already-arrived completion challenge. It never synchronously waits for Jev.

## Shadow mode guarantees

`nervous_mode=shadow` runs the nervous-system admission, router, and assessment paths for observation, but it does not steer the Hermes turn:

- provider assessments and local routing still run, so the profile can measure what supervision would have done;
- high-confidence disagreements are recorded, but `inject_challenge()` returns no challenge and `before_tool_call()` does not enforce a control lease;
- a would-be completion-integrity block is written as `progress_completion_blocked` with `shadow=true`, increments the shadow metric, and returns no continuation instruction;
- no already-completed or irreversible action is undone, and shadow mode does not mean that no provider calls occur.

For a profile-wide watch-only trial, pair `nervous_mode=shadow` with `gate_mode=advisory` and `work_supervision_mode=shadow`. `gate_mode=advisory` records gate observations without blocking; the work-supervision setting keeps the separate Kanban surface from enforcing controls.

## Admission risk floor

When `nervous_admission_risk_floor=true` (default), deterministic risky-operation markers in the user turn impose a minimum `WATCH` admission. A provider result below that floor is raised to `WATCH`. If engine construction or admission assessment fails, the same floor is applied before the failure is recorded, so a provider outage cannot silently turn a risky turn into `OFF`. The fallback row is `admission_floor_fallback` and carries the exception class plus a fixed provider error category.

This floor is separate from the synchronous Action Gate. In `enforce` and `ask-only` gate modes, a provider failure returns an approval request with rule key `nerve:provider-unavailable`; `advisory` mode fails open.

## Content-aware tool risk

`hermes_nerve.toolrisk` classifies command and code content, not only the tool name. The router uses the resulting risk prior; the gate shares the conservative terminal parser for bypass decisions. It examines pipelines/chains and wrappers, nested shells, heredocs, substitutions, redirects, interpreter/code bodies, database statements, service CLIs, and network-client write flags. Deep or unparseable input is `ambiguous`, not silently read-only. The fixed categories and scores are:

| Category | Risk prior |
|---|---:|
| `read_only` | 0.10 |
| `network_read` | 0.30 |
| `unknown` | 0.36 |
| `ambiguous` | 0.40 |
| `mutating` | 0.68 |

Named read-only tools such as `skill_view`, `skills_list`, `web_search`, and `read_file` remain in the read-only set, but content-aware inspection takes precedence for terminal and code tools.

## Provider failures and circuit breaker

The provider client uses typed `JevConnectionError` for transport-level outages, including DNS/connect failures, timeouts, TLS/resets, truncated transport failures, and an open circuit. The defaults are:

- `timeout_seconds`: 10 seconds;
- `breaker_failure_threshold`: 2 consecutive connection failures;
- `breaker_cooldown_seconds`: 60 seconds.

These settings are configurable in `plugins.entries.nerve.settings`. The breaker fails fast while open, resets after a successful call, and does not trip for HTTP status errors or malformed responses because those mean the provider answered. `error_category()` emits fixed labels such as `circuit-open`, `timeout`, `connection`, `http-402`, `transport-error`, `invalid-response`, `no-provider`, `jev-error`, and `other`; raw provider text is not used as the event category.

`nervous.report()` exposes a privacy-safe `provider_breaker` snapshot with `open`, `failures`, `open_for_s`, `threshold`, and `cooldown_seconds`. Assessment failures also increment provider-error metrics and write categorized `assessment_error` rows, while the nervous worker continues supervising locally.

## Staleness and reversibility

Events may carry `state_version` and `decision_version`. A challenge is deliverable only while the relevant decision state is current. Late opinions remain receipts, not commands. Completed irreversible actions are never automatically undone.

## Long-running workers

Supervision is scoped to the turn, not a wall-clock timeout. A two-hour turn with hundreds of tool calls can remain ON. Raw events are accumulated locally; remote calls are driven by semantic relevance and batching rather than volume.

## Local learning

`OutcomeStore` can record labeled decision outcomes. After `nervous_local_learning_min_samples`, a tiny local historical model can calibrate router sensitivity from observed usefulness. It does not add another remote LLM. Set `nervous_local_learning=false` to keep the outcome ledger without feeding it back into routing.

## Shadow outcome labeling

A shadow intervention is stored by safe failure and action fingerprints rather than prompt or tool content. The intervention is eligible for an automatic counterfactual label only when the agent did not see a delivered challenge:

- the same failure fingerprint recurs after the intervention: `useful_disagreement=true`, reason `failure-recurred`;
- the same action later succeeds: `useful_disagreement=false`, reason `same-action-succeeded`;
- neither signal settles the intervention before turn completion: no outcome row is written and `shadow_outcomes_unresolved` is incremented.

Each decision is labeled at most once. Resolved rows are written to the profile outcome ledger and as `shadow_outcome` events with fingerprints, IDs, decision labels, and the reason only. `nervous_local_learning_min_samples` (default `8`) is the minimum number of labeled rows before the local model uses them.

## Decision and telemetry event fields

Nerve event rows use schema `hermes-nerve-nervous/v2` and include a timestamp plus a self-describing `kind`. Router rows carry normalized `tool_name`, status, fixed `failure_category`, `failure_repeat_count`, `deterministic_failure`, numeric `risk`, fixed `risk_reasons`, a safe decision-state fingerprint, and routing score/reasons. Assessment rows carry the safe tool/event type, decision labels, confidence, request ID, and latency; `assessment_error` rows carry the exception class and `error_category`. `turn_complete` adds `admission`, provider-call/error counts, challenge count, `tool_counts`, `tool_failures`, `failure_categories`, and a response hash. Shadow outcome rows carry only IDs, labels, fingerprints, and resolution reason.

Use `nervous.report()` for current-process metrics and the profile outcome ledger for durable outcome totals. Its `provider_breaker` object reports `open`, `failures`, `open_for_s`, `threshold`, and `cooldown_seconds` without provider text.

## Privacy

Nerve's structured router, admission, assessment, turn-complete, completion-observation, and shadow-outcome rows are content-free. They do not write prompt text, tool arguments, tool outputs, or raw provider error text. Turn boundaries use `prompt_sha256` and `response_sha256`; router/assessment rows use normalized tool names, fixed categories, risk/reasons, repeat counts, safe fingerprints, and aggregate labels. Assessment exception telemetry records the exception class and fixed privacy-safe category only. The bounded structured state used for an assessment is a provider request concern; it is not copied into the event ledger. The exceptional background `worker_error` diagnostic still records a truncated exception string; treat that legacy path as a caveat rather than as aggregate telemetry.

The content-free guarantee covers the nervous-system ledgers (`nervous-events.jsonl`, `decision-outcomes.jsonl`), gate events, and receipts. Other Nerve modules keep their own local stores with different contracts: the context governor's `context-ledger.jsonl` records recoverable tool results **including their content and arguments** (owner-only file mode) so curated context can be rehydrated later, and the work-supervision SQLite store records Kanban contracts, evidence previews, and diagnostics. Disable the context ledger with `context_ledger_enabled=false` (or turn the governor off entirely with `context_curation_mode=off` and `context_engine_register=false`) when a profile must not keep a second copy of tool output. See [`SHADOW_MODE_OPERATIONS.md`](SHADOW_MODE_OPERATIONS.md) for a per-file inventory.


## 0.2.1 repeated-failure control lease

0.2.1 adds an execution-side recovery seam for a failure mode observed in Muna:
Jev could repeatedly recommend `REPLAN` or `GATHER_EVIDENCE` while Hermes still
issued the exact same failing action.

Each failed tool action now receives two stable local identities:

- **action fingerprint**: tool name + canonicalized arguments
- **failure fingerprint**: action fingerprint + status/available exit code + normalized error signature

The first failure can be assessed remotely. Exact repeats are deduplicated locally
until materially new state/evidence appears. At the configured repeat threshold
(default `3`), a provider-independent local `REPLAN` control is created.

Under `correct_next` and `precommit`:

- `REPLAN`, `GATHER_EVIDENCE`, and `ESCALATE` block the exact controlled action;
- a materially different next action counts as following the control and consumes it;
- `RETRY` explicitly allows one next action and consumes the control;
- `shadow` records the same lifecycle but does not constrain execution.

The plugin uses one composed `pre_tool_call` callback: local control is checked
first, and the optional legacy synchronous gate is only consulted if local control
allows the action. This prevents a known-bad repeated action from also paying an
unnecessary gate provider round trip.

Control receipts use stable `decision_id` values and lifecycle stages so telemetry
can connect decision -> delivery -> next action -> followed/enforced/expired -> outcome.