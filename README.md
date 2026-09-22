# plat-harness

Agent-agnostic control plane for multifamily underwriting and asset operations.
**The model is rented. Certified numbers come from tools.**

```bash
git clone <this-repo> plat-harness && cd plat-harness
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

plat-harness ask --metric noi
# → CONFLICT_UNRESOLVED (will not average competing NOI formulas)

plat-harness scoreboard --asset example_property
# → uncertified_empty unless you point env at samples/ops (synthetic data only)

plat-harness underwrite --deal example_garden_style
# → MISSING_MILLAGE  (mills per $1,000)

plat-harness underwrite --deal example_garden_style --millage-rate 25.31
# → millage gate passes; CoC/IRR/DSCR/EM/cap are NOT invented
#    until you import a Decimal engine extra (see engine/README.md)
```

The synthetic **80-unit** garden-style sample does **not** invent cash-on-cash.
The certified board is built from **synthetic** fixtures in `samples/` (or from
data you mount with env vars). This repository does not ship anyone's live
deal room.

## What this is

| Command | Job |
|---|---|
| `plat-harness ask` | NL or `--metric` → tools only. CONFLICT metrics need `--context`. |
| `plat-harness scoreboard` | Pipeline tile: certified CoC/occupancy or `uncertified_empty`. |
| `plat-harness underwrite --millage-rate` | Deal path. Millage-less runs refuse. |

Occupancy answers must emit **occupied, vacant, down, and the denominator**.
Missing millage → `MISSING_MILLAGE`. Missing feed ≠ `$0`. `IRR = CoC * 0.8` is forbidden.

## Operator prompts (Spark, Cursor, Codex, Hermes)

This is a **CLI**, not a Boxscore-style TUI and not a required IDE plugin.
Coding agents and a DGX Spark session (FT Qwen or frontier) drive it by
shelling `plat-harness`. Paste [`prompts/SPARK_OPERATOR.md`](prompts/SPARK_OPERATOR.md)
as the system prompt. Host notes: [`prompts/HOSTS.md`](prompts/HOSTS.md).

Point env at **your** data on the machine that already holds it. Do not copy
OM / rent rolls / ops databases onto a GPU node.

Sequenced product path (generic): [`docs/ROADMAP.md`](docs/ROADMAP.md).
Folder-drop campaign: [`prompts/DEAL_ROOM_CAMPAIGN.md`](prompts/DEAL_ROOM_CAMPAIGN.md).
GPU workstation bootstrap: [`prompts/SPARK_BOOTSTRAP.md`](prompts/SPARK_BOOTSTRAP.md).

## Install

```bash
pip install -e ".[dev]"
pytest
```

If `python3 -m venv` is unavailable:

```bash
python3 -m pip install --target .deps 'pyyaml>=6.0' 'pytest>=7.0'
PYTHONPATH=harness/src:.deps python3 -m pytest
PYTHONPATH=harness/src:.deps python3 -m plat_harness scoreboard --asset example_property
```

Point at **your** data (never commit it here):

```bash
export PLAT_HARNESS_OPS_ROOT=/path/to/your/ops/properties
export PLAT_HARNESS_DEAL_ROOT=/path/to/your/deals
export PLAT_HARNESS_BOXSCORE_DB=/path/to/your/ops.db
export PLAT_HARNESS_ENGINE_ROOT=/path/to/your/decimal/engine
export PLAT_HARNESS_GLOSSARY=$PWD/docs/glossary.yaml
```

To try the synthetic ops sample:

```bash
export PLAT_HARNESS_OPS_ROOT=$PWD/samples/ops
plat-harness scoreboard --asset example_property
plat-harness ask --metric physical_occupancy --context ops_actuals --asset example_property
```

## Policy

Hurdles live in `policies/default.yaml`. The default file is a **generic GP
template**: `coc_hurdle` is `null` until you set it. Copy
`policies/examples/cashflow_first.yaml` and fill in **your** Year-1 CoC ratio.
This kernel does not ship a compiled cash-on-cash percentage or a unit-count box.

## Engine extra

`engine/` in this repo is a **path-dep stub**. Bring a Decimal `run_underwriting`
compatible with the harness adapter. See [`engine/README.md`](engine/README.md).

## What this package will not do

- Invent IRR / CoC / DSCR / EM / cap
- Average CONFLICT glossary rows
- Publish `memo_ready` with blockers
- Treat MCP `check_deal_feasibility` as the buy-box
- Ship live OM / T12 / rent rolls / ops databases
- Depend on `claude-agent-sdk`, `anthropic`, or `plat_agent.dispatch.sibling`

## License

Apache-2.0. See `LICENSE` and `NOTICE`.


## Running the test suite

Run the tests under a private umask — the storage and acceptance layers
**refuse group/world-writable artifact paths by design** (fail-closed mode
checks), so a default `umask 022`/`002` will surface those refusals as
failures:

```bash
umask 077
pytest -p no:cacheprovider -o addopts= -q tests
```

All host-specific configuration (`PLAT_HARNESS_PRIVATE_ROOT`,
`PLAT_HARNESS_NATIVE_LOCK_DIR`, `PLAT_HARNESS_SOVEREIGN_HOST`/`_UID`,
`PLAT_HARNESS_MODEL_PATH`, `PLAT_HARNESS_RUNTIME`,
`PLAT_HARNESS_SITE_PACKAGES`) is env-based and fail-closed: unset means
refuse, never fall back to a default path. See `docs/INSTALL.md`.

## Status and honest limitations

`plat-harness` is a **v0.1 research release**: a working, model-free control
plane with strict provenance gates — not a certified underwriting product.

- **Four-count reconciliation is blocked** on real evidence: unit use
  classification and independent "down unit" counts require reviewed source
  documents. The harness refuses to guess. See `docs/ACCEPTANCE.md`.
- Real-vendor validation is **not claimed**. All parser tests use synthetic
  fixtures; see `docs/PMS_COMPATIBILITY.md` for the honest matrix.
- Property-tax regimes are researched with statute-tier citations
  (`docs/TAX_REGIME_SPEC.md`) but require competent human review per
  jurisdiction before production use.
- Engine eligibility is always `not_evaluated` until separately approved.

## Security

See `SECURITY.md`. Report suspected vulnerabilities privately to the
maintainer address in `SECURITY.md`; do not open public issues for
exploitable findings.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
