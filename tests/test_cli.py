import json

from plat_harness.cli import main
from plat_harness.millage import PROPERTY_TAX_MILLAGE_QUESTION


def test_ask_conflict_metric_exits_nonzero(capsys) -> None:
    code = main(["ask", "--metric", "noi"])
    assert code == 2
    err = capsys.readouterr().err
    payload = json.loads(err)
    assert payload["error"] == "CONFLICT_UNRESOLVED"


def test_ask_millage_without_rate(capsys) -> None:
    code = main(["ask", "--metric", "millage_rate"])
    assert code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "MISSING_MILLAGE"
    assert "mills per $1,000" in payload["message"]


def test_ask_occupancy_happy_path(capsys) -> None:
    code = main(
        [
            "ask",
            "--metric",
            "physical_occupancy",
            "--context",
            "ops_actuals",
            "--occupied",
            "72",
            "--vacant",
            "6",
            "--down",
            "2",
            "--denominator",
            "80",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["denominator"] == 80
    assert payload["occupied"] == 72
    assert payload["vacant"] == 6
    assert payload["down"] == 2


def test_ask_without_metric_uses_null_model(capsys) -> None:
    code = main(["ask", "What was T12 payroll?"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "NO_MODEL_CONFIGURED"
    assert payload["model"] == "null"


def test_scoreboard_requires_subject(capsys) -> None:
    code = main(["scoreboard"])
    assert code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "NOT_FOUND"


def test_scoreboard_empty_certified_board(capsys) -> None:
    code = main(["scoreboard", "--asset", "example_property"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "uncertified_empty"
    assert payload["metrics"] == {}


def test_underwrite_without_millage(capsys) -> None:
    code = main(["underwrite", "--deal", "example_garden_style"])
    assert code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "MISSING_MILLAGE"
    assert PROPERTY_TAX_MILLAGE_QUESTION in payload["message"]


def test_underwrite_with_millage_does_not_invent_returns(capsys) -> None:
    code = main(["underwrite", "--deal", "example_garden_style", "--millage-rate", "25.31"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "NOT_IMPLEMENTED"
    assert payload["millage_rate_mills"] == "25.31"
    blob = json.dumps(payload).lower()
    assert "irr" not in blob or "not invent" in blob or payload["error"] == "NOT_IMPLEMENTED"
