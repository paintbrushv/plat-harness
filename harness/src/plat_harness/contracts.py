"""Versioned, fail-closed classification and approved-policy data contracts.

Pure validation only: no files, model calls, financial defaults or certification.
Approval registries must be supplied by a trusted host, never model output.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any, Mapping

from plat_harness.errors import HarnessError

TAXONOMY = ("core", "core+", "lease-up", "value-add", "opportunistic")
CLASSIFICATION_VERSION = "classification/1.0.0"
POLICY_VERSION = "policy-pack/1.0.0"
OVERRIDE_VERSION = "classification-override/1.0.0"
UNCERTIFIED = "uncertified_until_classifier_ships"
ASSUMPTION_UNITS = {
    "financing_ltv": "ratio", "interest_rate": "ratio",
    "millage_rate_mills": "mills_per_1000", "exit_cap": "ratio",
    "target_monthly_rent": "currency_per_month", "rent_premium": "currency_per_month",
    "investor_hurdle": "ratio", "hold_months": "months",
    "price_basis": "currency", "annual_expenses": "currency_per_year",
    "reserve_capex": "currency",
}


def _fail(message: str, code: str = "INVALID_CONTRACT") -> None:
    raise HarnessError(code, message)


def _object(value: Any, keys: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{where}: exact keys required: {sorted(keys)}")


def _text(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{where}: nonempty string required")


def _sha(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail("lowercase SHA256 required")


def _version(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        _fail("exact semantic version required")


def canonical_sha256(value: Any) -> str:
    """Hash canonical JSON; reject NaN/Infinity rather than normalizing them."""
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise HarnessError("INVALID_CONTRACT", "canonical JSON required") from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_evidence(value: dict, *, subject_id: str) -> dict:
    _object(value, {"artifact", "sha256", "subject_id", "locator", "source_kind"}, "evidence")
    _text(value["artifact"], "artifact")
    _sha(value["sha256"])
    if value["subject_id"] != subject_id:
        _fail("evidence subject mismatch", "SUBJECT_MISMATCH")
    if value["source_kind"] not in ("broker_claim", "analyst_inference", "reviewed_evidence"):
        _fail("unknown evidence source kind")
    loc = value["locator"]
    if not isinstance(loc, dict):
        _fail("structured evidence locator required")
    if set(loc) == {"page"}:
        if type(loc["page"]) is not int or loc["page"] < 1:
            _fail("one-based integer page required")
    elif set(loc) == {"sheet", "row"}:
        _text(loc["sheet"], "sheet")
        if type(loc["row"]) is not int or loc["row"] < 1:
            _fail("one-based integer row required")
    elif set(loc) == {"json_pointer"}:
        pointer = loc["json_pointer"]
        if not isinstance(pointer, str) or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
            _fail("non-root RFC6901 pointer required")
    else:
        _fail("locator must be page, sheet/row or json_pointer")
    return deepcopy(value)


def _evidence(values: Any, subject: str, *, required: bool = False) -> None:
    if not isinstance(values, list) or (required and not values):
        _fail("evidence list required")
    for value in values:
        validate_evidence(value, subject_id=subject)
    if len({canonical_sha256(x) for x in values}) != len(values):
        _fail("duplicate evidence locator")


def _approval(value: Any, payload: dict, registry: Mapping[str, dict] | None) -> None:
    _object(value, {"approval_id", "actor_id", "actor_type", "approved_at", "reason", "record_locator", "payload_sha256"}, "approval")
    for field in ("approval_id", "actor_id", "approved_at", "reason", "record_locator"):
        _text(value[field], field)
    if value["actor_type"] != "human":
        _fail("human approval required", "APPROVAL_REQUIRED")
    try:
        stamp = datetime.fromisoformat(value["approved_at"].replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            raise ValueError("timezone required")
    except ValueError as exc:
        raise HarnessError("INVALID_CONTRACT", "timezone-aware approval timestamp required") from exc
    _sha(value["payload_sha256"])
    expected = canonical_sha256(payload)
    if value["payload_sha256"] != expected:
        _fail("approval does not bind exact payload", "APPROVAL_MISMATCH")
    trusted = (registry or {}).get(value["approval_id"])
    if (not trusted or trusted.get("actor_id") != value["actor_id"]
            or trusted.get("payload_sha256") != expected
            or trusted.get("approval_sha256") != canonical_sha256(value)):
        _fail("approval absent from host-owned registry or provenance altered", "APPROVAL_REQUIRED")


def validate_classification(value: dict, *, approval_registry: Mapping[str, dict] | None = None) -> dict:
    """Validate draft/review state. This function can NEVER return certified."""
    _object(value, {"contract_version", "subject_id", "run_id", "input_sha256", "deal_type", "candidates", "confidence", "ambiguity", "conflicting_signals", "evidence", "policy_pack", "state", "reason", "review", "certification"}, "classification")
    if value["contract_version"] != CLASSIFICATION_VERSION:
        _fail("unsupported classification version")
    for key in ("subject_id", "run_id", "reason"):
        _text(value[key], key)
    _sha(value["input_sha256"])
    if value["certification"] != UNCERTIFIED:
        _fail("code shipping/review does not certify a classification", "UNCERTIFIED_CLASSIFICATION")
    label = value["deal_type"]
    if label is not None and label not in TAXONOMY:
        _fail("deal type outside taxonomy")
    candidates = value["candidates"]
    if not isinstance(candidates, list) or any(c not in TAXONOMY for c in candidates) or len(set(candidates)) != len(candidates):
        _fail("unique taxonomy candidates required")
    conf = value["confidence"]
    if type(conf) not in (float, int) or not math.isfinite(conf) or not 0 <= conf <= 1:
        _fail("finite confidence in [0,1] required")
    for key in ("ambiguity", "conflicting_signals"):
        if not isinstance(value[key], list):
            _fail(f"{key}: list required")
        for item in value[key]:
            if key == "ambiguity":
                _text(item, key)
            else:
                _object(item, {"signal", "evidence"}, "conflicting signal")
                _text(item["signal"], "signal")
                _evidence(item["evidence"], value["subject_id"], required=True)
    _evidence(value["evidence"], value["subject_id"], required=label is not None)
    _object(value["policy_pack"], {"policy_id", "version"}, "policy reference")
    _text(value["policy_pack"]["policy_id"], "policy_id")
    _version(value["policy_pack"]["version"])
    state = value["state"]
    if state not in ("review_required", "abstained", "evidence_reviewed"):
        _fail("unknown review state")
    if (state == "abstained") != (label is None):
        _fail("abstention requires null label; null label requires abstention")
    if label is not None and label not in candidates:
        _fail("selected label must be a candidate")
    if state == "evidence_reviewed":
        if conf < 0.8 or value["ambiguity"] or value["conflicting_signals"] or len(candidates) != 1:
            _fail("uncertainty/conflict still requires review", "CLASSIFICATION_REVIEW_REQUIRED")
        if any(e["source_kind"] != "reviewed_evidence" for e in value["evidence"]):
            _fail("broker/analyst draft is not reviewed ground truth", "CLASSIFICATION_REVIEW_REQUIRED")
        _approval(value["review"], {k: v for k, v in value.items() if k != "review"}, approval_registry)
    elif value["review"] is not None:
        _fail("only evidence_reviewed state may carry a review")
    return deepcopy(value)


def validate_policy_pack(value: dict, *, approval_registry: Mapping[str, dict] | None = None) -> dict:
    """Record supplied business policy, never select assumptions from a label.

    A valid approved pack is not an economic-input validator or engine approval.
    Missing keys/values remain missing; downstream required-input gates still run.
    """
    _object(value, {"contract_version", "policy_id", "version", "subject_id", "status", "assumptions", "approval"}, "policy pack")
    if value["contract_version"] != POLICY_VERSION or value["status"] not in ("draft", "approved"):
        _fail("unsupported policy version/status")
    for key in ("policy_id", "subject_id"):
        _text(value[key], key)
    _version(value["version"])
    assumptions = value["assumptions"]
    if not isinstance(assumptions, list):
        _fail("assumptions list required")
    names = []
    for item in assumptions:
        _object(item, {"name", "value", "unit", "evidence"}, "assumption")
        name = item["name"]
        if not isinstance(name, str) or name not in ASSUMPTION_UNITS or item["unit"] != ASSUMPTION_UNITS[name]:
            _fail("unknown assumption or incorrect explicit unit")
        names.append(name)
        supplied = item["value"]
        if supplied is not None:
            if not isinstance(supplied, str):
                _fail("financial values must be decimal strings or null")
            try:
                number = Decimal(supplied)
                if not number.is_finite() or number < 0:
                    raise InvalidOperation
            except InvalidOperation as exc:
                raise HarnessError("INVALID_CONTRACT", "finite nonnegative decimal required") from exc
        _evidence(item["evidence"], value["subject_id"], required=supplied is not None)
    if len(set(names)) != len(names):
        _fail("duplicate policy assumption")
    if value["status"] == "approved":
        _approval(value["approval"], {k: v for k, v in value.items() if k != "approval"}, approval_registry)
    elif value["approval"] is not None:
        _fail("draft pack cannot carry approval")
    return deepcopy(value)


def require_approved_policy(classification: dict, policy: dict, *, approval_registry: Mapping[str, dict] | None = None) -> dict:
    """Check exact subject and policy version, without granting certification."""
    draft = validate_classification(classification, approval_registry=approval_registry)
    pack = validate_policy_pack(policy, approval_registry=approval_registry)
    if draft["subject_id"] != pack["subject_id"]:
        _fail("policy subject mismatch", "SUBJECT_MISMATCH")
    if draft["policy_pack"] != {"policy_id": pack["policy_id"], "version": pack["version"]}:
        _fail("policy version mismatch", "POLICY_VERSION_MISMATCH")
    if pack["status"] != "approved":
        _fail("business policy approval required", "APPROVAL_REQUIRED")
    if draft["state"] != "evidence_reviewed":
        _fail("classification evidence review required", "CLASSIFICATION_REVIEW_REQUIRED")
    return pack


def record_human_override(original: dict, replacement: dict, approval: dict, *, approval_registry: Mapping[str, dict] | None = None) -> dict:
    """Append-only envelope; never mutate the original or relax financial gates."""
    before = validate_classification(original, approval_registry=approval_registry)
    after = validate_classification(replacement, approval_registry=approval_registry)
    for key in ("subject_id", "run_id", "input_sha256"):
        if before[key] != after[key]:
            _fail("override must bind the same subject/run/input", "SUBJECT_RUN_MISMATCH")
    if before == after:
        _fail("override must describe a change")
    payload = {"contract_version": OVERRIDE_VERSION, "original_sha256": canonical_sha256(before), "replacement_sha256": canonical_sha256(after)}
    _approval(approval, payload, approval_registry)
    return {**payload, "original": before, "replacement": after, "approval": deepcopy(approval)}
