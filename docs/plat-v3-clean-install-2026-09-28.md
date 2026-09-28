# PLAT V3 public clean install, 2026-09-28

Two fresh Python 3.14 venvs under `/tmp` were installed from local public
checkouts, with `PYTHONPATH` unset. No private packages or live deal files were
used. All four packages built wheels and installed together in each venv.

| Set | Harness | Costmodel | Agent | Underwriting |
|---|---|---|---|---|
| Public baseline | `e9ec04a` | `518142e` | `4726813` | `0d106d6` |
| New candidate | `51bc477` | `518142e` | `8bed970` | `0d106d6` |

Baseline install:

```bash
python3 -m venv /tmp/plat-v3-clean-public-20260928
env -u PYTHONPATH /tmp/plat-v3-clean-public-20260928/bin/python -m pip install \
  /home/ubuntu/plat-harness-v3-public-replay \
  /home/ubuntu/projects/.worktrees/plat-costmodel-deferred-maintenance \
  /home/ubuntu/projects/.worktrees/plat-agent-v3-from-main \
  /home/ubuntu/projects/.worktrees/plat-multifamily-underwriting-missing-price-cap-public
env -u PYTHONPATH /tmp/plat-v3-clean-public-20260928/bin/python -m pip check
```

`pip check`: no broken requirements. A smoke run from `/tmp` confirmed all four
imports came from the venv's `site-packages`. The installed underwriting engine
returned `year_2_unlevered_noi=1039354.8` for the saved public TEST-001 input.
The baseline agent, when run with the costmodel sibling source checkout, still
returned the stale yield `0.07864680842697181506276973290` because it used
the old 1,200,004.80 constant. This is the defect fixed by agent `8bed970`.

Candidate install repeated the command above in
`/tmp/plat-v3-clean-candidate-20260928`, substituting the harness worktree at
`51bc477` and agent worktree at `8bed970`. `pip check` passed. An installed
package smoke run from `/tmp`, still without `PYTHONPATH`, returned the new
year-2 NOI `1039354.8`, capex `1758150.0`, thesis yield
`0.06811800906400841517484098662`, and `bid=None`. Both installed HTTP
resources returned 200 to a loopback client. The candidate refused the older
underwriting primary checkout `79d83b3` because its HEAD differs from
`0d106d6`.

## Remaining sibling checkout dependency

Installing all four wheels is not sufficient to run the agent's synthetic
cross-repo path on its own. The agent still loads costmodel source from a
checkout using `PLAT_COSTMODEL_PATH` and `PLAT_COSTMODEL_DEFERRED_PATH`; the
deferred estimator requires costmodel HEAD `518142e`. The new TEST-001 path
also requires `PLAT_MULTIFAMILY_UNDERWRITING_PATH` to point to underwriting
HEAD `0d106d6`, which it checks before running the engine. If the installed
costmodel package was imported first, the baseline agent refused its loaded
estimator path because it was in `site-packages` rather than that sibling
checkout. That smoke failure is recorded rather than called an integrated
package pass. The candidate smoke passed with the exact sibling paths set.

This is synthetic install and protocol evidence only. It does not validate a
consumer app host, live migration, or release candidate.
