"""Bounded, synthetic opening-state import into the local temporal rehearsal.

Only an observed snapshot is imported.  No earlier PLAT knowledge, approval,
unit-day event, or external effect is invented.  This is not a live migration
or a complete source-of-record conversion protocol.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping
from uuid import uuid4

from plat_harness.temporal import TemporalLedger, TemporalRefusal

try:  # The locked checkpoint lane is Linux-local; importing the package stays portable.
    import fcntl
except ImportError:  # pragma: no cover - exercised by portable import smoke
    fcntl = None


SNAPSHOT_VERSION = "plat.opening-snapshot/1"
CHECKPOINT_VERSION = "plat.opening-checkpoint/1"
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_ROWS = 5_000


class MigrationRefusal(ValueError):
    """A source, mapping, checkpoint, or reconciliation failure."""


@dataclass(frozen=True)
class OpeningRow:
    sequence: int
    source_property_id: str
    stable_property_id: str
    source_record_id: str
    revision: str
    period: str
    account: str
    unit_category: str
    amount: Decimal | None
    deleted: bool


@dataclass(frozen=True)
class OpeningPlan:
    workspace_id: str
    source_system: str
    source_account_id: str
    as_of: date
    snapshot_sha256: str
    mapping_sha256: str
    rows: tuple[OpeningRow, ...]


@dataclass(frozen=True)
class MigrationProgress:
    snapshot_sha256: str
    next_index: int
    total_rows: int
    validated: bool


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise MigrationRefusal("Duplicate JSON field in opening snapshot")
        value[key] = item
    return value


def _parse_json(data: bytes):
    def reject_constant(_):
        raise MigrationRefusal("Nonfinite JSON value")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationRefusal("Opening snapshot is not valid UTF-8 JSON data") from exc


def _text(value):
    if type(value) is not str or not value or value != value.strip() or \
            len(value) > 128 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
                                    for char in value):
        raise MigrationRefusal("Opening identity must be a nonempty bounded string")
    return value


def _amount(value, deleted):
    if deleted:
        if value is not None:
            raise MigrationRefusal("Tombstone amount must be null, not zero")
        return None
    if type(value) is not str or not re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]{1,2})?", value):
        raise MigrationRefusal("Opening money must be finite to USD cents")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise MigrationRefusal("Opening money is invalid") from exc
    if not amount.is_finite() or abs(amount) > Decimal("1000000000000") or \
            amount != amount.quantize(Decimal("0.01")):
        raise MigrationRefusal("Opening money must be finite to USD cents")
    return amount


def plan_opening_snapshot(
    snapshot_bytes: bytes, *, expected_sha256: str,
    identity_map: Mapping[str, str],
) -> OpeningPlan:
    """Validate and plan a dry run without creating any target/checkpoint file."""
    if type(snapshot_bytes) is not bytes or not 0 < len(snapshot_bytes) <= MAX_SNAPSHOT_BYTES:
        raise MigrationRefusal("Opening snapshot byte bound exceeded")
    snapshot_sha = sha256(snapshot_bytes).hexdigest()
    if expected_sha256 != snapshot_sha:
        raise MigrationRefusal("Opening snapshot hash differs from the frozen baseline")
    raw = _parse_json(snapshot_bytes)
    if type(raw) is not dict or set(raw) != {
        "schema_version", "workspace_id", "source_system", "source_account_id",
        "as_of", "rows",
    } or raw["schema_version"] != SNAPSHOT_VERSION:
        raise MigrationRefusal("Unsupported opening snapshot schema")
    workspace = _text(raw["workspace_id"])
    system = _text(raw["source_system"])
    account_id = _text(raw["source_account_id"])
    if type(raw["as_of"]) is not str:
        raise MigrationRefusal("Opening observation date is required")
    try:
        as_of = date.fromisoformat(raw["as_of"])
    except ValueError as exc:
        raise MigrationRefusal("Opening observation date is invalid") from exc
    if as_of.isoformat() != raw["as_of"]:
        raise MigrationRefusal("Opening observation date must remain date-only")
    if as_of == date.max:
        raise MigrationRefusal("Opening observation date cannot have a bounded interval")
    if not isinstance(identity_map, Mapping) or not identity_map:
        raise MigrationRefusal("Explicit stable-ID mapping is required")
    mapping = {_text(source): _text(stable) for source, stable in identity_map.items()}
    if len(set(mapping.values())) != len(mapping):
        raise MigrationRefusal("Implicit source-identity merge is refused")
    mapping_sha = sha256(json.dumps(
        [workspace, system, account_id, mapping], sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    data_rows = raw["rows"]
    if type(data_rows) is not list or not 0 < len(data_rows) <= MAX_ROWS:
        raise MigrationRefusal("Opening snapshot needs a bounded nonempty row set")
    rows = []
    seen = set()
    for index, item in enumerate(data_rows, 1):
        if type(item) is not dict or set(item) != {
            "sequence", "source_property_id", "source_record_id", "revision",
            "period", "account", "unit_category", "amount", "deleted",
        }:
            raise MigrationRefusal("Opening row has an unsupported shape")
        if type(item["sequence"]) is not int or item["sequence"] != index:
            raise MigrationRefusal("Opening source sequence has a gap or duplicate")
        source_property = _text(item["source_property_id"])
        if source_property not in mapping:
            raise MigrationRefusal("Opening property has no reviewed stable-ID mapping")
        record_id = _text(item["source_record_id"])
        revision = _text(item["revision"])
        source_key = (source_property, record_id)
        if source_key in seen:
            raise MigrationRefusal("Multiple revisions of one source record need a separate review")
        seen.add(source_key)
        period = item["period"]
        if type(period) is not str or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
            raise MigrationRefusal("Opening accounting period is invalid")
        try:
            date.fromisoformat(period + "-01")
        except ValueError as exc:
            raise MigrationRefusal("Opening accounting period is invalid") from exc
        account = _text(item["account"])
        category = _text(item["unit_category"])
        if type(item["deleted"]) is not bool:
            raise MigrationRefusal("Opening deletion marker must be explicit")
        amount = _amount(item["amount"], item["deleted"])
        rows.append(OpeningRow(
            index, source_property, mapping[source_property], record_id, revision,
            period, account, category, amount, item["deleted"],
        ))
    return OpeningPlan(workspace, system, account_id, as_of, snapshot_sha,
                       mapping_sha, tuple(rows))


def _event_key(plan: OpeningPlan, row: OpeningRow) -> str:
    return f"opening:{plan.snapshot_sha256}:{row.sequence:08d}"


def _source_record_id(plan: OpeningPlan, row: OpeningRow) -> str:
    return json.dumps([plan.source_account_id, row.source_property_id,
                       row.source_record_id], separators=(",", ":"))


def _payload(plan: OpeningPlan, row: OpeningRow) -> dict:
    return {
        "kind": "observed_opening_state/1",
        "history_before": "unavailable",
        "snapshot_sha256": plan.snapshot_sha256,
        "as_of": plan.as_of.isoformat(),
        "source_property_id": row.source_property_id,
        "source_record_id": row.source_record_id,
        "revision": row.revision,
        "period": row.period,
        "account": row.account,
        "unit_category": row.unit_category,
        "amount": str(row.amount) if row.amount is not None else None,
        "deleted": row.deleted,
    }


def _checkpoint_bytes(plan: OpeningPlan, next_index: int, validated: bool) -> bytes:
    return (json.dumps({
        "version": CHECKPOINT_VERSION,
        "snapshot_sha256": plan.snapshot_sha256,
        "mapping_sha256": plan.mapping_sha256,
        "workspace_id": plan.workspace_id,
        "next_index": next_index,
        "validated": validated,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_checkpoint(path: Path, plan: OpeningPlan) -> tuple[int, bool] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise MigrationRefusal("Opening checkpoint must be a private regular file")
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise MigrationRefusal("Opening checkpoint exceeds bound")
    value = _parse_json(raw)
    if type(value) is not dict or set(value) != {
        "version", "snapshot_sha256", "mapping_sha256", "workspace_id",
        "next_index", "validated",
    } or value["version"] != CHECKPOINT_VERSION or value["snapshot_sha256"] != plan.snapshot_sha256 \
            or value["mapping_sha256"] != plan.mapping_sha256 or value["workspace_id"] != plan.workspace_id:
        raise MigrationRefusal("Opening checkpoint does not match frozen source and mapping")
    position = value["next_index"]
    validated = value["validated"]
    if type(position) is not int or not 0 <= position <= len(plan.rows) or type(validated) is not bool:
        raise MigrationRefusal("Opening checkpoint position is invalid")
    if validated and position != len(plan.rows):
        raise MigrationRefusal("Incomplete opening checkpoint cannot be validated")
    return position, validated


def _write_checkpoint(path: Path, plan: OpeningPlan, next_index: int, validated: bool) -> None:
    payload = _checkpoint_bytes(plan, next_index, validated)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def _checkpoint_lock(path: Path):
    if fcntl is None:
        raise MigrationRefusal("Opening checkpoint requires local POSIX file locking")
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise MigrationRefusal("Opening checkpoint lock must be private")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def reconcile_opening(
    plan: OpeningPlan, ledger: TemporalLedger, *, actor_id: str, known_at: datetime,
) -> dict[str, str]:
    """Compare every scoped source/revision row and exact USD-cent totals."""
    expected_by_property = {}
    for row in plan.rows:
        expected_by_property.setdefault(row.stable_property_id, {})[_event_key(plan, row)] = _payload(plan, row)
    totals = {}
    for property_id, expected in expected_by_property.items():
        found = ledger.as_known(
            actor_id=actor_id, workspace_id=plan.workspace_id, aggregate_id=property_id,
            effective_at=plan.as_of, known_at=known_at,
        )
        prefix = f"opening:{plan.snapshot_sha256}:"
        relevant = {key: value for key, value in found.items() if key.startswith(prefix)}
        if relevant != expected:
            raise MigrationRefusal("Opening row-level source/revision reconciliation failed")
        for value in relevant.values():
            if value["deleted"]:
                continue
            group = (property_id, value["period"], value["account"], value["unit_category"])
            totals[group] = totals.get(group, Decimal("0")) + Decimal(value["amount"])
    return {json.dumps(group, separators=(",", ":")): str(amount)
            for group, amount in sorted(totals.items())}


def read_opening_baseline(
    plan: OpeningPlan, ledger: TemporalLedger, *, actor_id: str,
    effective_at: date, known_at: datetime,
) -> dict[str, str]:
    """Read only the observed opening date; earlier history is unavailable."""
    if type(effective_at) is not date or effective_at != plan.as_of:
        raise MigrationRefusal("Opening snapshot does not establish other effective dates")
    return reconcile_opening(plan, ledger, actor_id=actor_id, known_at=known_at)


def execute_opening_batch(
    plan: OpeningPlan, ledger: TemporalLedger, *, actor_id: str,
    checkpoint_path: Path, batch_size: int, known_at: datetime,
) -> MigrationProgress:
    """Import at most one bounded batch; retry replays a committed uncheckpointed row.

    The checkpoint advances only after the ledger append commits. If the
    process stops between those operations, exact source-revision dedupe makes
    the resumed row a no-op. Validation runs before `validated` is persisted.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise MigrationRefusal("Opening batch size must be 1 to 100")
    path = Path(checkpoint_path)
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink() or parent.stat().st_mode & 0o022:
        raise MigrationRefusal("Opening checkpoint needs a private parent directory")
    for property_id in sorted({row.stable_property_id for row in plan.rows}):
        ledger.stream_version(actor_id=actor_id, workspace_id=plan.workspace_id,
                              aggregate_id=property_id)
        ledger.require_access(actor_id=actor_id, workspace_id=plan.workspace_id,
                              aggregate_id=property_id, capability="read")
    with _checkpoint_lock(path):
        return _execute_locked(plan, ledger, actor_id=actor_id, path=path,
                               batch_size=batch_size, known_at=known_at)


def _execute_locked(
    plan: OpeningPlan, ledger: TemporalLedger, *, actor_id: str,
    path: Path, batch_size: int, known_at: datetime,
) -> MigrationProgress:
    checkpoint = _read_checkpoint(path, plan)
    if checkpoint is None:
        _write_checkpoint(path, plan, 0, False)
        next_index, validated = 0, False
    else:
        next_index, validated = checkpoint
    if validated:
        reconcile_opening(plan, ledger, actor_id=actor_id, known_at=known_at)
        return MigrationProgress(plan.snapshot_sha256, next_index, len(plan.rows), True)
    stop = min(next_index + batch_size, len(plan.rows))
    for index in range(next_index, stop):
        row = plan.rows[index]
        ledger.append(
            actor_id=actor_id, workspace_id=plan.workspace_id,
            aggregate_id=row.stable_property_id,
            expected_version=ledger.stream_version(
                actor_id=actor_id, workspace_id=plan.workspace_id,
                aggregate_id=row.stable_property_id,
            ),
            key=_event_key(plan, row), value=_payload(plan, row),
            valid_from=plan.as_of, valid_to=plan.as_of + timedelta(days=1),
            source_system=plan.source_system,
            source_record_id=_source_record_id(plan, row),
            source_revision=row.revision,
        )
        _write_checkpoint(path, plan, index + 1, False)
    completed = stop == len(plan.rows)
    if completed:
        reconcile_opening(plan, ledger, actor_id=actor_id, known_at=known_at)
        _write_checkpoint(path, plan, stop, True)
    return MigrationProgress(plan.snapshot_sha256, stop, len(plan.rows), completed)
