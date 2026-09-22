# Private immutable intake storage — Task 1.4

Candidate for parent and independent security review. Host-internal, Linux-only
local filesystem storage; no service, database, engine, model, network, new
approval registry or source-path API. Frozen ingestion contracts, migration,
reconciliation and host approval interfaces are unchanged.

## Trust boundary

`plat_harness.ingest.store.IntakeStore` is **not a model/tool request API**.
Only the embedding trusted host constructs it. Root, scope, envelope/context
pins and the original-byte loader must come from independently established host
intake, never from request JSON, document text or this store's own metadata.
The host must establish original-source role, lawful access and privacy-safe
identity tokens before normalization. Schema validation cannot establish those
facts or scrub arbitrary PII disguised as a host identifier.

Normalize with the existing parser and `migrate_v1` before calling the store.
Only exact native, strictly validated `ingest-observation/2.0.0` dictionaries are
accepted. V1, raw original bytes, resident payloads, arbitrary metadata and
caller-supplied overlays are refused before filesystem writes. Observations are
saved unchanged inside the recomputed overlay, including order, citations,
issues, unknowns, blocked state and completeness. Storage is not source truth.

Every reconciliation uses frozen `reconcile_request`, which dynamically loads
the existing `review_bridge.host_registry`. There is no registry/rank/approved
argument on any store operation. The host-only `source_loader` must construct a
fresh, exact `ByteSourceResolver` from **current** independently authorized
originals on every invocation. It must not return a cached verification receipt
or resolver subclass. The store calls it repeatedly, including during readback;
it cannot detect a trusted host deliberately supplying stale in-memory originals.
Current authority is checked at the publication and final-return checkpoints
below; these are not a globally atomic transaction with the host registry or
original-source storage.

No original-source bytes, raw identities, source-to-path maps or registry copies
are written. The original bytes remain in trusted intake memory/source storage.
There is intentionally no path-based source reader: no-follow source acquisition,
current source selection and source-file safety remain trusted intake duties.
Never pass a generated normalized CSV as an original to satisfy reconciliation.

Full signed decisions, **including approval reason and record locator**, are
retained privately. Returned overlays contain only frozen redacted decision
history and hash references, never those approval strings. No logging occurs.
Do not log host/request arguments, signed decisions, source-loader exceptions,
raw stored revision files or traceback locals. Redaction is not egress permission.

## Exact host API

```python
IntakeStore(
    *, root: str, subject_id, as_of,
    expected_envelope_sha256: str,
    expected_context_sha256: str,
    source_loader,                       # zero-argument trusted host callable
)
store.preview(run_id, observations, decisions, *, expected_prior) -> pin
store.create(run_id, observations, decisions) -> {"pin": pin, "overlay": overlay}
store.append(run_id, observations, decisions, *, expected) -> same
store.read(run_id, *, expected) -> same
```

All arguments are mandatory, except that `preview` explicitly accepts
`expected_prior=None` to describe a creation. Other operations cannot accept
request-granted paths, resolvers, registries, ranks, approval flags or proofs.
There is no `latest`, head-discovery, arbitrary-artifact read, overwrite, delete,
repair, historical-authority read or idempotent-create API.

- `root`: existing absolute host-owned private directory, exact canonical
  spelling; no `Path` objects, relative paths, empty components, trailing slash,
  `.` or `..`. No `resolve()` normalization, automatic mkdir of ancestors or
  chmod repair. The host provisions the root independently.
- `run_id`: exactly `run_[0-9a-f]{32}`, matched in full. IDs are host-minted opaque
  identities, not document names. Existing run IDs always refuse creation, even
  with identical content or an empty existing directory.
- `observations`: exact native validated v2 tree. Subject/date must exactly equal
  independent host scope; null is not a wildcard. SHA256 of canonical v2 bytes
  must equal the host envelope pin. Null-scoped intake can be stored but remains
  blocked by frozen source reconciliation.
- `decisions`: exact native list of full `ingest-mapping/1.0.0` decisions accepted
  by frozen reconciliation, with distinct decision IDs. No encoded subdocuments.
  Unlike pure reconciliation's replay seam, duplicate decision entries refuse.
- `source_loader`: host capability; source bytes, records, provenance and adapter
  must match the independent context pin. It is not serialized or loaded from
  requests or stored artifacts.
- `expected`: independently retained **current latest** pin. Required even after
  restart. Never construct it by reading `head.json` and assuming it is latest.
- `preview`: validates/reconciles using host-provided current originals and computes
  a prospective pin without store-filesystem IO. The trusted source loader may do
  its own secure intake IO. Preview does not read this store, test a prefix/CAS,
  reserve an ID, persist anything or prove a commit. A prospective append can
  still fail history/CAS validation.

All public operation failures are `StoreError`, with the single static code
`STORE_REFUSED_OR_UNCERTAIN` and message:

```text
Intake store refused or uncertain (STORE_REFUSED_OR_UNCERTAIN).
```

There is no input-bearing cause/context chain. A refusal does **not** prove that
nothing became visible. Host process termination is not converted to success.

### Example: trusted normalization, create, append and exact resume

The following is host integration code, not a request schema. Host configuration
and approval decisions are independently supplied; no approval is manufactured.
The tests exercise the same flow using actual parser/migration output on synthetic
original CSV bytes, both supported and blocked source dialects.

```python
import hashlib
from plat_harness.ingest.contracts import canonical_bytes
from plat_harness.ingest.source_resolver import ByteSourceResolver, digest
from plat_harness.ingest.store import IntakeStore

# observations is actual validated v2 output from the unchanged intake pipeline.
# All host_* values below come from independently authorized host state.
envelope_pin = hashlib.sha256(canonical_bytes(
    observations, subject_id=host_subject, as_of=host_date)).hexdigest()

def load_current_originals():
    # Trusted intake owns secure acquisition. Do not read request-selected paths.
    originals = host_current_original_bytes()
    return ByteSourceResolver(
        originals=originals, sources=host_source_records,
        expected_envelope_sha256=envelope_pin,
        subject_id=host_subject, as_of=host_date, adapter=host_adapter,
        intake_provenance=host_intake_provenance,
    )

context_pin = digest(load_current_originals().context())
store = IntakeStore(root=host_private_root,
    subject_id=host_subject, as_of=host_date,
    expected_envelope_sha256=envelope_pin,
    expected_context_sha256=context_pin, source_loader=load_current_originals)

# Example opaque ID; production host mints and tracks never-reused run identities.
run_id = 'run_' + '1' * 32
pending = store.preview(run_id, observations, [], expected_prior=None)
# Durably retain pending in the host's independent journal BEFORE the write.
created = store.create(run_id, observations, [])
assert created['pin'] == pending
# Durably advance the independently retained latest pin only with verified outcome.
latest = created['pin']

# Full chain, not just the new delta, from the EXISTING host human review process.
pending = store.preview(run_id, observations, host_full_signed_decision_chain,
                        expected_prior=latest)
# Retain both latest and pending independently before starting the CAS append.
updated = store.append(run_id, observations, host_full_signed_decision_chain,
                       expected=latest)
assert updated['pin'] == pending
latest = updated['pin']

# On restart, reload latest from independent trusted state, not local head metadata.
verified = store.read(run_id, expected=latest)
assert verified['pin'] == latest
```

If a write raises after visibility, exact `read(..., expected=pending)` can
establish the complete intended commit and durability. Never fall back silently
to an older pin because this read failed. Keep the pending outcome uncertain
until independently reviewed. A missing prerequisite, revocation, partial publish
or corruption can prevent resume; no automatic rollback/repair is provided.

## Exact artifact format

Each run is a private directory with bounded internally derived names:

```text
<host-private-root>/
  .lock                         # permanent zero-byte private flock inode
  run_<32 lowercase hex>/
    head.json                   # exact canonical latest pin, atomically replaced
    rev_000001.json             # immutable complete revision
    rev_000002.json             # subsequent immutable complete revision, if any
```

Artifact slots are internal, not request paths: `rev_` + exactly six decimal
digits + `.json`, corresponding only to integer revisions 1 through 64. Callers
address them through a validated run ID and exact pin, not an arbitrary filename.
Each revision is one self-contained JSON file, not several independently
committed observation/decision/overlay files.

Exact revision keys, all required with no extras:

```text
{
  contract_version: "ingest-store/1.0.0",
  run_id: <opaque run ID>,
  revision: <strict integer 1..64>,
  previous: null | <exact prior pin>,
  overlay: <entire recomputed ingest-resolution/1.0.0 object>,
  decisions: <full ordered signed decision list, including private approvals>
}
```

The first revision has `previous=null`; each successor binds the whole preceding
pin and advances by exactly one. `overlay.observations` is the unchanged complete
v2 envelope. Its `envelope_sha256`, `context_sha256`, decision hashes and approvals
are checked by fresh reconciliation, not accepted as saved verification receipts.

Exact pin keys, all required with no extras:

```text
{
  run_id: <opaque run ID>,
  revision: <strict integer 1..64>,
  sha256: <64 lowercase hex, SHA256 of entire exact revision file bytes>,
  envelope_sha256: <independently expected canonical v2 hash>,
  context_sha256: <independently expected resolver-context hash>,
  decision_head_sha256: null | <whole last signed-decision SHA256>
}
```

The context hash uses frozen resolver semantics: envelope, subject/date, adapter,
authorized source records and original-intake-provenance digest. Decision head
hash includes approval. Local head metadata is consistency state, **not approval
authority** and not an independently trusted latestness oracle.

All JSON uses the frozen compact, sorted-key, UTF-8 encoding, preserved array
order, `ensure_ascii=False`, no newline and no nonfinite values. This is not a JCS
claim. Reads require exact canonical bytes; duplicate keys, floats/exponents,
nonfinite constants, invalid types, extra keys, noncanonical encoding, oversized
or overdeep JSON refuse. Booleans cannot substitute for revision integers.

## History, authority and latestness

Append holds a root-wide exclusive `flock`, validates the independently expected
latest head, verifies all revision hashes and recomputes every historical overlay
against current host originals and registry membership. It then requires the new
full signed-decision list to be a **strict extension of the exact old list**.
Every preceding payload and approval byte represented in canonical JSON must
match; length must increase; IDs must remain unique. Shortened valid prefixes,
no-op replays, edited old decisions, reordered history and branches refuse.
Frozen predecessor/supersession rules still apply to the new decisions.

Read traverses the entire chain back to revision one, checking exact predecessor
pins, contiguous revision numbers, scope/context/envelope pins, complete signed
prefixes, canonical bytes and all saved overlays. Revoked decisions refuse even
when superseded. No historical artifact grants current approval. There is no
public historical-read operation; private files remain available to a separately
authorized audit process, explicitly as noncurrent records with no authority.

### Current-authorization checkpoints

Initial build/preview authorization is not permission to publish later. Each
checkpoint reconstructs a fresh exact source resolver through the trusted host
loader and invokes unchanged `reconcile_request`, which reloads the existing host
registry. The **complete proposed/latest signed chain**, including superseded
decisions, is checked, not merely the previous revision or historical empty prefix.
The fresh overlay must byte-match the saved/intended overlay, and the complete
canonical record must reproduce the exact saved/intended pin.

- **Create, before run publication:** after staged writes, fsync, staged readback
  and the filesystem/lock guard, revalidate the proposed complete record.
- **Append, before immutable revision publication:** after staged revision/head
  writes, fsync and filesystem/lock attachment guards, revalidate the proposed
  complete record. Validating only the prior chain cannot authorize new decisions.
- **Append, before logical head commit:** after immutable publication, intermediate
  directory fsyncs, expected-head recheck and attachment guards, revalidate the
  proposed complete record again. Failure here retains the permanent prepared
  revision and old head; it does not roll back or delete the orphan.
- **Read/create/append, before return:** retain the newest full signed record while
  verifying all history. After all store durability work, staging cleanup, and
  context-manager end checks/descriptor closure, recompute that newest record once
  more. Return only this last freshly computed redacted overlay and matching pin,
  never the overlay saved before checking older history. No store filesystem work
  follows this final authorization checkpoint.

The final checkpoint follows release of the store lock and does no further store
IO. The lock serializes store history/CAS/publication, **not** registry updates or
original-source changes. As with any completed operation, another append can occur
after the locked store snapshot; a returned pin is not a lease on latestness.
Authority can also change after any checkpoint. These checks do **not** provide
atomic global revocation isolation or promise zero artifacts for a late failure.
An atomic authority/publication boundary requires an external host coordination
contract covering registry/source updates and store operations. Trusted host
callbacks must not mutate store paths or operation inputs; the store cannot turn
same-UID/root code into an isolated principal. Retain independent latest/pending
pins and treat every post-publication refusal as uncertain, not as a safe rollback.

A newer named revision behind a stale local head also causes refusal, including
an orphan prepared revision left by interruption. The store never chooses a
successor automatically or accepts a shorter prefix just because its hashes and
old approvals are valid. Concurrent append contenders with the same prior pin
have at most one successful CAS; the loser cannot overwrite a revision/head.

**Full privileged filesystem rollback cannot be defeated by local hashes.** An
attacker able to restore the entire root to a formerly valid prefix can also
restore its local head. Only the independently retained newer latest/pending pin
(or stronger external trusted monotonic journal) distinguishes that rollback.
The host must preserve those pins across restart and track previously used run
identities outside the rollback domain. Losing the external journal is not
permission to rediscover authority from disk or call `create` again for an old
identity. Hashes provide integrity relative to trusted pins, not authenticity or
an external clock.

## Filesystem protocol and crash states

Every ancestor from `/` is opened through anchored dirfds with `O_DIRECTORY` and
`O_NOFOLLOW`. Each entry's inode/device is compared to its opened descriptor;
ownership, modes and path attachment are rechecked before publication and before
success. Ancestors must be owned by root or the effective host UID, without
any group/world-write or special mode bits. Root, run and staging directories
must be owned by the effective host UID with exact mode `0700`. Every file,
including lock and head, must be a host-owned regular file, mode `0600`, nlink one.

Files are opened with no-follow and nonblocking flags before fstat, so FIFOs do
not hang the reader. Symlinks (including ancestors, run, head, revision and lock),
hardlink aliases, wrong owners/modes, sockets, directories-as-files and devices
refuse. No chmod, chown or path repair is performed. The operating umask must
permit owner access; overly restrictive modes refuse rather than being repaired.

The persistent `.lock` inode is never truncated or replaced by the store. Creation
can initialize it with `O_EXCL`; existing-run reads/appends require it already to
exist. The descriptor held by `flock` is checked against its pathname after
acquiring and immediately before publication, never reopened to follow a changed
lock inode. All reads and writes serialize under this lock. This is a small
host-local store, not a high-concurrency database; locking has no built-in timeout.
This serialization covers store filesystem IO; the last authority-only check is
after lock release as described above.

Creation writes a new operation-owned `0700` staging directory, fsyncs both
`0600` files and the directory, verifies readback and current proposed authority
after the guard, then publishes the entire run
with Linux `renameat2(RENAME_NOREPLACE)`. There is no clobbering rename or hardlink
fallback. It fsyncs the root and verifies the published run again before success.

Append writes/fsyncs a complete staged revision and head, then revalidates current
authority for the proposed full chain. The immutable revision is published
no-clobber, and both affected directories are fsynced **before** the validated
current head is atomically replaced. Proposed authority is checked again after
those fsyncs and head/attachment checks. Head replacement is the logical
commit point: it references exactly one already-complete revision. Both source
staging and destination run directories are fsynced. Readback of the new current
head and every revision, cleanup/end barriers and the final complete-chain current
authority checkpoint precede success. Prior revisions are never overwritten or
removed.

| Interruption point | Result and recovery |
|---|---|
| Before publication | No new committed run/revision; private staging can remain after process death. An old committed head is not a partial artifact. |
| Revision published, old head retained | Prepared orphan is not accepted. Old and proposed reads both refuse. No automatic completion or deletion; host-owned recovery review required. |
| Head/run visible, final fsync/readback/authority checkpoint fails | Exception/uncertain, never success. Retained exact prospective pin permits resume read if the full commit is present and still authorized. |
| Head and complete chain verified/durable, final authority checkpoint passes | Return only the pin and last freshly recomputed redacted overlay; not a continuing authority/latestness lease. |

Read fsyncs every read revision/head file, the run directory and root before
success, so exact resume is a durability operation as well as validation.
Fsync failures refuse. It never treats an exception as evidence that the old
state is safely current. Normal exception cleanup removes only fixed known temp
files within this operation's new anchored staging directory, never glob-deletes
other attempts, permanent revisions or unrelated files. Abrupt termination can
leave private `.stage_<32hex>` directories; these are ignored, not published or
automatically scavenged. Host cleanup and capacity management are separate.

## Bounds and limits

- At most **64 immutable revisions per run**, with at most **256 distinct signed
  decisions** in each full chain. No no-op revisions; append must add a decision.
- Every JSON file and native encoded revision is at most **8 MiB**. At most
  512 MiB of permanent revision bytes per run, plus bounded head/staging overhead.
  This is not a global quota: the host must limit number of runs, disk usage and
  abandoned crash staging directories.
- Frozen tree limits remain: depth 16, 500,000 nodes including keys, 50,000 entries
  per collection, strings at most 128 code points, cumulative UTF-8 text 8 MiB.
  Added record/overlay structure can make the effective capacity lower than a
  standalone v2 payload. Refuse rather than truncate observations or review text.
- Frozen source resolver bounds still apply (1 MiB per original, 8 MiB aggregate,
  at most 32 source records and 10,000 CSV data rows). Source/dialect semantics
  remain Task 1.3's bounded seven-column CSV only; unsupported layouts stay
  `review_required`, not source accepted, engine eligible or certified.
- Reading recomputes the whole bounded history and can be expensive. Host worker
  time/memory/deadline limits remain necessary for untrusted documents and host
  callbacks. This is not a parser sandbox.

Trust assumes a functioning Linux local filesystem with reliable fsync,
renameat2 and flock semantics. No NFS/distributed filesystem or power-loss hardware
certification is claimed. Tests exercise real local filesystem operations plus
explicit negative fault injection, not actual disk-controller failure. Root or a
malicious same-UID process can bypass POSIX permissions, tamper with code/memory,
ignore locks or race private paths; the store detects tested drift but cannot
create an isolation boundary against that principal. Use a separately isolated
host account/service for mutually untrusted processes. Hashes are not signatures.

## Recorded TDD evidence

Evidence directories resolve beneath the parent campaign root. Every runner
invocation retains `command.json`, `pytest.log`, `results.xml`, `test_ids.json`
and `result.json` with actual controls and exits. All tests use fresh private
basetemps under the approved uplift root, offline CPU mode and disabled plugin
autoload. Positive filesystem tests use actual safe ancestor checks, not mocked
acceptance paths. Ownership mutation is explicitly negative fstat fault injection.

| Slice | Assertion RED | GREEN |
|---|---|---|
| Valid normalized create/read + authority | `t14_intake_red.uaevzram`: 12 failed (module seam assertions) | `t14_intake_green.5uemo6f3`: 12 passed |
| Append/prefix/CAS/history | `t14_history_red.gk6dv7c3`: 11 failed, 12 passed (API seam assertions) | `t14_history_green.bv0a2nbc`: 23 passed |
| Filesystem and stale local head | `t14_filesystem_red.zx2iz_u1`: 2 failed, 67 passed | `t14_filesystem_green.ks5gw08c`: 69 passed |
| Strict head types, cleanup, durable resume | `t14_crash_red.d2lht2__`: 4 failed, 106 passed | `t14_crash_green.juawjlzn`: 110 passed |
| Lock continuity/publication | `t14_lock_red.2h20q4qc`: 3 failed, 123 passed | `t14_lock_green.q440q14p`: 126 passed |
| Added boundary/canary coverage (no behavior change) | Existing safety behavior retained | `t14_boundary_green.dpvfzlf1`: 136 passed |

RED invocations exited 1 with assertion failures, not collection errors. GREEN
invocations exited 0 with no failures/errors/skips. Synthetic coverage includes
actual parser -> migration -> store -> source-backed reconciliation -> append ->
read, both supported and blocked; private signed approval retention; current and
superseded revocation; complete external-pin rollback; hostile JSON/native trees;
PII canaries; real symlink/hardlink/FIFO/socket cases; concurrent conflicting CAS;
inode replacement; every fsync ordinal exercised by create/append/read; staging,
publication and post-visibility read failures. Independent review and parent
acceptance remain required. No real deal sources, engines, models, installations,
network, staging of Git files or commits were performed for this task.

### Historical scoped regression and preservation (pre-fix, not acceptance)

`t14_final_full.pilslhat` completed **1,305 passed, zero failures/errors/skips,
exit 0** using the parent runner **before** adversarial review reproduced the two
authorization-timing defects. This historical green is not acceptance evidence
for the corrected implementation. Programmatic test-ID comparison with
`parent_t13_fix_full._72p9923` retains all **1,167** prior scoped IDs and adds
**138** store cases. This is the declared isolated CPU/offline suite, not excluded
engine/GPU/live-model tiers or real-source/vendor acceptance. Two final coverage
variants explicitly exercise a resolver subclass and empty-directory no-clobber;
no production behavior changed after the recorded lock GREEN.

All **107** files in `t14_before.f7s5y5qx/frozen.json` remain byte-identical.
Additionally, **15** protected parser/authority/native files present in the
read-only original repository match those frozen hashes. Only the three Task1.4
files are new in the isolated workspace. Git index remains untouched and HEAD
remains `12c0e24f304e431a018fe7399e1e2898f54ff2c7`.

Independent post-test readback of the positive blocked fixture's exact revision
bytes matches its stored head pin, SHA256:
`b61b8914a2f5c6985061006889ffeae2fd2bcf84ef4051f9bdc6e2c8eb0b4490`.
The test suite separately asserts the same byte-hash relationship on creation.

Verification tooling note: an initial broad post-test artifact scan timed out
because the adversarial fixtures deliberately include a FIFO. It did not affect
the store or passing tests. The corrected verification selects the positive
fixture with lstat (excluding pytest's `current` symlink), opens only exact named
files with no-follow/nonblocking flags, checks regular-file/nlink/size constraints,
then compares exact byte hashes. No hostile fixture was repaired or removed.

### Authorization-timing correction: permanent RED/GREEN and re-review

The independent 15-case audit was rerun unchanged before correction:
`t14_fix_original_red.jpb3uvin` reproduced **5 assertion failures, 10 passes**.
Permanent synthetic regressions were added to `tests/test_ingest_store.py` before
each corresponding production fix; existing tests were not weakened or rewritten.

| Correction | Permanent assertion RED | GREEN |
|---|---|---|
| Latest full-chain authorization after history, durability and end barriers | `t14_fix_final_red.7wr0s7th`: 17 failed, 6 passed, zero errors/skips | `t14_fix_final_green.nae0211v`: 161 store tests passed |
| Proposed full-chain authorization before publication and logical commit | `t14_fix_publish_red.vbiukh0q`: 21 failed, 6 passed, zero errors/skips | `t14_fix_publish_green.fogxvc2p`: 188 store tests passed |

The added cases exercise deterministic current membership revocation; actual
on-disk hash drift through the **unchanged production registry loader** with its
configured pin left unchanged; and current original-byte loader drift. Injections
follow real staged head writes, immutable publication, intermediate fsync/head
checks, final durability/attachment barriers and staging cleanup fsync. Positive
flows use the same real filesystem checks. No permissive ancestry or file-safety
validator replacement is used. The tests assert exact old-head/prepared-orphan
states, no permanent deletion, pin/hash preservation, and exact pending-pin resume
after restoring current authority when a complete logical commit exists.

Post-fix verification, all with zero failures/errors/skips and exit 0:

- `t14_fix_original_green.ojcs6srf`: **15 passed**, unchanged independent audit.
- `t14_fix_host_green.j2wtsgve`: **1 passed**, parent's actual host-registry
  create/append/restart/revoke integration.
- `t14_fix_full_green.gmxbkrl4`: **1,355 passed**, complete scoped offline CPU
  suite (including 188 store cases). This remains synthetic regression evidence,
  not real-source/vendor acceptance, external atomic-revocation certification,
  power-loss certification, engine/GPU/live-model testing or parent acceptance.

Private snapshots, hashes, diffs, commands, test-ID comparisons and frozen-file
preservation evidence are under campaign directory
`t14_authorization_fix.s1bwj6qh`. Only `store.py`, `test_ingest_store.py` and this
document are candidates for modification. No staging or commit; return to parent
and independent re-review is still required.
