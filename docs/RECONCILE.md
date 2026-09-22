# Human reconciliation UX: `reconcile/1.0.0`

**Task 6.3.** Concise, trustworthy prompts for the fields a human must
reconcile on an underwriting run. A thin layer over the Task 6.1
[`workflow/1.0.0`](WORKFLOW.md) state machine: it reads the public record,
plans one question per unresolved blocked field, validates answers through
the existing millage gate, and records decisions through
`workflow.record_review`. It is never an engine, never a parser, never a
second authority: no financial math beyond delegation to the frozen millage
parser, no source-byte reads, no network, no threads, no engine calls. A
missing amount stays `null`, never zero, and no prompt, decision or stage is
financial certification.

## Batched questions

`plan_questions(run_dir)` returns one question per unresolved
`pending_human_action` blocker, in stable field order, each carrying the
field, the canonical prompt, the source evidence, the consequence of leaving
it blocked, the valid options and an explicit `defer` action. Missing source
documents (`t12`, `debt`) are batched with scalar values but are not
prompt-answerable: they need a pinned file, never a typed string. The
canonical millage question is retained verbatim unless owner policy changes
it (the example is formatting guidance only, never a default for a live
deal):

> What combined property-tax millage should be used? Enter mills per $1,000
> of assessed value (for example, `25.31`).

New tax regimes may require additional jurisdictional inputs and evidence;
they extend the plan, they never bypass it.

## Interactive review

```python
from plat_harness import reconcile

questions = reconcile.plan_questions(run_dir)
result = reconcile.review_interactive(run_dir, iter(['25.31']), {})
# result['decision']  -> reconcile-decision/1.0.0 document, or None on EOF/defer
# result['record']    -> public workflow record (recoverable draft when blocked)
```

`review_interactive` consumes scripted stdin line-per-question (a test may
also pass answers directly as a mapping). EOF or `defer` leaves a
recoverable draft: no journal event, no resolutions, and no hidden economic
default — the run stays at `review_required` and can be resumed later.
Malformed answers (`MISSING_MILLAGE`, `MISSING_HORIZON`) refuse the whole
review before any journal write, so a bad unit can never half-apply.

## Noninteractive review

```python
reconcile.review_noninteractive(run_dir, {
    'decision_id': 'dec_001', 'resolutions': {
        'millage': {'value': '25.31', 'evidence': 'host-reviewed'}}})
```

Host-reviewed decision files and interactive answers produce the same
content-bound decision schema `reconcile-decision/1.0.0`:
`decision_id`, `status` (`approved`), `reviewer`, `content_sha256`
(SHA-256 over the schema, run identity and resolutions), `resolutions`,
`certified: false` and `authorizes_execution: false`. `decision_id` defaults
to a content-derived id, so identical content yields the same id. A decision
whose resolutions disagree with an already-recorded value refuses
`CONFLICT_UNRESOLVED`: the field stays at its first recorded value. A
malformed or unsigned decision object refuses `INVALID_INPUT` with no journal
write.

## Trust boundaries

- **Prompts are not authority.** An answer typed at a prompt is an operator
  convenience, never an authenticated human approval; decisions record
  through `workflow.record_review` only. Anonymous local typing cannot
  self-authorize execution — a resolved review reaches `canonical_ready` at
  most, and execution authority stays with the separately reviewed execution
  envelope.
- **Local private evidence.** Question evidence cites pinned source
  identities and field roles only; source bytes, paths beyond the pinned
  host path, resident text and PII never appear in questions, decisions,
  records or errors. A local evidence viewer for precise page/cell context
  remains host-side tooling, authorized by the trusted host, not this module.
- **No hidden defaults.** Deferred or EOF'd fields stay `null`, never zero;
  `plan_questions` on an unblocked run returns `[]`.

## Synthetic tests

`tests/test_reconcile.py` — scripted stdin/EOF and host-authority fixtures,
29 tests; no real deal bytes, no models, no network, no engine import.