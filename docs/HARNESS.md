# Harness architecture (public)

Control plane: `plat_harness`. Calc backends stay siblings you bring.

## Loop

Model-agnostic ReAct with hard exits (`max_steps`, `max_tool_calls`, cost cap).
Slice 0/A in this extract: **NullModel**. Arithmetic is never the model.

Middleware (not prompt soup): MetricConsistency, Citation, millage gate,
occupancy four-counts. CONFLICT rows are not averaged.

## Tools (18)

`get_certified_metric` is the model-visible metric surface. Occupancy, T12 R&M,
and Year-1 CoC fold into it when backends are configured via env.
`run_underwriting_model` requires millage and a Decimal engine extra.
`check_buy_box` is GP policy + millage — **not** an MCP feasibility helper that
ignores CoC.

## Permission ranks

0 Explain · 1 Recommend · 2 Draft · 3 Execute (HITL).

## Data

Never copy OM / T12 / Standardized / ops `.db` into git.
Set `PLAT_HARNESS_OPS_ROOT`, `PLAT_HARNESS_DEAL_ROOT`,
`PLAT_HARNESS_BOXSCORE_DB`, `PLAT_HARNESS_ENGINE_ROOT`.

## Agent-agnostic

No `claude-agent-sdk`, no `anthropic`, no `plat_agent.dispatch.sibling`.
`pytest` with vendor keys unset must pass. A missing `claude` binary is allowed.

## Public extract rules

New repo, clean history. Synthetic fixtures only. Engine-in-tree is a stub
until a second audit of a sanitized Decimal core. See `HISTORY_AUDIT.md`.
Sequenced path (generic): [`ROADMAP.md`](ROADMAP.md).
