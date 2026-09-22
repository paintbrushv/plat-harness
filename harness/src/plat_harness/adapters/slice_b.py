"""Fail-closed, synthetic-only bridge to the existing UW engine and recon CLI.

Host-owned approval files are inputs, never model-authored authority. No policy
assumptions are selected or applied here. A review approves the *exact* canonical
bytes. Engine/recon outputs remain drafts. The versioned review bridge adds
append-only synthetic coverage reviews, never live financial certification.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from plat_harness.adapters import paths
from plat_harness.errors import HarnessError
from plat_harness.millage import parse_millage_rate
from plat_harness.ranks import PermissionRank
from plat_harness.tools.catalog import require_rank

# The private data root is host configuration, not code: it is read from
# PLAT_HARNESS_PRIVATE_ROOT at call time (fail-closed when unset). Tests and
# other hosts point it at their own 0700 directory; nothing is hardcoded.
PRIVATE_ROOT_ENV = "PLAT_HARNESS_PRIVATE_ROOT"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
VERSION = "slice-b-synthetic-v1"


def private_root() -> Path:
    """Read the configured private root from the environment at call time.

    Refuses when unset or empty: the fail-closed default is "no root at all",
    never a guessed or builtin path.
    """
    raw = os.environ.get(PRIVATE_ROOT_ENV, "")
    if not raw:
        refuse("UNSAFE_PATH", "PLAT_HARNESS_PRIVATE_ROOT must configure the private data root.")
    return Path(raw)


def refuse(code: str, message: str, **details: Any) -> None:
    raise HarnessError(code, message, details=details)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: Any) -> bytes:
    # Decimal strings never round-trip through a binary float in this adapter.
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False,
                      default=lambda x: str(x) if isinstance(x, Decimal) else _unsupported(x)).encode()


def _unsupported(value: Any) -> None:
    raise TypeError(type(value).__name__)


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            refuse("INVALID_INPUT", "Duplicate JSON key.", key=key)
        result[key] = value
    return result


def _json_number(raw: str) -> Decimal:
    if len(raw) > 1000:
        refuse("INVALID_INPUT", "JSON numeric token exceeds the supported bound.")
    value = Decimal(raw)
    # The existing recon consumes binary floats. Refuse overflow/underflow at
    # that boundary rather than quietly producing Infinity or an implicit zero.
    if not value.is_finite() or (value and not -308 <= value.adjusted() <= 308) or not math.isfinite(float(value)):
        refuse("INVALID_INPUT", "JSON number exceeds supported finite range.")
    return value


def decode(data: bytes) -> dict:
    try:
        value = json.loads(data, parse_float=_json_number, object_pairs_hook=_pairs,
                           parse_int=lambda raw: int(_json_number(raw)),
                           parse_constant=lambda x: refuse("INVALID_INPUT", "Nonfinite JSON number."))
    except (ValueError, UnicodeError, InvalidOperation, OverflowError, RecursionError) as exc:
        refuse("INVALID_INPUT", "Invalid or out-of-range JSON.", reason=type(exc).__name__)
    if not isinstance(value, dict):
        refuse("INVALID_INPUT", "JSON root must be an object.")
    return value


def safe_path(raw: str | Path, *, directory: bool = False) -> Path:
    root = private_root()
    p = Path(raw)
    if not p.is_absolute() or ".." in p.parts or p != p.resolve():
        refuse("UNSAFE_PATH", "Absolute private paths without symlinks or traversal are required.")
    if not p.is_relative_to(root) or p == root:
        refuse("UNSAFE_PATH", "Slice B artifacts must remain below the private data root.")
    for ancestor in (p, *p.parents):
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            refuse("UNSAFE_PATH", "Symlink paths are forbidden.")
        # The private root blocks access even if a historical intermediate
        # campaign directory retains broader mode bits. Enforce root and target
        # modes without changing campaign originals.
        if ancestor in (p, root) and ancestor.exists() and ancestor.stat().st_mode & 0o077:
            refuse("UNSAFE_PATH", "Private root and target must not be group/world accessible.")
    if directory and not p.is_dir():
        refuse("NOT_FOUND", "Configured private run root must already exist.")
    return p


def _parent_fd(path: Path) -> int:
    """Walk every ancestor with no-follow descriptors, not a check/open race."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(path: str | Path, *, limit: int = 32 * 1024 * 1024) -> bytes:
    p = safe_path(path)
    parent = fd = None
    try:
        parent = _parent_fd(p)
        fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_uid != os.getuid()):
            refuse("UNSAFE_PATH", "Private owned single-link regular inputs are required.")
        if info.st_size > limit:
            refuse("INVALID_INPUT", "Input exceeds the bounded adapter size.")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        after = os.fstat(fd)
        if len(data) > limit or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            refuse("HASH_MISMATCH", "Input changed during the bounded descriptor read.")
        return data
    except FileNotFoundError:
        refuse("NOT_FOUND", "Required private input does not exist.", path=str(p))
    except OSError as exc:
        refuse("UNSAFE_PATH", "Unsafe or unreadable private input.", reason=type(exc).__name__)
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def _write(path: Path, data: bytes) -> None:
    parent = _parent_fd(safe_path(path))
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(parent)


def _identity(subject_id: str, run_id: str, input_sha256: str, policy_version: str) -> dict:
    if not isinstance(subject_id, str) or not ID.fullmatch(subject_id):
        refuse("INVALID_ID", "Invalid exact subject identifier.")
    if not isinstance(run_id, str) or not ID.fullmatch(run_id):
        refuse("INVALID_ID", "Invalid exact run identifier.")
    if not isinstance(input_sha256, str) or not SHA.fullmatch(input_sha256):
        refuse("INVALID_HASH", "Expected a lowercase SHA256, without normalization.")
    if not isinstance(policy_version, str) or not ID.fullmatch(policy_version):
        refuse("POLICY_CONFLICT", "An explicit policy version is required.")
    return dict(subject_id=subject_id, run_id=run_id, input_sha256=input_sha256,
                policy_version=policy_version)


def _root() -> Path:
    raw = os.environ.get("PLAT_HARNESS_SLICE_B_ROOT")
    if not raw:
        refuse("NOT_IMPLEMENTED", "Configure a private PLAT_HARNESS_SLICE_B_ROOT first.")
    return safe_path(raw, directory=True)


def _engine() -> tuple[Path, Path, str]:
    root = paths.engine_root()
    if root is None or not root.is_absolute() or root != root.resolve():
        refuse("NOT_IMPLEMENTED", "An absolute existing UW engine checkout is required.")
    python = root / ".venv/bin/python"
    files = sorted([*root.glob("engine/**/*.py"), *root.glob("engine/schemas/*.json"),
                    root / "runs/build_underwriting_reconciliation.py"])
    if not python.is_file() or not (root / "engine/engine.py").is_file() or not files[-1].is_file():
        refuse("NOT_IMPLEMENTED", "Existing UW interpreter, engine and recon are required.")
    h = hashlib.sha256()
    for p in files:
        h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes() + b"\0")
    return root, python, h.hexdigest()


def _required_fields(inputs: dict) -> None:
    required = {
        "purchase_assumptions": ("purchase_price", "closing_costs", "equity_contribution", "total_equity_basis"),
        "debt_terms": ("commitment", "rate", "amort_years", "io_months"),
        "exit_assumptions": ("exit_cap_rate", "sale_cost_percent", "exit_month"),
        "fund_assumptions": ("asset_management_fee_pct", "annual_partnership_expenses", "promote_splits"),
    }
    for section, keys in required.items():
        row = inputs.get(section)
        if not isinstance(row, dict) or any(k not in row or row[k] is None for k in keys):
            refuse("INCOMPLETE_INPUTS", "Explicit approved economics required; null is not zero.", section=section)
    for key in ("unit_cohorts", "market_rent_curve", "loss_to_lease", "physical_vacancy_curve",
                "collection_loss_curve", "opex_table"):
        if not isinstance(inputs.get(key), list) or not inputs[key]:
            refuse("INCOMPLETE_INPUTS", "Required canonical table is absent/empty.", section=key)
    for key in ("revenue_programs", "program_adoption_curve", "replacement_reserves", "capex_schedule"):
        if not isinstance(inputs.get(key), list):
            refuse("INCOMPLETE_INPUTS", "Explicit empty list or supported values required.", section=key)
    for key in ("purchase_price", "total_equity_basis"):
        try:
            n = Decimal(str(inputs["purchase_assumptions"][key]))
            if not n.is_finite() or n <= 0:
                raise ValueError()
        except (ValueError, InvalidOperation):
            refuse("INCOMPLETE_INPUTS", "Positive finite approved purchase and equity basis required.", field=key)


def _gates(identity: dict, canonical: bytes, approval: dict, policy: dict,
           policy_hash: str, evidence: dict[str, bytes]) -> dict:
    if digest(canonical) != identity["input_sha256"]:
        refuse("HASH_MISMATCH", "Canonical bytes differ from the requested hash.")
    inputs = decode(canonical)
    meta = inputs.get("metadata") or {}
    if meta.get("deal_id") != identity["subject_id"] or meta.get("run_id") != identity["run_id"]:
        refuse("IDENTITY_MISMATCH", "Canonical subject/run must exactly match the requested run.")
    if not identity["subject_id"].startswith("synthetic_") or meta.get("purpose") != "synthetic_slice_b":
        refuse("LIVE_RUN_NOT_AUTHORIZED", "This minimum slice authorizes only explicit synthetic runs.")
    grid = inputs.get("time_grid") or {}
    for key in ("analysis_start_date", "analysis_end_date"):
        v = grid.get(key)
        if not isinstance(v, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
            refuse("NEEDS_ANALYSIS_WINDOW", "Approved YYYY-MM start/end are required.")
    if grid["analysis_start_date"] > grid["analysis_end_date"]:
        refuse("NEEDS_ANALYSIS_WINDOW", "Analysis end precedes start.")
    tax = (meta.get("property_summary") or {}).get("property_tax_policy") or {}
    parse_millage_rate(tax.get("millage_rate_mills"))
    _required_fields(inputs)
    if "contract_version" in approval:
        from plat_harness.adapters.review_bridge import execution_gates
        approval, policy = execution_gates(identity, canonical, approval, policy, policy_hash, evidence)
    for k, v in identity.items():
        if approval.get(k) != v:
            refuse("APPROVAL_MISMATCH", "Host approval is for different inputs, policy or identity.", field=k)
    if (approval.get("status") != "approved" or approval.get("synthetic") is not True
            or not approval.get("reviewer") or approval.get("conflicts") != []
            or approval.get("intake_validated") is not True
            or approval.get("analysis_window") != grid):
        refuse("REVIEW_REQUIRED", "Exact input/intake/date review is required; a draft is not approval.")
    classification = approval.get("classification") or {}
    if (classification.get("review_state") != "approved"
            or classification.get("deal_type") not in {"core", "core+", "lease-up", "value-add", "opportunistic"}
            or not classification.get("evidence_locators")):
        refuse("CLASSIFICATION_REVIEW_REQUIRED", "Evidence-based classification review is required.")
    if (policy.get("version") != identity["policy_version"] or policy.get("status") != "approved"
            or policy.get("conflicts") != [] or policy.get("overrides") != []
            or approval.get("policy_sha256") != policy_hash):
        refuse("POLICY_CONFLICT", "Policy hash/version/review conflict; this wrapper applies no overrides.")
    expected = approval.get("evidence_sha256")
    if not isinstance(expected, dict) or set(expected) != {"t12", "broker", "classification"}:
        refuse("EVIDENCE_INCOMPLETE", "Reviewed T12, broker and classification evidence hashes required.")
    for k, data in evidence.items():
        if expected.get(k) != digest(data):
            refuse("HASH_MISMATCH", "Evidence differs from the approved bytes.", evidence=k)
    if not evidence or set(evidence) != set(expected):
        refuse("EVIDENCE_INCOMPLETE", "All approved evidence is required.")
    # Broker nulls must remain null, not enter the recon's legacy zero fallbacks.
    broker = decode(evidence["broker"])
    if not broker.get("revenue_assumptions") or not broker.get("expense_assumptions") or broker.get("noi") is None:
        refuse("EVIDENCE_INCOMPLETE", "Explicit broker revenue/expense/NOI surfaces required.")
    for section in ("revenue_assumptions", "expense_assumptions"):
        if not isinstance(broker[section], dict):
            refuse("EVIDENCE_INCOMPLETE", "Broker sections must be explicit mappings.")
        for value in broker[section].values():
            try:
                if value is None or isinstance(value, bool) or not Decimal(str(value)).is_finite():
                    raise ValueError()
            except (ValueError, InvalidOperation):
                refuse("EVIDENCE_INCOMPLETE", "Unknown/nonfinite broker values cannot become implicit zeros.")
    return inputs


def run_underwriting_model(*, subject_id: str, run_id: str, input_sha256: str,
                           policy_version: str, inputs_path: str, approval_path: str,
                           policy_path: str, t12_path: str, broker_path: str,
                           classification_path: str,
                           session_rank: PermissionRank) -> dict:
    """Host adapter API. Approval paths/rank must not come from model authority."""
    require_rank("run_underwriting_model", session_rank)
    if session_rank != PermissionRank.DRAFT:
        refuse("RANK_FORBIDDEN", "Only host draft rank 2 is permitted.")
    identity = _identity(subject_id, run_id, input_sha256, policy_version)
    root = _root()
    data = {"inputs.json": _read(inputs_path), "approval.json": _read(approval_path),
            "policy.json": _read(policy_path), "t12.xlsx": _read(t12_path),
            "broker.json": _read(broker_path), "classification.json": _read(classification_path)}
    # A document saying "approved" is not host authority. Pin exact approval
    # bytes in host configuration, never in the tool's argument surface.
    if os.environ.get("PLAT_HARNESS_SLICE_B_APPROVAL_SHA256") != digest(data["approval.json"]):
        refuse("APPROVAL_REQUIRED", "Approval is not registered by this host session.")
    evidence = {"t12": data["t12.xlsx"], "broker": data["broker.json"], "classification": data["classification.json"]}
    _gates(identity, data["inputs.json"], decode(data["approval.json"]), decode(data["policy.json"]),
           digest(data["policy.json"]), evidence)
    engine_root, python, engine_hash = _engine()
    worker = Path(__file__).with_name("slice_b_worker.py")
    from plat_harness.adapters.review_bridge import code_hashes
    provenance = {**code_hashes(), "adapter_version": VERSION, "engine_sha256": engine_hash,
                  "worker_sha256": digest(worker.read_bytes()),
                  "adapter_sha256": digest(Path(__file__).read_bytes()),
                  "source_hashes": {k: digest(v) for k, v in data.items()}}
    request_hash = digest(encode({**identity, **provenance}))
    parent = safe_path(root / subject_id)
    parent.mkdir(mode=0o700, exist_ok=True)
    target = safe_path(parent / run_id)
    if target.exists():
        return load_run(**identity, request_sha256=request_hash)
    stage = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=parent))
    for name, value in data.items():
        _write(stage / name, value)
    command = [str(python), str(worker), str(engine_root), str(stage)]
    # Do not inherit credentials, API URLs, model settings or Graph state.
    env = {"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "COMPS_MODE": "skip",
           "MAX_DEALS": "5", "ALLOW_LIFECYCLE": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    timed_out = False
    with (stage / "worker.stdout.log").open("xb") as stdout, (stage / "worker.stderr.log").open("xb") as stderr:
        os.chmod(stdout.name, 0o600)
        os.chmod(stderr.name, 0o600)
        proc = subprocess.Popen(command, cwd=engine_root, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            exit_code = proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            import signal
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            exit_code = proc.wait()
    _write(stage / "child_exit.json", encode({"command": command, "exit_code": exit_code, "timed_out": timed_out}))
    outcome = decode(_read(stage / "worker_result.json")) if (stage / "worker_result.json").is_file() else {
        "status": "blocked", "error": "ENGINE_TIMEOUT" if timed_out else "ENGINE_FAILED", "certified": False}
    if exit_code != 0:
        outcome.update(status="blocked", certified=False)
    artifacts = {p.name: digest(_read(p)) for p in stage.iterdir() if p.is_file()}
    manifest = {**identity, **provenance, **outcome, "request_sha256": request_hash,
                "artifacts": artifacts, "draft_only": True}
    _write(stage / "manifest.json", encode(manifest))
    # Atomic immutable publication; never replace a populated previous run.
    try:
        stage.rename(target)
    except OSError:
        if target.exists():
            refuse("RUN_CONFLICT", "Concurrent run already exists; retained private attempt.")
        raise
    return load_run(**identity, request_sha256=request_hash)


def load_run(*, subject_id: str, run_id: str, input_sha256: str,
             policy_version: str, request_sha256: str | None = None) -> dict:
    identity = _identity(subject_id, run_id, input_sha256, policy_version)
    target = safe_path(_root() / subject_id / run_id)
    manifest = decode(_read(target / "manifest.json"))
    if any(manifest.get(k) != v for k, v in identity.items()):
        refuse("IDENTITY_MISMATCH", "Saved run identity/hash/policy is not the requested run.")
    if request_sha256 is not None and manifest.get("request_sha256") != request_sha256:
        refuse("RUN_CONFLICT", "This run ID already binds different reviewed inputs or code.")
    from plat_harness.adapters.review_bridge import code_hashes
    current_code = code_hashes()
    if any(manifest.get(k) != v for k, v in current_code.items()):
        refuse("STALE", "Saved run was produced by different bridge/contracts code.")
    _, _, current_engine = _engine()
    if (manifest.get("engine_sha256") != current_engine
            or manifest.get("adapter_sha256") != digest(Path(__file__).read_bytes())
            or manifest.get("worker_sha256") != digest(Path(__file__).with_name("slice_b_worker.py").read_bytes())):
        refuse("STALE", "Saved run was produced by different engine/adapter code.")
    artifacts = manifest.get("artifacts")
    required = {"inputs.json", "approval.json", "policy.json", "t12.xlsx", "broker.json", "classification.json", "child_exit.json"}
    if not isinstance(artifacts, dict) or not required.issubset(artifacts):
        refuse("ARTIFACT_INCOMPLETE", "Manifest lacks required provenance artifacts.")
    for name, expected in artifacts.items():
        if not isinstance(name, str) or not ID.fullmatch(name) or not isinstance(expected, str) or not SHA.fullmatch(expected) or digest(_read(target / name)) != expected:
            refuse("HASH_MISMATCH", "Saved artifact is missing, changed, or unsafe.", artifact=name)
    canonical = _read(target / "inputs.json")
    if os.environ.get("PLAT_HARNESS_SLICE_B_APPROVAL_SHA256") != digest(_read(target / "approval.json")):
        refuse("APPROVAL_REQUIRED", "Saved approval is not registered by this host session.")
    source_names = ("inputs.json", "approval.json", "policy.json", "t12.xlsx", "broker.json", "classification.json")
    source_hashes = {name: digest(_read(target / name)) for name in source_names}
    provenance = {k: manifest.get(k) for k in ("adapter_version", *current_code)}
    provenance["source_hashes"] = source_hashes
    if (manifest.get("source_hashes") != source_hashes or
            manifest.get("request_sha256") != digest(encode({**identity, **provenance}))):
        refuse("HASH_MISMATCH", "Saved request fingerprint no longer binds its sources.")
    if "worker_result.json" in artifacts:
        outcome = decode(encode(decode(_read(target / "worker_result.json"))))
        if any(manifest.get(k) != v for k, v in outcome.items()):
            refuse("HASH_MISMATCH", "Manifest status differs from the exact saved worker result.")
    _gates(identity, canonical, decode(_read(target / "approval.json")), decode(_read(target / "policy.json")),
           digest(_read(target / "policy.json")), {"t12": _read(target / "t12.xlsx"),
           "broker": _read(target / "broker.json"), "classification": _read(target / "classification.json")})
    # No automatic certification until the recon coverage/review contract exists.
    if manifest.get("certified") is not False:
        refuse("UNCERTIFIED_METRIC", "This adapter does not issue certified run manifests.")
    return {**manifest, "artifact_dir": str(target)}


def load_cash_on_cash(*, subject_id: str, run_id: str, input_sha256: str,
                      policy_version: str) -> dict:
    saved = load_run(subject_id=subject_id, run_id=run_id, input_sha256=input_sha256,
                     policy_version=policy_version)
    refuse("UNCERTIFIED_METRIC", "Engine/recon draft is not financial certification.",
           blocker=saved.get("error", "RECON_REVIEW_REQUIRED"), run_id=run_id,
           source=str(Path(saved["artifact_dir"]) / "manifest.json"))
