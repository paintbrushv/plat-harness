"""Local, synthetic bitemporal assertion lane for one PLAT workflow.

This module records accepted assertions and frozen reports.  It does not
replace source evidence, perform a live migration, or authorize external
actions.  The embedding host supplies current read/write authorization and
keeps the SQLite file in its approved private storage boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import sqlite3
from typing import Callable
from uuid import uuid4


SCHEMA_VERSION = "plat.temporal.assertion/1"
MAX_JSON_BYTES = 1_000_000


class TemporalRefusal(ValueError):
    """An invalid, unauthorized, or unsupported temporal operation."""


class TemporalConflict(TemporalRefusal):
    """A stale stream version or unresolved competing assertion."""


@dataclass(frozen=True)
class Assertion:
    event_id: str
    workspace_id: str
    aggregate_id: str
    stream_version: int
    actor_id: str
    key: str
    value: object
    valid_from: date
    valid_to: date | None
    recorded_at: datetime
    supersedes_event_id: str | None


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TemporalRefusal("Knowledge time must be a timezone-aware instant")
    return value.astimezone(timezone.utc)


def _date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TemporalRefusal("Effective time must remain a date, not a timestamp")
    return value


def _json(value: object) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TemporalRefusal("Assertion must be finite JSON data") from exc
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise TemporalRefusal("Assertion exceeds the local rehearsal bound")
    return encoded


class TemporalLedger:
    """SQLite assertion stream with trusted record time and current access checks.

    ``authorize`` must consult the host's *current* authenticated grants.  It
    must not derive authority from old events, prompts, or an as-issued report.
    The host owns private file placement and its backup/redaction lifecycle.
    """

    def __init__(
        self,
        path: str,
        *,
        authorize: Callable[[str, str, str, str], bool],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not path or not callable(authorize):
            raise TemporalRefusal("Storage path and current authorizer are required")
        self._authorize = authorize
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA synchronous=FULL")
        db_version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if db_version not in (0, 1):
            self._db.close()
            raise TemporalRefusal("Unsupported temporal store schema")
        if db_version == 0 and self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone():
            self._db.close()
            raise TemporalRefusal("Unversioned existing store refused")
        if db_version == 1:
            tables = {
                row[0] for row in self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"assertions", "issued_reports"} <= tables:
                self._db.close()
                raise TemporalRefusal("Incomplete temporal store schema")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS assertions (
                event_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                stream_version INTEGER NOT NULL,
                schema_version TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                recorded_at TEXT NOT NULL,
                supersedes_event_id TEXT,
                source_system TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                command_sha256 TEXT NOT NULL,
                UNIQUE (workspace_id, aggregate_id, stream_version),
                UNIQUE (workspace_id, aggregate_id, source_system, source_record_id, source_revision)
            );
            CREATE TABLE IF NOT EXISTS issued_reports (
                workspace_id TEXT NOT NULL,
                report_id TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                issued_by TEXT NOT NULL,
                content_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                PRIMARY KEY (workspace_id, report_id)
            );
            """
        )
        expected_columns = {
            "assertions": (
                "event_id", "workspace_id", "aggregate_id", "stream_version",
                "schema_version", "actor_id", "event_key", "value_json", "valid_from",
                "valid_to", "recorded_at", "supersedes_event_id", "source_system",
                "source_record_id", "source_revision", "command_sha256",
            ),
            "issued_reports": (
                "workspace_id", "report_id", "aggregate_id", "issued_at", "issued_by",
                "content_json", "content_sha256",
            ),
        }
        for table, columns in expected_columns.items():
            actual = tuple(row[1] for row in self._db.execute(f"PRAGMA table_info({table})"))
            if actual != columns:
                self._db.close()
                raise TemporalRefusal("Temporal store schema does not match this reader")
        if db_version == 0:
            self._db.execute("PRAGMA user_version=1")

    def close(self) -> None:
        self._db.close()

    def _permit(self, actor_id: str, workspace_id: str, aggregate_id: str, capability: str) -> None:
        if not actor_id or not workspace_id or not aggregate_id or not self._authorize(
            actor_id, workspace_id, aggregate_id, capability
        ):
            raise TemporalRefusal("Current workspace authorization refused")

    def append(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        aggregate_id: str,
        expected_version: int,
        key: str,
        value: object,
        valid_from: date,
        valid_to: date | None,
        source_system: str,
        source_record_id: str,
        source_revision: str,
        supersedes_event_id: str | None = None,
    ) -> Assertion:
        """Append one accepted fact; source identity and revision make retries safe.

        An equal amount or text from another source remains a distinct fact.
        A correction must name the exact prior event in the same stream/key.
        """
        self._permit(actor_id, workspace_id, aggregate_id, "write")
        if not all((aggregate_id, key, source_system, source_record_id, source_revision)):
            raise TemporalRefusal("Scoped aggregate, key, and source revision are required")
        if type(expected_version) is not int or expected_version < 0:
            raise TemporalRefusal("Expected stream version is invalid")
        start = _date(valid_from)
        end = _date(valid_to) if valid_to is not None else None
        if end is not None and end <= start:
            raise TemporalRefusal("Effective interval must be half-open and nonempty")
        encoded = _json(value)
        command = _json([
            workspace_id, aggregate_id, key, encoded, start.isoformat(),
            end.isoformat() if end else None, source_system,
            source_record_id, source_revision, supersedes_event_id,
        ])
        command_hash = sha256(command.encode()).hexdigest()
        db = self._db
        db.execute("BEGIN IMMEDIATE")
        try:
            existing = db.execute(
                """SELECT event_id, command_sha256 FROM assertions
                   WHERE workspace_id=? AND aggregate_id=? AND source_system=? AND source_record_id=?
                     AND source_revision=?""",
                (workspace_id, aggregate_id, source_system, source_record_id, source_revision),
            ).fetchone()
            if existing:
                if existing[1] != command_hash:
                    raise TemporalConflict("Source revision was already accepted with different content")
                result = self._event(existing[0])
                db.execute("COMMIT")
                return result
            current = db.execute(
                """SELECT COALESCE(MAX(stream_version), 0) FROM assertions
                   WHERE workspace_id=? AND aggregate_id=?""",
                (workspace_id, aggregate_id),
            ).fetchone()[0]
            if current != expected_version:
                raise TemporalConflict("Obsolete stream version")
            if supersedes_event_id is not None:
                old = db.execute(
                    """SELECT event_key FROM assertions WHERE event_id=?
                       AND workspace_id=? AND aggregate_id=?""",
                    (supersedes_event_id, workspace_id, aggregate_id),
                ).fetchone()
                if old is None or old[0] != key:
                    raise TemporalRefusal("Correction must link to a fact in the same stream/key")
                if db.execute(
                    "SELECT 1 FROM assertions WHERE supersedes_event_id=?", (supersedes_event_id,)
                ).fetchone():
                    raise TemporalConflict("Fact already has a correction")
            recorded = _utc(self._clock())
            last_recorded = db.execute(
                """SELECT recorded_at FROM assertions WHERE workspace_id=? AND aggregate_id=?
                   ORDER BY stream_version DESC LIMIT 1""", (workspace_id, aggregate_id),
            ).fetchone()
            if last_recorded is not None and recorded < datetime.fromisoformat(last_recorded[0]):
                raise TemporalRefusal("Trusted record clock moved backward")
            event_id = uuid4().hex
            db.execute(
                """INSERT INTO assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, workspace_id, aggregate_id, current + 1, SCHEMA_VERSION,
                    actor_id, key, encoded, start.isoformat(), end.isoformat() if end else None,
                    recorded.isoformat(), supersedes_event_id, source_system,
                    source_record_id, source_revision, command_hash,
                ),
            )
            db.execute("COMMIT")
            return self._event(event_id)
        except Exception:
            db.execute("ROLLBACK")
            raise

    def _event(self, event_id: str) -> Assertion:
        row = self._db.execute(
            """SELECT event_id, workspace_id, aggregate_id, stream_version,
                      schema_version, actor_id, event_key, value_json, valid_from,
                      valid_to, recorded_at, supersedes_event_id
               FROM assertions WHERE event_id=?""", (event_id,)
        ).fetchone()
        if row is None or row[4] != SCHEMA_VERSION:
            raise TemporalRefusal("Unknown or unsupported event schema")
        return Assertion(
            row[0], row[1], row[2], row[3], row[5], row[6], json.loads(row[7]),
            date.fromisoformat(row[8]), date.fromisoformat(row[9]) if row[9] else None,
            datetime.fromisoformat(row[10]), row[11],
        )

    def as_known(
        self, *, actor_id: str, workspace_id: str, aggregate_id: str,
        effective_at: date, known_at: datetime,
    ) -> dict[str, object]:
        """Return unambiguous facts in force on a date using evidence known by K."""
        self._permit(actor_id, workspace_id, aggregate_id, "read")
        effective = _date(effective_at)
        cutoff = _utc(known_at)
        rows = self._db.execute(
            """SELECT event_id FROM assertions WHERE workspace_id=? AND aggregate_id=?
               AND recorded_at<=? ORDER BY stream_version""",
            (workspace_id, aggregate_id, cutoff.isoformat()),
        ).fetchall()
        events = [self._event(row[0]) for row in rows]
        active = [
            event for event in events
            if event.valid_from <= effective and (event.valid_to is None or effective < event.valid_to)
        ]
        superseded = {event.supersedes_event_id for event in active if event.supersedes_event_id}
        result: dict[str, object] = {}
        for event in active:
            if event.event_id in superseded:
                continue
            if event.key in result:
                raise TemporalConflict("Competing source assertions require resolution")
            result[event.key] = event.value
        return result

    def issue_report(
        self, *, actor_id: str, workspace_id: str, aggregate_id: str,
        report_id: str, content: object,
    ) -> None:
        """Freeze the exact issued artifact; a later correction cannot edit it."""
        self._permit(actor_id, workspace_id, aggregate_id, "write")
        if not aggregate_id or not report_id:
            raise TemporalRefusal("Report and aggregate IDs are required")
        encoded = _json(content)
        self._db.execute(
            """INSERT INTO issued_reports VALUES (?,?,?,?,?,?,?)""",
            (workspace_id, report_id, aggregate_id, _utc(self._clock()).isoformat(),
             actor_id, encoded, sha256(encoded.encode()).hexdigest()),
        )

    def as_issued(self, *, actor_id: str, workspace_id: str, report_id: str) -> object:
        row = self._db.execute(
            """SELECT aggregate_id, content_json, content_sha256 FROM issued_reports
               WHERE workspace_id=? AND report_id=?""",
            (workspace_id, report_id),
        ).fetchone()
        if row is None:
            raise TemporalRefusal("Report is unavailable in this workspace")
        self._permit(actor_id, workspace_id, row[0], "read")
        if sha256(row[1].encode()).hexdigest() != row[2]:
            raise TemporalRefusal("Issued report integrity failed")
        content = json.loads(row[1])
        if isinstance(content, dict) and content.get("type") == "original_thesis/1":
            if content.get("deal_id") != row[0]:
                raise TemporalRefusal("Issued thesis scope does not match its report")
        return content

    def operations_noi_as_known(
        self, *, actor_id: str, workspace_id: str, aggregate_id: str,
        effective_at: date, known_at: datetime,
    ) -> Decimal:
        """Compute a period NOI from accepted base and distinct expense facts."""
        facts = self.as_known(
            actor_id=actor_id, workspace_id=workspace_id,
            aggregate_id=aggregate_id, effective_at=effective_at, known_at=known_at,
        )
        if "operations_noi_base" not in facts:
            raise TemporalRefusal("NOI base is unavailable for this historical view")
        try:
            base = Decimal(str(facts["operations_noi_base"]))
            expenses = [
                Decimal(str(value)) for key, value in facts.items()
                if key.startswith("expense:")
            ]
        except InvalidOperation as exc:
            raise TemporalRefusal("NOI assertion is not a decimal amount") from exc
        if not base.is_finite() or any(not amount.is_finite() for amount in expenses):
            raise TemporalRefusal("NOI assertion must be finite")
        return base - sum(expenses, Decimal("0"))

    def issue_original_thesis(
        self, *, actor_id: str, workspace_id: str, report_id: str,
        deal_id: str, purchase_price: object, year_2_unlevered_noi: object,
        capex: object, present_as_bid: bool,
    ) -> None:
        """Freeze the existing product's original-thesis result as issued."""
        from plat_harness.original_thesis import record_original_thesis
        from plat_harness.underwriting_direction import YIELD_FORMULA

        self._permit(actor_id, workspace_id, deal_id, "write")
        record = record_original_thesis(
            deal_id, purchase_price=purchase_price,
            year_2_unlevered_noi=year_2_unlevered_noi, capex=capex,
            present_as_bid=present_as_bid,
        )
        thesis = record.thesis
        self.issue_report(
            actor_id=actor_id, workspace_id=workspace_id,
            aggregate_id=deal_id, report_id=report_id,
            content={
                "type": "original_thesis/1", "deal_id": deal_id,
                "purchase_price": str(thesis.purchase_price),
                "year_2_unlevered_noi": str(thesis.year_2_unlevered_noi),
                "capex": str(thesis.capex),
                "yield": str(thesis.year_2_unlevered_yield_on_cost),
                "yield_formula": YIELD_FORMULA,
                "present_as_bid": thesis.present_as_bid,
            },
        )

    def thesis_to_actual(
        self, *, actor_id: str, workspace_id: str, report_id: str,
        effective_at: date, known_at: datetime,
    ):
        """Keep the issued thesis fixed and attach a later operations actual."""
        from plat_harness.original_thesis import OriginalThesis, ThesisRecord, record_operations_actual

        frozen = self.as_issued(actor_id=actor_id, workspace_id=workspace_id, report_id=report_id)
        if not isinstance(frozen, dict) or frozen.get("type") != "original_thesis/1":
            raise TemporalRefusal("Report is not a supported original thesis")
        thesis = OriginalThesis(
            deal_id=frozen["deal_id"],
            purchase_price=Decimal(frozen["purchase_price"]),
            year_2_unlevered_noi=Decimal(frozen["year_2_unlevered_noi"]),
            capex=Decimal(frozen["capex"]),
            year_2_unlevered_yield_on_cost=Decimal(frozen["yield"]),
            present_as_bid=frozen["present_as_bid"],
        )
        actual = self.operations_noi_as_known(
            actor_id=actor_id, workspace_id=workspace_id, aggregate_id=thesis.deal_id,
            effective_at=effective_at, known_at=known_at,
        )
        return record_operations_actual(ThesisRecord(thesis), actual)
