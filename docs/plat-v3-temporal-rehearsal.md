# Local temporal assertion rehearsal

This is a bounded synthetic implementation of the first PLAT V3 temporal
workflow. It records accepted assertions in a local SQLite stream and freezes
an issued original thesis separately from later operations NOI. It is useful
for testing semantics; it is **not** a source-of-record migration, live host,
outbox, approval service, or release-ready event platform.

## Contract and authority

`plat_harness.temporal.TemporalLedger` uses `plat.temporal.assertion/1`.
The host supplies a private SQLite location, current authenticated
`authorize(actor_id, workspace_id, aggregate_id, capability)` callback, and trusted UTC
clock. A model, source document, or historical event cannot grant itself
access. The default clock is UTC now; tests inject a deterministic clock.
Effective intervals use date-only, half-open `[valid_from, valid_to)` values.
Knowledge time is an offset-aware instant normalized to UTC when the local
transaction commits. Source identity includes workspace, aggregate, source
system, record, and revision; equal monetary amounts remain separate records.
Append requires the expected stream version. A duplicate exact source
revision returns its original event, while changed content and stale writers
fail. A correction must name the prior event in the same workspace, stream,
and key; absent that link, competing sources fail historical reads rather
than silently making the latest arrival truth.

`as_known` evaluates effective and knowledge cutoffs; `as_issued` returns an
immutable report with a stored content hash, after a fresh current access
check scoped to the report's deal. `issue_original_thesis` calls the existing yield owner once and stores
the resulting figures. `thesis_to_actual` reads that frozen result and adds
the later operations NOI without recalculating its historical yield.

The executable rehearsal is `python -m pytest tests/test_temporal_rehearsal.py`.
Its eleven synthetic tests cover the late August $15,000 expense; a future
lease; a linked backdated unit correction; stale stream writers and exact
reimport; a former user's revoked access to historical facts and reports;
an issued thesis beside a later actual; unsupported event-version refusal;
scoped source identity and equal-amount transactions; date-only and UTC
offset handling; durable reopen and unsupported store version refusal;
issued-report hash integrity; and competing writers against one stream
version. These are component-level observations. The product's full
eleven-case mandate remains blocked until the missing boundaries below are
implemented and exercised together.

## Release blockers after this slice

- This API has no source evidence/acceptance state, occurrence time, source
  timestamps, source coverage/watermarks, authenticated actor type, classification,
  scenario/definition version, or property-local calendar policy. The host
  must not treat this small assertion envelope as the final mandate event
  contract.
- It is not connected to ingestion, approvals, connector reads, or current
  grant storage. The synthetic callback shows where a current authorization
  decision belongs; it does not prove a real host's tenant policy or approval
  binding. Historical reports may contain sensitive content; no retention or
  redaction service is attached.
- There is no verified checkpoint/rebuild, transactional outbox, pending-work
  inventory, crash/uncertain-external-outcome reconciliation, schema upgrade,
  backup/restore redaction overlay, or atomic replacement projection.
- No stable legacy identity map, opening-state history boundary, bounded
  restartable conversion, quarantine, row-level old/new parity, shadow
  catch-up, or one-writer cutover rehearsal exists. No private or live data
  was read by this test suite.
- The local SQLite file security and backup policy belong to the embedding
  host. This module does not establish a multiuser server boundary.

The next package should connect one synthetic ingest revision and the existing
read connector to this lane, add a versioned source/approval envelope, and
implement a restartable opening-state migration with scoped reconciliation
before any release-candidate claim.
