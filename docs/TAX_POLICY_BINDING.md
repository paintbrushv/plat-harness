# Harness tax-policy binding adapter: `tax-policy-binding/1.0.0`

**Task 4.3.** Harness-side binding of a *reviewed* tax-policy package to the
underwriting engine's pinned statutory schedule builder. The harness only
**selects and validates** an approved regime package; it performs **zero tax
arithmetic**. The engine (read-only sibling repository, never imported,
executed or modified by this module) owns every unit, rounding and
arithmetic decision through its pinned entry point
`engine.tax_regimes.build_tax_regime_schedule`
(contract `engine-tax-regimes/1.0.0`), produced by Task 4.2 from the Task 4.1
research recorded in `docs/TAX_REGIME_SPEC.md`.

```python
from plat_harness.adapters.tax_policy import (
    bind_tax_policy, bind_tax_schedule, policy_sha256,
    CONTRACT_VERSION, POLICY_CONTRACT_VERSION, ENGINE_SCHEDULE_PATH,
    ENGINE_CONTRACT_VERSION, SUPPORTED_STATES,
)

result = bind_tax_policy(
    policy,                      # reviewed package, contract below
    subject_id='synthetic_tx_policy',   # optional scope assertions
    tax_year=2026,
    expected_policy_sha256=None, # optional drift pin
    engine_schedule=callable,    # host binds ENGINE_SCHEDULE_PATH
    approval_registry=None,      # default: review_bridge.host_registry()
)
```

`policy` is a plain mapping carrying
`tax-regime-research/1.0.0` provenance: `contract_version`, `policy_id`,
`subject_id`, `parcel_id`, `state` (one of the researched `TX`/`CA`/`FL`/`AL`
regimes), `tax_year`, `effective_date`, `basis`,
`purchase_price_basis` (an explicit basis — never purchase-price-inferred),
`levy_unit` (the regime's statutory unit label), regime inputs (e.g. TX
`taxing_units` per-$100 rates, AL `total_millage_mills` +
`jurisdiction`), `millage_rate_mills`, `assessment_ratio`, `source`,
`source_locator`, `requires_competent_human_review: True`, and optionally
`analyst_override: True` plus an `override_approval` record.

## Result contract

`bind_tax_policy` returns `{'status': 'bound', 'engine_schedule': ...,
'engine_schedule_sha256': ..., 'bound_envelope': ...}` or
`{'status': 'refused', 'code': ..., 'message': ..., 'engine_schedule': None,
'bound_envelope': None}`. Refusals are **typed marker returns** — never
raised, never chained, never echoing policy values, engine internals or
exception details. The engine schedule passes through **verbatim**
(byte-identical canonical JSON, SHA256-pinned); applicability and approval
stay in a **separate bound envelope** keyed to the policy SHA256 — the
engine object never receives provenance keys. `bind_tax_schedule(policy,
years=[...])` binds consecutive years; the first schedule year must be
byte-identical to a fresh standalone first-year binding, else
`SCHEDULE_DIVERGENCE` refuses the whole schedule.

## Gates (fail-closed, in order, all before any engine call)

| Gate | Effect |
|---|---|
| Policy shape/version/identifiers/dates/decimal strings | refuse `INVALID_INPUT` |
| Requested subject/parcel/tax year differs from the reviewed policy | refuse `SCOPE_MISMATCH` |
| `state` outside the researched regimes (`TX`,`CA`,`FL`,`AL`) | refuse `UNSUPPORTED_TAX_REGIME` — no "Standard" fallback jurisdiction |
| Policy bytes differ from `expected_policy_sha256` pin | refuse `POLICY_DRIFT` |
| `source`/`source_locator` names a comparison T12/trailing-twelve statement | refuse `T12_NOT_POLICY_EVIDENCE` — a comparison T12 tax row can never substitute for required policy evidence |
| `levy_unit` differs from the regime's statutory unit label, a `declared_unit_for_rate` contradicts it, a taxing unit is *named* after a unit of measure, or a duplicate taxing unit | refuse `RATE_UNIT_INCONSISTENT` — the harness compares declared labels only and never converts between mills / per-$100 / per-$1,000 |
| `millage_rate_mills` missing or blank | refuse `MILLAGE_MISSING` — a missing millage is never zero and **no engine call is made** |
| `purchase_price_basis` absent or `inferred_from_price` | refuse `INFERRED_BASIS_FORBIDDEN` — the basis is explicit or blocked, never inferred from the price |
| `analyst_override` without an `override_approval` record validated by the frozen `contracts._approval` binding against the host-owned registry | refuse `APPROVAL_REQUIRED` / `APPROVAL_MISMATCH` — there is **no second approval database and no `approved: true` shortcut** |
| No bound engine callable, or the callable raises/returns a non-object | refuse `NOT_IMPLEMENTED` / `INVALID_CONTRACT` (sanitized; the harness never fabricates a schedule) |
| First-year output differs from the multiyear schedule's first year | refuse `SCHEDULE_DIVERGENCE` |

## Engine routing

`_engine_inputs` routes only the regime's mapped policy keys to the engine's
documented schedule inputs (TX → `purchase_price`,
`taxing_unit_rates_per_100`; CA → `base_year_value`, `annual_cpi_factors`,
`ad_valorem_rate`; FL → `unit_count`, `just_value`, millage pair; AL →
`jurisdiction`, `fair_market_value`, `total_millage_mills`). Free-text
provenance, review records and approval records **never reach the engine**.
The harness performs no conversion of any value and no arithmetic of any
kind.

## Honest limits

- Every binding reports `requires_competent_human_review=True`; research is
  research, not law — production statutory approval requires competent
  human review outside this module.
- Insurance escalation (including windstorm) is a **separately approved
  assumption**, never a tax-regime default; it is out of scope here.
- TX circuit-breaker applicability beyond tax year 2026 is
  uncertainty-blocked at the engine schedule (`UNCERTAINTY_BLOCK`); the
  harness propagates that refusal rather than defaulting.
- Only the four researched regimes are bindable. An unresearched
  jurisdiction refuses rather than borrowing another state's mechanics.
- Free-text policy fields (`source`, `source_locator`) are not copied into
  the bound envelope or engine inputs, so hostile free text cannot surface
  in results; canaries stay absent from results and refusals.
