# Nerve verification receipts

> **Fork branch `deploy/robweb-20261023` (2026-10-23).** The historical v0.2.1.2 receipt below is preserved unchanged. The current branch's verification state is recorded first.

## Fork branch `deploy/robweb-20261023` — verification notes (2026-10-23)

- Branch base: upstream `main` at `d3f3990`; snapshot commit `b2b6831` replaced the tree with the installed `v0.3.1` release tree plus fork fixes. Feature commits on top: `ac9cf86` (content-aware tool risk), `719a8a1` (typed provider outages, breaker, error categories), `aef26bd` (shadow outcome labeling), `088ec50` (content-free telemetry). Documentation commits follow `088ec50` and change no code.
- Full suite (`PYTHONPATH=. python3 -m unittest discover -s tests`): **412 tests, 6 failures, 1 error, exit 1**. The 6 failures are the pre-existing `tests/test_core.py::ProvenanceAndLedgerTests` profile-home-inference cases (fresh/default root, marked alternate root, nested vendor directory, unmarked nested `.hermes`, named profile from plugin path, deepest scratch profile); they predate the fork work. The 1 error is a temporary-directory cleanup race in `tests/test_shadow_outcome_labeling.py::test_learned_probability_feeds_router_after_min_samples`: all assertions pass, then the background nervous worker writes a late ledger row while `TemporaryDirectory` is removing the directory. It is a test-teardown defect introduced with `aef26bd`, not a runtime failure; fix pending (drain the worker before leaving the context). **The suite is not green**; report these numbers, not "pass".
- Focused suites for the fork features pass: tool-risk classification, provider outages/breaker/error categories, shadow outcome labeling (assertions), content-free telemetry, progress-integrity parsing, V4A paths, autobind identity.
- `python3 -m compileall -q .` passes.
- `scripts/verify_release.py` passes only when the `0.3.2.dev0` manifest label is tolerated as `0.3.1` (version/profile registration, single authority, controller completion, Reflex Laya and OpenJev checks pass). The label mismatch is pre-existing and deliberately unchanged on this branch.
- Live evidence on three real profiles (watch-only): gateways restarted one at a time with zero restart loops; deployed file hashes identical to the source commit; `turn_start`/`turn_admission`/`turn_complete` rows recorded; zero provider errors and zero blocked tools in the first hours.
- Known runtime noise: per-tool-call `autobind_task_missing` diagnostics on gateway processes; the context governor ledger stores tool output locally by default. See `docs/SHADOW_MODE_OPERATIONS.md`.

---

# Nerve v0.2.1.2 verification receipt

> **Historical stable-release evidence.** This receipt verifies v0.2.1.2 only; it does not verify the current v0.2.2 development checkout. For development builds, use the exact commit plus its GitHub Actions result.

## Build target

- Baseline commit: `4feea5ef45aeb301622f18175ed4cf2e068b99bd` (v0.2.1.1).
- Version target: `0.2.1.2`.
- Patch scope: bounded automatic context curation, complete fail-open containment, anchor idempotence, accurate shadow telemetry, and direct-TypeSafe evidence documentation.

## Verified in the isolated candidate

- Full unit suite: **83/83 PASS**.
- Python bytecode compile: **PASS**.
- Release-equivalent version/registration/schema/trace assertions: **PASS**.
- Registered public surface remains **8 tools**, **7 hook names**, **8 hook callbacks**.
- Preserved vNext implementation trace remains exactly **1,119** sequential requirement IDs with unique verification IDs.
- The literal `scripts/verify_release.py` entrypoint could not run in the minimal MegaMCP runner because PyYAML is absent; the verifier's substantive assertions were rerun independently without modifying the verifier.

## Focused 0.2.1.2 verification

- Exactly 48 candidates remain within the public curation contract.
- 49, 60, and 851 candidate sessions select only 48 per automatic boundary.
- Jev curation failure falls back without escaping into the host turn.
- Jev curation plus fallback failure returns the original messages.
- Existing `NERVE_CONTEXT_ANCHOR` results are not re-curated or nested.
- Unrecoverable evidence consumes no automatic semantic provider capacity.
- Shadow proposals do not increment applied-compaction telemetry.
- Shadow failures are observable while remaining fail-open.
- 48 items fan out to at most 12 semantic assessment requests.

## Preserved release invariants

The candidate retains the v0.2.1 repeated-failure loop breaker/control leases, bounded `nerve_stats`, explicit rehydration, and the v0.2.1.1 deferred schema, self-observation suppression, receipt-backed provenance, and direct TypeSafe wire-contract coverage.

## External live evidence

Issue #1 contains an independent successful direct-TypeSafe account smoke against v0.2.1.1: `transport=typesafe-system-one`, provider `TypeSafe`, model `jev-1.13.0`, verified request/receipt provenance, `ok=true`, and about 299 ms latency.

That evidence belongs to the v0.2.1.1 transport path. Unless the v0.2.1.2 candidate is explicitly rerun with a real TypeSafe credential, this build does not claim a new credentialed live direct-account validation.
