"""Synthetic opening-state migration and exact scoped reconciliation."""

from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from threading import Barrier

import pytest

import plat_harness.opening_migration as migration
from plat_harness.opening_migration import MigrationRefusal
from plat_harness.temporal import TemporalLedger, TemporalRefusal


def snapshot():
    return {
        "schema_version": migration.SNAPSHOT_VERSION,
        "workspace_id": "alpha",
        "source_system": "legacy-pm",
        "source_account_id": "account-7",
        "as_of": "2026-09-01",
        "rows": [
            {"sequence": 1, "source_property_id": "legacy-A", "source_record_id": "rev-1",
             "revision": "1", "period": "2026-08", "account": "revenue",
             "unit_category": "residential", "amount": "200000.00", "deleted": False},
            {"sequence": 2, "source_property_id": "legacy-A", "source_record_id": "exp-1",
             "revision": "1", "period": "2026-08", "account": "expense",
             "unit_category": "residential", "amount": "-100000.00", "deleted": False},
            {"sequence": 3, "source_property_id": "legacy-B", "source_record_id": "rev-1",
             "revision": "1", "period": "2026-08", "account": "revenue",
             "unit_category": "commercial", "amount": "30000.00", "deleted": False},
            {"sequence": 4, "source_property_id": "legacy-B", "source_record_id": "void-1",
             "revision": "1", "period": "2026-08", "account": "expense",
             "unit_category": "commercial", "amount": None, "deleted": True},
        ],
    }


def baseline(value=None, mapping=None):
    value = snapshot() if value is None else value
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    mapping = {"legacy-A": "prop-101", "legacy-B": "prop-202"} if mapping is None else mapping
    return migration.plan_opening_snapshot(
        raw, expected_sha256=sha256(raw).hexdigest(), identity_map=mapping,
    )


def ledger(path: Path):
    return TemporalLedger(
        str(path), authorize=lambda actor, workspace, aggregate, action: (
            actor == "operator" and workspace == "alpha" and
            aggregate in {"prop-101", "prop-202"} and action in {"read", "write"}
        ),
        clock=lambda: datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
    )


KNOWN = datetime(2026, 9, 20, 13, tzinfo=timezone.utc)


def test_dry_run_is_pure_then_bounded_resume_reconciles_independent_goldens(tmp_path):
    plan = baseline()
    assert not list(tmp_path.iterdir())
    store = ledger(tmp_path / "opening.sqlite")
    checkpoint = tmp_path / "checkpoint.json"
    first = migration.execute_opening_batch(plan, store, actor_id="operator",
                                            checkpoint_path=checkpoint, batch_size=2,
                                            known_at=KNOWN)
    assert (first.next_index, first.total_rows, first.validated) == (2, 4, False)
    second = migration.execute_opening_batch(plan, store, actor_id="operator",
                                             checkpoint_path=checkpoint, batch_size=2,
                                             known_at=KNOWN)
    assert (second.next_index, second.validated) == (4, True)
    totals = migration.read_opening_baseline(
        plan, store, actor_id="operator", effective_at=date(2026, 9, 1), known_at=KNOWN,
    )
    # Independent expected row groups: no portfolio-level tolerance hides a scoped delta.
    assert totals == {
        '["prop-101","2026-08","expense","residential"]': "-100000.00",
        '["prop-101","2026-08","revenue","residential"]': "200000.00",
        '["prop-202","2026-08","revenue","commercial"]': "30000.00",
    }
    assert checkpoint.stat().st_mode & 0o777 == 0o600
    version = store.stream_version(actor_id="operator", workspace_id="alpha",
                                   aggregate_id="prop-101")
    repeated = migration.execute_opening_batch(plan, store, actor_id="operator",
                                               checkpoint_path=checkpoint, batch_size=2,
                                               known_at=KNOWN)
    assert repeated.validated
    assert store.stream_version(actor_id="operator", workspace_id="alpha",
                                aggregate_id="prop-101") == version
    checkpoint.unlink()
    recovered = migration.execute_opening_batch(plan, store, actor_id="operator",
                                                checkpoint_path=checkpoint, batch_size=4,
                                                known_at=KNOWN)
    assert recovered.validated
    assert store.stream_version(actor_id="operator", workspace_id="alpha",
                                aggregate_id="prop-101") == version
    with pytest.raises(MigrationRefusal, match="other effective dates"):
        migration.read_opening_baseline(plan, store, actor_id="operator",
                                        effective_at=date(2026, 8, 31), known_at=KNOWN)
    later = store.as_known(actor_id="operator", workspace_id="alpha",
                           aggregate_id="prop-101", effective_at=date(2026, 9, 2),
                           known_at=KNOWN)
    assert not any(key.startswith("opening:") for key in later)
    with pytest.raises(MigrationRefusal, match="row-level"):
        migration.read_opening_baseline(
            plan, store, actor_id="operator", effective_at=date(2026, 9, 1),
            known_at=datetime(2026, 9, 10, 12, tzinfo=timezone.utc),
        )


def test_commit_before_checkpoint_interruption_resumes_without_duplicate(tmp_path, monkeypatch):
    plan = baseline()
    store = ledger(tmp_path / "opening.sqlite")
    checkpoint = tmp_path / "checkpoint.json"
    real_write = migration._write_checkpoint

    def fail_after_first_append(path, planned, next_index, validated):
        if next_index == 1:
            raise RuntimeError("simulated process interruption before checkpoint")
        return real_write(path, planned, next_index, validated)

    monkeypatch.setattr(migration, "_write_checkpoint", fail_after_first_append)
    with pytest.raises(RuntimeError, match="interruption"):
        migration.execute_opening_batch(plan, store, actor_id="operator",
                                        checkpoint_path=checkpoint, batch_size=2,
                                        known_at=KNOWN)
    assert store.stream_version(actor_id="operator", workspace_id="alpha",
                                aggregate_id="prop-101") == 1
    monkeypatch.setattr(migration, "_write_checkpoint", real_write)
    assert json.loads(checkpoint.read_text())["next_index"] == 0
    migration.execute_opening_batch(plan, store, actor_id="operator",
                                    checkpoint_path=checkpoint, batch_size=4,
                                    known_at=KNOWN)
    assert store.stream_version(actor_id="operator", workspace_id="alpha",
                                aggregate_id="prop-101") == 2
    assert migration.reconcile_opening(plan, store, actor_id="operator", known_at=KNOWN)


def test_hash_mapping_gap_tombstone_and_money_refusals_leave_no_target(tmp_path):
    original = snapshot()
    raw = json.dumps(original).encode()
    with pytest.raises(MigrationRefusal, match="hash differs"):
        migration.plan_opening_snapshot(raw, expected_sha256="0" * 64,
                                        identity_map={"legacy-A": "prop-101"})
    with pytest.raises(MigrationRefusal, match="stable-ID mapping"):
        baseline(mapping={"legacy-A": "prop-101"})
    with pytest.raises(MigrationRefusal, match="identity merge"):
        baseline(mapping={"legacy-A": "prop-101", "legacy-B": "prop-101"})
    gapped = snapshot()
    gapped["rows"][1]["sequence"] = 9
    with pytest.raises(MigrationRefusal, match="gap"):
        baseline(gapped)
    invalid_tombstone = snapshot()
    invalid_tombstone["rows"][3]["amount"] = "0.00"
    with pytest.raises(MigrationRefusal, match="Tombstone"):
        baseline(invalid_tombstone)
    invalid_money = snapshot()
    invalid_money["rows"][0]["amount"] = "200000.001"
    with pytest.raises(MigrationRefusal, match="USD cents"):
        baseline(invalid_money)
    with pytest.raises(MigrationRefusal, match="Duplicate JSON field"):
        duplicate = b'{"schema_version":"x","schema_version":"y"}'
        migration.plan_opening_snapshot(duplicate, expected_sha256=sha256(duplicate).hexdigest(),
                                        identity_map={"legacy-A": "prop-101"})
    assert not list(tmp_path.iterdir())


def test_changed_mapping_refuses_resume_and_row_delta_is_detected(tmp_path):
    plan = baseline()
    store = ledger(tmp_path / "opening.sqlite")
    checkpoint = tmp_path / "checkpoint.json"
    migration.execute_opening_batch(plan, store, actor_id="operator",
                                    checkpoint_path=checkpoint, batch_size=2, known_at=KNOWN)
    changed = baseline(mapping={"legacy-A": "prop-202", "legacy-B": "prop-101"})
    with pytest.raises(MigrationRefusal, match="checkpoint does not match"):
        migration.execute_opening_batch(changed, store, actor_id="operator",
                                        checkpoint_path=checkpoint, batch_size=2,
                                        known_at=KNOWN)
    store._db.execute("UPDATE assertions SET value_json=? WHERE event_key LIKE 'opening:%:00000001'",
                      ('{"amount":"999999.00"}',))
    with pytest.raises(MigrationRefusal, match="row-level"):
        migration.reconcile_opening(plan, store, actor_id="operator", known_at=KNOWN)


def test_current_resource_grant_is_required_for_import_and_readback(tmp_path):
    plan = baseline()
    store = ledger(tmp_path / "opening.sqlite")
    checkpoint = tmp_path / "checkpoint.json"
    with pytest.raises(TemporalRefusal, match="authorization"):
        migration.execute_opening_batch(plan, store, actor_id="former-employee",
                                        checkpoint_path=checkpoint, batch_size=2,
                                        known_at=KNOWN)
    assert not checkpoint.exists()
    with pytest.raises(TemporalRefusal, match="authorization"):
        migration.read_opening_baseline(plan, store, actor_id="former-employee",
                                        effective_at=date(2026, 9, 1), known_at=KNOWN)


def test_two_resume_workers_serialize_checkpoint_progress(tmp_path):
    plan = baseline()
    database = tmp_path / "opening.sqlite"
    initial = ledger(database)
    initial.close()
    checkpoint = tmp_path / "checkpoint.json"
    barrier = Barrier(2)

    def resume(_):
        store = ledger(database)
        try:
            barrier.wait(timeout=5)
            return migration.execute_opening_batch(
                plan, store, actor_id="operator", checkpoint_path=checkpoint,
                batch_size=2, known_at=KNOWN,
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        progress = list(workers.map(resume, (1, 2)))
    assert sorted(item.next_index for item in progress) == [2, 4]
    assert json.loads(checkpoint.read_text())["validated"] is True
    verified = ledger(database)
    assert migration.reconcile_opening(plan, verified, actor_id="operator", known_at=KNOWN)
