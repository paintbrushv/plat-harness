"""Issued engine results stay withheld when a rank or a flag calls them reasonable."""

from __future__ import annotations

import json
from decimal import Decimal

from plat_harness.cli import main
from plat_harness.ranks import PermissionRank
from plat_harness.reasonability import approve_issued_result, present_underwriting

_ASPEN = {
    "strike": 3902842,
    "going_in_cap": Decimal("0.3067"),
    "exit_cap": Decimal("0.0725"),
    "price_per_unit": 9566,
    "units": 408,
    "min_dscr": Decimal("4.82"),
    "year1_noi": 915293,
}


def test_aspen_shaped_withhold_leaves_engine_numbers_unchanged() -> None:
    issued = present_underwriting(_ASPEN, kind="solved_strike", market="Kansas City, MO")
    engine = issued["engine"]
    assert issued["present_as_bid"] is False
    assert issued["bid"] is None
    assert engine["strike"] == Decimal("3902842")
    assert engine["price_per_unit"] == Decimal("9566")
    assert engine["going_in_cap"] == Decimal("0.3067")
    assert engine["exit_cap"] == Decimal("0.0725")
    assert engine["year1_noi"] == Decimal("915293")
    assert engine["min_dscr"] == Decimal("4.82")
    assert engine["units"] == 408
    fields = {item["field"] for item in issued["breaches"]}
    assert "going_in_cap" in fields
    assert "price_per_unit" in fields
    assert "year1_noi" not in fields
    assert "min_dscr" not in fields
    assert "exit_cap" not in fields
    assert issued["as_issued_history"] is None


def test_model_reasonable_does_not_restore_aspen_bid() -> None:
    issued = present_underwriting(
        _ASPEN,
        model_reasonable=True,
        session_rank=PermissionRank.EXECUTE,
    )
    assert issued["model_called_reasonable"] is True
    assert issued["session_rank"] == int(PermissionRank.EXECUTE)
    assert issued["present_as_bid"] is False
    assert issued["bid"] is None
    assert issued["engine"]["strike"] == Decimal("3902842")
    assert issued["engine"]["year1_noi"] == Decimal("915293")
    assert issued["engine"]["going_in_cap"] == Decimal("0.3067")


def test_issued_result_rank_or_flag_cannot_approve() -> None:
    issued = present_underwriting(_ASPEN, model_reasonable=True)
    attempt = approve_issued_result(
        issued,
        session_rank=PermissionRank.EXECUTE,
        reasonable=True,
        flag="approve",
    )
    assert attempt["approved"] is False
    assert attempt["reasonable_flag"] is True
    assert attempt["present_as_bid"] is False
    assert attempt["bid"] is None
    assert attempt["session_rank"] == int(PermissionRank.EXECUTE)
    assert attempt["engine"]["strike"] == issued["engine"]["strike"]
    assert attempt["engine"]["year1_noi"] == issued["engine"]["year1_noi"]
    assert attempt["engine"]["going_in_cap"] == issued["engine"]["going_in_cap"]
    assert attempt["engine"]["price_per_unit"] == Decimal("9566")
    assert attempt["as_issued_history"] is None


def test_negative_noi_on_a_price_and_dscr_under_1_10_withhold() -> None:
    negative = present_underwriting(
        {
            "strike": 5_000_000,
            "going_in_cap": Decimal("0.07"),
            "price_per_unit": 100_000,
            "min_dscr": Decimal("1.25"),
            "year1_noi": Decimal("-1"),
            "units": 50,
        }
    )
    assert negative["present_as_bid"] is False
    assert negative["engine"]["year1_noi"] == Decimal("-1")
    assert negative["engine"]["strike"] == Decimal("5000000")

    thin_debt = present_underwriting(
        {
            "strike": 5_000_000,
            "going_in_cap": Decimal("0.07"),
            "price_per_unit": 100_000,
            "min_dscr": Decimal("1.09"),
            "year1_noi": Decimal("350000"),
            "units": 50,
        },
        model_reasonable=True,
    )
    assert thin_debt["present_as_bid"] is False
    assert thin_debt["engine"]["min_dscr"] == Decimal("1.09")
    assert any(item["field"] == "min_dscr" for item in thin_debt["breaches"])


def test_inside_bands_can_be_shown_and_is_still_not_rank_approved() -> None:
    issued = present_underwriting(
        {
            "strike": 10_000_000,
            "going_in_cap": Decimal("0.07"),
            "exit_cap": Decimal("0.12"),
            "price_per_unit": 100_000,
            "min_dscr": Decimal("1.10"),
            "year1_noi": Decimal("700000"),
            "units": 100,
        }
    )
    assert issued["present_as_bid"] is True
    assert issued["bid"] == Decimal("10000000")
    assert issued["breaches"] == []
    attempt = approve_issued_result(issued, session_rank=PermissionRank.EXECUTE, flag="pass")
    assert attempt["approved"] is False
    assert attempt["engine"]["year1_noi"] == Decimal("700000")


def test_present_cli_withholds_aspen_json(tmp_path, capsys) -> None:
    path = tmp_path / "aspen.json"
    path.write_text(
        json.dumps(
            {
                "solved_purchase_price": 3902842,
                "going_in_cap_rate": "0.3067",
                "exit_cap_rate": "0.0725",
                "price_per_unit": 9566,
                "units": 408,
                "minimum_dscr": "4.82",
                "projected_noi_used_for_sizing": 915293,
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "present",
            "--kind",
            "solved_strike",
            "--market",
            "Kansas City, MO",
            "--result",
            str(path),
            "--model-reasonable",
            "--rank",
            "3",
        ]
    )
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["present_as_bid"] is False
    assert payload["bid"] is None
    assert payload["model_called_reasonable"] is True
    assert payload["engine"]["strike"] == "3902842"
    assert payload["engine"]["year1_noi"] == "915293"
    assert payload["engine"]["going_in_cap"] == "0.3067"
    assert payload["engine"]["price_per_unit"] == "9566"
    assert payload["as_issued_history"] is None
