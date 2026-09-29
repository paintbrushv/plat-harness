"""Public synthetic September handoff and closed-period correction rehearsal.

The source-date policy and financial reconciliation below are test oracles,
not production adapters or assertions about real CCAR/TC books.
"""

from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from plat_harness.temporal import TemporalConflict, TemporalLedger, TemporalRefusal


FIXTURE = Path(__file__).resolve().parents[1] / "samples/operations/synthetic_september_handoff.json"
WORKSPACE = "synthetic-september"
CLOSE = date(2026, 9, 30)


def at(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time(12), timezone.utc)


def money_map(values: dict[str, str]) -> dict[str, Decimal]:
    return {account: Decimal(amount) for account, amount in values.items()}


def source_for(property_case: dict, row: dict) -> str:
    """Synthetic host authority by effective date, independent of record time."""
    effective = date.fromisoformat(row["effective_on"])
    cutover = date.fromisoformat(property_case["cutover_on"])
    required_feed = "before" if effective < cutover else "after"
    if row["feed"] != required_feed:
        raise TemporalRefusal("Feed is not authoritative for this property/effective date")
    if effective.month != 9 or effective.year != 2026:
        raise TemporalRefusal("Synthetic packet is outside September 2026")
    return property_case["sources"][row["feed"]]["source_system"]


def post(ledger: TemporalLedger, property_case: dict, row: dict, *,
         superseded: dict[int, str] | None = None, retry: bool = False):
    source = source_for(property_case, row)
    postings = row["postings"]
    if sum((Decimal(amount) for amount in postings.values()), Decimal(0)) != 0:
        raise TemporalRefusal("Synthetic GL transaction does not balance")
    property_id = property_case["property_id"]
    accepted = {}
    for index, (account, amount) in enumerate(postings.items()):
        event = ledger.append(
            actor_id="importer", workspace_id=WORKSPACE, aggregate_id=property_id,
            expected_version=0 if retry else ledger.stream_version(
                actor_id="importer", workspace_id=WORKSPACE, aggregate_id=property_id,
            ),
            key=f"gl:{source}:{row['record_id']}:{index}",
            value={"account": account, "amount": amount},
            valid_from=date.fromisoformat(row["effective_on"]), valid_to=None,
            source_system=source, source_record_id=f"{row['record_id']}:{index}",
            source_revision=row["revision"],
            supersedes_event_id=None if superseded is None else superseded[index],
        )
        accepted[index] = event.event_id
    return accepted


def closing(ledger: TemporalLedger, property_case: dict, *, known_at: datetime,
            actor: str = "importer") -> tuple[dict[str, Decimal], Decimal, int]:
    facts = ledger.as_known(
        actor_id=actor, workspace_id=WORKSPACE,
        aggregate_id=property_case["property_id"], effective_at=CLOSE,
        known_at=known_at,
    )
    trial_balance = defaultdict(Decimal, money_map(property_case["opening_trial_balance"]))
    for key, posting in facts.items():
        assert key.startswith("gl:")
        trial_balance[posting["account"]] += Decimal(posting["amount"])
    assert sum(trial_balance.values(), Decimal(0)) == 0
    noi = -trial_balance["rental_revenue"] - trial_balance["operating_expense"]
    return dict(trial_balance), noi, len(facts)


def test_synthetic_handoff_closed_period_correction_retry_restore_and_revocation(tmp_path):
    scenario = json.loads(FIXTURE.read_text())
    assert scenario["synthetic"] is True
    tc, ccar = scenario["properties"]
    assert (tc["label"], tc["cutover_on"], tc["sources"]["after"]["platform"]) == (
        "TC", "2026-09-23", "ResMan"
    )
    assert (ccar["label"], ccar["cutover_on"], ccar["sources"]["after"]["platform"]) == (
        "CCAR", "2026-09-25", "Yardi"
    )
    assert ccar["sources"]["before"]["source_system"] != ccar["sources"]["after"]["source_system"]

    grants = set()
    for case in (tc, ccar):
        for capability in ("read", "write"):
            grants.add(("importer", WORKSPACE, case["property_id"], capability))
        grants.add((f"outgoing-{case['label'].lower()}", WORKSPACE, case["property_id"], "read"))
        grants.add((f"incoming-{case['label'].lower()}", WORKSPACE, case["property_id"], "read"))
    authorize = lambda actor, workspace, aggregate, capability: (
        actor, workspace, aggregate, capability
    ) in grants
    clock = [at("2026-09-01")]
    path = tmp_path / "synthetic-temporal.sqlite"
    ledger = TemporalLedger(str(path), authorize=authorize, clock=lambda: clock[0])
    originals = {}
    try:
        for case in (tc, ccar):
            assert sum(money_map(case["opening_trial_balance"]).values(), Decimal(0)) == 0
            for row in case["transactions"]:
                clock[0] = at(row["effective_on"])
                originals[(case["property_id"], row["feed"], row["record_id"])] = post(
                    ledger, case, row,
                )

        # Both feeds reuse IDs; TC even has two equal $5,000 deposit receipts.
        tc_before = tc["transactions"][2]
        tc_after = tc["transactions"][4]
        assert tc_before["record_id"] == tc_after["record_id"] == "TX-003"
        assert tc_before["postings"] == tc_after["postings"]
        assert originals[(tc["property_id"], "before", "TX-003")] != originals[
            (tc["property_id"], "after", "TX-003")
        ]
        prior_version = ledger.stream_version(
            actor_id="importer", workspace_id=WORKSPACE, aggregate_id=tc["property_id"],
        )
        assert post(ledger, tc, tc_after, retry=True) == originals[
            (tc["property_id"], "after", "TX-003")
        ]
        assert ledger.stream_version(
            actor_id="importer", workspace_id=WORKSPACE, aggregate_id=tc["property_id"],
        ) == prior_version
        with pytest.raises(TemporalConflict, match="different content"):
            ledger.append(
                actor_id="importer", workspace_id=WORKSPACE, aggregate_id=tc["property_id"],
                expected_version=0, key=f"gl:{source_for(tc, tc_after)}:TX-003:0",
                value={"account": "cash", "amount": "9999.00"},
                valid_from=date(2026, 9, 23), valid_to=None,
                source_system=source_for(tc, tc_after), source_record_id="TX-003:0",
                source_revision="1",
            )

        # A new feed cannot claim a pre-handoff date; an old feed cannot own the cutover day.
        for case in (tc, ccar):
            boundary = date.fromisoformat(case["cutover_on"])
            for feed, effective in (("before", boundary), ("after", boundary.replace(day=boundary.day - 1))):
                with pytest.raises(TemporalRefusal, match="not authoritative"):
                    post(ledger, case, {"feed": feed, "record_id": "BAD",
                                        "effective_on": effective.isoformat(),
                                        "revision": "1", "postings": {"cash": "1", "deposits": "-1"}})

        clock[0] = at(scenario["issued_on"])
        for case in (tc, ccar):
            tb, noi, count = closing(ledger, case, known_at=clock[0])
            assert tb == money_map(case["expected_issued_trial_balance"])
            assert noi == Decimal(case["expected_issued_noi"])
            assert count == 2 * len(case["transactions"])
            ledger.issue_report(
                actor_id="importer", workspace_id=WORKSPACE,
                aggregate_id=case["property_id"], report_id=f"synthetic-close-{case['label']}",
                content={"synthetic": True, "period": scenario["period"],
                         "property_id": case["property_id"], "noi": str(noi),
                         "budget_noi": case["budget_noi"],
                         "variance": str(noi - Decimal(case["budget_noi"]))},
            )

        clock[0] = at(scenario["correction_known_on"])
        corrected = post(
            ledger, tc, tc["correction"],
            superseded=originals[(tc["property_id"], "before", "TX-002")],
        )
        assert len(corrected) == 2
        tc_before_tb, tc_before_noi, _ = closing(ledger, tc, known_at=at("2026-10-01"))
        tc_after_tb, tc_after_noi, tc_count = closing(ledger, tc, known_at=clock[0])
        assert tc_before_tb == money_map(tc["expected_issued_trial_balance"])
        assert tc_before_noi == Decimal("45000.00")
        assert tc_after_tb == money_map(tc["expected_restated_trial_balance"])
        assert tc_after_noi == Decimal("44500.00")
        assert tc_count == 2 * len(tc["transactions"])
        assert tc_before_noi - tc_after_noi == Decimal("500.00")
        assert ledger.as_issued(
            actor_id="importer", workspace_id=WORKSPACE, report_id="synthetic-close-TC",
        )["noi"] == "45000.00"
        ccar_tb, ccar_noi, _ = closing(ledger, ccar, known_at=clock[0])
        assert ccar_tb == money_map(ccar["expected_restated_trial_balance"])
        assert ccar_noi == Decimal(ccar["expected_restated_noi"])
        assert ledger.as_issued(
            actor_id="importer", workspace_id=WORKSPACE, report_id="synthetic-close-CCAR",
        )["noi"] == "44000.00"
        assert ledger.as_known(
            actor_id="importer", workspace_id=WORKSPACE, aggregate_id=tc["property_id"],
            effective_at=date(2026, 9, 22), known_at=clock[0],
        ).keys().isdisjoint({f"gl:{source_for(tc, tc_after)}:TX-003:0"})

        # The host's current grants, not old event/report access, control historical reads.
        for case in (tc, ccar):
            grants.remove((f"outgoing-{case['label'].lower()}", WORKSPACE,
                           case["property_id"], "read"))
            with pytest.raises(TemporalRefusal, match="authorization refused"):
                closing(ledger, case, known_at=at("2026-10-01"),
                        actor=f"outgoing-{case['label'].lower()}")
            with pytest.raises(TemporalRefusal, match="unavailable or unauthorized"):
                ledger.as_issued(
                    actor_id=f"outgoing-{case['label'].lower()}", workspace_id=WORKSPACE,
                    report_id=f"synthetic-close-{case['label']}",
                )
        with pytest.raises(TemporalRefusal, match="authorization refused"):
            closing(ledger, ccar, known_at=clock[0], actor="incoming-tc")
    finally:
        ledger.close()

    restored_path = tmp_path / "restored-synthetic-temporal.sqlite"
    with sqlite3.connect(path) as source, sqlite3.connect(restored_path) as target:
        source.backup(target)
    restored = TemporalLedger(str(restored_path), authorize=authorize, clock=lambda: clock[0])
    try:
        assert closing(restored, tc, known_at=clock[0])[:2] == (
            money_map(tc["expected_restated_trial_balance"]), Decimal("44500.00"),
        )
        assert restored.as_issued(
            actor_id="importer", workspace_id=WORKSPACE, report_id="synthetic-close-TC",
        )["noi"] == "45000.00"
        with pytest.raises(TemporalRefusal, match="authorization refused"):
            closing(restored, tc, known_at=at("2026-10-01"), actor="outgoing-tc")
        with pytest.raises(TemporalRefusal, match="unavailable or unauthorized"):
            restored.as_issued(
                actor_id="outgoing-tc", workspace_id=WORKSPACE,
                report_id="synthetic-close-TC",
            )
        assert post(restored, tc, tc["correction"],
                    superseded=originals[(tc["property_id"], "before", "TX-002")],
                    retry=True) == corrected
    finally:
        restored.close()
