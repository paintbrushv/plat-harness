# Policy plugin

The kernel does not compile a GP’s cash-on-cash hurdle or unit-count box.

1. Copy `policies/default.yaml`.
2. Set `coc_hurdle` to **your** Year-1 post-debt ratio (or leave `null` and
   accept that CoC is not a certified hurdle).
3. Set millage handling (hard gate stays on: missing millage → `MISSING_MILLAGE`).
4. Occupancy always requires occupied, vacant, down, denominator.
5. Crime / flood / employer screens **refuse** without a certified feed.
6. Comp four-pack missing → rent upside `NEEDS_DATA`.

Example overlay: `policies/examples/cashflow_first.yaml` (still not a
recommended number — fill in your own).

Point the harness at a file with `PLAT_HARNESS_POLICY` when that loader lands.
Until then, the YAML is the contract other GPs edit.
