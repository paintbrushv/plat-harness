# Synthetic deal matrix

Six invented canonical inputs, derived from the public packaged TEST-001 fixture, test distinct deal decisions with the reviewed
installed underwriting and harness packages. They contain no owner, resident,
manager, lender, or real-property data. The shared 100-unit control makes the
lease-up, operating-cost, and price sensitivities comparable. The separate
40-unit distressed asset tests a different size and an explicit $250,000 roof
expense. The missing-price input must fail validation and withhold a bid.

| Fixture | Change from control | Expected observation |
|---|---|---|
| `stabilized.json` | 100 units, 5% vacancy, 1% collection loss | Stabilized; year-2 yield in the 7–8% target band. |
| `lease_up.json` | 25% vacancy, 3% collection loss, 5% loss to lease | Lease-up; lower NOI and failed feasibility. |
| `cost_stress.json` | Insurance $1,200/unit and R&M $1,400/unit | Lower NOI; numeric presentation and cashflow feasibility reported separately. |
| `high_price.json` | $18m purchase, 65% LTV | Lower yield and reasonability withhold. |
| `small_distressed.json` | 40 units, 4 down, 30% modeled vacancy, roof expense | Distressed; roof cost included in the denominator. |
| `missing_price.json` | No purchase price | Validation failure; missing price cannot become zero or a bid. |

Run after installing the four pinned public packages used by
`.github/workflows/v3-public-integration.yml`:

```sh
env -u PYTHONPATH -u PLAT_COSTMODEL_PATH -u PLAT_COSTMODEL_DEFERRED_PATH \
  -u PLAT_MULTIFAMILY_UNDERWRITING_PATH -u UNDERWRITING_ENGINE_PATH \
  -u PLAT_DEALS_ROOT -u UNDERWRITING_MCP_CMD -u PLAT_COSTMODEL_CMD \
  python -I scripts/verify_synthetic_deal_matrix.py
```

The physical occupancy counts live in `manifest.json` beside the canonical
deal inputs. The script reads the committed fixtures, checks installed package origins and
the underwriting content pin, then runs the engine and adapter functions
without source checkout imports. The existing installed integration verifier
separately tests the MCP transport. All economics are invented; none prove a
market bid, a closed accounting period, a property-manager handoff, real
permission grants, or release readiness. The numeric presentation gate and
cashflow feasibility gate are separate today. A displayed numeric bid in this
matrix is not an approved investment decision.
