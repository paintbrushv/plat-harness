"""Synthetic BIFF8 contract tests. Embedded bytes contain no private sources.

Recipe: xlwt 1.3.0 Workbook/add_sheet; write the cells in fixture_rows() at
zero-based coordinates; save BytesIO; zlib.compress and base64.b64encode.
No xlwt runtime/test dependency. Mutated edge cases use an in-memory fake book;
the positive test parses the actual embedded OLE/BIFF8 with xlrd.
"""
import base64
import builtins
import hashlib
import io
import json
import traceback
import warnings
import zlib
from types import SimpleNamespace

import pytest

from plat_harness.ingest.onesite import normalize_onesite_xls
from plat_harness.ingest.pms_normalizer import RentRollNormalizationError

SYNTHETIC_BIFF_ZLIB = 'eJztWE1sG0UUfusktuP4b23nx3biLkmaQkkobS4IqXVdx0kswsa1TaAIqWzjVVl1uxvZW6JygELJEQmJEwgJVeqFSwsXftQKwQ0kpCI4ICEhJXDkhAQShzbmzfN44w1bqTm0gORn7ex8b7753tvZ2ZmRv7slbl75OLUFu+wY9MB2sx+8HT4Br/42iAK2N5us2r778Wp27X9l/X58kd4+uBH61sfeIXvfW+CBj3q/whLgF7xegDWQTUOVHqCdoBwUgeVwFEsB3kdPGJKUVYzKVSrjVF4n5k0qj5PnLSqPIndTeB5uZeWDT/BZ/JxnnNrCwHQ/pT4/kecwDMLXbBa/9rbQ4vZBrq4p+n+zIdMbhKuA721BNdS6om9CAl/gVfizKQH80f5Sv5S6/gfrFwD9fzn9Phf/O55egEvQfJEm+AakQaT1Ng6VU3J1sVAt5qVSsShVFguF6m047XkMGw/gNQywbKgVzVKlsmpYDaly0bBeUi1tFb9m5pHKpq5Lc6qlaDrOj1algdGeMTQL1/B53TTra7piRFsupDa0s4ZiaaaBrMrJ+arYajm0pCoNNVCxFOsCE5CV82oAoFpXjEYgb9bUEALTUvTACU3XNeNsD35Gjx8OQ+czLOXkCDraSUpVc12t43axvLp6YU1Ta8lOdrlQKc4V5KqUz8m58imMWUbkoOSXqwU5Z1MG8IFyTxVm8ou58kIBI5VUo4apsMFR1xUdHzi3tqZrq4phpTp1cqXSUjG/I0S5HwntJDYjV1eWhjq7LOO93EmfRfqKwqRnaKhq3jb22YmMOEajIM8V5QWu4ePD13hygL+K1lCjf0JiuBHdSWdakk0JUwo5XNWV+A5mUGolYifGMb62XO28ZhyaM9cN0W6UzQ4CtDaEqGNDCNFCGcSyBhGqi7RcRnHLv/3h798/faaUPU2eS3QIaB0V9rPZDU14nfXAzmHy7qP2WbwOUo9HqXyDVNNUT1GZwEUO71OlQV6Zv0ycN6l1CuPMkv2QPdBRfxjrG7+d/Cyz8Wv2EaxfW9h6JXHtx+wVGMejSw37s99lmBamhffeZfZ5tn0X+LbyM5XJf2wxfk+UnkCAZusDFiJwBwLkE8nBGB4aLyfDg2MnEo8xvC4aXtJoR/HTCDkZfmLgjOPIg6jXRj5EfTYKIfLaSMSfz0ZxRH4bTSDqt9ERRAGeQ9AlhyDlMMD5QcohaCOWQ8hGLGrYRixqxEYsKs4y6ME5EaSo37z8xQfBye3jLDLTmNwVOUQKIlcIkUKMZxqm2eLkh4kd5/ywHZ/xIy76EYdi1EUxSooJrhglxUHOF134Io3VEOeLjrESHWMl0m/YRkx5hCvHXJRjpJzk/JhDOeZQjpFyykZMOc2V4y7KrTEe5fw44THOT7jM2gRlkuEM9gSDuxjDtFbs44rD2ENkeyDxR1z4I8R/CB2vehhK8JnM+EkXfpL445yfJH77K0q58FPEn+D8lEM/7cJPE3+S89MO/qgLf5T4+zl/1JHPmAt/jPhTnD/m4Gdc+BniZzg/Q3w2/495YvAJ64iH3x0LQNe61rWu3dXYoYStG+xgwY4T7BDBFjh2YGD/69zBa/vf/pOia/fNymDiz8JNuQAG3utwcU/zZwj6hLaWcI992v8XMnsWo9fhHJyhPM7tKTYzPP4Inc9zzx2jew51N9tz/O295Hmf4/8NGCW+1w=='
MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
NULL_COUNTS = dict.fromkeys(('occupied', 'vacant', 'down', 'total'))
ROOT_KEYS = {'version', 'pms_type', 'source_sha256', 'residential_units',
             'commercial_units', 'counts', 'summary', 'issues', 'status'}
CANARIES = ('SYNTHETIC RESIDENT CANARY', 'SYNTHETIC COTENANT CANARY',
            'SYNTHETIC APPLICANT CANARY', 'SYNTHETIC OTHER CANARY',
            'SYNTHETIC PENDING CANARY', 'SYNTHETIC PII SHEET', '123456.789',
            'Synthetic Tower', 'SYNTHETIC PLAN')


def fixture_raw():
    return zlib.decompress(base64.b64decode(SYNTHETIC_BIFF_ZLIB))


def fixture_rows():
    xlrd = pytest.importorskip('xlrd')
    book = xlrd.open_workbook(file_contents=fixture_raw(), logfile=io.StringIO())
    sheet = book.sheet_by_index(0)
    rows = [sheet.row_values(r) for r in range(sheet.nrows)]
    book.release_resources()
    return rows


def fake_book(monkeypatch, changes=(), *, extra_sheets=0, nrows=None):
    import xlrd
    monkeypatch.undo()
    rows = fixture_rows()
    for r, c, value in changes:
        while len(rows) < r:
            rows.append([''] * 51)
        rows[r-1][c-1] = value

    class Sheet:
        ncols = 51
        def cell_value(self, r, c):
            return rows[r][c]
        def cell_type(self, r, c):
            value = rows[r][c]
            return 2 if isinstance(value, (int, float)) else 1 if value else 0
    sheet = Sheet()
    sheet.nrows = nrows if nrows is not None else len(rows)
    book = SimpleNamespace(nsheets=1+extra_sheets, sheet_by_index=lambda i: sheet,
                           release_resources=lambda: None)
    monkeypatch.setattr(xlrd, 'open_workbook', lambda **kwargs: book)
    return fixture_raw()


def codes(result):
    return {i['code'] for i in result['issues']}


def report(result):
    return next(i['observation'] for i in result['issues']
                if i['code'] == 'ONESITE_REPORT_SUMMARY')


def observations(result):
    return [i['observation'] for i in result['issues']
            if i['code'] == 'UNRESOLVED_UNIT_USE']


def check_citations(value, raw):
    if isinstance(value, dict):
        if {'sheet','row','row_end','column'} <= value.keys():
            assert value['source_sha256'] == hashlib.sha256(raw).hexdigest()
            assert value['sheet'] == 1
            assert 1 <= value['row'] <= value['row_end']
            assert 1 <= value['column'] <= 51
        for item in value.values():
            check_citations(item, raw)
    elif isinstance(value, list):
        for item in value:
            check_citations(item, raw)


def test_real_synthetic_biff_partial_reconciliation_never_occupancy():
    pytest.importorskip('xlrd')
    raw = fixture_raw()
    assert raw.startswith(MAGIC)
    result = normalize_onesite_xls(raw)
    assert set(result) == ROOT_KEYS
    assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['pms_type'] == 'realpage'
    assert result['status'] == 'blocked'
    assert result['counts'] == {s: NULL_COUNTS for s in ('residential','commercial')}
    assert result['residential_units'] == result['commercial_units'] == []
    assert len(observations(result)) == 3
    assert [u['status'] for u in observations(result)] == ['occupied','occupied','vacant']
    assert all(u['unit_type'] is None and u['unit_id'] == '[REDACTED]'
               for u in observations(result))
    assert report(result)['row_derived_counts'] == {'occupied':2,'vacant':1,'down':None,'total':3}
    assert report(result)['reported_counts'] == {'occupied':2,'vacant':1,'down':None,'total':3}
    assert report(result)['status'] == 'unresolved'
    assert report(result)['matched_fields'] == ['occupied','vacant','total']
    assert 'DOWN_EVIDENCE_UNRESOLVED' in codes(result)
    assert 'NON_CURRENT_LEASE_EXCLUDED' in codes(result)
    assert 'CONTINUATION_EVIDENCE' in codes(result)
    assert observations(result)[0]['evidence'][0]['status']['row'] == 13
    assert observations(result)[0]['evidence'][0]['status']['column'] == 18
    assert report(result)['citations']['totals']['row'] == 31
    check_citations(result, raw)
    encoded = json.dumps(result, allow_nan=False)
    assert not any(c in encoded for c in CANARIES)


@pytest.mark.parametrize('payload', [b'', b'Unit,Status\n101,Occupied', b'PK\x03\x04bad', 'not bytes', None])
def test_requires_original_binary_xls_magic(payload):
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_onesite_xls(payload)
    assert error.value.code == 'UNSUPPORTED_XLS_FORMAT'
    assert error.value.__context__ is error.value.__cause__ is None


def test_corrupt_biff_is_sanitized():
    raw = MAGIC + CANARIES[0].encode()
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_onesite_xls(raw)
    assert error.value.code == 'MALFORMED_XLS'
    assert error.value.__context__ is error.value.__cause__ is None
    assert CANARIES[0] not in ''.join(traceback.format_exception(error.value))


@pytest.mark.parametrize('changes', [[(2,1,'Other PMS')],[(9,18,'Status')],[(9,36,'')],[(3,13,'Other Report')]])
def test_compact_and_changed_layouts_refused(monkeypatch, changes):
    raw = fake_book(monkeypatch, changes)
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_onesite_xls(raw)
    assert error.value.code == 'UNSUPPORTED_ONESITE_LAYOUT'


@pytest.mark.parametrize('status', ['Model','Admin','NonRevenue','Down','SYNTHETIC RESIDENT CANARY'])
def test_unsupported_status_retained_redacted_not_down(monkeypatch, status):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(13,18,status)]))
    assert 'UNSUPPORTED_UNIT_STATUS' in codes(result)
    assert observations(result)[0]['status'] is None
    assert report(result)['row_derived_counts']['occupied'] is None
    assert all(v is None for c in result['counts'].values() for v in c.values())
    assert CANARIES[0] not in json.dumps(result)


@pytest.mark.parametrize('status', ['Applicant','Pending Renewal','Pending','Former','Future'])
def test_non_current_primary_row_excluded(monkeypatch, status):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(18,18,status)]))
    assert len(observations(result)) == 2
    assert 'NON_CURRENT_LEASE_EXCLUDED' in codes(result)
    assert report(result)['status'] == 'mismatch'


def test_duplicate_unit_conflict_and_orphan_continuation(monkeypatch):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(19,1,'101'),(11,36,'FAKE-CHARGE')]))
    assert {'DUPLICATE_UNIT_CONFLICT','ORPHAN_CONTINUATION'} <= codes(result)
    assert report(result)['row_derived_counts']['total'] is None


def test_identical_primary_repeat_not_another_unit(monkeypatch):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(18,1,'101'),(18,18,'Occupied')]))
    assert len(observations(result)) == 2
    assert 'DUPLICATE_UNIT_EVIDENCE' in codes(result)
    assert report(result)['row_derived_counts']['total'] == 2


@pytest.mark.parametrize('value', [None, -1, 1.25, 'SYNTHETIC RESIDENT CANARY'])
def test_summary_invalid_counts_do_not_coerce(monkeypatch, value):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(25,21,value)]))
    assert report(result)['status'] == 'unresolved'
    assert 'INVALID_VENDOR_SUMMARY' in codes(result)


def test_summary_total_mismatch_and_absence(monkeypatch):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(31,21,4)]))
    assert report(result)['status'] == 'mismatch'
    assert 'SUMMARY_MISMATCH' in codes(result)
    result = normalize_onesite_xls(fake_book(monkeypatch, [(24,2,'')]))
    assert report(result)['status'] == 'absent'


def test_pending_lease_charge_not_attached_to_current_evidence():
    result = normalize_onesite_xls(fixture_raw())
    charge_rows = [e['charge']['row'] for u in observations(result)
                   for e in u['evidence'] if 'charge' in e]
    assert 14 in charge_rows  # Current co-tenant/charge continuation.
    assert 16 not in charge_rows  # Charge follows Pending Renewal.
    assert 'NON_CURRENT_CONTINUATION_EXCLUDED' in codes(result)


def test_future_section_not_promoted_to_physical_inventory(monkeypatch):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(18,1,'Future Residents'),
                                  (18,3,''),(18,8,''),(18,18,''),(18,20,'')]))
    assert len(observations(result)) == 1
    assert 'NON_CURRENT_SECTION_EXCLUDED' in codes(result)


def test_source_summary_punctuation_and_count_header(monkeypatch):
    raw = fake_book(monkeypatch, [(24,21,'# Units'), (25,2,'Occupied, No NTV'),
                                 (26,2,'Occupied, NTV')])
    result = normalize_onesite_xls(raw)
    assert report(result)['status'] == 'unresolved'
    assert report(result)['matched_fields'] == ['occupied','vacant','total']
    assert report(result)['reported_counts']['total'] == 3


def test_missing_inventory_terminator_does_not_claim_complete(monkeypatch):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(21,1,'')]))
    assert 'INVENTORY_BOUNDARY_MISSING' in codes(result)
    assert report(result)['row_derived_counts']['total'] is None


def test_unrecognized_secondary_status_blocks_completeness(monkeypatch):
    result = normalize_onesite_xls(fake_book(monkeypatch, [(15,18,'Unrecognized')]))
    assert 'UNSUPPORTED_SECONDARY_STATUS' in codes(result)
    assert report(result)['row_derived_counts']['total'] is None


def test_limits_and_extra_sheet_refusal(monkeypatch):
    for kwargs, code in [({'nrows':50001},'INPUT_LIMIT_EXCEEDED'),
                         ({'extra_sheets':1},'UNSUPPORTED_ONESITE_LAYOUT')]:
        with pytest.raises(RentRollNormalizationError) as error:
            normalize_onesite_xls(fake_book(monkeypatch, **kwargs))
        assert error.value.code == code
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_onesite_xls(MAGIC + b'x' * (8*1024*1024))
    assert error.value.code == 'INPUT_LIMIT_EXCEEDED'


def test_missing_dependency_is_lazy_and_sanitized(monkeypatch):
    original = builtins.__import__
    def no_xlrd(name, *args, **kwargs):
        if name == 'xlrd':
            raise ImportError(CANARIES[0])
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_xlrd)
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_onesite_xls(fixture_raw())
    assert error.value.code == 'XLS_DEPENDENCY_MISSING'
    assert error.value.__context__ is error.value.__cause__ is None


@pytest.mark.parametrize('kind', ['warning','exception','log'])
def test_library_diagnostics_are_not_exposed(monkeypatch, capsys, kind):
    import xlrd
    def bad_reader(**kwargs):
        if kind == 'warning':
            warnings.warn(CANARIES[0])
        if kind == 'log':
            kwargs['logfile'].write(CANARIES[0])
        raise ValueError(CANARIES[0])
    monkeypatch.setattr(xlrd, 'open_workbook', bad_reader)
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_onesite_xls(fixture_raw())
    assert error.value.code == 'MALFORMED_XLS'
    assert error.value.__context__ is error.value.__cause__ is None
    assert CANARIES[0] not in ''.join(traceback.format_exception(error.value))
    assert CANARIES[0] not in ''.join(capsys.readouterr())
