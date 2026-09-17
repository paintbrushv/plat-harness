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
