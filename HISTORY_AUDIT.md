# History audit — public extract (Slice E)

Date: 2026-09-17
Repo: new `git init` at this tree. Clean history. Not a fork of any private remote.

This file records what was copied, rewritten, and excluded. It contains
**no tenant IDs, no private emails, no SharePoint site IDs, and no live asset names.**

## Method

Rewritten file contents were copied into a fresh `git init`.
`git clone --mirror`, GitHub-fork of private remotes, and `git subtree split`
were **not** used.

## Copied (after rewrite)

- `plat_harness` Slice 0+A loop, tools, millage gate, occupancy four-counts, glossary loader, NullModel, CLI
- Glossary **schema** (metric ids, CONFLICT handshake) with GP hurdles removed from the kernel
- Occupancy / T12 / CoC adapters, using **env only** (`PLAT_HARNESS_*`)
- Tests for millage refusal, occupancy four-counts, CONFLICT, NullModel, no vendor agent SDK

## Engine-in-tree verdict: FAIL

The Decimal engine stays a **path-dep stub**. Public extract ships
`engine/README.md` (“bring your engine”), not a copy of an underwriting tree.
Reasons the full engine did not pass the audit: hardcoded cloud-tenant connectors,
CRM tracker paths, live deal names used as fixtures, compiled GP CoC hurdle,
ops-folder coupling, brand constants, and private git history.

## Default public policy

`policies/default.yaml` is a generic GP template. `coc_hurdle` is `null`.
It does not compile a Year-1 cash-on-cash percentage or a unit-count buy-box.

## Excluded (never in this tree)

- Private sanitize / VPS session prompts / design inventory of private remotes
- Ops Standardized CSVs, GL parquet, rent rolls, AR, weekly reports
- Ops SQLite databases and private inbox data
- Dashboard production config, secrets, Graph tokens, `.env`
- GP funnel SOP and underwriting-standards overlays (unit box, market $/unit kills)
- Governance metric docs used as runtime
- Agent traces, LP names, private emails
- `.claude/` agents, skills, error-memory (deal names as if they were fixtures)
- Deal rooms (`raw_inputs/`, live OM / T12 / rent roll)
- OneDrive / CRM publisher modules and brand constants
- Live market-study property YAML, report trees, paid data
- `claude-agent-sdk`, `dispatch/sibling.py`, vendor agent runtimes
- Occupancy gold that mixes a later CSV mutation with an earlier snapshot

## Samples

`samples/deals/example_garden_style` is a fabricated 80-unit deal.
`samples/ops/example_property` is fabricated Standardized CSVs.
No `resident_name` column.

## License

Apache-2.0 (`LICENSE`).
