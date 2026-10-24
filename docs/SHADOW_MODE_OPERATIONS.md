# Shadow-mode operations

How to run Nerve as an observer on a real Hermes profile, prove it is not steering, read what it recorded, and remove it. This is the generic runbook for fork branch `deploy/robweb-20261023`; it assumes Hermes profiles and `systemd --user` gateways, but nothing here depends on a particular host.

## 1. What "watch-only" means on this branch

Nerve has several independent surfaces. Each has its own mode setting, and only the combination below is observe-only:

| Surface | Setting | Observe-only value | What still happens |
|---|---|---|---|
| Nervous system (per-turn admission, router, Jev assessments) | `nervous_mode` | `shadow` (default is `correct_next`, which **steers**) | Provider calls, risk scoring, and would-be decisions are recorded; `inject_challenge()` returns nothing and `before_tool_call()` enforces no lease. |
| Completion integrity | follows `nervous_mode` | `shadow` | A would-be block is logged as `progress_completion_blocked` with `shadow=true`; the turn is not continued or challenged. |
| Action Gate (synchronous pre-tool verdicts) | `gate_mode` | `off` (default) or `advisory` | `off`: no verdicts, gate-hook observations still logged. `advisory`: one provider verdict per gated tool, log only. |
| Kanban work supervision | `work_supervision_mode` | `shadow` (default is `advisory`) | `advisory` can deliver high-confidence WATCH/REPLAN/BLOCK advice to a worker that has bound a Kanban task; `shadow` records only. Processes with no dispatcher-provided task never bind. |
| Context governor | `context_curation_mode` | `shadow` (default) | Curation proposals are recorded, not applied. The ledger still stores recoverable tool results (see §3). |

Minimum settings for a watch-only trial, under `plugins.entries.nerve.settings`:

```yaml
plugins:
  entries:
    nerve:
      enabled: true
      settings:
        jev_provider: typesafe        # or openrouter / opencode, see PROVIDER_SETUP.md
        nervous_mode: shadow
        work_supervision_mode: shadow # optional hardening; default advisory
```

Shadow mode does **not** mean "no provider calls" and does not reduce what the judge sees: each assessed turn sends bounded structured state (user message and tool-result previews up to the configured limits) to the configured provider. Decide that privacy question before enabling the plugin on a profile that handles sensitive content.

## 2. Install and prove it

1. Back up the profile's `config.yaml` and any existing `plugins/nerve/` tree (owner-only directory).
2. Copy the plugin tree into `plugins/nerve/` and compare file hashes against the source commit you intend to run.
3. Add the settings above; restart **that one gateway**. Confirm the service is active with no restart loop and the journal shows the plugin's loaded version and path.
4. Send one harmless message. Expect `turn_start`, `turn_admission`, and `turn_complete` rows in `nerve/nervous-events.jsonl`, and confirm no tool call was blocked and the answer was normal.
5. Repeat per profile, one at a time. Do not restart all gateways at once.

Proof that nothing steered: no `challenge_delivered`/control rows with a non-shadow disposition in `decision-outcomes.jsonl`; `progress_completion_blocked` rows carry `shadow=true`; `run_controls` and `run_directives` tables in `work-supervision.sqlite3` are empty; chat output unchanged.

## 3. What Nerve writes locally (per profile `nerve/` directory)

| File | Content contract |
|---|---|
| `nervous-events.jsonl` | Schema `hermes-nerve-nervous/v2`. Content-free: hashes, normalized tool names, fixed categories, risk/reasons, counts, latency. |
| `decision-outcomes.jsonl` | Decision, control-lifecycle, and `outcome` rows. Fingerprints, labels, scores; no content. |
| `gate-events.jsonl` | Gate-hook observations (`mode`, `action`, `tool_name`, `reason`). No arguments. Written even when `gate_mode=off`. |
| `receipts.jsonl` | Provider request/response provenance: hashes, model, latency, decision contract. No prompt text. |
| `context-ledger.jsonl` | **Contains content.** Recoverable tool results with `content`, `chars`, and `metadata.args`, file mode `600`. Grows with every tool call. Disable with `context_ledger_enabled=false` if a second copy of tool output is unacceptable. |
| `work-supervision.sqlite3` | Kanban contracts, run bindings, evidence (with previews), controls, usage, and `supervision_diagnostics`. On non-worker profiles only diagnostics accumulate. |

Rows written **before** the telemetry hardening on this branch may still carry bounded tool-result previews; aggregate old rows, do not quote them.

## 4. Reading the evidence (aggregates only)

Filter rows by `timestamp` from the install time, then count:

- turns (`turn_start`), admission mix (`turn_admission` → `OFF` / `WATCH` / `ON`);
- `router` rows by `risk_reasons` category (`read_only`, `network_read`, `ambiguous`, `unknown`, `mutating`) and by `failure_category`;
- `assessment_*` rows, `assessment_error` rows by `error_category`, and breaker trips (`circuit-open`);
- `shadow_outcome` rows (`useful_disagreement` true / false) plus `shadow_outcomes_unresolved` in `turn_complete`;
- `progress_completion_blocked` rows with `shadow=true` — the would-be blocks; compare against the tool the agent actually ran next;
- latency fields on assessment rows (p50 / p95 added per turn).

Never print `content`, `preview`, `prompt`, `args`, or `output` fields into a report, issue tracker, or chat. The questions a trial should answer: how often would Nerve have intervened, would those interventions have been justified, how much latency and provider cost did it add, and how did it behave when the provider failed.

## 5. Known noise on this branch

- Gateway (non-worker) processes write one `autobind_task_missing` diagnostic per tool call into `work-supervision.sqlite3` when the hook-local fallback identifier is not a canonical Kanban task. The #60 fix stops that identifier from being pinned into the environment, but the lookup and diagnostic still run. Harmless, content-free, and a candidate for a follow-up change.
- `plugin.yaml` reports `0.3.2.dev0` while `scripts/verify_release.py` expects `0.3.1`; the verifier passes only when told to tolerate the label.
- `tests/test_shadow_outcome_labeling.py::test_learned_probability_feeds_router_after_min_samples` passes its assertions but can error during temporary-directory cleanup because the background nervous worker writes a late row; the fix is to drain the worker before leaving the test's context.

## 6. Rolling back one profile

1. Stop that gateway after confirming no active turn.
2. Restore the pre-install `config.yaml` (removes the `nerve` entry) and delete `plugins/nerve/`, or restore the previous plugin tree from the backup if only the build should be reverted.
3. Start the gateway; confirm active, no restart loop, one normal request succeeds.
4. The `nerve/` directory holds only local ledgers; archive or delete it according to the profile's data policy. The context ledger contains tool output and should be treated like session history.

## 7. Moving beyond shadow

Do not switch `nervous_mode`, `gate_mode`, or `work_supervision_mode` to an intervening value on a profile that handles real work until the shadow evidence shows the would-be interventions were justified, the launcher/test-runner recognition fixes on this branch have been re-validated on that profile's tooling, and one low-risk profile has run the stricter mode first. In the isolated trial that preceded this branch, intervening modes blocked honest work because test launchers were misclassified; that is exactly the class of error shadow evidence is meant to surface first.
