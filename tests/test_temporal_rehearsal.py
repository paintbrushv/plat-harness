"""Synthetic mandate cases against the local temporal assertion workflow."""

from datetime import date, datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest

from plat_harness.temporal import TemporalConflict, TemporalLedger, TemporalRefusal


def instant(day: int) -> datetime:
    return datetime(2026, 9, day, 12, tzinfo=timezone.utc)


class Host:
    def __init__(self, path):
        self.now = instant(1)
        self.grants = {
            ("analyst", "alpha", "deal-1", "read"),
            ("analyst", "alpha", "deal-1", "write"),
        }
        self.ledger = TemporalLedger(
            str(path), authorize=lambda actor, workspace, aggregate, action: (
                actor, workspace, aggregate, action
            ) in self.grants, clock=lambda: self.now,
        )

    def accept(self, *, key, value, revision, valid_from=date(2026, 8, 1),
               expected_version=0, supersedes=None, aggregate="deal-1", system="legacy"):
        return self.ledger.append(
            actor_id="analyst", workspace_id="alpha", aggregate_id=aggregate,
            expected_version=expected_version, key=key, value=value,
            valid_from=valid_from, valid_to=None, source_system=system,
            source_record_id=key, source_revision=revision,
            supersedes_event_id=supersedes,
        )


def test_late_expense_preserves_issued_and_as_known_noi(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    host.accept(key="operations_noi_base", value="100000", revision="1")
    host.now = instant(10)
    host.ledger.issue_report(
        actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
        report_id="august-issued-sept10", content={"august_noi": "100000"},
    )
    host.now = instant(20)
    expense = host.accept(key="expense:invoice-1", value="15000", revision="1", expected_version=1)
    assert expense.actor_id == "analyst"
    repeated = host.accept(key="expense:invoice-1", value="15000", revision="1", expected_version=0)
    assert repeated.event_id == expense.event_id
    query = dict(actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                 effective_at=date(2026, 8, 31))
    assert host.ledger.operations_noi_as_known(**query, known_at=instant(10)) == Decimal("100000")
    assert host.ledger.operations_noi_as_known(**query, known_at=instant(20)) == Decimal("85000")
    assert host.ledger.as_issued(actor_id="analyst", workspace_id="alpha",
                                 report_id="august-issued-sept10") == {"august_noi": "100000"}
    with pytest.raises(Exception):
        host.ledger.issue_report(
            actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
            report_id="august-issued-sept10", content={"august_noi": "85000"},
        )


def test_future_lease_is_known_preleasing_but_not_september_physical_occupancy(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    original = host.accept(key="unit:101:physical", value="vacant", revision="1",
                           valid_from=date(2026, 9, 1))
    host.now = instant(5)
    host.accept(key="unit:101:prelease", value={"commences_on": "2026-10-01"},
                revision="1", valid_from=date(2026, 9, 5), expected_version=1)
    host.now = instant(6)
    host.accept(key="unit:101:physical", value="occupied", revision="2",
                valid_from=date(2026, 10, 1), expected_version=2,
                supersedes=original.event_id)
    query = dict(actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                 known_at=instant(6))
    september = host.ledger.as_known(**query, effective_at=date(2026, 9, 15))
    october = host.ledger.as_known(**query, effective_at=date(2026, 10, 15))
    assert september["unit:101:physical"] == "vacant"
    assert september["unit:101:prelease"] == {"commences_on": "2026-10-01"}
    assert october["unit:101:physical"] == "occupied"


def test_linked_correction_and_version_conflict_preserve_prior_view(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    original = host.accept(key="unit:101:status", value="vacant", revision="1")
    host.now = instant(20)
    correction = host.accept(key="unit:101:status", value="down", revision="2",
                             expected_version=1, supersedes=original.event_id)
    query = dict(actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                 effective_at=date(2026, 8, 15))
    assert host.ledger.as_known(**query, known_at=instant(10))["unit:101:status"] == "vacant"
    assert host.ledger.as_known(**query, known_at=instant(20))["unit:101:status"] == "down"
    assert correction.stream_version == 2
    with pytest.raises(TemporalConflict):
        host.accept(key="budget:2026", value="999", revision="1", expected_version=1)
    with pytest.raises(TemporalConflict):
        host.accept(key="unit:101:status", value="occupied", revision="2", expected_version=2)


def test_current_authorization_controls_historical_reads(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    host.accept(key="operations_noi_base", value="100000", revision="1")
    host.ledger.issue_report(actor_id="analyst", workspace_id="alpha",
                             aggregate_id="deal-1", report_id="issued", content={"noi": "100000"})
    query = dict(actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                 effective_at=date(2026, 8, 1), known_at=instant(10))
    assert host.ledger.as_known(**query)["operations_noi_base"] == "100000"
    host.grants.remove(("analyst", "alpha", "deal-1", "read"))
    with pytest.raises(TemporalRefusal):
        host.ledger.as_known(**query)
    with pytest.raises(TemporalRefusal):
        host.ledger.as_issued(actor_id="analyst", workspace_id="alpha", report_id="issued")
    with pytest.raises(TemporalRefusal):
        host.ledger.as_known(actor_id="analyst", workspace_id="other",
                             aggregate_id="deal-1", effective_at=date(2026, 8, 1),
                             known_at=instant(10))
    host.grants.add(("analyst", "alpha", "deal-1", "read"))
    with pytest.raises(TemporalRefusal):
        host.ledger.as_known(actor_id="analyst", workspace_id="alpha",
                             aggregate_id="deal-2", effective_at=date(2026, 8, 1),
                             known_at=instant(10))
    host.grants.add(("analyst", "alpha", "deal-2", "write"))
    host.ledger.issue_report(actor_id="analyst", workspace_id="alpha",
                             aggregate_id="deal-2", report_id="other-deal",
                             content={"noi": "200000"})
    with pytest.raises(TemporalRefusal):
        host.ledger.as_issued(actor_id="analyst", workspace_id="alpha",
                              report_id="other-deal")


def test_original_thesis_to_later_actual_keeps_frozen_economics(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    host.ledger.issue_original_thesis(
        actor_id="analyst", workspace_id="alpha", report_id="original",
        deal_id="deal-1", purchase_price="10000000", year_2_unlevered_noi="1000000",
        capex="2000000", present_as_bid=False,
    )
    host.accept(key="operations_noi_base", value="900000", revision="1")
    record = host.ledger.thesis_to_actual(
        actor_id="analyst", workspace_id="alpha", report_id="original",
        effective_at=date(2026, 8, 1), known_at=instant(10),
    )
    assert record.thesis.year_2_unlevered_yield_on_cost == Decimal("1000000") / Decimal("12000000")
    assert record.thesis.present_as_bid is False
    assert record.operations_actual_noi == Decimal("900000")


def test_unsupported_event_version_refuses_historical_read(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    event = host.accept(key="operations_noi_base", value="100000", revision="1")
    host.ledger._db.execute("UPDATE assertions SET schema_version='unknown/99' WHERE event_id=?", (event.event_id,))
    with pytest.raises(TemporalRefusal, match="unsupported event schema"):
        host.ledger.as_known(actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                             effective_at=date(2026, 8, 1), known_at=instant(10))


def test_source_identity_scopes_entity_and_equal_amount_is_not_a_duplicate(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    first = host.accept(key="expense:invoice-1", value="15000", revision="1", system="old-pm")
    second = host.accept(key="expense:invoice-2", value="15000", revision="1",
                         expected_version=1, system="new-pm")
    assert first.event_id != second.event_id
    assert len(host.ledger.as_known(actor_id="analyst", workspace_id="alpha",
                                    aggregate_id="deal-1", effective_at=date(2026, 8, 31),
                                    known_at=instant(10))) == 2
    host.grants.add(("analyst", "alpha", "deal-2", "write"))
    other = host.accept(key="expense:invoice-1", value="15000", revision="1",
                        aggregate="deal-2", system="old-pm")
    assert other.event_id != first.event_id


def test_date_only_and_offset_clock_do_not_invent_midnight(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    host.now = datetime(2026, 9, 5, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
    event = host.accept(key="lease:101", value={"signed_on": "2026-09-05"},
                        revision="1", valid_from=date(2026, 10, 1))
    assert event.valid_from == date(2026, 10, 1)
    assert event.recorded_at == datetime(2026, 9, 5, 13, 30, tzinfo=timezone.utc)
    with pytest.raises(TemporalRefusal, match="remain a date"):
        host.accept(key="lease:102", value="signed", revision="1", expected_version=1,
                    valid_from=datetime(2026, 10, 1, tzinfo=timezone.utc))
    with pytest.raises(TemporalRefusal, match="timezone-aware"):
        host.ledger.as_known(actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                             effective_at=date(2026, 10, 1), known_at=datetime(2026, 9, 5, 13, 30))


def test_reopen_preserves_records_and_refuses_unsupported_store_version(tmp_path):
    path = tmp_path / "temporal.sqlite"
    host = Host(path)
    host.accept(key="operations_noi_base", value="100000", revision="1")
    host.ledger.close()
    reopened = TemporalLedger(
        str(path), authorize=lambda actor, workspace, aggregate, capability: (
            actor, workspace, aggregate, capability
        ) in host.grants,
    )
    assert reopened.as_known(actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                             effective_at=date(2026, 8, 1), known_at=instant(10)) == {
                                 "operations_noi_base": "100000"
                             }
    reopened.close()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(TemporalRefusal, match="Unsupported temporal store schema"):
        TemporalLedger(str(path), authorize=lambda *_: True)


def test_issued_report_integrity_check_refuses_modified_content(tmp_path):
    host = Host(tmp_path / "temporal.sqlite")
    host.ledger.issue_report(actor_id="analyst", workspace_id="alpha",
                             aggregate_id="deal-1", report_id="issued", content={"noi": "100000"})
    host.ledger._db.execute("UPDATE issued_reports SET content_json=? WHERE report_id='issued'",
                            ('{"noi":"85000"}',))
    with pytest.raises(TemporalRefusal, match="integrity"):
        host.ledger.as_issued(actor_id="analyst", workspace_id="alpha", report_id="issued")


def test_competing_writers_cannot_both_accept_the_same_stream_version(tmp_path):
    path = tmp_path / "temporal.sqlite"
    host = Host(path)
    host.ledger.close()
    barrier = Barrier(2)

    def submit(source):
        ledger = TemporalLedger(str(path), authorize=lambda *_: True,
                                clock=lambda: instant(1))
        try:
            barrier.wait(timeout=5)
            try:
                return ledger.append(
                    actor_id="analyst", workspace_id="alpha", aggregate_id="deal-1",
                    expected_version=0, key=f"expense:{source}", value="15000",
                    valid_from=date(2026, 8, 1), valid_to=None,
                    source_system="pm", source_record_id=source, source_revision="1",
                )
            except TemporalConflict as exc:
                return exc
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(submit, ("a", "b")))
    assert sum(not isinstance(result, Exception) for result in outcomes) == 1
    assert sum(isinstance(result, TemporalConflict) for result in outcomes) == 1
