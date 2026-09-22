"""Synthetic tests for the human reconciliation UX (Task 6.3).

Self-contained public tests; no real deal bytes, no models, no network, no
engine import or execution, no threads. All fixtures are synthetic; the canary
string must never surface in questions, decisions, records, errors, exception
chains or captured output. Interactive answers typed at a prompt are never an
authenticated human approval and never self-authorize execution.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError

CANARY = 'PRIVATE_CANARY_RESIDENT_TEXT'
SUBJECT = 'synthetic_property'
FULL_VALUES = {'millage': '25.31', 'horizon': '5'}
MILLAGE_QUESTION_CORE = 'combined property-tax millage'


def api():
    """Import the Task 6.3 seam; the RED run may raise ModuleNotFoundError."""
    from plat_harness import reconcile
    return reconcile


# ------------------------------------------------------------------ fixtures


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return path


def _pin(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(path: Path, fmt: str = 'pdf') -> dict:
    return {'sha256': _pin(path.read_bytes()), 'path': str(path), 'format': fmt}


def _sources(tmp_path: Path):
    root = tmp_path / 'sources'
    om = _write(root / 'om.pdf', b'synthetic offering memorandum ' + CANARY.encode())
    rr = _write(root / 'rr.pdf', b'synthetic rent roll rows')
    t12 = _write(root / 't12.xlsx', b'synthetic t12 workbook bytes')
    debt = _write(root / 'debt.csv', b'synthetic debt schedule bytes')
    return om, rr, t12, debt


def _full_inputs(om, rr, t12, debt) -> dict:
    return {'om': _ref(om), 'rr': _ref(rr),
            't12': _ref(t12, 'xlsx'), 'debt': _ref(debt, 'csv')}


def _two_inputs(om, rr) -> dict:
    return {'om': _ref(om), 'rr': _ref(rr)}


def _make_run(tmp_path, inputs, values=None, *, name='run', to='review_required'):
    from plat_harness import workflow
    workflow.create_run(tmp_path / name, SUBJECT, inputs, values)
    run_dir = tmp_path / name
    workflow.advance(run_dir, 'normalized')
    if to == 'review_required':
        workflow.advance(run_dir, 'review_required')
    return run_dir


def _blocked_run(tmp_path, values=None):
    """Two sources only: t12/debt/millage/horizon all blocked at review."""
    om, rr, _, _ = _sources(tmp_path)
    return _make_run(tmp_path, _two_inputs(om, rr), values)


def _review_run(tmp_path, values, *, name='run'):
    """Full sources with one value still blocked at the review stage."""
    om, rr, t12, debt = _sources(tmp_path)
    return _make_run(tmp_path, _full_inputs(om, rr, t12, debt), values, name=name)


def _record(run_dir: Path, field: str):
    from plat_harness import workflow
    record = workflow.resume(run_dir)
    return record['values'].get(field) is None


# ------------------------------------------------------- decision content gate


class TestDecisionContent:
    def test_interactive_and_noninteractive_decisions_match(self, tmp_path):
        """Both construction paths produce the same content-bound schema."""
        rec = api()
        run_a = _review_run(tmp_path, {'horizon': '5'}, name='run_a')
        run_b = _review_run(tmp_path, {'horizon': '5'}, name='run_b')
        interactive = rec.review_interactive(run_a, iter([]), {'millage': '25.31'})
        noninteractive = rec.review_noninteractive(run_b, {
            'decision_id': interactive['decision']['decision_id'],
            'resolutions': {'millage': {'value': '25.31',
                                        'evidence': 'host-review'}}})
        assert interactive['decision'] == noninteractive['decision']
        decision = interactive['decision']
        assert decision['schema'] == 'reconcile-decision/1.0.0'
        assert re.fullmatch(r'[0-9a-f]{64}', decision['content_sha256'])
        assert decision['resolutions']['millage'] == {
            'value': '25.31', 'evidence': 'host-review'}

    def test_decision_records_cannot_certify(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        result = rec.review_interactive(run_dir, iter([]), {'millage': '25.31'})
        assert result['decision']['certified'] is False
        assert result['decision']['authorizes_execution'] is False

    def test_interactive_review_records_decision_on_workflow(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        record = rec.review_interactive(run_dir, iter([]),
                                        {'millage': '25.31'})['record']
        assert record['stage'] == 'canonical_ready'
        assert record['resolutions']['millage'] == {'value': '25.31',
                                                   'evidence': 'host-review'}

    def test_decision_content_is_stable_across_construction(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        first = rec.decision_document(run_dir, {
            'millage': {'value': '25.31', 'evidence': 'host-review'}},
            decision_id='dec_one')
        second = rec.decision_document(run_dir, {
            'millage': {'value': '25.31', 'evidence': 'host-review'}},
            decision_id='dec_two', reviewer='another_host_reviewer')
        assert first['content_sha256'] == second['content_sha256']

    def test_generated_decision_id_is_content_bound(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        first = rec.decision_document(run_dir, {
            'millage': {'value': '25.31', 'evidence': 'host-review'}})
        second = rec.decision_document(run_dir, {
            'millage': {'value': '25.31', 'evidence': 'host-review'}})
        assert first['decision_id'] == second['decision_id']


# ------------------------------------------------------------- prompt quality


class TestPrompts:
    def test_each_blocked_field_prompted_exactly_once(self, tmp_path):
        rec = api()
        run_dir = _blocked_run(tmp_path)
        questions = rec.plan_questions(run_dir)
        fields = [q['field'] for q in questions]
        assert sorted(fields) == ['debt', 'horizon', 'millage', 't12']
        assert len(fields) == len(set(fields))

    def test_unblocked_run_has_no_questions(self, tmp_path):
        rec = api()
        om, rr, t12, debt = _sources(tmp_path)
        run_dir = _make_run(tmp_path, _full_inputs(om, rr, t12, debt),
                            FULL_VALUES)
        assert rec.plan_questions(run_dir) == []

    def test_question_shows_field_evidence_consequence_options_defer(self, tmp_path):
        rec = api()
        run_dir = _blocked_run(tmp_path)
        questions = rec.plan_questions(run_dir)
        millage = next(q for q in questions if q['field'] == 'millage')
        assert millage['prompt'] == rec.MILLAGE_PROMPT
        assert MILLAGE_QUESTION_CORE in millage['prompt']
        for key in ('field', 'prompt', 'evidence', 'consequence', 'options',
                    'defer'):
            assert key in millage
        assert 'millage' in millage['consequence']
        assert millage['defer'] == 'defer'

    def test_millage_prompt_retains_canonical_wording(self, tmp_path):
        rec = api()
        run_dir = _blocked_run(tmp_path)
        questions = rec.plan_questions(run_dir)
        millage = next(q for q in questions if q['field'] == 'millage')
        assert millage['prompt'] == (
            'What combined property-tax millage should be used? Enter mills '
            'per $1,000 of assessed value (for example, `25.31`).')
        assert rec.MILLAGE_PROMPT == millage['prompt']

    def test_t12_question_cites_pinned_evidence_not_bytes(self, tmp_path):
        rec = api()
        om, rr, _, _ = _sources(tmp_path)
        run_dir = _make_run(tmp_path, _two_inputs(om, rr))
        questions = rec.plan_questions(run_dir)
        t12 = next(q for q in questions if q['field'] == 't12')
        blob = json.dumps(t12)
        assert CANARY not in blob
        assert t12['evidence'] == ('missing source document; expected a pinned '
                                   'trailing-twelve-months statement')
        assert 'defer' in t12['options']

    def test_deferred_fields_stay_blocked(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        record = rec.review_interactive(run_dir, iter([]), {})['record']
        assert record['stage'] == 'review_required'
        assert record['outcome'] == 'needs_review_or_data'
        blocker = next(b for b in record['blockers'] if b['field'] == 'millage')
        assert blocker['resolved_by'] is None


# --------------------------------------------------------- EOF / cancellation


class TestEofCancellation:
    def test_eof_leaves_recoverable_draft(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        result = rec.review_interactive(run_dir, iter([]), {})
        assert result['decision'] is None
        record = result['record']
        assert record['stage'] == 'review_required'
        assert record['resolutions'] == {}

    def test_eof_never_applies_hidden_defaults(self, tmp_path):
        rec = api()
        run_dir = _blocked_run(tmp_path)
        record = rec.review_interactive(run_dir, iter([]), {})['record']
        for field in ('millage', 'horizon', 't12'):
            blocker = next(b for b in record['blockers'] if b['field'] == field)
            assert blocker['resolved_by'] is None
        assert record['values']['millage'] is None

    def test_defer_answer_defers_single_field(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        record = rec.review_interactive(run_dir, iter([]),
                                        {'millage': 'defer'})['record']
        assert record['stage'] == 'review_required'
        assert record['resolutions'] == {}

    def test_scripted_stdin_answers_question(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        record = rec.review_interactive(run_dir, iter(['25.31']), {})['record']
        assert record['stage'] == 'canonical_ready'
        assert record['resolutions']['millage']['value'] == '25.31'


# ------------------------------------------------------------- input blocking


class TestBlockedInputs:
    def test_malformed_millage_is_refused_not_defaulted(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        with pytest.raises(HarnessError) as caught:
            rec.review_interactive(run_dir, iter([]), {'millage': 'not-a-number'})
        assert caught.value.code == 'MISSING_MILLAGE'
        assert _record(run_dir, 'millage')

    def test_negative_and_zero_millage_refused(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        for bad in ('0', '-3.5'):
            with pytest.raises(HarnessError) as caught:
                rec.review_interactive(run_dir, iter([]), {'millage': bad})
            assert caught.value.code == 'MISSING_MILLAGE'

    def test_malformed_horizon_refused(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'millage': '25.31'})
        with pytest.raises(HarnessError) as caught:
            rec.review_interactive(run_dir, iter([]), {'horizon': 'soon'})
        assert caught.value.code == 'MISSING_HORIZON'
        assert _record(run_dir, 'horizon')

    def test_malformed_units_blocked_without_journal_write(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        before = sorted(os.listdir(run_dir / 'events'))
        with pytest.raises(HarnessError) as caught:
            rec.review_interactive(run_dir, iter([]), {'millage': '1.2.3'})
        assert caught.value.code == 'MISSING_MILLAGE'
        assert sorted(os.listdir(run_dir / 'events')) == before

    def test_unknown_field_resolution_refused(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        with pytest.raises(HarnessError) as caught:
            rec.review_noninteractive(run_dir, {
                'decision_id': 'dec_001', 'resolutions': {
                    'noi_target': {'value': '0.65',
                                   'evidence': 'synthetic-policy'}}})
        assert caught.value.code == 'INVALID_RESOLUTION'

    def test_source_document_field_not_prompt_answerable(self, tmp_path):
        rec = api()
        run_dir = _blocked_run(tmp_path)
        with pytest.raises(HarnessError) as caught:
            rec.review_interactive(run_dir, iter([]), {'t12': 'anything'})
        assert caught.value.code == 'INVALID_RESOLUTION'

    def test_unsigned_noninteractive_decision_refused(self, tmp_path):
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        with pytest.raises(HarnessError) as caught:
            rec.review_noninteractive(run_dir, {'resolutions': {}})
        assert caught.value.code == 'INVALID_INPUT'
        with pytest.raises(HarnessError) as caught:
            rec.review_noninteractive(run_dir, 'not-a-decision')
        assert caught.value.code == 'INVALID_INPUT'
        with pytest.raises(HarnessError) as caught:
            rec.review_noninteractive(run_dir, {
                'decision_id': 'dec_001', 'resolutions': {
                    'millage': {'value': '25.31'}}, 'extra_key': 'x'})
        assert caught.value.code == 'INVALID_INPUT'

    def test_conflicting_evidence_stays_blocked(self, tmp_path):
        """Two decisions disagreeing on a value never resolve the field."""
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        rec.review_noninteractive(run_dir, {
            'decision_id': 'dec_a', 'resolutions': {
                'millage': {'value': '25.31', 'evidence': 'synthetic-a'}}})
        with pytest.raises(HarnessError) as caught:
            rec.review_noninteractive(run_dir, {
                'decision_id': 'dec_b', 'resolutions': {
                    'millage': {'value': '19.99', 'evidence': 'synthetic-b'}}})
        assert caught.value.code == 'CONFLICT_UNRESOLVED'
        from plat_harness import workflow
        record = workflow.resume(run_dir)
        assert record['resolutions']['millage']['value'] == '25.31'

    def test_no_hidden_economic_defaults(self, tmp_path):
        rec = api()
        run_dir = _blocked_run(tmp_path)
        record = rec.review_interactive(run_dir, iter([]), {})['record']
        assert record['values'] == {'millage': None, 'horizon': None}


# --------------------------------------------------------- review-consequence


class TestReviewConsequence:
    def test_review_does_not_authorize_execution(self, tmp_path):
        """An approved review leaves execution unimplementable from the UX."""
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        record = rec.review_interactive(run_dir, iter([]),
                                        {'millage': '25.31'})['record']
        assert record['stage'] == 'canonical_ready'
        with pytest.raises(HarnessError) as caught:
            from plat_harness import workflow
            workflow.advance(run_dir, 'engine_executed', artifact_sha256='a' * 64)
        assert caught.value.code == 'INVALID_TRANSITION'

    def test_prompted_review_cannot_self_authorize(self, tmp_path):
        """Typing an answer at a prompt is never an approval record."""
        rec = api()
        run_dir = _review_run(tmp_path, {'horizon': '5'})
        decision = rec.review_interactive(run_dir, iter([]),
                                          {'millage': '25.31'})['decision']
        blob = json.dumps(decision)
        for banned in ('approval', 'execution_authorized', 'certified": true'):
            assert banned not in blob


# -------------------------------------------------------------- hygiene


class TestHygiene:
    def test_no_canary_in_questions_or_errors(self, tmp_path):
        rec = api()
        run_dir = _blocked_run(tmp_path)
        for q in rec.plan_questions(run_dir):
            assert CANARY not in json.dumps(q)
        with pytest.raises(HarnessError) as caught:
            rec.review_interactive(run_dir, iter([]), {'millage': 'junk'})
        assert CANARY not in str(caught.value)
        assert caught.value.__cause__ is None

    def test_module_source_hygiene(self):
        rec = api()
        source = Path(rec.__file__).read_text(encoding='utf-8')
        for banned in ('import socket', 'urllib', 'subprocess', 'threading',
                       'requests', 'adapters', 'ingest', 'openpyxl', 'xlrd',
                       'prompt('):
            assert banned not in source, banned

    def test_doc_exists(self):
        repo = Path(__file__).resolve().parents[1]
        doc = repo / 'docs' / 'RECONCILE.md'
        assert doc.is_file()
        text = doc.read_text(encoding='utf-8')
        assert 'reconcile/1.0.0' in text
        assert 'defer' in text