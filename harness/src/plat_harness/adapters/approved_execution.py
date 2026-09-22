"""Separately versioned reviewed-draft execution envelope (Task 3.4).

This envelope reuses the existing authority primitives — ``contracts._approval``
binding and the host-owned contracts registry via
``review_bridge.host_registry`` — and never creates a second approval
database or an easier ``approved: true`` path. It grants nothing new: an
executed run is a *reviewed draft* carrying an explicit economic-reconciliation
blocker; live-deal certification stays unimplemented and requires human
authorization that this module neither accepts nor fabricates.

Fail-closed gates, in order (all before any engine call or artifact write):
exact envelope shape/version, exact identifier formats, explicit synthetic
scope (envelope flag AND canonical subject prefix AND purpose allowlist AND
canonical/envelope identity binding), no overrides, current code pins, exact
canonical-byte pin, exact classification/policy evidence pins, an empty
private run directory, supported source locators only, **then** a freshly
loaded host registry and the frozen approval/policy/classification
contracts. The registry is reloaded from host configuration on every
invocation, including readback; revocation blocks both execution and
readback.

The economic engine is never imported or executed here. The pinned existing
engine path is ``engine.engine.run_underwriting``
(``ENGINE_PATH``); production hosts bind that exact callable, synthetic
tests bind a stub representing it, and the engine wait is bounded
(``engine_timeout_s``); no unbounded waits exist in this module. All
economics are produced by the bound callable; this module performs zero
financial arithmetic and never alters an engine value. The existing
reconciliation entry point is pinned as ``RECON_PATH`` but is NOT invoked by
this envelope: without a reviewed economic reconciliation the run stays a
draft with blocker ``RECON_ECONOMICS_REQUIRED`` and can never certify.

Refusals are typed (``HarnessError``), static and sanitized: input values,
engine internals and chained exceptions never surface.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import select
import signal
import tempfile
import time
from pathlib import Path

from plat_harness import contracts
from plat_harness.adapters import review_bridge as rb
from plat_harness.adapters import slice_b as b
from plat_harness.errors import HarnessError

ENVELOPE_VERSION = 'approved-execution/1.0.0'
ENGINE_MODULE = 'engine.engine'
ENGINE_FUNCTION = 'run_underwriting'
RECON_MODULE = 'runs.build_underwriting_reconciliation'
RECON_FUNCTION = 'main'
ENGINE_PATH = ENGINE_MODULE + '.' + ENGINE_FUNCTION
RECON_PATH = RECON_MODULE + '.' + RECON_FUNCTION
# The only source artifacts this envelope can resolve evidence against:
# exact canonical input bytes held in memory, never filesystem IO.
SOURCE_ARTIFACTS = ('inputs.json',)
EVIDENCE_NAMES = ('classification', 'policy')
STATUS = 'draft_pending_review'
BLOCKER = 'RECON_ECONOMICS_REQUIRED'
RECON_STATUS = 'NOT_REVIEWED'
SYNTHETIC_SUBJECT_PREFIX = 'synthetic_'
SYNTHETIC_PURPOSES = frozenset({'synthetic_slice_b', 'synthetic_approved_execution'})
MILLAGE_BINDING = 'millage_rate_mills'
DEFAULT_ENGINE_TIMEOUT_S = 60.0
MAX_ENGINE_TIMEOUT_S = 180.0

# Closed, static refusal code set for this envelope.
ERROR_CODES = frozenset({
    'APPROVAL_MISMATCH', 'APPROVAL_REQUIRED', 'CLASSIFICATION_REVIEW_REQUIRED',
    'EVIDENCE_INCOMPLETE', 'EVIDENCE_LOCATOR_UNSUPPORTED', 'ENGINE_TIMEOUT',
    'HASH_MISMATCH', 'INVALID_CONTRACT', 'INVALID_ID', 'INVALID_INPUT',
    'LIVE_RUN_NOT_AUTHORIZED', 'NOT_FOUND', 'NOT_IMPLEMENTED', 'POLICY_CONFLICT',
    'POLICY_VERSION_MISMATCH', 'RECON_ECONOMICS_REQUIRED', 'REVIEW_REQUIRED',
    'RUN_CONFLICT', 'STALE', 'SUBJECT_MISMATCH', 'UNCERTIFIED_CLASSIFICATION',
    'UNCERTIFIED_METRIC', 'UNSAFE_PATH'})

ENVELOPE_KEYS = frozenset({
    'contract_version', 'subject_id', 'run_id', 'input_sha256', 'policy_version',
    'synthetic', 'intake_validated', 'analysis_window', 'policy_sha256',
    'evidence_sha256', 'code_sha256', 'overrides', 'approval'})

# Exact manifest surface; the only documented run-metadata differences
# between two executions of identical canonical bytes are inside executed_at.
RUN_METADATA = tuple(sorted({
    'subject_id', 'run_id', 'input_sha256', 'policy_version',
    'envelope_version', 'approval_id', 'synthetic', 'status', 'certified',
    'certification_eligible', 'blocker', 'recon_status', 'engine_path',
    'recon_path', 'executed_at', 'analysis_window', 'policy_sha256',
    'evidence_sha256', 'code_sha256', 'economics', 'economics_sha256',
    'envelope_sha256', 'run_metadata_keys'}))

ARTIFACT_NAMES = ('classification.json', 'economics.json', 'envelope.json',
                  'inputs.json', 'manifest.json', 'policy.json')


def code_pins() -> dict:
    """Current harness authority-code pins. Never touches the engine repo."""
    files = (
        ('approved_execution_sha256', Path(__file__)),
        ('contracts_sha256', Path(contracts.__file__)),
        ('review_bridge_sha256', Path(rb.__file__)),
        ('slice_b_sha256', Path(b.__file__)),
        ('slice_b_worker_sha256', Path(b.__file__).with_name('slice_b_worker.py')))
    return {name: b.digest(path.read_bytes()) for name, path in files}


def _identity(record: dict) -> None:
    for key in ('subject_id', 'run_id', 'policy_version'):
        value = record.get(key)
        if not isinstance(value, str) or not b.ID.fullmatch(value):
            b.refuse('INVALID_ID', 'Exact identifier required.', field=key)
    digest = record.get('input_sha256')
    if not isinstance(digest, str) or not b.SHA.fullmatch(digest):
        b.refuse('INVALID_ID', 'Exact canonical SHA256 required.',
                 field='input_sha256')


def _synthetic_scope(envelope: dict, inputs: dict) -> None:
    """RED 1: a synthetic permit cannot execute live inputs (fail closed)."""
    subject = envelope['subject_id']
    if not subject.startswith(SYNTHETIC_SUBJECT_PREFIX):
        b.refuse('LIVE_RUN_NOT_AUTHORIZED',
                 'Only explicit synthetic subjects are executable in this envelope.')
    meta = inputs.get('metadata')
    if not isinstance(meta, dict):
        b.refuse('LIVE_RUN_NOT_AUTHORIZED',
                 'Synthetic scope cannot be established without canonical metadata.')
    deal_id = meta.get('deal_id')
    purpose = meta.get('purpose')
    if not isinstance(deal_id, str) or not deal_id.startswith(SYNTHETIC_SUBJECT_PREFIX):
        b.refuse('LIVE_RUN_NOT_AUTHORIZED',
                 'Live inputs cannot execute under a synthetic permit.')
    if purpose not in SYNTHETIC_PURPOSES:
        b.refuse('LIVE_RUN_NOT_AUTHORIZED',
                 'Live inputs cannot execute under a synthetic permit.')
    if deal_id != subject or meta.get('run_id') != envelope['run_id']:
        b.refuse('APPROVAL_MISMATCH',
                 'Canonical identity does not bind the exact envelope request.')
    if envelope['analysis_window'] != inputs.get('time_grid'):
        b.refuse('APPROVAL_MISMATCH',
                 'Envelope analysis window does not bind the exact canonical window.')


def _evidence_pins(envelope: dict, evidence: dict) -> dict:
    """RED 5: exact classification/policy evidence pins are mandatory."""
    pins = envelope['evidence_sha256']
    if not isinstance(pins, dict) or set(pins) != set(EVIDENCE_NAMES):
        b.refuse('INVALID_CONTRACT',
                 'Exact classification/policy evidence pins are required.')
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_NAMES):
        b.refuse('EVIDENCE_INCOMPLETE',
                 'Reviewed classification and policy evidence bytes are both required.')
    exact_bytes = {}
    for name in EVIDENCE_NAMES:
        raw = evidence[name]
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            b.refuse('EVIDENCE_INCOMPLETE', 'Exact evidence bytes are required.',
                     evidence=name)
        exact_bytes[name] = bytes(raw)
        if b.digest(exact_bytes[name]) != pins[name]:
            b.refuse('HASH_MISMATCH', 'Evidence bytes differ from the envelope pin.',
                     evidence=name)
    if envelope['policy_sha256'] != pins['policy']:
        b.refuse('HASH_MISMATCH',
                 'Policy pin does not bind the exact policy evidence bytes.')
    return exact_bytes


def _supported_locators(classification: dict, policy: dict, canonical: bytes) -> None:
    """RED 3: unsupported locator types never become weaker evidence.

    Runs before the frozen contracts validation so a locator outside this
    envelope's resolvable surface (json_pointer into the exact canonical
    inputs.json bytes) is refused here rather than translated.
    """
    items = []
    evidence = classification.get('evidence') if isinstance(classification, dict) else None
    if isinstance(evidence, list):
        items.extend(evidence)
    assumptions = policy.get('assumptions') if isinstance(policy, dict) else None
    if isinstance(assumptions, list):
        for assumption in assumptions:
            if isinstance(assumption, dict) and isinstance(assumption.get('evidence'), list):
                items.extend(assumption['evidence'])
    digest = b.digest(canonical)
    for item in items:
        if not isinstance(item, dict):
            b.refuse('INVALID_CONTRACT', 'Structured evidence records are required.')
        if item.get('artifact') not in SOURCE_ARTIFACTS:
            b.refuse('EVIDENCE_LOCATOR_UNSUPPORTED',
                     'Only exact canonical source artifacts are resolvable.')
        locator = item.get('locator')
        if (not isinstance(locator, dict) or set(locator) != {'json_pointer'}
                or not isinstance(locator['json_pointer'], str)):
            b.refuse('EVIDENCE_LOCATOR_UNSUPPORTED',
                     'Unsupported source-locator types cannot become weaker evidence.')
        if item.get('sha256') != digest:
            b.refuse('HASH_MISMATCH',
                     'Evidence does not bind the exact canonical bytes.')


def _load_registry(loader) -> dict:
    """RED 6: registry membership is loaded fresh for every invocation.

    The env path is the host-owned pinned registry from the frozen review
    bridge. The optional loader is a labelled host/test fault-injection seam
    for the same record validation; it is never model output.
    """
    if loader is None:
        return rb.host_registry()
    if not callable(loader):
        b.refuse('INVALID_CONTRACT', 'Registry loader must be callable.')
    registry = loader()
    if not isinstance(registry, dict):
        b.refuse('INVALID_CONTRACT', 'Host registry must be a mapping.')
    for approval_id, record in registry.items():
        if not isinstance(approval_id, str) or not b.ID.fullmatch(approval_id):
            b.refuse('INVALID_CONTRACT', 'Exact host approval identifier required.')
        rb.exact(record, {'actor_id', 'payload_sha256', 'approval_sha256'},
                 'host approval record')
        contracts._text(record['actor_id'], 'actor_id')
        contracts._sha(record['payload_sha256'])
        contracts._sha(record['approval_sha256'])
    return registry


def _millage_binding(policy: dict, inputs: dict) -> None:
    """The reviewed policy must carry the exact canonical millage value."""
    meta = inputs.get('metadata') or {}
    summary = meta.get('property_summary') or {}
    tax = summary.get('property_tax_policy') or {}
    canonical_millage = tax.get('millage_rate_mills')
    names = set()
    for assumption in policy.get('assumptions') or []:
        if assumption.get('value') is None:
            continue
        name = assumption.get('name')
        names.add(name)
        if name != MILLAGE_BINDING:
            b.refuse('POLICY_CONFLICT',
                     'Policy assumption has no verified canonical binding in this envelope.',
                     assumption=name)
        if canonical_millage is None:
            b.refuse('POLICY_CONFLICT',
                     'Canonical millage is absent; reviewed policy cannot bind it.',
                     assumption=name)
        if rb.number(assumption['value']) != rb.number(canonical_millage):
            b.refuse('POLICY_CONFLICT',
                     'Reviewed policy millage differs from the exact canonical value.',
                     assumption=name)
    if MILLAGE_BINDING not in names:
        b.refuse('POLICY_CONFLICT', 'Explicit reviewed policy millage is required.')


def _run_engine(engine_callable, inputs: dict, timeout_s) -> dict:
    """RED 8: the engine wait is bounded; failures stay sanitized.

    The engine callable runs inside a forked child process so it can always be
    SIGKILLed at the bound — matching the frozen slice_b pattern of bounding
    the real engine with a killable subprocess. The parent stays
    single-threaded (the frozen worker-context contract requires
    ``threading.active_count() == 1``); there is no thread, poll loop or
    unbounded wait anywhere in this module.
    """
    if not callable(engine_callable):
        b.refuse('NOT_IMPLEMENTED',
                 'The pinned existing engine callable is required for execution.')
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s) or not 0 < timeout_s <= MAX_ENGINE_TIMEOUT_S):
        b.refuse('INVALID_CONTRACT', 'A bounded positive engine wait is required.')
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        # Child: run the engine and write the result; never leak to the run dir.
        os.close(read_fd)
        status = 3
        try:
            output = engine_callable(inputs, federation_mode=True)
            raw = json.dumps(output, sort_keys=True, indent=2, allow_nan=False,
                            default=str).encode('utf-8')
            os.write(write_fd, raw)
            status = 0
        except BaseException:
            status = 3
        finally:
            os.close(write_fd)
            os._exit(status)
    os.close(write_fd)
    deadline = time.monotonic() + float(timeout_s)
    chunks = []
    killed = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.kill(pid, signal.SIGKILL)
                killed = True
                break
            ready, _, _ = select.select([read_fd], [], [], remaining)
            if ready:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
    finally:
        os.close(read_fd)
    _, exit_status = os.waitpid(pid, 0)
    if killed:
        b.refuse('ENGINE_TIMEOUT', 'Bounded engine wait elapsed; execution refused.')
    economics = None
    if exit_status == 0:
        try:
            economics = json.loads(b''.join(chunks))
        except ValueError:
            economics = None
    if not isinstance(economics, dict):
        b.refuse('NOT_IMPLEMENTED', 'Engine execution failed; details are not surfaced.')
    return economics


def _sanitize(fn, *args, **kwargs):
    """Sanitizing boundary around frozen-bridge refusals.

    The frozen bridge raises HarnessError inside ``except`` blocks, leaving a
    chained ``__context__`` that can carry input-derived values. Re-raise the
    same typed refusal with chaining cleared so no input value, engine
    internal or decoder detail ever surfaces in an exception chain.
    """
    try:
        return fn(*args, **kwargs)
    except HarnessError as exc:
        clean = HarnessError(exc.code, exc.message, details=exc.details)
    raise clean


def _verify_reviews(envelope: dict, canonical: bytes, classification: dict,
                    policy: dict, registry: dict, inputs: dict) -> None:
    """RED 2/5/6: authority, classification, policy, locators, millage."""
    _sanitize(rb.approved, envelope, registry)
    _sanitize(contracts.require_approved_policy, classification, policy,
              approval_registry=registry)
    _millage_binding(policy, inputs)
    sources = {name: canonical for name in SOURCE_ARTIFACTS}
    _sanitize(rb.evidence_exists, classification['evidence'], sources)
    for assumption in policy['assumptions']:
        _sanitize(rb.evidence_exists, assumption['evidence'], sources)


def execute_reviewed_draft(*, envelope: dict, canonical: bytes, evidence: dict,
                           engine_callable=None, run_dir: str,
                           registry_loader=None,
                           engine_timeout_s: float = DEFAULT_ENGINE_TIMEOUT_S) -> dict:
    """Execute an approved reviewed draft; never certifies economics.

    ``engine_callable`` must be the pinned existing engine entry point
    (``ENGINE_PATH``) bound by the host, or a stub representing it in
    synthetic tests. Economics come only from that callable; this envelope
    adds, alters or rounds nothing.
    """
    rb.exact(envelope, ENVELOPE_KEYS, 'reviewed-draft execution envelope')
    if envelope['contract_version'] != ENVELOPE_VERSION:
        b.refuse('INVALID_CONTRACT', 'Unsupported reviewed-draft execution contract.')
    _identity(envelope)
    if envelope['synthetic'] is not True:
        b.refuse('LIVE_RUN_NOT_AUTHORIZED',
                 'Only explicit synthetic scope can execute in this envelope.')
    if envelope['intake_validated'] is not True:
        b.refuse('REVIEW_REQUIRED', 'Explicit intake validation is required.')
    if envelope['overrides'] != []:
        b.refuse('POLICY_CONFLICT',
                 'Execution overrides are not implemented; approve new canonical bytes instead.')
    if envelope['code_sha256'] != code_pins():
        b.refuse('STALE', 'Execution envelope binds different harness code.')
    if not isinstance(canonical, (bytes, bytearray)) or not canonical:
        b.refuse('INVALID_INPUT', 'Exact canonical bytes are required.')
    canonical = bytes(canonical)
    if b.digest(canonical) != envelope['input_sha256']:
        b.refuse('HASH_MISMATCH', 'Canonical bytes differ from the envelope pin.')
    inputs = b.decode(canonical)
    _synthetic_scope(envelope, inputs)
    evidence = _evidence_pins(envelope, evidence)
    target = b.safe_path(run_dir, directory=True)
    if any(target.iterdir()):
        b.refuse('RUN_CONFLICT', 'Run directory already holds artifacts.')
    classification = rb.decode(evidence['classification'])
    policy = rb.decode(evidence['policy'])
    _supported_locators(classification, policy, canonical)
    registry = _load_registry(registry_loader)
    _verify_reviews(envelope, canonical, classification, policy, registry, inputs)
    economics = _run_engine(engine_callable, inputs, engine_timeout_s)
    economics_bytes = b.encode(economics)
    economics_sha256 = b.digest(economics_bytes)
    envelope_bytes = b.encode(envelope)
    # Atomic immutable publication (frozen slice_b/review_bridge lineage
    # pattern): stage inside the validated parent, then rename; a failed or
    # concurrent publication never poisons the run directory.
    stage = Path(tempfile.mkdtemp(prefix='.' + target.name + '-', dir=target.parent))
    for name, raw in (('inputs.json', canonical),
                      ('classification.json', evidence['classification']),
                      ('policy.json', evidence['policy']),
                      ('envelope.json', envelope_bytes),
                      ('economics.json', economics_bytes)):
        b._write(stage / name, raw)
    manifest = {
        'subject_id': envelope['subject_id'], 'run_id': envelope['run_id'],
        'input_sha256': envelope['input_sha256'],
        'policy_version': envelope['policy_version'],
        'envelope_version': ENVELOPE_VERSION,
        'approval_id': envelope['approval']['approval_id'],
        'synthetic': True, 'status': STATUS, 'certified': False,
        'certification_eligible': False, 'blocker': BLOCKER,
        'recon_status': RECON_STATUS, 'engine_path': ENGINE_PATH,
        'recon_path': RECON_PATH,
        'executed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'analysis_window': envelope['analysis_window'],
        'policy_sha256': envelope['policy_sha256'],
        'evidence_sha256': envelope['evidence_sha256'],
        'code_sha256': code_pins(), 'economics': b.decode(economics_bytes),
        'economics_sha256': economics_sha256,
        'envelope_sha256': b.digest(envelope_bytes),
        'run_metadata_keys': list(RUN_METADATA),
    }
    b._write(stage / 'manifest.json', b.encode(manifest))
    # Atomic immutable publication; never replace a populated run directory.
    try:
        stage.rename(target)
    except OSError:
        b.refuse('RUN_CONFLICT', 'Run directory already holds artifacts.')
    return {**manifest, 'artifact_dir': str(target)}


def read_reviewed_draft(*, run_dir: str, registry_loader=None) -> dict:
    """Re-verify every pin against current host state; refuse any drift.

    RED 5/6: code, input, evidence, economics and envelope pins are re-hashed,
    the registry is reloaded, and the frozen approval/policy/classification
    contracts re-run on the saved bytes before the draft is returned.
    """
    target = b.safe_path(run_dir, directory=True)
    data = {name: b._read(target / name) for name in ARTIFACT_NAMES}
    manifest = b.decode(data['manifest.json'])
    rb.exact(manifest, set(RUN_METADATA), 'reviewed-draft run manifest')
    _identity(manifest)
    if manifest['synthetic'] is not True:
        b.refuse('LIVE_RUN_NOT_AUTHORIZED',
                 'Saved run is not an explicit synthetic draft.')
    if manifest['input_sha256'] != b.digest(data['inputs.json']):
        b.refuse('HASH_MISMATCH', 'Saved canonical bytes differ from the manifest pin.')
    expected = {'classification': b.digest(data['classification.json']),
                'policy': b.digest(data['policy.json'])}
    if manifest['evidence_sha256'] != expected:
        b.refuse('HASH_MISMATCH', 'Saved evidence bytes differ from the manifest pins.')
    if manifest['policy_sha256'] != expected['policy']:
        b.refuse('HASH_MISMATCH',
                 'Saved policy pin does not bind the exact policy bytes.')
    if manifest['economics_sha256'] != b.digest(data['economics.json']):
        b.refuse('HASH_MISMATCH', 'Saved economics bytes differ from the manifest pin.')
    if manifest['economics'] != b.decode(data['economics.json']):
        b.refuse('HASH_MISMATCH', 'Saved economics record differs from its exact bytes.')
    if manifest['envelope_sha256'] != b.digest(data['envelope.json']):
        b.refuse('HASH_MISMATCH', 'Saved envelope bytes differ from the manifest pin.')
    if manifest['code_sha256'] != code_pins():
        b.refuse('STALE', 'Saved run was produced by different harness code.')
    inputs = b.decode(data['inputs.json'])
    classification = rb.decode(data['classification.json'])
    policy = rb.decode(data['policy.json'])
    envelope = rb.decode(data['envelope.json'])
    rb.exact(envelope, ENVELOPE_KEYS, 'reviewed-draft execution envelope')
    if envelope['contract_version'] != ENVELOPE_VERSION:
        b.refuse('INVALID_CONTRACT', 'Unsupported reviewed-draft execution contract.')
    for key in ('subject_id', 'run_id', 'input_sha256', 'policy_version'):
        if envelope.get(key) != manifest.get(key):
            b.refuse('APPROVAL_MISMATCH',
                     'Saved envelope does not bind the exact run identity.')
    _synthetic_scope(envelope, inputs)
    _supported_locators(classification, policy, data['inputs.json'])
    registry = _load_registry(registry_loader)
    _verify_reviews(envelope, data['inputs.json'], classification, policy,
                    registry, inputs)
    if manifest['certified'] is not False:
        b.refuse('UNCERTIFIED_METRIC',
                 'This envelope never issues certified run manifests.')
    if (manifest['certification_eligible'] is not False
            or manifest['status'] != STATUS or manifest['blocker'] != BLOCKER
            or manifest['recon_status'] != RECON_STATUS):
        b.refuse('INVALID_CONTRACT', 'Saved run state is not a reviewed draft.')
    return {**manifest, 'artifact_dir': str(target)}