"""Synthetic tool-loop and security regressions; no model/server is loaded."""
from dataclasses import replace
import hashlib
import json
import multiprocessing
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from plat_harness.cli import main
from plat_harness.errors import HarnessError
from plat_harness.models import ModelTurn, NullModel
from plat_harness.tool_loop import (LoopConfig, OpsMetricExecutor, bounded_call, run_question,
                                   validate_arguments, validate_metric)

ARGS = {"metric_id": "physical_occupancy", "context": "ops_actuals", "asset_or_deal_id": "Property_A"}


def call(args=None, name="get_certified_metric", call_id="call_one"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(ARGS if args is None else args)}}


class ScriptedModel:
    model_id = "synthetic-protocol-test-not-a-live-model"

    def __init__(self, calls=None, final='{"answer_from":["call_one"]}', repeat=False):
        self.calls = (call(),) if calls is None else tuple(calls)
        self.final = final
        self.repeat = repeat

    def complete(self, messages, tools):
        if self.repeat or not any(m["role"] == "tool" for m in messages):
            return ModelTurn("ignored model draft 99999", self.calls, self.model_id)
        return ModelTurn(self.final, (), self.model_id)


class FinalModel:
    model_id = "synthetic-final"

    def __init__(self, content):
        self.content = content

    def complete(self, messages, tools):
        return ModelTurn(self.content, (), self.model_id)


@pytest.fixture
def fixture(tmp_path):
    # tmp_path is an explicitly private pytest directory, never real deal data.
    root = tmp_path / "ops"
    csv = root / "property_a" / "Standardized" / "rent_roll.csv"
    csv.parent.mkdir(parents=True)
    csv.write_text("status,snapshot_date\noccupied,2026-01-31\nvacant,2026-01-31\ndown,2026-01-31\n")
    config = LoopConfig((tmp_path,), ("Property_A",), tmp_path / "run")
    return config, OpsMetricExecutor((tmp_path,), ops_root=root), csv


def test_offline_synthetic_end_to_end_real_tool_artifact(fixture):
    config, executor, csv = fixture
    result = run_question("What is the latest physical occupancy for Property_A?", ScriptedModel(), config, executor)
    assert result["status"] == "answered", result
    answer = result["answers"][0]
    assert answer["metric"]["occupied"] == 1
    assert answer["metric"]["vacant"] == 1
    assert answer["metric"]["down"] == 1
    assert answer["metric"]["denominator"] == 3
    artifact = config.run_dir / answer["citation"]["artifact"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == answer["citation"]["sha256"]
    assert answer["citation"]["source_artifacts"][0]["sha256"] == hashlib.sha256(csv.read_bytes()).hexdigest()
    assert "99999" not in json.dumps(result)
    assert result["timing"]["model_s"] > 0 and result["timing"]["tool_s"] > 0
    for path in config.run_dir.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    assert config.run_dir.stat().st_mode & 0o777 == 0o700
    assert json.loads((config.run_dir / "result.json").read_text())["status"] == "answered"


@pytest.mark.parametrize("patch", [
    {"rank": 3}, {"occupied": 9999}, {"shell": "touch /tmp/not-allowed"}, {"period": "1999-01"},
    {"asset_or_deal_id": "../escape"}, {"asset_or_deal_id": "/etc/passwd"},
    {"asset_or_deal_id": "Property_B"}, {"context": "governance"}, {"metric_id": "cash_on_cash"},
    {"asset_or_deal_id": 7},
])
def test_strict_schema_never_calls_executor(fixture, patch):
    config, executor, csv = fixture
    bad = dict(ARGS, **patch)
    result = run_question("synthetic", ScriptedModel([call(bad)]), config, executor)
    assert result["status"] == "refused" and result["error"] == "INVALID_TOOL_ARGUMENTS"
    assert not list(config.run_dir.glob("tool-*.json"))


def test_model_rank_three_cannot_execute(fixture):
    config, executor, _ = fixture
    with pytest.raises(HarnessError) as exc:
        run_question("synthetic", ScriptedModel(), replace(config, rank=3), executor)
    assert exc.value.code == "RANK_FORBIDDEN"
    assert not config.run_dir.exists()


@pytest.mark.parametrize("name", ["shell", "publish", "run_underwriting_model", "parse_om"])
def test_unallowlisted_tool_denied(fixture, name):
    config, executor, _ = fixture
    result = run_question("synthetic", ScriptedModel([call(name=name)]), config, executor)
    assert result["error"] == "TOOL_NOT_ALLOWED"


@pytest.mark.parametrize("final", [
    'Occupancy is 100%.', 'Occupancy is one hundred percent.',
    '{"answer_from":["call_one"],"value":100}', '{"answer_from":["fabricated-id"]}',
    '{"answer_from":[]}', '{"answer_from":["call_one","call_one"]}',
])
def test_model_numbers_prose_and_fabricated_citations_rejected(fixture, final):
    config, executor, _ = fixture
    result = run_question("synthetic", ScriptedModel(final=final), config, executor)
    assert result["status"] == "refused" and "answers" not in result


def test_typed_model_refusal(fixture):
    config, executor, _ = fixture
    result = run_question("Calculate IRR", FinalModel('{"refusal":"UNSUPPORTED_REQUEST"}'), config, executor)
    assert result["error"] == "UNSUPPORTED_REQUEST"


def test_null_model_retained_with_durable_refusal(fixture):
    config, executor, _ = fixture
    result = run_question("synthetic", NullModel(), config, executor)
    assert result["error"] == "NO_MODEL_CONFIGURED"
    assert "NO_MODEL_CONFIGURED" in (config.run_dir / "events.jsonl").read_text()


def test_symlink_file_escape_denied(fixture, tmp_path):
    config, executor, csv = fixture
    secret = tmp_path / "secret.csv"
    secret.write_text("private data must not be read")
    csv.unlink()
    csv.symlink_to(secret)
    result = run_question("synthetic", ScriptedModel(), config, executor)
    assert result["error"] == "PATH_DENIED"
    assert "private data" not in json.dumps(result)


def test_symlink_directory_escape_denied(fixture, tmp_path):
    config, executor, csv = fixture
    moved = tmp_path / "outside"
    csv.parent.rename(moved)
    csv.parent.symlink_to(moved, target_is_directory=True)
    result = run_question("synthetic", ScriptedModel(), config, executor)
    assert result["error"] == "PATH_DENIED"


def test_unapproved_root_refused(fixture):
    config, executor, _ = fixture
    executor.ops_root = Path("/etc")
    result = run_question("synthetic", ScriptedModel(), config, executor)
    assert result["error"] == "PATH_DENIED"


def test_run_dir_symlink_parent_denied(fixture, tmp_path):
    config, executor, _ = fixture
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(HarnessError) as exc:
        run_question("synthetic", ScriptedModel(), replace(config, run_dir=link / "run"), executor)
    assert exc.value.code == "PATH_DENIED"


def test_existing_run_dir_not_overwritten(fixture):
    config, executor, _ = fixture
    config.run_dir.mkdir()
    with pytest.raises(HarnessError) as exc:
        run_question("synthetic", ScriptedModel(), config, executor)
    assert exc.value.code == "EVIDENCE_PATH_ERROR"


def test_malformed_arguments_rejected_no_defaults(fixture):
    config, executor, _ = fixture
    bad = call()
    bad["function"]["arguments"] = '{"metric_id":'
    result = run_question("synthetic", ScriptedModel([bad]), config, executor)
    assert result["error"] == "INVALID_MODEL_JSON"


def test_batch_validation_atomic_before_execution(fixture):
    config, executor, _ = fixture
    result = run_question("synthetic", ScriptedModel([call(), call(name="shell", call_id="other")]), config, executor)
    assert result["error"] == "TOOL_NOT_ALLOWED"
    assert not list(config.run_dir.glob("tool-*.json"))


def test_max_calls_bound(fixture):
    config, executor, _ = fixture
    calls = [call(call_id=str(i)) for i in range(3)]
    result = run_question("synthetic", ScriptedModel(calls), config, executor)
    assert result["error"] == "TOOL_CALL_LIMIT"


def test_max_steps_bound(fixture):
    config, executor, _ = fixture
    result = run_question("synthetic", ScriptedModel(), replace(config, max_steps=1), executor)
    assert result["error"] == "STEP_LIMIT"


def test_repeated_call_id_denied(fixture):
    config, executor, _ = fixture
    result = run_question("synthetic", ScriptedModel(repeat=True), config, executor)
    assert result["error"] == "INVALID_TOOL_CALL"


def sleeper(*args):
    time.sleep(10)


def test_hard_tool_timeout_no_worker_left(fixture):
    config, _, _ = fixture
    before = {p.pid for p in multiprocessing.active_children()}
    result = run_question("synthetic", ScriptedModel(), replace(config, tool_timeout_s=0.05), sleeper)
    assert result["error"] == "TIMEOUT" and result["phase"] == "tool"
    assert {p.pid for p in multiprocessing.active_children()} == before
    assert "TIMEOUT" in (config.run_dir / "events.jsonl").read_text()


def test_cancel_during_worker_is_durable(fixture):
    config, _, _ = fixture
    context = multiprocessing.get_context("fork")
    event = context.Event()
    def trigger():
        time.sleep(0.1)
        event.set()
    timer = context.Process(target=trigger)
    timer.start()
    try:
        result = run_question("synthetic", ScriptedModel(), config, sleeper, cancel=event)
    finally:
        timer.join(timeout=1)
    assert result["error"] == "CANCELLED"
    assert (config.run_dir / "result.json").exists()


def test_cancel_before_first_call(fixture):
    config, executor, _ = fixture
    event = threading.Event()
    event.set()
    result = run_question("synthetic", ScriptedModel(), config, executor, cancel=event)
    assert result["error"] == "CANCELLED" and result["timing"]["tool_s"] == 0


def test_worker_output_bound(fixture):
    config, executor, _ = fixture
    result = run_question("synthetic", FinalModel("x" * 50000), config, executor)
    assert result["error"] == "OUTPUT_LIMIT"


def test_wrong_artifact_subject_is_refused(fixture):
    config, executor, _ = fixture

    def wrong(args, rank):
        data = executor(args, rank)
        data["asset_or_deal_id"] = "Property_B"
        return data

    result = run_question("synthetic", ScriptedModel(), config, wrong)
    assert result["error"] == "ARTIFACT_MISMATCH"


@pytest.mark.parametrize("patch", [{"denominator": 2}, {"occupied": True}, {"value": 0.9},
    {"as_of": "1999-01-01"}, {"certification": "UNKNOWN"}, {"source": []}, {"input_artifacts": []}])
def test_bad_metric_artifact_rejected(fixture, patch):
    config, executor, _ = fixture

    def wrong(args, rank):
        return dict(executor(args, rank), **patch)

    result = run_question("synthetic", ScriptedModel(), config, wrong)
    assert result["status"] == "refused"


def test_real_readonly_sqlite_snapshot_adapter(fixture, tmp_path):
    config, _, _ = fixture
    db = tmp_path / "snapshot.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE properties(id TEXT,name TEXT); INSERT INTO properties VALUES ('property_a','Property_A');"
                     "CREATE TABLE rent_roll_snapshots(property_id TEXT, as_of_date TEXT, occupied_units INTEGER, vacant_units INTEGER, down_units INTEGER, source_file TEXT, created_at TEXT);"
                     "INSERT INTO rent_roll_snapshots VALUES ('property_a','2026-01-31',8,1,1,'synthetic_snapshot.csv','2026-01-31');")
    con.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = run_question("synthetic", ScriptedModel(), config, OpsMetricExecutor((tmp_path,), boxscore_db=db))
    assert result["status"] == "answered", result
    assert result["answers"][0]["metric"]["denominator"] == 10
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_wal_input_refused(fixture, tmp_path):
    config, _, _ = fixture
    db = tmp_path / "snapshot.db"
    db.write_bytes(b"not read")
    Path(str(db) + "-wal").write_bytes(b"journal")
    result = run_question("synthetic", ScriptedModel(), config, OpsMetricExecutor((tmp_path,), boxscore_db=db))
    assert result["error"] == "SNAPSHOT_REQUIRED"


def test_local_cli_private_summary_and_null_unchanged(fixture, monkeypatch, capsys):
    config, _, csv = fixture
    monkeypatch.setattr("plat_harness.local_model.LocalModel", lambda *a, **kw: ScriptedModel())
    code = main(["local-ask", "latest occupancy", "--endpoint", "http://127.0.0.1:8000", "--model-id", "exact-id",
                 "--asset", "Property_A", "--approved-root", str(config.approved_roots[0]), "--run-dir", str(config.run_dir),
                 "--ops-root", str(csv.parents[2])])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0 and summary["status"] == "answered" and "answers" not in summary
    assert main(["ask", "hello"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "NO_MODEL_CONFIGURED"


def test_threaded_host_fails_closed_before_fork():
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait)
    thread.start()
    try:
        with pytest.raises(HarnessError) as exc:
            bounded_call(lambda: {}, (), 1)
        assert exc.value.code == "UNSAFE_WORKER_CONTEXT"
    finally:
        stop.set()
        thread.join()


def test_model_hard_timeout(fixture):
    config, executor, _ = fixture
    class SlowModel:
        complete = staticmethod(sleeper)
    result = run_question("synthetic", SlowModel(), replace(config, model_timeout_s=0.05), executor)
    assert result["error"] == "TIMEOUT" and result["phase"] == "model"
    assert result["timing"]["tool_s"] == 0


def test_real_local_protocol_offline_transport_to_real_tool(fixture):
    from test_local_model import Response, sse, event
    from plat_harness.local_model import LocalModel
    config, executor, _ = fixture

    class OfflineProtocolTransport:
        def open(self, req, timeout):
            if req.full_url.endswith("/health"):
                return Response(b"ok")
            if req.full_url.endswith("/v1/models"):
                return Response(b'{"data":[{"id":"exact-id"}]}')
            payload = json.loads(req.data)
            if any(m["role"] == "tool" for m in payload["messages"]):
                # Model never receives raw counts or resident source material.
                assert "occupied" not in payload["messages"][-1]["content"]
                return sse([event({"content": '{"answer_from":["call_one"]}'}, "stop")])
            proposed = call()
            proposed["index"] = 0
            return sse([event({"tool_calls": [proposed]}, "tool_calls")])

    model = LocalModel("http://127.0.0.1:12345", "exact-id")
    model.opener = OfflineProtocolTransport()
    result = run_question("Latest occupancy of Property_A?", model, config, executor)
    assert result["status"] == "answered", result
    turns = [e for e in result["timing"]["turns"] if e["phase"] == "model"]
    assert len(turns) == 2 and all(e["ttft_s"] is not None for e in turns)
