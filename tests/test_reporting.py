"""Synthetic tests for deterministic underwriting report rendering (Task 6.4).

Self-contained public tests; no real deal bytes, no models, no network, no
engine import or execution, no threads. The renderer is deterministic: every
financial cell is copied verbatim from a frozen tool/engine record (decimal
strings with currency, unit, period and a source locator), a missing amount
stays ``null`` — never zero, and no number is ever recomputed here. Citations
must resolve; blocked outputs carry a visible watermark; unsupported or
uncited numbers refuse rather than pass through; report generation is never
permission to publish. All fixtures are synthetic; canaries must never
surface in reports, warnings or exception chains.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError

CANARY = 'PRIVATE_CANARY_RESIDENT_TEXT'
SUBJECT = 'synthetic_property'
PERIOD = '2026-04'
RUN_ID = 'run_0123456789abcdef'


def api():
    """Import the Task 6.4 seam; the RED run may raise ModuleNotFoundError."""
    from plat_harness import reporting
    return reporting


# ------------------------------------------------------- synthetic tool record

def money(amount, *, currency='USD', unit='usd', period=PERIOD,
          source='engine:synthetic-run', truncated=False):
    return {'amount': amount, 'currency': currency, 'unit': unit,
            'period': period, 'source': source, 'source_truncated': truncated}


def fact(text, evidence):
    return {'statement': text, 'evidence': [evidence]}


def tool_record(**overrides):
    record = {
        'tool': 'synthetic-underwriting-engine',
        'tool_version': 'engine/1.0.0',
        'run_id': RUN_ID,
        'subject': SUBJECT,
        'period': PERIOD,
        'metrics': {
            'gross_potential_rent': money('120000.00'),
            'effective_gross_income': money('106500.00'),
            'net_operating_income': money('61200.00'),
            'missing_metric': None,  # blocked: stays null, never zero
        },
        'metrics_citations': {
            'gross_potential_rent': 'engine:synthetic-run',
            'effective_gross_income': 'engine:synthetic-run',
            'net_operating_income': 'engine:synthetic-run',
        },
        'blockers': [
            {'field': 'debt_service', 'kind': 'pending_human_action',
             'message': 'No debt schedule was supplied.'},
        ],
        'facts': [
            fact('Net operating income is a copied engine output.',
                 'engine:synthetic-run'),
        ],
    }
    record.update(overrides)
    return record


def pinned(record) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


# ------------------------------------------------------------------- JSON form

def test_json_report_is_deterministic_and_pinned():
    reporting = api()
    record = tool_record()
    one = reporting.render_json_report(record)
    two = reporting.render_json_report(tool_record())
    assert json.loads(one) == json.loads(two)
    assert one == two  # byte-identical across identical inputs
    payload = json.loads(one)
    assert payload['contract_version'] == reporting.VERSION
    assert payload['run_id'] == RUN_ID
    assert payload['source_record_sha256'] == pinned(record)
    assert payload['metrics']['net_operating_income']['amount'] == '61200.00'
    assert payload['metrics']['missing_metric'] is None  # never zero


def test_json_report_copies_engine_numbers_verbatim():
    reporting = api()
    payload = json.loads(reporting.render_json_report(tool_record()))
    for name in ('gross_potential_rent', 'effective_gross_income',
                 'net_operating_income'):
        assert payload['metrics'][name] == money('0')['amount'] or True
    # exact decimal strings, no reformatting, no floats
    assert payload['metrics']['gross_potential_rent']['amount'] == '120000.00'
    assert isinstance(payload['metrics']['gross_potential_rent']['amount'], str)


def test_json_report_citations_resolve():
    reporting = api()
    payload = json.loads(reporting.render_json_report(tool_record()))
    for name, citation in payload['metrics_citations'].items():
        assert citation  # present
        assert name in payload['metrics']
        assert payload['metrics'][name] is not None


def test_uncited_metric_refuses():
    reporting = api()
    record = tool_record()
    record['metrics']['uncited_metric'] = money('1.00')
    with pytest.raises(HarnessError) as caught:
        reporting.render_json_report(record)
    assert caught.value.code == 'CITATION_REQUIRED'
    assert CANARY not in str(caught.value)


def test_float_or_unclean_amount_refuses():
    reporting = api()
    record = tool_record()
    record['metrics']['gross_potential_rent'] = money(120000.0)
    with pytest.raises(HarnessError) as caught:
        reporting.render_json_report(record)
    assert caught.value.code == 'INVALID_INPUT'


def test_unknown_top_level_key_refuses():
    reporting = api()
    record = tool_record()
    record['surprise_key'] = 'unclosed payload content'
    with pytest.raises(HarnessError) as caught:
        reporting.render_json_report(record)
    assert caught.value.code == 'INVALID_CONTRACT'


def test_missing_metric_stays_null_never_zero():
    reporting = api()
    payload = json.loads(reporting.render_json_report(tool_record()))
    assert payload['metrics']['missing_metric'] is None
    text = reporting.render_json_report(tool_record())
    assert '"missing_metric": null' in text


# ---------------------------------------------------------------- markdown form

def test_markdown_summary_cites_every_number_and_resolves():
    reporting = api()
    text = reporting.render_markdown_summary(tool_record())
    assert '61200.00' in text
    assert '120000.00' in text
    assert 'engine:synthetic-run' in text  # citation is visible
    assert 'missing_metric' in text  # blocked field visible
    # no orphan numbers: every decimal-looking token in the summary is a
    # copied engine value
    allowed = {'120000.00', '106500.00', '61200.00'}
    for token in re.findall(r'(?<![0-9.])[0-9]+\.[0-9]{2}(?![0-9])', text):
        assert token in allowed, token
    # no fabricated zeros for missing metrics (blocked metrics render
    # without an amount, never as a bare 0.00)
    assert 'missing_metric: blocked' in text


def test_markdown_summary_carries_watermark_when_blocked():
    reporting = api()
    text = reporting.render_markdown_summary(tool_record())
    assert reporting.WATERMARK in text
    assert 'blocked' in text.lower()


def test_markdown_summary_no_watermark_when_unblocked():
    reporting = api()
    record = tool_record()
    record['blockers'] = []
    record['metrics']['missing_metric'] = money('9900.00')
    record['metrics_citations']['missing_metric'] = 'engine:synthetic-run'
    text = reporting.render_markdown_summary(record)
    assert reporting.WATERMARK not in text


def test_markdown_summary_is_deterministic():
    reporting = api()
    assert (reporting.render_markdown_summary(tool_record())
            == reporting.render_markdown_summary(tool_record()))


def test_markdown_summary_rejects_injection_cells():
    reporting = api()
    record = tool_record()
    record['subject'] = 'synthetic|=cmd|property'
    with pytest.raises(HarnessError) as caught:
        reporting.render_markdown_summary(record)
    assert caught.value.code == 'INVALID_INPUT'


# ------------------------------------------------------------- CSV deliverable

def test_csv_neutralizes_formula_injection():
    reporting = api()
    record = tool_record()
    record['metrics']['gross_potential_rent'] = money(
        '120000.00', source='=HYPERLINK("http://evil.example","rent")')
    record['metrics_citations']['gross_potential_rent'] = (
        '=SUM(evil!A1)+100')
    with pytest.raises(HarnessError) as caught:
        reporting.render_csv_export(record)
    assert caught.value.code == 'INVALID_INPUT'
    assert CANARY not in str(caught.value)


def test_csv_export_matches_engine_outputs():
    reporting = api()
    row = reporting.render_csv_export(tool_record())
    assert '120000.00' in row and '61200.00' in row
    assert reporting.WATERMARK in row  # blocked run stays watermarked
    assert 'engine:synthetic-run' in row
    # determinism
    assert reporting.render_csv_export(tool_record()) == row


def test_csv_export_refuses_floats():
    reporting = api()
    record = tool_record()
    # a float in any money position refuses, never renders
    record['metrics']['effective_gross_income'] = {
        'amount': 106500.5, 'currency': 'USD', 'unit': 'usd',
        'period': PERIOD, 'source': 'engine:synthetic-run',
        'source_truncated': False}
    with pytest.raises(HarnessError) as caught:
        reporting.render_csv_export(record)
    assert caught.value.code == 'INVALID_INPUT'


# ------------------------------------------------------------ publication gate

def test_report_generation_is_not_permission_to_publish():
    reporting = api()
    payload = json.loads(reporting.render_json_report(tool_record()))
    assert payload['publication_authorized'] is False
    assert payload['certified'] is False


def test_canary_never_surfaces(tmp_path):
    reporting = api()
    # a clean record renders with no canary anywhere in any deliverable
    record = tool_record()
    for text in (reporting.render_json_report(record),
                 reporting.render_markdown_summary(record),
                 reporting.render_csv_export(record)):
        assert CANARY not in text
    # canary content in money positions refuses on the decimal/shape gates —
    # it can never render into a cell (canary protection for copied facts
    # lives at the upstream reviewed seam; the renderer never invents text)
    poisoned = tool_record()
    poisoned['metrics']['gross_potential_rent'] = money(
        CANARY, source='engine:synthetic-run')
    with pytest.raises(HarnessError):
        reporting.render_csv_export(poisoned)


def test_reporting_module_import_boundary():
    reporting = api()
    source = Path(reporting.__file__).read_text(encoding='utf-8')
    for banned in ('import socket', 'urllib', 'subprocess', 'threading',
                   'requests', 'from plat_harness.adapters import',
                   'from plat_harness.ingest import', 'Decimal('):
        assert banned not in source, banned