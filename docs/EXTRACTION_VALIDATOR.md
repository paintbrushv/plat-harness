# Extraction Validator (Task 5.4)

`plat_harness.ingest.extraction_validator.validate_extraction(claims, source, *, egress_approved=False, provider=None) -> dict`

Deterministic re-anchoring of model-claimed extraction facts to cited
packet content. Model output is **untrusted extraction claims, never
financial results**: every claim must resolve to the exact original
source bytes and locator it cites, or it is recorded as unverified.

---

## Contract

`claims` is a list of model-claimed facts; `source` is the egress-shaped
packet (`ingest-extraction-packet/1.0` shape: `version`, `source_sha256`,
`total_pages`, `metadata`, `sections`, `ocr_required_pages`, `issues`).
Validation refuses (typed `ExtractionValidationError`, never chained, never
echoing input) when the packet is malformed, a claim is malformed, egress is
not approved, or claims exceed `MAX_CLAIMS` (1000).

**Claim keys** — required: `kind`, `label`, `value`, `section_code`,
`citation`; optional: `unit`, `period`, `confidence`, `tool_action`,
`approval`, `narrative`. Only `fact` is a supported `kind` (everything
else — assumptions, derived metrics, approvals — is
`UNSUPPORTED_CLAIM_KIND`). `narrative` is accepted in the shape and then
**dropped whole**: prose can never introduce a new amount into results.
Citations use the shared grid schema (`source_sha256`, `sheet`, `row`,
`row_end`, `column`).

## Deterministic checks (per claim, in order)

1. **Injection/authority** — a claim carrying `tool_action` or `approval`
   is `unverified` with `PROMPT_INJECTED_TOOL_ACTION`. Model output can
   never carry tool or approval authority; confidence cannot bypass this.
2. **Kind** — unsupported kinds are `unverified` (`UNSUPPORTED_CLAIM_KIND`).
3. **Source identity** — a citation whose `source_sha256` differs from the
   packet's is `CITATION_NOT_IN_SOURCE` (conflicting source versions can
   never pass).
4. **Locator** — the citation must match a line or row citation inside the
   claimed section; otherwise `CITATION_NOT_IN_SOURCE` (invented
   page/cell).
5. **Quarantine** — a cited line containing prompt-injection strings is
   `PROMPT_INJECTION_SUSPECT_CITATION`; its text never anchors a verified
   fact and never travels onward in results.
6. **Value** — the claimed value must appear **verbatim** in the cited
   content (`VALUE_NOT_IN_CITED_CONTENT` otherwise). Derived/transformed
   figures (e.g. `1,500,000` where the source shows `1,000,000`) fail here.
7. **Label / period / unit** — a label absent from the cited content is a
   review flag (`LABEL_NOT_IN_CITED_CONTENT`); `period`/`unit` tokens not
   present in the cited content are review flags
   (`PERIOD_MISMATCH_REVIEW` / `UNIT_MISMATCH_REVIEW`). Review flags never
   fail a value check, and value failures outrank review flags.

## Result shape

Root keys (exact set): `version` (`ingest-extraction-validator/1.0.0`),
`source_sha256`, `status`, `facts`, `issues`, `provider`.

- `status`: `verified` (no issues) / `needs_review` (only review flags) /
  `invalid` (any unverified fact).
- `facts`: one entry per claim, keys exactly `label`, `value`, `citation`,
  `kind`, `status` (`verified`/`unverified`/`review`). Values are the exact
  claimed strings — the validator performs **zero numeric parsing or
  arithmetic**; ambiguous figures (`1.234`) survive only verbatim.
- `issues`: `{"code", "citation"}` entries from `ISSUE_CODES`.
- `provider`: optional measurement metadata (`provider_id`, `model_id`),
  recorded as an isolated deep copy; it can never alter facts, statuses,
  citations or the source digest. Absent measurements stay absent — no
  accuracy/cost/latency fields are ever fabricated.

## Provider independence

Provider/model labels are transport metadata only. Together with the
canonical intake translation (`docs/INGEST_CANONICAL_INTAKE.md`) and the
provider bridge (`docs/PROVIDER_BRIDGE.md`), this guarantees: identical
validated claims produce byte-identical canonical JSON and SHA256
regardless of provider label; a local provider failure never falls back to
a cloud provider; and attribution (`broker_claim`) is a source property no
provider label can relabel.

## Test targets

- `tests/test_extraction_validator.py` — synthetic packets/claims only;
  canaries must stay absent from results, issues and exceptions.
- `tests/test_provider_independence.py` — provider-label invariance
  across the bridge, validator and canonical intake; no live providers,
  no credentials, no network.

No model metrics are reported unless a model actually ran;
`tests/test_provider_independence.py` labels its synthetic measurement
explicitly.
