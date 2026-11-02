# Rob fork decisions

## 2026-11-01 — v0.4.15-rob becomes fork main
- Old 0.3.x fork main preserved (branch `archive/main-0.3-20261101`, tag
  `archive-main-0.3`, `d3f3990`) before `main` was moved. No branches deleted.
- `main` now tracks upstream v0.4.15 plus Rob's nudge-only patches. Releases are
  tagged `v0.4.15-rob.N`. Nothing is sent upstream without Rob's OK.

## Old-fork branch review vs v0.4.15
| Branch | Status | Commit |
|---|---|---|
| judge-outage fast-fail (deploy/robweb-20261023 9d18cc6+719a8a1) | still-needed → ported (client breaker only; nervous.py metric changes not needed) | 9d5cb75 |
| pr/progress-integrity | already-in-upstream (branch tests pass unchanged) | — |
| fix/test-runner-recognition | already-in-upstream (identical tests in tree) | — |
| fix/launcher-prefixes | already-in-upstream (identical tests in tree) | — |
| fix/kee-804-progress-guard-harness-skip | already-in-upstream (`harness_instructions_message`, `is_harness_path`; branch tests pass) | — |
| pr/admission-risk-floor | already-in-upstream (identical tests in tree) | — |
| feature/block-review-judge | already-in-upstream (`work/block_review.py`); also blocking-strengthening, so not ported regardless | — |
| pr/shadow-observe-only | already-in-upstream (identical tests in tree) | — |

## Failing tests were environment, not code
The 46 pre-existing failures came from running the suite inside a live Hermes
worker: TMPDIR under the user's home (tmp fixtures looked like profile paths)
and `HERMES_KANBAN_*` / `HERMES_DELEGATED_CHILD_CONTEXT` (worker
completion-authority rules applied). `tests/conftest.py` now neutralises both.
No assertion was changed, skipped or deleted. `NERVE_TEST_TMPDIR` overrides the
neutral temp base (keep it short; Chrome socket paths are length-limited).

## Breaker never adds blocking
Fail-fast raises `JevConnectionError`, a `JevError`, so the existing outage path
(fail-open) applies. HTTP status errors do not trip it. Tunables:
`breaker_failure_threshold` (2), `breaker_cooldown_seconds` (60).
