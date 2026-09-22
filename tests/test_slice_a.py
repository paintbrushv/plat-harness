"""Synthetic backends. Gold is fabricated SQLite/CSV; NullModel does not invent it."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from plat_harness.cli import main
from plat_harness.errors import OCCUPANCY_COUNTS_REQUIRED, UNCERTIFIED_METRIC, HarnessError
from plat_harness.tools.certified_metric import get_certified_metric

_PROPERTY_ID = "example_property"
_PROPERTY_NAME = "example_property"


def _make_ops_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE properties (id TEXT, name TEXT, market TEXT, unit_count INTEGER, owner_entity TEXT, property_manager TEXT, created_at TEXT);
        CREATE TABLE rent_roll_snapshots (
            id TEXT, property_id TEXT, as_of_date TEXT, occupied_units INTEGER, vacant_units INTEGER,
            leased_units INTEGER, notice_units INTEGER, down_units INTEGER, market_rent_total REAL,
            in_place_rent_total REAL, source_file TEXT, source_row INTEGER, created_at TEXT
        );
        CREATE TABLE periods (id TEXT, year INTEGER, month INTEGER, label TEXT);
        CREATE TABLE gl_actuals (
            id TEXT, property_id TEXT, period_id TEXT, account_code TEXT, account_name TEXT,
            category TEXT, amount REAL, source_file TEXT, source_row INTEGER, created_at TEXT
        );
        CREATE TABLE account_mappings (
            id TEXT, source_system TEXT, property_scope TEXT, account_code TEXT, account_name TEXT,
            noi_category TEXT, confidence_score REAL, status TEXT, created_at TEXT, updated_at TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO properties (id, name, unit_count) VALUES (?, ?, 80)",
        (_PROPERTY_ID, _PROPERTY_NAME),
    )
    con.execute(
        """
        INSERT INTO rent_roll_snapshots
        (id, property_id, as_of_date, occupied_units, vacant_units, leased_units, notice_units, down_units, source_file, source_row, created_at)
        VALUES ('snap1', ?, '2026-06-30', 72, 6, 72, 0, 2, 'rent_roll.csv', 1, '2026-07-01T00:00:00+00:00')
        """,
        (_PROPERTY_ID,),
    )
    window = [
        (2025, 6),
        (2025, 7),
        (2025, 8),
        (2025, 9),
        (2025, 10),
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
        (2026, 3),
        (2026, 4),
        (2026, 5),
    ]
    for year, month in window:
        label = f"{year:04d}-{month:02d}"
        pid = f"p-{label}"
        con.execute(
            "INSERT INTO periods (id, year, month, label) VALUES (?, ?, ?, ?)",
            (pid, year, month, label),
        )
        con.execute(
            """
            INSERT INTO gl_actuals
            (id, property_id, period_id, account_code, account_name, category, amount, source_file, source_row)
            VALUES (?, ?, ?, '52100', 'Plumbing Repairs', 'Repairs & Maintenance', 100.00, 'budget_comparison.csv', 10)
            """,
            (f"gl-{label}-a", _PROPERTY_ID, pid),
        )
        con.execute(
            """
            INSERT INTO gl_actuals
            (id, property_id, period_id, account_code, account_name, category, amount, source_file, source_row)
            VALUES (?, ?, ?, '52100', 'Plumbing Repairs', 'Repairs & Maintenance', 100.00, 'budget_comparison.csv', 10)
            """,
            (f"gl-{label}-b", _PROPERTY_ID, pid),
        )
    con.execute(
        """
        INSERT INTO account_mappings
        (id, source_system, property_scope, account_code, account_name, noi_category, status)
        VALUES ('m1', 'csv', 'example_property', '52100', 'Plumbing Repairs', 'Repairs & Maintenance', 'approved')
        """
    )
    con.commit()
    con.close()
    return path


@pytest.fixture
def ops_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = _make_ops_db(tmp_path / "ops.db")
    monkeypatch.setenv("PLAT_HARNESS_BOXSCORE_DB", str(db_path))
    return db_path


def test_occupancy_without_counts_or_backend_still_refuses(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric(
            "physical_occupancy",
            context="ops_actuals",
            asset_or_deal_id="example_property",
            glossary=glossary,
        )
    assert caught.value.code == OCCUPANCY_COUNTS_REQUIRED


def test_occupancy_from_ops_db_emits_four_counts(ops_db: Path, glossary) -> None:
    result = get_certified_metric(
        "physical_occupancy",
        context="ops_actuals",
        asset_or_deal_id="example_property",
        glossary=glossary,
    )
    assert result["occupied"] == 72
    assert result["vacant"] == 6
    assert result["down"] == 2
    assert result["denominator"] == 80
    assert result["as_of"] == "2026-06-30"
    assert result["source"]


def test_t12_rm_dedupes_and_cites_codes(ops_db: Path, glossary) -> None:
    result = get_certified_metric(
        "t12_repairs_and_maintenance",
        context="ops_actuals",
        asset_or_deal_id="example_property",
        glossary=glossary,
    )
    assert Decimal(result["value"]) == Decimal("1200.00")
    assert "52100" in result["account_codes"]
    assert result["source"]


def test_scoreboard_empty_without_backend(capsys) -> None:
    code = main(["scoreboard", "--asset", "example_property"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "uncertified_empty"
    assert payload["metrics"] == {}


def test_scoreboard_occupancy_from_backend(ops_db: Path, capsys) -> None:
    code = main(["scoreboard", "--asset", "example_property"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "certified_partial"
    occ = payload["metrics"]["physical_occupancy"]
    assert occ["occupied"] == 72
    assert occ["denominator"] == 80


def test_synthetic_coc_refuses_without_engine_extra(glossary) -> None:
    with pytest.raises(HarnessError) as caught:
        get_certified_metric(
            "cash_on_cash",
            context="uw_proforma",
            asset_or_deal_id="example_garden_style",
            glossary=glossary,
        )
    assert caught.value.code == UNCERTIFIED_METRIC
    assert "invent" in caught.value.message.lower() or "engine" in caught.value.message.lower()
