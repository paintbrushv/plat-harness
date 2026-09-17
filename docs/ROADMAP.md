# ROADMAP

The harness is the product. The model is rented. Certified numbers come from
a Decimal engine extra and from ops adapters you mount with env — never from
chat.

## Spirit

One product, two loops:

- **Acquisitions:** drop a data room, classify the deal type, load that type’s
  deterministic assumption pack, underwrite, reconcile, test. Same slug twice
  must yield the same CoC / IRR / DSCR.
- **Asset management:** period actuals vs budget, occupancy four-counts, PM
  questions with sources, longer-horizon budget/strategy. Never auto-send LP
  commentary.

Intelligence is a certified `deal_type` classifier + policy packs + tools.
The model chooses commands and writes cited prose. It does not invent returns.

Deal types (classifier ships with the live underwrite wrap):

| Type | Pack sketch |
|---|---|
| `core` / `core+` | Cashflow-first; in-place rents; low-end vintage opex floors; CoC hurdle |
| `lease-up` | Do not treat trailing NOI as going-in; vacancy/concession curves |
| `value-add` | Mid vintage floors; reno not silent in base; label Y1 vs Y2/Y3 CoC |
| `opportunistic` | High-end R&M/CapEx; do not haircut insurance to make CoC |

Public `policies/default.yaml` keeps `coc_hurdle: null`. Set **your** hurdle
in an overlay. Copy `policies/examples/cashflow_first.yaml`.

## Sequence

| Slice | Status in this extract | Done when |
|---|---|---|
| 0 | Landed | CONFLICT / `MISSING_MILLAGE` / occupancy four-counts |
| A | Landed (synthetic samples here) | Scoreboard occupancy + T12 R&M when `PLAT_HARNESS_OPS_ROOT` points at `samples/ops` |
| E | This repo | Stranger clones, pytest, millage-less underwrite refuses |
| B | Not in this extract yet | `underwrite` wraps ingest → type → millage → engine extra → recon → IC draft. Engine extra required for CoC |
| C | Later | Ops variance + exceptions; missing budget ≠ $0 |
| D | Later | HITL publish only |
| Cockpit + local MCP | Later | UI displays CLI JSON; MCP shells the same binary |

**Not now:** ChatGPT as the calculator, uploading an OM into a Custom GPT,
`claude-agent-sdk`, a second TUI, overnight publish.

Non-technical Claude Desktop users: local stdio MCP **after** B. ChatGPT
remote MCP is hosted HTTPS — do not tunnel live rooms by default. See
[`prompts/HOSTS.md`](../prompts/HOSTS.md).

## What to run today

```bash
export PLAT_HARNESS_OPS_ROOT=$PWD/samples/ops
export PLAT_HARNESS_DEAL_ROOT=$PWD/samples/deals
plat-harness scoreboard --asset example_property
plat-harness underwrite --deal example_garden_style
# → MISSING_MILLAGE
```

Point `PLAT_HARNESS_*` at **your** folders beside this clone. Never commit them.
A folder-drop campaign prompt (no vendor paths):
[`prompts/DEAL_ROOM_CAMPAIGN.md`](../prompts/DEAL_ROOM_CAMPAIGN.md).
