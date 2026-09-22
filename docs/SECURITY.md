# Security and privacy release gates (Task 7.2)

This document states what the plat-harness ingest and provider surfaces
actually enforce against hostile inputs, the gates that are explicitly
blocked **resolved** during the same release cycle (parent-extended the archive gate; tripwires converted to positive tests), and the residual risk that remains
even when every test passes. It is release evidence, not an advertisement:
passing tests used tiny synthetic inputs, and that proves fail-closed
behavior on those inputs — **not** real-world hostile-file safety.

Enforcement is verified by `tests/test_ingest_security_release.py`
(the consolidated release-gate suite). Every enforced gate named below was
mutation-tested in a scratch copy of the harness: removing the gate makes at
least one test in the suite fail. Mutation run evidence lives under the
private campaign root (`t72mut_*` run directories).

## Enforced gates (verified fail-closed)

### ZIP/XML expansion and archive hygiene (XLSX adapters)
- **Byte budget** — inputs above 32 MiB (8 MiB for the flat PMS CSV/XLSX
  stream) refuse with `INPUT_LIMIT_EXCEEDED` before parsing.
- **Declared-size preflight** — the sum of central-directory-declared
  uncompressed member sizes is checked *before any member is decompressed*;
  a 64 MiB "zip bomb" member refuses without being read.
- **Entry-count budget** — archives with more than 1024 members refuse.
- **Declared-size lies** — central directories that declare 1-byte members
  cannot smuggle past the preflight; the parse still refuses typed.
- **Encryption** — a set encryption flag on any member refuses with
  `MALFORMED_INPUT`/`UNSUPPORTED_XLSX_FORMAT` at zip preflight, *before the
  spreadsheet library is ever invoked* (verified by an instrumented
  `load_workbook` that must not be called).
- **Macros and external links** — `xl/vbaProject*` and
  `xl/externalLinks/*` members refuse with `MALFORMED_INPUT` in every XLSX
  adapter.
- **Formulas** — formula cells are inert text (`data_only=False` reads the
  literal; nothing is ever recalculated) and formula literals never appear in
  results.

### Sparse worksheets / XML coordinate abuse
- Worksheet bounds are preflighted by streaming the sheet XML **before any
  row iterator allocates**: duplicate rows, out-of-order or duplicated
  coordinates, coordinates past the row/column/cell budgets, and padded-cell
  budgets all refuse before `iter_rows` starts (verified with a forbidden
  iterator that must never be called).
- A lying `<dimension ref="A1:A1">` cannot smuggle rows: the budget keys on
  actual cell coordinates, not the declared dimension.

### BIFF (XLS) resource exhaustion
- The detailed and compact OneSite adapters enforce row/column/cell budgets
  on the parsed sheet, byte budgets on the input, and OLE-magic type checks.
  Truncated books, corrupt OLE containers and oversized inputs all refuse
  with typed, unchained errors.
- **xlrd diagnostics are discarded** — xlrd writes input-bearing warnings
  through its logfile handle even at verbosity 0; the adapters route that
  handle to a discard sink. A half-truncated book produces xlrd "file is
  truncated / OLE2 MSAT is corrupt" diagnostics, and none of it may reach
  stdout/stderr (asserted; mutation-tested).

### Pathological PDFs
- Structural garbage, header-only, and empty inputs refuse typed
  (`MALFORMED_INPUT`/`EMPTY_INPUT`); an `/Encrypt` trailer dictionary
  refuses rather than prompting for a password. Frozen ceilings: ≤500 pages,
  ≤50,000 lines, ≤32 MiB.

### PII, secrets, and injection hygiene
- Tenant identity is redacted to an explicit `[REDACTED]` marker (verified
  by asserting the marker, not merely canary absence — a gate that silently
  dropped the fields would otherwise pass the weaker test).
- Parser refusals never echo canary-bearing cell text or document metadata
  through results, logs, warnings, exception messages or chained tracebacks.
- The ingest package is **structurally offline**: no ingest module imports
  `socket`, `requests`, `urllib.request` or `http.client` (asserted by
  source scan; the only egress surface is the provider bridge).
- Provider bridge telemetry carries bounded metadata only (provider id,
  model id, path, byte counts, payload SHA-256, status): raw prompt bytes
  and credentials never enter bridge events, and credentials travel only on
  the wire (asserted with a synthetic credential and recording transport).
  SSRF is bounded by origin pinning: cloud providers must use their exact
  official origins; local providers must be a literal `http` loopback IP.
- Prompt-injection strings in source text are excluded from egress and
  quarantined `local_only` with `PROMPT_INJECTION_SUSPECT` (re-asserted at
  the release-gate level; the extraction packet remains the enforcing seam).

### Corrupted manifests, source replay, approval revocation
- A bit-flipped acceptance manifest refuses before any source I/O.
- Source bytes changed after intake (replay) cannot satisfy the original
  digest: the resolver refuses construction before any claim verification.
- An approval absent from a reloaded host registry refuses reconciliation
  (registry is reloaded on every invocation; revocation blocks).
- A bit-flipped store revision refuses readback instead of silently healing.
- A symlinked run directory refuses: `O_DIRECTORY` opens plus
  inode-attachment and mode checks (0700/0600, owner-only, no setuid/sticky
  ancestors) make the store's symlink defense layered.

## BLOCKED GATE — embedded active content (fail-open, known and recorded)

**RESOLVED within this release cycle.** The XLSX archive-membership gate
originally covered `vbaProject` and `externalLinks/` only, and members under

- `xl/activeX/` (ActiveX controls),
- `xl/ctrlProps/` (form control properties),
- `xl/embeddings/` (embedded OLE objects, e.g. a nested workbook)

were accepted by all three XLSX adapters (`pms_normalizer`,
`lease_charge_xlsx`, `ledger_xlsx`) — a latent-content fail-open found by the
7.2 release gate. The parent extended the member-name gate in all three
adapters to refuse `activex/`, `ctrlprops/` and `embeddings/` members (typed
`MALFORMED_INPUT` before any library parse), and the former xfail tripwires
in `tests/test_ingest_security_release.py`
(`test_embedded_active_content_gap_is_a_recorded_blocked_gate`) are now
positive assertions: each adapter's own valid synthetic workbook plus one
hostile member must refuse. The gate cannot be silently narrowed again
without the suite going red.

## Worker sandbox boundary

The acceptance runner parses untrusted sources in a forked worker with
`RLIMIT_CPU` / `RLIMIT_AS` / `RLIMIT_FSIZE` / `RLIMIT_CORE` caps, a wall
deadline, a stripped environment (no vendor keys, offline model env, no
inherited Python path), a `/dev/null` stdio detach, and an embedding check
that refuses to run inside an already-exceeded host ceiling. Worker output
is size-capped and the supervisor SIGKILLs deadline overruns. The plain
adapters (library parse in-process) do **not** carry these process limits;
that gap is the residual risk below.

## Residual risk (documented, not advertised away)

1. **Library-level attack surface remains.** openpyxl, xlrd and
   pdfplumber/pdfminer are large parsers; the harness bounds their *inputs*
   (sizes, counts, coordinates, declared totals) but cannot bound every
   internal allocation path. Hostile-file safety beyond the tested classes
   is **not claimed**. The worker rlimits above are the mitigation for the
   acceptance path only.
2. **Embedded active content** is closed at the archive-membership gate
   (above); the residual surface is member names not yet enumerated — the
   gate is a deny-list, and novel container paths require a release-gate
   extension, not an emergency parse.
3. **PDF structural attacks** beyond the tested classes (crafted font
   programs, nested object graphs) are bounded only by pdfplumber's own
   behavior plus the page/line/byte ceilings; no in-process memory cap
   applies outside the acceptance worker.
4. **BIFF (XLS)** is parsed by xlrd in-process with input bounds only;
   the acceptance-worker rlimits do not apply to direct adapter calls.
5. **Telemetry**: native-runtime telemetry (`telemetry.jsonl` under private
   run roots) records phase metrics only; provider bridge events never carry
   source documents or secrets (asserted). Secrets and source documents must
   never be added to telemetry or provider audit logs — this is an
   invariant for all future slices, not just this one.
6. **The sanitize gate** (`tests/test_sanitize.py`) is unchanged and remains
   the authority on public-tree private-literal hygiene. Publishing also
   requires inspecting generated package archives and reachable history
   (including training-data commits) — a separate release-curation decision
   that this document does not grant.

## Dependency vulnerability review

The dependency set is deliberately tiny (core: `pyyaml`; ingest extras:
`openpyxl>=3.1,<4`, `xlrd>=2.0.2,<3`; pdf extra: `pdfplumber>=0.11,<1`,
`reportlab>=4,<6` — see `docs/INSTALL.md`). Known constraints:

- `xlrd 2.x` deliberately dropped XLSX support, shrinking its attack surface
  to BIFF only; the adapters require that line.
- No known published CVE fixes are pending against these pins at the time of
  this slice; the pins are upper-bounded so a future audit can tighten them
  with a single-version review.
- A formal `pip-audit`-style scan is a release-time activity (Task 7.4
  release gate) and is **not** claimed as performed here.

## What a green suite does and does not mean

`test_ingest_security_release.py` green means: the named gates above are
present and mutation-killed, on this platform (Linux aarch64), with these
library versions, against synthetic hostile inputs. It does not mean the
software is safe against all hostile files, and this document must not be
cited as such a claim. Publishing, curation of reachable history, and the
blocked-gate fix remain open parent-owned decisions.