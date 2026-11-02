# Rob fork status — hermes-nerve v0.4.15-rob.1

Fork: `rharmanca/hermes-nerve`. Default branch `main` = this line (upstream
keeltrace v0.4.15 `76320ec` + Rob patches). Previous 0.3.x fork main is kept at
branch `archive/main-0.3-20261101` and tag `archive-main-0.3` (`d3f3990`).

## Patches on top of v0.4.15
| Commit | What |
|---|---|
| 691cca0 | `nudge_only` supervision mode + INF-31 external-write progress |
| cb19f98 | nudge_only counts suppressed completion checks like shadow |
| 3056d38 | unknown `nervous_mode` fails safe to shadow; `home=` in load logs |
| 012f46c | tests isolated from host Hermes runtime (home-resident TMPDIR, Kanban env) |
| 9d5cb75 | judge-outage fast-fail circuit breaker (ported from old fork) |

## Verification (2026-11-01)
- Full suite: 1194 passed, 0 failed, 18 skipped (was 1149 passed / 46 failed —
  all failures were host-environment leakage, see DECISIONS.md).
- nervelab qualification (real TypeSafe judge, nudge_only): 11/11 checks pass,
  0 blocked, 0 forced continues, 0 approval prompts; scorecard 8/12/0/0/0.

## Deployment
- nervelab: 9d5cb75 (qualification profile).
- ops: still on 3056d38 (feat/nudge-only-v0415). Moving ops to this tag needs a
  separate yes from Rob.
- No profile runs a blocking mode.
