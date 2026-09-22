"""Thin SQL/csv adapters over an ops SQLite DB and Standardized exports.

Does not rewrite variance/T12 engines. Does not print resident names.
Asset folders are `{OPS_ROOT}/<asset_id>/Standardized/` — no private aliases.
"""

from __future__ import annotations

import csv
import sqlite3
from calendar import month_abbr
from pathlib import Path
from typing import Any, Iterable

from plat_harness.adapters import paths as paths
from plat_harness.errors import HarnessError, IMPLICIT_ZERO_FORBIDDEN, NOT_FOUND
from plat_harness.occupancy import OccupancyCounts

_VACANCY_MARKERS = frozenset({"vacant", "vacancy", "available", "model", "admin", "down"})
_ABBR_TO_MONTH = {name: idx for idx, name in enumerate(month_abbr) if name}


def load_occupancy(asset_id: str | None) -> dict[str, Any] | None:
    """Return four-counts + as-of from a snapshot table, else Standardized CSV.

    Returns None when no certified backend is configured (caller still refuses).
    """
    if not asset_id or not str(asset_id).strip():
        return None
    if not paths.ops_backend_configured():
        return None
    key = _normalize_asset(asset_id)
    db_row = _occupancy_from_db(key)
    if db_row is not None:
        return db_row
    csv_row = _occupancy_from_csv(key)
    if csv_row is not None:
        return csv_row
    raise HarnessError(
        NOT_FOUND,
        f"No rent-roll snapshot or Standardized rent_roll.csv for asset '{asset_id}'.",
        details={"asset_id": asset_id},
    )


def load_t12_repairs_and_maintenance(asset_id: str | None, period: str | None = None) -> dict[str, Any]:
    """T12 R&M from gl_actuals (deduped) or budget_comparison.csv.

    Missing feed raises NOT_FOUND — never $0.
    """
    if not asset_id or not str(asset_id).strip():
        raise HarnessError(
            NOT_FOUND,
            "T12 Repairs & Maintenance requires --asset.",
            details={"metric_id": "t12_repairs_and_maintenance"},
        )
    if not paths.ops_backend_configured():
        raise HarnessError(
            NOT_FOUND,
            "No ops backend configured (PLAT_HARNESS_BOXSCORE_DB / PLAT_HARNESS_OPS_ROOT).",
            details={"metric_id": "t12_repairs_and_maintenance"},
        )
    key = _normalize_asset(asset_id)
    db_payload = _t12_rm_from_db(key, period)
    if db_payload is not None:
        return db_payload
    csv_payload = _t12_rm_from_budget_comparison(key, period)
    if csv_payload is not None:
        return csv_payload
    raise HarnessError(
        NOT_FOUND,
        f"No T12 Repairs & Maintenance feed for asset '{asset_id}'. Missing feed is not $0.",
        details={"asset_id": asset_id, "metric_id": "t12_repairs_and_maintenance"},
    )


def _normalize_asset(asset_id: str) -> str:
    return str(asset_id).strip().lower().replace(" ", "_")


def _occupancy_from_db(asset_key: str) -> dict[str, Any] | None:
    db_path = paths.boxscore_db()
    if db_path is None:
        return None
    property_id = _property_id(db_path, asset_key)
    if property_id is None:
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            """
            SELECT as_of_date, occupied_units, vacant_units, down_units, source_file
            FROM rent_roll_snapshots
            WHERE property_id = ?
            ORDER BY as_of_date DESC, created_at DESC
            LIMIT 1
            """,
            (property_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    as_of, occupied, vacant, down, source_file = row
    counts = _counts(int(occupied), int(vacant), int(down))
    payload = counts.as_dict()
    payload.update(
        {
            "as_of": as_of,
            "source": [
                {
                    "artifact": str(source_file or db_path),
                    "row": "rent_roll_snapshots.occupied/vacant/down",
                    "period": as_of,
                }
            ],
            "freshness": as_of,
            "backend": "boxscore_db",
        }
    )
    return payload


def _occupancy_from_csv(asset_key: str) -> dict[str, Any] | None:
    csv_path = _rent_roll_csv(asset_key)
    if csv_path is None:
        return None
    occupied = vacant = down_count = 0
    as_of = None
    n_rows = 0
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for rec in reader:
            n_rows += 1
            status = (rec.get("status") or "").strip().lower()
            # resident_name / name may exist on private feeds; never emit them.
            resident = (rec.get("resident") or rec.get("resident_code") or "").strip()
            name = (rec.get("name") or rec.get("resident_name") or "").strip()
            is_down = status == "down" or _is_down_marker(resident) or _is_down_marker(name)
            is_vacant = (
                status in _VACANCY_MARKERS
                or (resident == "" and name == "" and status in {"", "vacant"})
                or _is_vacancy_marker(resident)
                or _is_vacancy_marker(name)
            )
            if is_down:
                down_count += 1
            elif is_vacant or status == "vacant":
                vacant += 1
            else:
                occupied += 1
            for field in ("snapshot_date", "as_of_date"):
                value = (rec.get(field) or "").strip()
                if value:
                    as_of = value
    if n_rows == 0:
        return None
    counts = _counts(occupied, vacant, down_count)
    payload = counts.as_dict()
    payload.update(
        {
            "as_of": as_of,
            "source": [
                {
                    "artifact": str(csv_path),
                    "row": "status occupied/vacant/down (resident_name not emitted)",
                    "period": as_of,
                }
            ],
            "freshness": as_of,
            "backend": "standardized_csv",
        }
    )
    return payload


def _t12_rm_from_db(asset_key: str, period: str | None) -> dict[str, Any] | None:
    db_path = paths.boxscore_db()
    if db_path is None:
        return None
    property_id = _property_id(db_path, asset_key)
    if property_id is None:
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        end_period = _end_period(con, property_id, period)
        if end_period is None:
            return None
        window = period_window(end_period, 12)
        placeholders = ",".join("?" * len(window))
        rows = list(
            con.execute(
                f"""
                SELECT p.label, a.account_code, a.account_name, a.amount, a.source_file, a.source_row
                FROM gl_actuals a
                JOIN periods p ON p.id = a.period_id
                WHERE a.property_id = ?
                  AND a.category = 'Repairs & Maintenance'
                  AND p.label IN ({placeholders})
                GROUP BY p.label, a.account_code, a.account_name, a.source_row, a.amount
                """,
                (property_id, *window),
            )
        )
        maps = list(
            con.execute(
                """
                SELECT account_code, account_name, status
                FROM account_mappings
                WHERE noi_category = 'Repairs & Maintenance'
                  AND (
                    lower(property_scope) = ?
                    OR property_scope = '*'
                    OR property_scope = ''
                    OR property_scope IS NULL
                  )
                """,
                (asset_key,),
            )
        )
    finally:
        con.close()
    if not rows:
        return None
    total = sum(float(row[3]) for row in rows)
    codes = sorted({row[1] for row in rows})
    return _t12_payload(
        total=total,
        end_period=end_period,
        window=window,
        account_codes=codes,
        source_artifact=str(db_path),
        backend="boxscore_db",
        mapping_codes=[row[0] for row in maps],
    )


def _t12_rm_from_budget_comparison(asset_key: str, period: str | None) -> dict[str, Any] | None:
    csv_path = _budget_comparison_csv(asset_key)
    if csv_path is None:
        return None
    codes = _rm_codes_from_derived_mappings(asset_key)
    rows: list[dict[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for rec in reader:
            rows.append(rec)
    if not rows:
        return None
    periods = sorted({_parse_period(r.get("period") or "") for r in rows if r.get("period")})
    periods = [p for p in periods if p and len(p) == 7]
    if not periods:
        return None
    end_period = period if period and len(period) == 7 else periods[-1]
    window = period_window(end_period, 12)
    if not codes:
        return None
    total = 0.0
    used: set[str] = set()
    for rec in rows:
        label = _parse_period(rec.get("period") or "")
        code = (rec.get("account_code") or "").strip()
        if label not in window or code not in codes:
            continue
        amount = _parse_amount(rec.get("ptd_actual") or "0")
        total += amount
        used.add(code)
    if not used:
        return None
    return _t12_payload(
        total=total,
        end_period=end_period,
        window=window,
        account_codes=sorted(used),
        source_artifact=str(csv_path),
        backend="budget_comparison_csv",
        mapping_codes=sorted(codes),
    )


def _t12_payload(
    *,
    total: float,
    end_period: str,
    window: list[str],
    account_codes: list[str],
    source_artifact: str,
    backend: str,
    mapping_codes: Iterable[str],
) -> dict[str, Any]:
    value = f"{total:.2f}"
    return {
        "value": value,
        "unit": "usd",
        "as_of": end_period,
        "period": end_period,
        "t12_window": window,
        "account_codes": list(account_codes),
        "mapped_account_codes": list(mapping_codes),
        "source": [
            {
                "artifact": source_artifact,
                "row": "account_mappings.noi_category=Repairs & Maintenance",
                "period": f"{window[0]}:{window[-1]}" if window else end_period,
            }
        ],
        "freshness": end_period,
        "backend": backend,
        "certification": "CERTIFIED",
    }


def period_window(end_period: str, n: int) -> list[str]:
    year = int(end_period[:4])
    month = int(end_period[5:7])
    end_ordinal = year * 12 + (month - 1)
    out = []
    for offset in range(n - 1, -1, -1):
        ordinal = end_ordinal - offset
        y, m = divmod(ordinal, 12)
        out.append(f"{y:04d}-{m + 1:02d}")
    return out


def _end_period(con: sqlite3.Connection, property_id: str, period: str | None) -> str | None:
    if period:
        return period
    row = con.execute(
        """
        SELECT p.label FROM gl_actuals a
        JOIN periods p ON p.id = a.period_id
        WHERE a.property_id = ?
        ORDER BY p.label DESC
        LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    return row[0] if row else None


def _property_id(db_path: Path, asset_key: str) -> str | None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT id FROM properties WHERE id = ? LIMIT 1",
            (asset_key,),
        ).fetchone()
        if row:
            return str(row[0])
        row = con.execute(
            "SELECT id FROM properties WHERE lower(name) = lower(?) LIMIT 1",
            (asset_key,),
        ).fetchone()
        if row:
            return str(row[0])
        row = con.execute(
            "SELECT id FROM properties WHERE lower(replace(name, ' ', '_')) = ? LIMIT 1",
            (asset_key,),
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        con.close()


def _standardized_csv(asset_key: str, filename: str) -> Path | None:
    root = paths.ops_root()
    if root is None:
        return None
    path = root / asset_key / "Standardized" / filename
    return path if path.is_file() else None


def _rent_roll_csv(asset_key: str) -> Path | None:
    return _standardized_csv(asset_key, "rent_roll.csv")


def _budget_comparison_csv(asset_key: str) -> Path | None:
    return _standardized_csv(asset_key, "budget_comparison.csv")


def _rm_codes_from_derived_mappings(asset_key: str) -> set[str]:
    path = _standardized_csv(asset_key, "account_mappings_derived.csv")
    if path is None:
        return set()
    codes: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for rec in csv.DictReader(handle):
            category = (
                rec.get("reviewed_category")
                or rec.get("current_category")
                or rec.get("suggested_category")
                or rec.get("noi_category")
                or ""
            )
            if category.strip() == "Repairs & Maintenance":
                code = (rec.get("account_code") or "").strip()
                if code:
                    codes.add(code)
    return codes


def _counts(occupied: int, vacant: int, down: int) -> OccupancyCounts:
    denominator = occupied + vacant + down
    if denominator == 0:
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            "Occupancy denominator is zero; refusing an implicit 0% or 100% rate.",
        )
    return OccupancyCounts(occupied=occupied, vacant=vacant, down=down, denominator=denominator)


def _is_vacancy_marker(value: str) -> bool:
    return value.strip().lower() in _VACANCY_MARKERS


def _is_down_marker(value: str) -> bool:
    return value.strip().lower() == "down"


def _parse_period(value: str) -> str:
    text = value.strip()
    if len(text) >= 7 and text[4] == "-":
        return text[:7]
    parts = text.replace(",", " ").split()
    if len(parts) == 2:
        month_token, year = parts[0][:3].title(), parts[1]
        month = _ABBR_TO_MONTH.get(month_token)
        if month and year.isdigit():
            return f"{int(year):04d}-{month:02d}"
    return text


def _parse_amount(value: str) -> float:
    text = str(value).strip().replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    if not text or text in {"-", "—"}:
        return 0.0
    return float(text)
