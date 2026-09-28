# Synthetic read HTTP seam

`plat_harness.synthetic_read_http` is a versioned, loopback-only HTTP/JSON
surface for **two fixed public synthetic resources**. It is a protocol client
test of the Wave 4 connector seam. No ChatGPT, Claude, Grok, or Muse host has
been tested. It reads no deal folders or databases, calls no engines, and
creates no business artifacts.

| GET path | Existing contract returned | Scope |
|---|---|---|
| `/v1/synthetic/deals/DEMO-LEASE-UP` | Occupancy classification, year-2 yield on cost, numeric reasonability withhold, original thesis | Exact path |
| `/v1/synthetic/operations/oak-ridge-2026-05` | Pinned Boxscore Oak Ridge May 2026 sample NOI variance and occupancy counts | Exact path |

The deal is an invented fixture, not an engine output or a bid. The reasonability
result is the harness **numeric band check**, not an LLM review of current public
market evidence. The Oak Ridge values come from `plat-operations` commit
`171eb9622f53fe9d6b9703191d5a6832064100ed`,
`boxscore/tests/oak_ridge_demo.rs`: actual NOI 339150, budget NOI 353200,
variance -14050, occupied 153, vacant 15, down 4. It supplies no occupancy
denominator, so this endpoint supplies no occupancy rate for Oak Ridge.

Start a local server from an installed harness package:

```bash
export PLAT_SYNTHETIC_READ_TOKEN='set-a-long-random-local-token'
python3 -m plat_harness.synthetic_read_http --port 8765
curl -H "Authorization: Bearer $PLAT_SYNTHETIC_READ_TOKEN" \
  http://127.0.0.1:8765/v1/synthetic/deals/DEMO-LEASE-UP
```

`make_server` accepts a map of bearer tokens to exact allowed paths, so an
embedding client can grant only the deal or only the operations sample. The
command-line demo grants its one token both public fixtures. Missing tokens get
401, disallowed scopes 403, unknown paths 404, and writes 405. Responses use
`Cache-Control: no-store`. The server binds only `127.0.0.1`; it is not a
deployed authentication, tenant, or production data service.

The fixed GET resources follow HTTP's safe read semantics in
[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html). This surface uses
plain versioned HTTP/JSON rather than claiming MCP host compatibility. Any
future exposed service needs its own authorization and host-specific validation.
