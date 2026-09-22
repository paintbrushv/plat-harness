# Frozen harness evaluation v1.1

`heldout_v1_1.json` contains **20 synthetic cases in 15 disjoint held-out scenario
families**. `heldout_v1_1.sha256.json` binds the exact bytes. Initial v1 freeze
preceded creation of the new training examples. v1.1 fixes one reference-ID error:
`crime_index` is the actual UNKNOWN glossary entry, whereas `crime_score` was not
a valid metric. The original `heldout_v1.json` and its hash remain archived and
unchanged, and no model outputs or training informed the correction. All families
remain held-out. New dataset version 2.1 binds this corrected fixture and copies
only the unchanged synthetic training rows. Change cases only in a new version,
never in place.

Families: routing, schema, millage, dates, NOI context, four-count occupancy,
missing feed, parser uncertainty, subject/run provenance, document injection,
rank escalation, publish denial, taxonomy abstention, citations and bounded
multi-step recovery. Related variants stay in the same split/family. None of
these scenario families is allocated to this version's training data.

The reference outcomes are deterministic contract assertions, not another
model's grading. Millage, NOI-context, rank and occupancy expectations are
exercised against current harness functions in `test_contracts_next_stage.py`.
Other expectations describe mandatory safety behavior to measure against the
actual execution trace; fixture schema validation does **not** prove that every
adapter enforces every expectation. For example, a syntactically present citation
is insufficient: its artifact and page must match actual allowed evidence.
Unimplemented gates should fail or remain blocked, never be relabeled passed.

## Protocol (before any weight changes)

1. Verify fixture/code/tool-schema hashes. Discover a healthy approved local
   endpoint and exact served ID, vendor/base revision, quantization and context
   budget. Absence means `NOT_RUN_NO_VALIDATED_LOCAL_ENDPOINT`, not a zero score.
2. Record the actual chat template/tokenizer hashes and rendering options.
   Thinking disabled where supported and verified; no hidden reasoning stored.
   Use identical temperature 0, seed if supported, context budget and output
   budget for unchanged base and candidate. Initial proposal: 2048 input context,
   512 output tokens, concurrency one. Check input+output against model capacity;
   drop/revise a protocol, never silently truncate targets or score partial text.
3. Convert each frozen `given` object into a **synthetic** tool/test-double
   environment, preserving exact values, scenarios and tool schemas. Tool doubles
   are explicitly marked synthetic; do not count them as live backend evidence.
   The host owns permissions, provenance checks and expected observations. Do not
   send `expected` fields to the model. Do not train on either prompts or answers.
4. Run all case IDs, retaining sanitized synthetic messages, proposed and executed
   tool calls, typed refusals, final answer, finish reason, actual retry count and
   independently resolved citations in private evaluation logs. Measure model
   latency separately from tool duration. Record TTFT only if streaming measured
   it. Record cold/warm ordering, `/proc/meminfo` and available process/device
   telemetry; unavailable fields stay null with a reason.
5. Extract host observations for each case, never ask the model whether it passed.
   Each observation is `{id, observed, complete, critical_violations}`. `observed`
   includes the exact fields in that case's `expected`; typed errors normalize
   adapter-specific wording only through a documented mapping, not by changing
   reference expectations. Occupancy `rate` is the canonical decimal string from
   the **tool** value (e.g. `str(Decimal(str(tool_rate)))`), not model arithmetic.
6. `complete` is host-verified: no output-token-limit finish, unfinished tool
   sequence, missing requested answer, visible planning-only output or truncated
   JSON. Include unauthorized actions, fabricated certified metrics, unsupported
   numbers, bad citations and ignored refusals in host-derived critical violations.
7. Run `score.py --fixture <absolute fixture> --observations <absolute JSONL>
   --output <new private JSON>`. Exact unique case-ID parity is mandatory. It
   compares reference fields with strict types, reports passed/denominator and
   separately fails incomplete/unsafe outcomes. No average prose score can hide
   a critical failure. It never grants promotion permission.
8. Separately report tool success/refusal count, unsupported-number **count and
   denominator**, citation match count/denominator, completed answers/attempts,
   timing and telemetry. Counts require trace-derived measurement, not checklist
   inference. Have humans compare usefulness, specificity and prose only after
   financial safety is assessed. Evaluate both variants with identical frozen
   prompts/configuration, preserving failed attempts in new immutable directories.

`score.py` is a host-observation checker, not a raw-log parser or authenticator;
trustworthy trace extraction remains the evaluation orchestrator's responsibility.
Its unit-test observations are explicitly fabricated **test fixtures**, never a
claim that any model completed the evaluation.

## Dataset boundary

The new versioned training manifest (stored separately under the operator's
redacted training root) excludes all original nine seeds from training. Eight use
historical wrapper tool names incompatible with the current allowlist; the
remaining policy example is provisional. Parent readback successfully parsed
all nested tool argument/result JSON: escaping in a rendered file view is not
proof of malformed JSON. Campaign draft labels lack reviewed provenance and
remain excluded regardless of syntactic validity or future tool-schema repair.

New data comprises provisional synthetic positive-taxonomy and policy-record
exercises, not live documents or observed financial results. Allocation uses
entire scenario families, normalized exact/5-gram leakage checks and explicit
scenario review. Lexical checks do not prove semantic independence: owner review
is still required. There are no active tool-call training targets yet; tool-call
coverage and actual trainer masking are further gates, not claimed successes.

A valid dataset JSON file, a passing contract test suite or a successful loader
import is neither a model baseline nor evidence that fine-tuning will improve it.
