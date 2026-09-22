"""Task 7.3 honest acceptance scorecard tests (docs/ACCEPTANCE.md).

Public, synthetic-only contract tests for the honest acceptance scorecard.
The scorecard scores ten dimensions separately and may stay blocked: a green
engineering suite does not close a blocked gate. This suite never claims Item
2 four-count acceptance, reads no original deal sources, uses no PII, and
launches no model. All fixtures and cited evidence are synthetic; the public
aggregates are recomputed from the published PMS compatibility matrix, never
declared by hand.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / 'docs' / 'ACCEPTANCE.md'

CONTRACT_VERSION = 'acceptance-scorecard/1.0.0'
STATUS_VOCAB = (
    'blocked',
    'green_engineering_only',
    'not_evaluated',
    'not_measured',
    'synthetic_only',
)
DIMENSIONS = (
    'format_identification_source_accounting',
    'physical_units_status_use_observation',
    'four_count_reconciliation',
    'source_citations_resolve_and_match',
    'pii_removed_from_payloads_logs_exports',
    'canonical_inputs_approved_and_complete',
    'deterministic_engine_equality_and_accounting_reconciliation',
    'underwriting_ops_report_completeness',
    'analyst_review_actions_time_and_blocker_reasons',
    'model_extraction_metrics_only_when_measured',
)
EXPECTED_STATUSES = {
    'format_identification_source_accounting': 'synthetic_only',
    'physical_units_status_use_observation': 'synthetic_only',
    'four_count_reconciliation': 'blocked',
    'source_citations_resolve_and_match': 'green_engineering_only',
    'pii_removed_from_payloads_logs_exports': 'green_engineering_only',
    'canonical_inputs_approved_and_complete': 'blocked',
    'deterministic_engine_equality_and_accounting_reconciliation': 'green_engineering_only',
    'underwriting_ops_report_completeness': 'synthetic_only',
    'analyst_review_actions_time_and_blocker_reasons': 'not_measured',
    'model_extraction_metrics_only_when_measured': 'not_measured',
}
AGGREGATE_KEYS = (
    'contract_version',
    'readers',
    'ran_against_originals',
    'real_export_verified',
    'four_count_reconciled',
    'acquisition_gaps',
    'engine_eligibility',
    'item2_milestone',
    'two_archetype_gate',
)
PRIVATE_LITERALS = (
    'raw_inputs',
    '/home/',
    '/Users/',
    'resident_name',
    '@gmail',
    '@yahoo',
    '.xlsm',
    '00099 -',
    '00101 -',
)
METRIC_WORDS = ('accuracy', 'precision', 'recall', 'f1')
ROW = re.compile(r'^\| `([a-z0-9_]+)` \| ([a-z_]+) \| (.+) \|$', re.M)
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def doc_text():
    assert DOC.is_file(), 'docs/ACCEPTANCE.md must exist'
    return DOC.read_text(encoding='utf-8')


def doc_flat():
    """Whitespace-normalized text: phrase checks must not depend on wrapping."""
    return ' '.join(doc_text().split())


def scorecard_rows(text):
    """Parse the scorecard table into {dimension: (status, basis)}."""
    rows = {}
    for match in ROW.finditer(text):
        slug, status, basis = match.groups()
        assert slug not in rows, f'duplicate dimension row: {slug}'
        rows[slug] = (status, basis)
    return rows


def matrix():
    """Deep-copied, validated public compatibility matrix."""
    from plat_harness.ingest import compatibility
    return copy.deepcopy(compatibility.validate_matrix(compatibility.public_matrix()))


def aggregates(text):
    blocks = re.findall(r'```json\n(.*?)```', text, re.S)
    assert len(blocks) == 1, 'exactly one public aggregate JSON block is required'
    return json.loads(blocks[0])


class TestScorecardContract:
    def test_doc_exists(self):
        assert DOC.is_file()

    def test_contract_version_stated(self):
        assert CONTRACT_VERSION in doc_text()

    def test_doc_disclaims_certification_engine_permission_and_milestone(self):
        text = doc_flat()
        assert 'not vendor certification' in text
        assert 'not engine permission' in text
        assert 'not Item 2 milestone acceptance' in text

    def test_all_ten_dimensions_scored_separately(self):
        rows = scorecard_rows(doc_text())
        assert set(rows) == set(DIMENSIONS)
        assert len(rows) == len(DIMENSIONS)

    def test_statuses_use_closed_vocabulary(self):
        for slug, (status, _basis) in scorecard_rows(doc_text()).items():
            assert status in STATUS_VOCAB, slug

    def test_statuses_are_the_honest_current_state(self):
        rows = scorecard_rows(doc_text())
        for slug, expected in EXPECTED_STATUSES.items():
            assert rows[slug][0] == expected, slug

    def test_every_dimension_has_a_nonempty_basis(self):
        for slug, (_status, basis) in scorecard_rows(doc_text()).items():
            assert len(basis) >= 40, slug

    def test_blocked_dimensions_carry_explicit_reasons(self):
        for slug, (status, basis) in scorecard_rows(doc_text()).items():
            if status == 'blocked':
                assert basis.startswith('Blocked: '), slug
                low = basis.lower()
                assert 'evidence' in low or 'approved' in low, slug


class TestHonestGates:
    def test_four_count_reconciliation_stays_blocked(self):
        rows = scorecard_rows(doc_text())
        status, basis = rows['four_count_reconciliation']
        assert status == 'blocked'
        low = ' '.join(basis.split()).lower()
        assert 'independently reviewed use evidence is absent' in low
        assert 'independent down evidence is absent' in low
        assert 'zero readers ran against originals' in low

    def test_rejected_noop_partial_parsing_never_counts_as_normalization(self):
        assert ('A rejected, no-op or partial parse never counts as completed '
                'normalization') in doc_flat()

    def test_synthetic_examples_are_not_real_vendor_validation(self):
        text = doc_flat()
        assert 'Synthetic examples are not real vendor validation' in text
        assert 'All synthetic evaluation stays labelled synthetic' in text

    def test_arithmetic_equality_is_not_economic_approval(self):
        text = doc_flat()
        assert 'Arithmetic equality is not economic approval' in text
        assert 'not_evaluated' in text

    def test_green_suite_does_not_close_blocked_gates(self):
        text = doc_flat()
        assert 'A green engineering suite does not close a blocked gate' in text
        assert ('remain blocked for missing evidence even with a green '
                'engineering suite') in text

    def test_test_count_does_not_replace_source_coverage(self):
        text = doc_flat()
        assert ('A green engineering suite and a high test count do not '
                'replace source coverage') in text
        assert 'a parse is not engine permission' in text

    def test_model_metrics_absent_unless_actually_measured(self):
        text = doc_text()
        low = text.lower()
        for word in METRIC_WORDS:
            assert word not in low, word
        assert '%' not in text
        rows = scorecard_rows(text)
        assert rows['model_extraction_metrics_only_when_measured'][0] == 'not_measured'
        assert rows['analyst_review_actions_time_and_blocker_reasons'][0] == 'not_measured'

    def test_heldout_source_policy_is_recorded_honestly(self):
        text = doc_flat()
        assert 'No real original documents are lawfully available to hold out' in text
        assert 'held-out source set and version' in text

    def test_private_manifests_public_aggregates_only(self):
        text = doc_flat()
        assert 'private acceptance manifests' in text
        assert 'anonymized public aggregates' in text

    def test_two_archetype_gate_blockers_are_named(self):
        text = doc_text()
        assert 'Abilene garden' in text
        assert '360 Market Square high-rise' in text
        assert 'MISSING_REVIEWED_USE' in text
        assert 'MISSING_INDEPENDENT_DOWN' in text

    def test_dimension_bases_name_their_evidence_seams(self):
        rows = scorecard_rows(doc_text())
        assert 'acquisition gap' in rows['format_identification_source_accounting'][1]
        assert 'unknown use' in rows['physical_units_status_use_observation'][1]
        assert 're-anchor' in rows['source_citations_resolve_and_match'][1]
        assert 'redaction' in rows['pii_removed_from_payloads_logs_exports'][1].lower()
        assert ('provider labels' in
                rows['deterministic_engine_equality_and_accounting_reconciliation'][1])
        assert 'watermark' in rows['underwriting_ops_report_completeness'][1]
        assert 'journal' in rows['analyst_review_actions_time_and_blocker_reasons'][1]


class TestPublicAggregates:
    def test_single_aggregate_block_matches_compatibility_matrix(self):
        parsed = aggregates(doc_text())
        current = matrix()
        expected = {
            'contract_version': CONTRACT_VERSION,
            'readers': current['totals']['readers'],
            'ran_against_originals': current['totals']['ran'],
            'real_export_verified': current['totals']['real_export_verified'],
            'four_count_reconciled': current['totals']['four_count_reconciled'],
            'acquisition_gaps': current['totals']['acquisition_gaps'],
            'engine_eligibility': current['engine_eligibility'],
            'item2_milestone': current['item2_milestone'],
            'two_archetype_gate': current['two_archetype_gate']['status'],
        }
        assert set(parsed) == set(AGGREGATE_KEYS)
        assert parsed == expected

    def test_aggregates_admit_zero_ran_and_blocked_gate(self):
        parsed = aggregates(doc_text())
        assert parsed['ran_against_originals'] == 0
        assert parsed['real_export_verified'] == 0
        assert parsed['four_count_reconciled'] == 0
        assert parsed['acquisition_gaps'] >= 1
        assert parsed['two_archetype_gate'] == 'blocked'
        assert parsed['engine_eligibility'] == 'not_evaluated'
        assert parsed['item2_milestone'] == 'not_evaluated'


class TestMatrixSourceIntegrity:
    def test_matrix_source_is_isolated_from_caller_mutation(self):
        from plat_harness.ingest import compatibility
        first = compatibility.public_matrix()
        first['totals']['ran'] = 99
        first['two_archetype_gate']['status'] = 'pass'
        first['readers'][0]['source_test_status'] = 'real_export_verified'
        second = compatibility.public_matrix()
        assert second['totals']['ran'] == 0
        assert second['two_archetype_gate']['status'] == 'blocked'
        assert second['readers'][0]['source_test_status'] == 'synthetic_only'


class TestPublicSanitize:
    def test_doc_has_no_private_identifiers_or_pii(self):
        text = doc_text()
        for snippet in PRIVATE_LITERALS:
            assert snippet not in text, snippet
        assert not EMAIL.search(text)