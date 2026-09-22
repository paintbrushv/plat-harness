"""Public synthetic tests for the T12 source-accounting preview; no IO or math."""
import copy
import hashlib
import importlib.util
import json
import traceback

import pytest

SUBJECT = 'subject_demo'
AS_OF = '2026-09-30'
SOURCE = 'src_' + '2' * 32
SHA = hashlib.sha256(b'public synthetic t12 source').hexdigest()
SHEET = 1
FIRST_ROW = 4
FIRST_COLUMN = 2

MONTH_KEYS = ['2025-09', '2025-10', '2025-11', '2025-12', '2026-01', '2026-02',
              '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08']
MONTH_HEADERS = ['Sep 2025', 'Oct 2025', 'Nov 2025', 'Dec 2025', 'Jan 2026',
                 'Feb 2026', 'Mar 2026', 'Apr 2026', 'May 2026', 'Jun 2026',
                 'Jul 2026', 'Aug 2026']
WINDOW = {'first_month': '2025-09', 'last_month': '2026-08'}

ACCOUNTS = {
    'Residential Rental Income': 'residential_rental_income',
    'Commercial Rental Income': 'commercial_rental_income',
    'Parking Income': 'ancillary_income_recurring',
    'Application Fees': 'ancillary_income_transactional',
    'Other Credits': 'other_income',
    'Property Taxes': 'property_taxes',
    'Property Insurance': 'property_insurance',
    'Employee Benefits': 'employee_benefits',
    'Repairs & Maintenance': 'repairs_and_maintenance',
}


def mapping_ref(label):
    return 'map_' + hashlib.sha256(('approved:' + label).encode()).hexdigest()


def mapping(extra=()):
    entries = [{'source_label': label, 'canonical_account': account,
                'mapping_ref': mapping_ref(label)}
               for label, account in ACCOUNTS.items()]
    entries.extend(extra)
    return tuple(entries)


SOURCE_RECORD = {'source_id': SOURCE, 'sha256': SHA, 'role': 'original',
                 'original_source_ids': [], 'subject_id': SUBJECT, 'as_of': AS_OF}


def api():
    from plat_harness.ingest import t12_normalizer as module
    assert callable(module.preview_t12)
    assert callable(module.validate_t12_preview)
    assert callable(module.accounting_envelope)
    assert callable(module.canonical_bytes)
    return module


def amounts(value, count=12):
    return [value] * count


def header(extra=()):
    return ['Account'] + MONTH_HEADERS + list(extra)


def happy_grid():
    return [
        header(),
        ['INCOME'],
        ['Residential Rental Income'] + amounts('41000.00'),
        ['Commercial Rental Income'] + amounts('3200.00'),
        ['Parking Income'] + amounts('1500.00'),
        ['Application Fees'] + amounts('600.00'),
        ['TOTAL REVENUE'] + amounts('99000.00'),
        ['EXPENSES'],
        ['Property Taxes'] + amounts('2100.00'),
        ['Property Insurance'] + amounts('(890.00)'),
        ['Employee Benefits'] + amounts('1200.00'),
        ['Repairs & Maintenance'] + amounts('3,450.00'),
        ['NET OPERATING INCOME'] + amounts('50000.00'),
    ]


def preview(grid=None, **over):
    module = api()
    kwargs = dict(grid=grid if grid is not None else happy_grid(),
                  first_row=FIRST_ROW, first_column=FIRST_COLUMN, sheet=SHEET,
                  source=SOURCE_RECORD, subject_id=SUBJECT, as_of=AS_OF,
                  window=WINDOW, header_row=0, label_column=0, mapping=mapping())
    kwargs.update(over)
    return module.preview_t12(**kwargs)


def refuse(**over):
    module = api()
    with pytest.raises(module.T12PreviewError) as caught:
        preview(**over)
    assert caught.value.code in module.ERROR_CODES
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))
    return caught.value.code


def refuse_value(value, subject_id=SUBJECT, as_of=AS_OF):
    module = api()
    with pytest.raises(module.T12PreviewError) as caught:
        module.validate_t12_preview(value, subject_id=subject_id, as_of=as_of)
    assert caught.value.code in module.ERROR_CODES
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))
    return caught.value.code


def line_items(result):
    return [row for row in result['rows'] if row['row_kind'] == 'line_item']


def totals_rows(result):
    return [row for row in result['rows'] if row['row_kind'] == 'total']


def issues(result, code):
    return [issue for issue in result['issues'] if issue['code'] == code]


def all_observations(result):
    return [entry['observation'] for row in result['rows'] for entry in row['monthly']]


def no_floats(node):
    stack = [node]
    while stack:
        item = stack.pop()
        if type(item) is float:
            return False
        if type(item) is dict:
            stack.extend(item.values())
        elif type(item) is list:
            stack.extend(item)
    return True


def test_t12_preview_seam_exists():
    assert importlib.util.find_spec('plat_harness.ingest.t12_normalizer') is not None


def test_contract_version_and_public_surface():
    module = api()
    assert module.CONTRACT_VERSION == 'ingest-t12-preview/1.0.0'
    assert module.ADAPTER == {'id': 't12-normalizer', 'version': '1.0.0'}
    assert type(module.ERROR_CODES) is frozenset
    assert issubclass(module.T12PreviewError, ValueError)
    accounts = module.CANONICAL_ACCOUNTS
    for account in ('residential_rental_income', 'commercial_rental_income',
                    'ancillary_income_recurring', 'ancillary_income_transactional',
                    'other_income', 'property_taxes', 'property_insurance',
                    'employee_benefits', 'repairs_and_maintenance'):
        assert account in accounts
    assert 'interest_expense' not in accounts
    assert 'depreciation' not in accounts
    assert accounts['employee_benefits'] if isinstance(accounts, dict) else True


def test_complete_t12_preview_emits_line_items_and_completeness():
    result = preview()
    assert result['contract_version'] == 'ingest-t12-preview/1.0.0'
    assert (result['subject_id'], result['as_of']) == (SUBJECT, AS_OF)
    assert result['adapter'] == {'id': 't12-normalizer', 'version': '1.0.0'}
    assert result['status'] == 'observed_unvalidated'
    assert result['issues'] == []
    assert result['coverage'] == {'status': 'complete', 'expected_months': MONTH_KEYS,
                                  'present_months': MONTH_KEYS, 'missing_months': []}
    assert [entry['month'] for entry in result['months']] == MONTH_KEYS
    assert result['months'][0] == {'month': '2025-09', 'column': FIRST_COLUMN + 1,
                                   'header': 'Sep 2025'}
    assert [row['row'] for row in result['rows']] == list(
        range(FIRST_ROW + 1, FIRST_ROW + 13))
    assert result['source'] == SOURCE_RECORD
    assert result['sheet'] == SHEET
    assert result['first_row'] == FIRST_ROW and result['first_column'] == FIRST_COLUMN
    assert result['window'] == WINDOW
    items = line_items(result)
    assert len(items) == 8
    assert [row['canonical_account'] for row in items] == [
        'residential_rental_income', 'commercial_rental_income',
        'ancillary_income_recurring', 'ancillary_income_transactional',
        'property_taxes', 'property_insurance', 'employee_benefits',
        'repairs_and_maintenance']
    assert all(row['mapping_state'] == 'mapped' for row in items)
    assert all(row['mapping_ref'] == mapping_ref(row['source_label']) for row in items)
    assert len(totals_rows(result)) == 2
    assert result['census'] == {'preceding': 0, 'header': 1, 'blank': 0,
                                'section': 2, 'line_item': 8, 'total': 2,
                                'unlabeled': 0}
    assert result['header_row'] == FIRST_ROW and result['label_column'] == FIRST_COLUMN
    assert set(result) == {'contract_version', 'subject_id', 'as_of', 'adapter',
                           'source', 'sheet', 'header_row', 'label_column',
                           'first_row', 'first_column', 'window', 'months',
                           'coverage', 'columns', 'rows', 'issues', 'census',
                           'status'}
    assert set(items[0]) == {'row', 'row_kind', 'source_label', 'canonical_account',
                             'mapping_state', 'mapping_ref', 'monthly'}
    assert set(result['columns'][0]) == {'column', 'role', 'header'}
    assert result['columns'][0] == {'column': FIRST_COLUMN, 'role': 'label',
                                    'header': 'Account'}
    assert no_floats(result)
    json.dumps(result, allow_nan=False)


def test_monthly_observations_are_accounting_money_with_citations():
    result = preview()
    item = line_items(result)[0]
    assert item['source_label'] == 'Residential Rental Income'
    assert len(item['monthly']) == 12
    entry = item['monthly'][0]
    assert set(entry) == {'month', 'column', 'observation'}
    assert entry['month'] == '2025-09' and entry['column'] == FIRST_COLUMN + 1
    observed = entry['observation']
    assert set(observed) == {'observation_id', 'source_id', 'kind',
                             'measurement_basis', 'amount', 'cell_origin', 'citation'}
    assert observed['source_id'] == SOURCE
    assert observed['kind'] == 'report_total'
    assert observed['measurement_basis'] == 'report'
    assert observed['cell_origin'] == 'typed'
    assert observed['amount']['decimal'] == '41000.00'
    assert observed['amount']['source_text'] == '41000.00'
    assert observed['amount']['currency'] == 'USD'
    assert observed['amount']['unit'] == 'currency'
    assert observed['amount']['period'] == 'month'
    citation = {'source_id': SOURCE, 'source_sha256': SHA, 'sheet': SHEET,
                'row': FIRST_ROW + 2, 'row_end': FIRST_ROW + 2,
                'column': FIRST_COLUMN + 1}
    assert observed['citation'] == citation
    expected = 'mny_' + hashlib.sha256(json.dumps(
        citation, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False).encode()).hexdigest()
    assert observed['observation_id'] == expected
    assert len({observed['observation_id'] for observed in all_observations(result)}) \
        == 120
    maintenance = [row for row in line_items(result)
                   if row['source_label'] == 'Repairs & Maintenance'][0]
    amount = maintenance['monthly'][0]['observation']['amount']
    assert amount['source_text'] == '3,450.00' and amount['decimal'] == '3450.00'
    insurance = [row for row in line_items(result)
                 if row['source_label'] == 'Property Insurance'][0]
    assert insurance['monthly'][0]['observation']['amount']['decimal'] == '-890.00'
    assert insurance['monthly'][0]['observation']['amount']['source_text'] == '(890.00)'


def test_validate_round_trip_and_defensive_copy():
    module = api()
    grid = happy_grid()
    snapshot = copy.deepcopy(grid)
    result = preview(grid=grid)
    assert grid == snapshot
    again = preview(grid=copy.deepcopy(grid))
    assert result == again
    checked = module.validate_t12_preview(copy.deepcopy(result),
                                          subject_id=SUBJECT, as_of=AS_OF)
    assert checked == result and checked is not result
    mutate = module.validate_t12_preview(copy.deepcopy(result),
                                         subject_id=SUBJECT, as_of=AS_OF)
    mutate['rows'][1]['monthly'][0]['observation']['amount']['decimal'] = '0'
    assert result['rows'][1]['monthly'][0]['observation']['amount']['decimal'] == \
        '41000.00'
    refuse_value(checked, subject_id=SUBJECT, as_of='2026-10-01')


def test_canonical_bytes_are_deterministic_and_validate_first():
    module = api()
    result = preview()
    expected = json.dumps(result, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False).encode()
    assert module.canonical_bytes(result, subject_id=SUBJECT, as_of=AS_OF) == expected
    assert module.canonical_bytes(preview(), subject_id=SUBJECT,
                                  as_of=AS_OF) == expected
    tampered = copy.deepcopy(result)
    tampered['status'] = 'blocked'
    with pytest.raises(module.T12PreviewError):
        module.canonical_bytes(tampered, subject_id=SUBJECT, as_of=AS_OF)


def test_duplicate_month_column_refuses():
    grid = [ ['Account', 'Sep 2025', 'Oct 2025', 'Sep 2025'],
             ['Residential Rental Income', '1.00', '2.00', '3.00']]
    assert refuse(grid=grid) == 'DUPLICATE_MONTH'
    grid = [ ['Account', 'Sep 2025', 'Oct 2025', 'September 2025'],
             ['Residential Rental Income', '1.00', '2.00', '3.00']]
    assert refuse(grid=grid) == 'DUPLICATE_MONTH'


def test_partial_month_coverage_blocks_and_names_missing_months():
    grid = [ ['Account'] + MONTH_HEADERS[:7],
             ['Residential Rental Income'] + amounts('41000.00', 7),
             ['Property Taxes'] + amounts('2100.00', 7)]
    result = preview(grid=grid)
    assert result['status'] == 'blocked'
    assert result['coverage']['status'] == 'partial'
    assert result['coverage']['expected_months'] == MONTH_KEYS
    assert result['coverage']['present_months'] == MONTH_KEYS[:7]
    assert result['coverage']['missing_months'] == [
        '2026-04', '2026-05', '2026-06', '2026-07', '2026-08']
    assert [entry['month'] for entry in result['months']] == MONTH_KEYS[:7]
    partial = issues(result, 'PARTIAL_MONTH_COVERAGE')
    assert len(partial) == 1 and partial[0]['severity'] == 'blocker'
    assert set(partial[0]) == {'issue_id', 'code', 'severity', 'citation',
                               'observation_ids'}
    assert partial[0]['citation']['row'] == FIRST_ROW
    assert partial[0]['citation']['column'] == FIRST_COLUMN + 1
    assert partial[0]['citation']['source_id'] == SOURCE
    assert partial[0]['citation']['source_sha256'] == SHA
    assert partial[0]['observation_ids'] == []


def test_partial_statement_is_never_annualized():
    module = api()
    grid = [ ['Account'] + MONTH_HEADERS[:7],
             ['Residential Rental Income'] + amounts('41000.00', 7),
             ['Property Taxes'] + amounts('2100.00', 7)]
    result = preview(grid=grid)
    assert result['status'] == 'blocked'
    assert not any('annual' in key for key in result)
    for name in ('sum', 'total', 'aggregate', 'annualize', 'annualized',
                 'noi', 'cap_rate', 'dscr', 'effective_rent'):
        assert not callable(getattr(module, name, None))
    assert not hasattr(module, 'run_underwriting')
    revenue = totals_rows(preview())[0]
    # The total row is source-stated evidence: preserved verbatim, never
    # recomputed or cross-checked against line items here.
    assert revenue['monthly'][0]['observation']['amount']['decimal'] == '99000.00'
    assert revenue['monthly'][0]['observation']['amount']['source_text'] == '99000.00'


def test_cumulative_and_total_columns_are_never_monthly_figures():
    grid = [header(['YTD', 'Total']),
            ['Residential Rental Income'] + amounts('41000.00') + ['555000.00', '492000.00'],
            ['Property Taxes'] + amounts('2100.00') + ['25200.00', '25200.00']]
    result = preview(grid=grid)
    roles = {entry['column']: entry['role'] for entry in result['columns']}
    assert roles[FIRST_COLUMN + 13] == 'cumulative'
    assert roles[FIRST_COLUMN + 14] == 'cumulative'
    assert len(result['months']) == 12
    cited = {observed['citation']['column'] for observed in all_observations(result)}
    assert FIRST_COLUMN + 13 not in cited and FIRST_COLUMN + 14 not in cited
    item = line_items(result)[0]
    assert len(item['monthly']) == 12
    assert all(entry['column'] <= FIRST_COLUMN + 12 for entry in item['monthly'])
    assert result['issues'] == []


def test_totals_and_subtotal_rows_never_reenter_line_items():
    result = preview()
    items = line_items(result)
    assert all(row['mapping_state'] == 'mapped' for row in items)
    assert sorted(row['source_label'] for row in items) == \
        sorted(set(ACCOUNTS) - {'Other Credits'})
    for row in totals_rows(result):
        assert row['row_kind'] == 'total'
        assert row['canonical_account'] is None and row['mapping_ref'] is None
        assert row['mapping_state'] == 'not_applicable'
        assert len(row['monthly']) == 12
        assert all(entry['observation'] is not None for entry in row['monthly'])
    assert [row['source_label'] for row in totals_rows(result)] == \
        ['TOTAL REVENUE', 'NET OPERATING INCOME']
    assert result['census']['total'] == 2 and result['census']['line_item'] == 8
    assert issues(result, 'UNMAPPED_ACCOUNT') == []
    assert not any(row['source_label'] and
                   row['source_label'].upper().startswith('TOTAL') for row in items)


def test_negatives_and_credits_are_preserved_as_is():
    grid = happy_grid() + [['Other Credits'] + amounts('-250.00')]
    result = preview(grid=grid)
    credit = [row for row in line_items(result)
              if row['source_label'] == 'Other Credits'][0]
    amount = credit['monthly'][5]['observation']['amount']
    assert amount['decimal'] == '-250.00'
    assert amount['source_text'] == '-250.00'
    insurance = [row for row in line_items(result)
                 if row['source_label'] == 'Property Insurance'][0]
    amount = insurance['monthly'][11]['observation']['amount']
    assert amount['decimal'] == '-890.00'
    assert amount['source_text'] == '(890.00)'
    assert result['status'] == 'observed_unvalidated'


def test_employee_benefits_and_property_insurance_stay_distinct():
    result = preview()
    benefits = [row for row in line_items(result)
                if row['source_label'] == 'Employee Benefits'][0]
    insurance = [row for row in line_items(result)
                 if row['source_label'] == 'Property Insurance'][0]
    assert benefits['canonical_account'] == 'employee_benefits'
    assert insurance['canonical_account'] == 'property_insurance'
    assert benefits['canonical_account'] != insurance['canonical_account']
    assert benefits['mapping_ref'] != insurance['mapping_ref']
    assert len({entry['observation']['observation_id']
                for entry in benefits['monthly']}) == 12
    assert not ({entry['observation']['observation_id']
                 for entry in benefits['monthly']} &
                {entry['observation']['observation_id']
                 for entry in insurance['monthly']})
    assert [entry['observation']['citation']['row'] for entry in benefits['monthly']] \
        == [FIRST_ROW + 10] * 12
    assert [entry['observation']['citation']['row'] for entry in insurance['monthly']] \
        == [FIRST_ROW + 9] * 12


def test_commercial_and_ancillary_income_categories_stay_separate():
    result = preview()
    by_label = {row['source_label']: row for row in line_items(result)}
    assert by_label['Residential Rental Income']['canonical_account'] == \
        'residential_rental_income'
    assert by_label['Commercial Rental Income']['canonical_account'] == \
        'commercial_rental_income'
    assert by_label['Parking Income']['canonical_account'] == \
        'ancillary_income_recurring'
    assert by_label['Application Fees']['canonical_account'] == \
        'ancillary_income_transactional'
    canonicals = [by_label[label]['canonical_account'] for label in
                  ('Residential Rental Income', 'Commercial Rental Income',
                   'Parking Income', 'Application Fees')]
    assert len(set(canonicals)) == 4
    assert len({row['row'] for row in by_label.values()}) == 8


def test_unmapped_material_account_blocks_but_is_never_dropped():
    grid = happy_grid() + [['Late Fees'] + amounts('75.00')]
    result = preview(grid=grid)
    assert result['status'] == 'blocked'
    row = [row for row in line_items(result) if row['source_label'] == 'Late Fees'][0]
    assert row['mapping_state'] == 'unmapped'
    assert row['canonical_account'] is None and row['mapping_ref'] is None
    assert len(row['monthly']) == 12
    assert row['monthly'][0]['observation']['amount']['decimal'] == '75.00'
    assert row['monthly'][0]['observation']['amount']['source_text'] == '75.00'
    unmapped = issues(result, 'UNMAPPED_ACCOUNT')
    assert len(unmapped) == 1
    assert unmapped[0]['severity'] == 'blocker'
    assert unmapped[0]['citation']['row'] == row['row']
    assert unmapped[0]['citation']['column'] == FIRST_COLUMN
    assert unmapped[0]['observation_ids'] == sorted(
        entry['observation']['observation_id'] for entry in row['monthly'])
    assert result['census']['line_item'] == 9


def test_unmapped_zero_balance_account_is_an_explicit_warning():
    grid = happy_grid() + [['Legacy Fee'] + amounts('0.00')]
    result = preview(grid=grid)
    assert result['status'] == 'observed_unvalidated'
    unmapped = issues(result, 'UNMAPPED_ACCOUNT')
    assert len(unmapped) == 1 and unmapped[0]['severity'] == 'warning'
    row = [row for row in line_items(result) if row['source_label'] == 'Legacy Fee'][0]
    assert row['mapping_state'] == 'unmapped'
    assert unmapped[0]['citation']['row'] == row['row']


def test_unsupported_categories_block_rather_than_omit():
    entry = {'source_label': 'Interest Expense',
             'canonical_account': 'interest_expense',
             'mapping_ref': mapping_ref('Interest Expense')}
    assert refuse(mapping=mapping([entry])) == 'INVALID_MAPPING'
    grid = happy_grid() + [['Interest Expense'] + amounts('3300.00')]
    result = preview(grid=grid)
    row = [row for row in line_items(result)
           if row['source_label'] == 'Interest Expense'][0]
    assert row['mapping_state'] == 'unmapped'
    assert len(row['monthly']) == 12
    assert row['monthly'][0]['observation']['amount']['decimal'] == '3300.00'
    unmapped = issues(result, 'UNMAPPED_ACCOUNT')
    assert len(unmapped) == 1 and unmapped[0]['severity'] == 'blocker'
    assert result['status'] == 'blocked'


def test_mapping_refs_are_opaque_and_required():
    label = 'Residential Rental Income'
    for bad in ({'source_label': label, 'canonical_account': 'residential_rental_income'},
                {'source_label': label, 'canonical_account': 'residential_rental_income',
                 'mapping_ref': 'map_' + 'a' * 63},
                {'source_label': label, 'canonical_account': 'residential_rental_income',
                 'mapping_ref': 'map_' + 'A' * 64},
                {'source_label': label, 'canonical_account': 'residential_rental_income',
                 'mapping_ref': 'guess_' + 'a' * 64}):
        assert refuse(mapping=mapping([bad])) == 'INVALID_MAPPING'
    result = preview()
    item = line_items(result)[0]
    assert item['mapping_ref'] == mapping_ref(label)
    assert item['mapping_ref'].startswith('map_')


def test_no_invented_or_fuzzy_mappings():
    grid = happy_grid() + [['parking income'] + amounts('9.00'),
                           ['Parking Income Extra'] + amounts('10.00'),
                           ['Parking Income '] + amounts('11.00')]
    result = preview(grid=grid)
    states = [(row['source_label'], row['mapping_state']) for row in line_items(result)]
    assert ('parking income', 'unmapped') in states
    assert ('Parking Income Extra', 'unmapped') in states
    assert ('Parking Income ', 'unmapped') in states
    assert ('Parking Income', 'mapped') in states
    unmapped = issues(result, 'UNMAPPED_ACCOUNT')
    assert len(unmapped) == 3
    assert all(issue['severity'] == 'blocker' for issue in unmapped)
    assert result['status'] == 'blocked'


def test_duplicate_mapping_label_refuses():
    entry = {'source_label': 'Residential Rental Income',
             'canonical_account': 'residential_rental_income',
             'mapping_ref': mapping_ref('Residential Rental Income')}
    assert refuse(mapping=mapping([entry])) == 'INVALID_MAPPING'


def test_window_shape_and_span_refusals():
    assert refuse(window={'first_month': '2026-13', 'last_month': '2026-12'}) == \
        'INVALID_WINDOW'
    assert refuse(window={'first_month': '2025-9', 'last_month': '2026-08'}) == \
        'INVALID_WINDOW'
    assert refuse(window={'first_month': '2026-08', 'last_month': '2025-09'}) == \
        'INVALID_WINDOW'
    assert refuse(window={'first_month': '2025-08', 'last_month': '2026-08'}) == \
        'INVALID_WINDOW'
    assert refuse(window={'first_month': '2025-09'}) == 'INVALID_WINDOW'
    grid = [ ['Account'] + MONTH_HEADERS[:10],
             ['Residential Rental Income'] + amounts('41000.00', 10)]
    result = preview(grid=grid, window={'first_month': '2025-09',
                                         'last_month': '2026-06'})
    assert result['status'] == 'blocked'
    assert result['coverage']['status'] == 'partial'
    assert result['coverage']['expected_months'] == MONTH_KEYS[:10]
    assert result['coverage']['missing_months'] == []
    assert len(issues(result, 'PARTIAL_MONTH_COVERAGE')) == 1


def test_month_outside_declared_window_refuses():
    grid = [ ['Account', 'Sep 2024'] + MONTH_HEADERS,
             ['Residential Rental Income', '40000.00'] + amounts('41000.00')]
    assert refuse(grid=grid) == 'MONTH_OUTSIDE_WINDOW'


def test_missing_month_cells_are_null_never_zero():
    grid = happy_grid()
    grid[2] = ['Residential Rental Income'] + amounts('41000.00')
    grid[2][3] = None
    grid[2][5] = 'n/a'
    result = preview(grid=grid)
    assert result['status'] == 'blocked'
    item = line_items(result)[0]
    assert item['monthly'][2]['observation']['amount'] is None
    assert item['monthly'][4]['observation']['amount'] is None
    assert item['monthly'][2]['observation']['citation']['column'] == FIRST_COLUMN + 3
    assert all(entry['observation']['amount'] is not None or
               entry['observation']['amount'] is None
               for entry in item['monthly'])
    assert not any(entry['observation']['amount'] is not None and
                   entry['observation']['amount']['decimal'] == '0' and
                   entry['observation']['amount']['source_text'] in (None, '', 'n/a')
                   for entry in item['monthly'])
    missing = issues(result, 'MISSING_MONTH_CELL')
    assert len(missing) == 1 and missing[0]['severity'] == 'blocker'
    assert missing[0]['observation_ids'] == sorted(
        [item['monthly'][2]['observation']['observation_id'],
         item['monthly'][4]['observation']['observation_id']])
    assert missing[0]['citation']['row'] == item['row']


def test_unlabeled_numeric_rows_block_and_are_preserved():
    grid = happy_grid() + [[None] + amounts('450.00')]
    result = preview(grid=grid)
    assert result['status'] == 'blocked'
    row = [row for row in result['rows'] if row['row_kind'] == 'unlabeled'][0]
    assert row['source_label'] is None
    assert row['mapping_state'] == 'unmapped'
    assert row['canonical_account'] is None and row['mapping_ref'] is None
    assert len(row['monthly']) == 12
    assert row['monthly'][0]['observation']['amount']['decimal'] == '450.00'
    unlabeled = issues(result, 'UNLABELED_ROW')
    assert len(unlabeled) == 1 and unlabeled[0]['severity'] == 'blocker'
    assert unlabeled[0]['citation']['row'] == row['row']
    assert unlabeled[0]['citation']['column'] == FIRST_COLUMN + 1
    assert unlabeled[0]['observation_ids'] == sorted(
        entry['observation']['observation_id'] for entry in row['monthly'])
    assert result['census']['unlabeled'] == 1


def test_unrecognized_columns_become_explicit_issues():
    grid = [header(['Variance']),
            ['Residential Rental Income'] + amounts('41000.00') + ['1200.00'],
            ['Property Taxes'] + amounts('2100.00') + ['50.00']]
    result = preview(grid=grid)
    column = [entry for entry in result['columns']
              if entry['column'] == FIRST_COLUMN + 13][0]
    assert column['role'] == 'unrecognized' and column['header'] == 'Variance'
    found = issues(result, 'UNRECOGNIZED_COLUMN')
    assert len(found) == 1 and found[0]['severity'] == 'blocker'
    assert found[0]['citation']['row'] == FIRST_ROW
    assert found[0]['citation']['column'] == FIRST_COLUMN + 13
    assert found[0]['observation_ids'] == []
    empty_data = [header(['Variance']),
                  ['Residential Rental Income'] + amounts('41000.00')]
    result = preview(grid=empty_data)
    found = issues(result, 'UNRECOGNIZED_COLUMN')
    assert len(found) == 1 and found[0]['severity'] == 'warning'
    resolved = [header(['Variance']),
                ['Residential Rental Income'] + amounts('41000.00') + ['1200.00']]
    result = preview(grid=resolved, column_roles={13: 'descriptor'})
    assert issues(result, 'UNRECOGNIZED_COLUMN') == []
    column = [entry for entry in result['columns']
              if entry['column'] == FIRST_COLUMN + 13][0]
    assert column['role'] == 'descriptor'


def test_empty_and_descriptor_columns_never_block():
    grid = [header() + [None],
            ['Residential Rental Income'] + amounts('41000.00')]
    result = preview(grid=grid)
    column = [entry for entry in result['columns']
              if entry['column'] == FIRST_COLUMN + 13][0]
    assert column['role'] == 'empty' and column['header'] is None
    assert result['issues'] == []
    grid = [header(['Notes']),
            ['Residential Rental Income'] + amounts('41000.00')]
    result = preview(grid=grid)
    column = [entry for entry in result['columns']
              if entry['column'] == FIRST_COLUMN + 13][0]
    assert column['role'] == 'descriptor'
    assert result['issues'] == []


def test_column_role_overrides_are_bounded():
    grid = [header(['Variance']),
            ['Residential Rental Income'] + amounts('41000.00') + ['1200.00']]
    assert refuse(grid=grid, column_roles={13: 'month'}) == 'INVALID_INPUT'
    assert refuse(grid=grid, column_roles={0: 'descriptor'}) == 'INVALID_INPUT'
    assert refuse(grid=grid, column_roles={99: 'descriptor'}) == 'INVALID_INPUT'
    assert refuse(grid=grid, column_roles={'13': 'descriptor'}) == 'INVALID_INPUT'
    grid = [ ['Account', 'Sep 2024'] + MONTH_HEADERS,
             ['Residential Rental Income', '39000.00'] + amounts('41000.00')]
    result = preview(grid=grid, column_roles={1: 'excluded'})
    assert result['coverage']['status'] == 'complete'
    assert result['status'] == 'observed_unvalidated'
    column = [entry for entry in result['columns']
              if entry['column'] == FIRST_COLUMN + 1][0]
    assert column['role'] == 'excluded'
    cited = {observed['citation']['column'] for observed in all_observations(result)}
    assert FIRST_COLUMN + 1 not in cited
    grid = [ ['Account', 'Sep 2025', 'Oct 2025'],
             ['Residential Rental Income', '1.00', '2.00']]
    assert refuse(grid=grid, window={'first_month': '2025-09',
                                     'last_month': '2026-08'},
                  column_roles={1: 'excluded'}) == 'EXCLUDED_MONTH_COLUMN'


def test_non_string_cells_refuse():
    for bad in (1450.0, 1450, True):
        grid = [header(),
                ['Residential Rental Income'] + [bad] + amounts('1.00', 11)]
        assert refuse(grid=grid) == 'INVALID_INPUT'


def test_formula_cache_origins_refuse():
    assert refuse(cell_origin='data_only_cache') == 'FORMULA_CACHE_NOT_ORIGINAL'
    assert refuse(cell_origin='formula') == 'FORMULA_CACHE_NOT_ORIGINAL'


def test_ambiguous_numeric_cells_refuse():
    grid = [header(),
            ['Residential Rental Income'] + ['1.234'] + amounts('1.00', 11)]
    assert refuse(grid=grid) == 'AMBIGUOUS_NUMERIC_FORMAT'
    grid = [header(),
            ['Residential Rental Income'] + ['1e3'] + amounts('1.00', 11)]
    assert refuse(grid=grid) == 'AMBIGUOUS_NUMERIC_FORMAT'


def test_scope_binding_mismatches_refuse():
    assert refuse(subject_id='subject_other') == 'SCOPE_MISMATCH'
    assert refuse(as_of='2026-09-29') == 'SCOPE_MISMATCH'
    source = dict(SOURCE_RECORD, subject_id='subject_other')
    assert refuse(source=source) == 'SCOPE_MISMATCH'


def test_accounting_envelope_validates_with_frozen_contract():
    module = api()
    from plat_harness.ingest import accounting
    result = preview()
    envelope = module.accounting_envelope(result, subject_id=SUBJECT, as_of=AS_OF)
    assert envelope['contract_version'] == 'ingest-accounting/1.0.0'
    assert accounting.validate_accounting_observations(
        envelope, subject_id=SUBJECT, as_of=AS_OF) == envelope
    assert len(envelope['observations']) == len(all_observations(result)) == 120
    assert envelope['status'] == 'observed_unvalidated'
    assert envelope['issues'] == [] and envelope['sources'] == [SOURCE_RECORD]
    grid = [ ['Account'] + MONTH_HEADERS[:7],
             ['Residential Rental Income'] + amounts('41000.00', 7)]
    partial = preview(grid=grid)
    assert partial['status'] == 'blocked'
    partial_envelope = module.accounting_envelope(partial, subject_id=SUBJECT,
                                                   as_of=AS_OF)
    # The frozen accounting contract only sees cited amounts; period
    # completeness is T12-preview authority and never leaks into it.
    assert partial_envelope['status'] == 'observed_unvalidated'
    assert partial_envelope['issues'] == partial['issues']
    assert accounting.validate_accounting_observations(
        partial_envelope, subject_id=SUBJECT, as_of=AS_OF) == partial_envelope


def test_no_financial_math_or_engine_surface():
    grid = happy_grid()
    result = preview(grid=grid)
    cited = {(observed['citation']['row'], observed['citation']['column'])
             for observed in all_observations(result)}
    expected = set()
    for index, row in enumerate(grid[1:], start=1):
        if len(row) > 1 and any(row[column] for column in range(1, 13)):
            for column in range(1, 13):
                expected.add((FIRST_ROW + index, FIRST_COLUMN + column))
    assert cited == expected
    assert len(cited) == 120
    module = api()
    for name in ('sum', 'total', 'aggregate', 'noi', 'cap_rate', 'dscr'):
        assert not callable(getattr(module, name, None))
    assert not hasattr(module, 'run_underwriting')
    assert not hasattr(module, 'engine')


def test_header_without_month_columns_refuses():
    grid = [ ['Account', 'Budget', 'Actual'],
             ['Residential Rental Income', '41000.00', '42000.00']]
    assert refuse(grid=grid) == 'NO_MONTH_COLUMNS'


def test_empty_statement_refuses():
    assert refuse(grid=[header()]) == 'EMPTY_STATEMENT'
    assert refuse(grid=[header(), ['INCOME']]) == 'EMPTY_STATEMENT'


@pytest.mark.parametrize('text,expected', [
    ('Sep 2025', '2025-09'), ('SEP 2025', '2025-09'), ('sept 2025', '2025-09'),
    ('September 2025', '2025-09'), ('Sep  2025', '2025-09'),
    ('sep-2025', '2025-09'), ('2025-09', '2025-09'), ('2025-09-01', '2025-09'),
    ('9/1/2025', '2025-09'), ('09/01/2025', '2025-09'),
])
def test_month_header_forms_parse(text, expected):
    grid = [ ['Account', text], ['Residential Rental Income', '41000.00']]
    result = preview(grid=grid)
    assert result['months'][0]['month'] == expected
    assert result['months'][0]['header'] == text.strip()
    assert result['coverage']['present_months'] == [expected]


@pytest.mark.parametrize('text', ['Budget', 'FY 2025', '2025', 'Sep', 'Total',
                                  'Q3', '2025-9'])
def test_non_month_headers_are_not_months(text):
    grid = [ ['Account', text], ['Residential Rental Income', '41000.00']]
    assert refuse(grid=grid) == 'NO_MONTH_COLUMNS'


def test_preceding_rows_are_counted_never_silent():
    grid = [['Abilene Gardens T12']] + happy_grid()
    result = preview(grid=grid, header_row=1)
    assert result['census']['preceding'] == 1
    assert result['header_row'] == FIRST_ROW + 1
    cited_rows = {observed['citation']['row'] for observed in all_observations(result)}
    assert FIRST_ROW not in cited_rows
    assert result['status'] == 'observed_unvalidated'


def test_section_and_blank_rows_carry_no_amounts():
    grid = happy_grid() + [[None], ['VACANCY SECTION']]
    result = preview(grid=grid)
    kinds = {row['source_label'] if row['source_label'] else None:
             row['row_kind'] for row in result['rows']}
    assert kinds['INCOME'] == 'section' and kinds['EXPENSES'] == 'section'
    assert kinds['VACANCY SECTION'] == 'section'
    assert kinds[None] == 'blank'
    for row in result['rows']:
        if row['row_kind'] in ('section', 'blank'):
            assert row['monthly'] == []
            assert row['mapping_state'] == 'not_applicable'
            assert row['canonical_account'] is None and row['mapping_ref'] is None
    assert result['census']['blank'] == 1 and result['census']['section'] == 3
    assert result['issues'] == []
    assert result['status'] == 'observed_unvalidated'


@pytest.mark.parametrize('mutate', [
    lambda value: value.__setitem__('status', 'blocked'),
    lambda value: value.__setitem__('noi', '1000.00'),
    lambda value: value['rows'][1]['monthly'][0]['observation']['amount'].__setitem__(
        'decimal', '0'),
    lambda value: value['rows'][1]['monthly'].reverse(),
    lambda value: value['rows'][1]['monthly'][0]['observation']['citation'].__setitem__(
        'row', value['rows'][1]['row'] + 5),
    lambda value: value['rows'][1].__setitem__('canonical_account', 'noi'),
    lambda value: value['coverage'].__setitem__('missing_months', ['2024-01']),
    lambda value: value['census'].__setitem__('line_item', 99),
    lambda value: value['months'].pop(),
    lambda value: value['window'].__setitem__('first_month', '2024-09'),
])
def test_validate_refuses_tampered_previews(mutate):
    result = preview()
    tampered = copy.deepcopy(result)
    mutate(tampered)
    refuse_value(tampered)


def test_validate_refuses_tampered_issue_fields():
    grid = happy_grid() + [['Late Fees'] + amounts('75.00')]
    result = preview(grid=grid)
    tampered = copy.deepcopy(result)
    tampered['issues'][0]['severity'] = 'warning'
    assert refuse_value(tampered) == 'INVALID_INPUT'
    tampered = copy.deepcopy(result)
    tampered['issues'][0]['issue_id'] = 'iss_' + '0' * 32
    assert refuse_value(tampered) == 'INVALID_INPUT'
    tampered = copy.deepcopy(result)
    row = [row for row in tampered['rows'] if row['source_label'] == 'Late Fees'][0]
    row['monthly'][0]['observation']['observation_id'] = 'mny_' + '0' * 64
    assert refuse_value(tampered) == 'INVALID_INPUT'


def test_issue_identifiers_are_deterministic_and_unique():
    partial_grid = [ ['Account'] + MONTH_HEADERS[:6],
                     ['Residential Rental Income'] + amounts('41000.00', 6),
                     ['Late Fees'] + amounts('75.00', 6)]
    result = preview(grid=partial_grid)
    again = preview(grid=copy.deepcopy(partial_grid))
    assert [issue['issue_id'] for issue in result['issues']] == \
        [issue['issue_id'] for issue in again['issues']]
    identifiers = [issue['issue_id'] for issue in result['issues']]
    assert len(set(identifiers)) == len(identifiers)
    codes = {issue['code'] for issue in result['issues']}
    assert codes == {'PARTIAL_MONTH_COVERAGE', 'UNMAPPED_ACCOUNT'}
    for issue in result['issues']:
        payload = json.dumps([issue['code'], issue['citation'],
                              sorted(issue['observation_ids'])],
                             sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False)
        expected = 'iss_' + hashlib.sha256(payload.encode()).hexdigest()[:32]
        assert issue['issue_id'] == expected
    assert result['status'] == 'blocked'


def test_structural_bounds_refuse():
    assert refuse(grid=[]) == 'INVALID_INPUT'
    assert refuse(grid=[[]]) == 'INVALID_INPUT'
    assert refuse(grid=[['Account', 'Sep 2025'], 'Residential Rental Income']) == \
        'INVALID_INPUT'
    assert refuse(header_row=99) == 'INVALID_INPUT'
    assert refuse(label_column=99) == 'INVALID_INPUT'
    assert refuse(sheet=0) == 'INVALID_INPUT'
    assert refuse(first_row=0) == 'INVALID_INPUT'
    assert refuse(first_column=0) == 'INVALID_INPUT'
    assert refuse(mapping={}) == 'INVALID_MAPPING'
    entry = {'source_label': 'Residential Rental Income',
             'canonical_account': 'residential_rental_income',
             'mapping_ref': mapping_ref('Residential Rental Income'),
             'extra': 'no'}
    assert refuse(mapping=mapping([entry])) == 'INVALID_MAPPING'
    assert refuse(source={'source_id': SOURCE}) == 'INVALID_INPUT'
    source = dict(SOURCE_RECORD, role='derivative', original_source_ids=[SOURCE])
    assert refuse(source=source) == 'INVALID_INPUT'


def test_error_messages_are_static():
    module = api()
    error = module.T12PreviewError('NO_MONTH_COLUMNS')
    assert error.code == 'NO_MONTH_COLUMNS'
    assert error.args == ('T12 preview refused (NO_MONTH_COLUMNS).',)
    assert 'PRIVATE_CANARY' not in str(error)
    assert module.T12PreviewError('NOT_A_CODE').code == 'INVALID_INPUT'