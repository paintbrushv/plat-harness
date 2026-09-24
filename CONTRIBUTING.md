# Contributing

1. Synthetic fixtures only. If a PR needs a “realistic” T12, fabricate it.
   Never copy a live OM, rent roll, or ops Standardized export.
2. No `resident_name`, SSNs, LP names, or loan numbers in tests, docs, or traces.
3. New metric → glossary row + owner + context. CONFLICT stays CONFLICT.
4. New PMS layout → fabricated fixture + parser test.
5. DCO: every commit must include
   `Signed-off-by: Name <email>` (use a public email).
6. `CODEOWNERS` covers glossary, millage, CoC policy, and occupancy denominator.
   Fill GitHub handles when the public org exists.

Run `pytest` with vendor API keys unset. Absence of a `claude` binary is allowed.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
umask 077
pytest
```

Run tests with vendor API keys unset; absence of any `claude`/SDK binary is
allowed — the core product is model-free and offline by default.

## Workflow

1. Issues first: non-trivial changes get an issue with the design sketch.
2. Small slices: strict TDD (failing test → minimal fix), like the codebase
   itself was built. See the tests/ directory for the house style.
3. Every PR must keep the full suite green. A red test is a bug report, not
   a to-do. Run under `umask 077` — the mode gates refuse group-writable
   test artifact paths by design.
4. Never weaken a gate to make a test pass. If a gate is wrong, change the
   gate in the same PR with its own red/green evidence.

## Release principles

- Honest refusals beat fabricated success (`MISSING_MILLAGE` is a feature).
- Synthetic examples never count as real vendor validation.
- Security/privacy gates (`tests/test_sanitize.py`,
   `tests/test_ingest_security_release.py`) are not editable except through a
   reviewed policy change with new red/green evidence.
