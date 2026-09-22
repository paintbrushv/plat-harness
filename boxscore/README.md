# Ops calc notes (sanitized)

BOXSCORE-like variance / T12 / occupancy is the **ops calc backend** the harness
wraps. This public tree does not vendor:

- a live SQLite `.db`
- asset display names
- TUI that renders `resident_name`

Keep: occupancy four-counts (down in the denominator for the ops_actuals
context), implicit-zero guards, unmapped-account disclosure, missing feed ≠ $0.

Wire a database with `PLAT_HARNESS_BOXSCORE_DB`. Tests use fabricated SQLite.
