"""Unified API tests using synthetic BIFF, never private deal fixtures."""
import builtins
import io
import json
import traceback

import pytest

from plat_harness.ingest import normalize_rent_roll
from plat_harness.ingest.pms_normalizer import RentRollNormalizationError
from plat_harness.ingest.onesite import normalize_onesite_xls
from test_pms_onesite import fixture_raw, CANARIES


@pytest.mark.parametrize('pms', ['realpage', 'RealPage OneSite', 'ONESITE'])
def test_public_stream_api_routes_exact_biff_bytes(pms):
    raw = fixture_raw()
    actual = normalize_rent_roll(io.BytesIO(raw), pms)
    assert actual == normalize_onesite_xls(raw)
    assert actual['status'] == 'blocked'
    assert all(value is None for counts in actual['counts'].values() for value in counts.values())
    assert not any(canary in json.dumps(actual) for canary in CANARIES)


@pytest.mark.parametrize('pms', ['yardi', 'entrata'])
def test_biff_does_not_override_explicit_vendor_selection(pms):
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_rent_roll(io.BytesIO(fixture_raw()), pms)
    assert error.value.code == 'UNSUPPORTED_PMS_FORMAT'
    assert error.value.__context__ is None


def test_public_biff_missing_reader_preserves_typed_sanitized_error(monkeypatch):
    original = builtins.__import__
    def reject(name, *args, **kwargs):
        if name == 'xlrd':
            raise ImportError('SYNTHETIC PRIVATE ERROR CANARY')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject)
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_rent_roll(io.BytesIO(fixture_raw()), 'realpage')
    assert error.value.code == 'XLS_DEPENDENCY_MISSING'
    assert error.value.__context__ is None
    assert 'PRIVATE ERROR CANARY' not in ''.join(traceback.format_exception(error.value))


def test_public_api_labels_unsupported_pdf_without_reader_or_text_echo():
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_rent_roll(io.BytesIO(b'%PDF-1.4 SYNTHETIC PRIVATE CANARY'), 'yardi')
    assert error.value.code == 'UNSUPPORTED_INPUT_FORMAT'
    assert 'PRIVATE CANARY' not in str(error.value)


def test_caller_stream_cannot_inject_normalization_error_code():
    class BrokenStream:
        def read(self, size):
            raise RentRollNormalizationError('SYNTHETIC PRIVATE CANARY')
    with pytest.raises(RentRollNormalizationError) as error:
        normalize_rent_roll(BrokenStream(), 'realpage')
    assert error.value.code == 'MALFORMED_INPUT'
    assert error.value.__context__ is None
    assert 'PRIVATE CANARY' not in str(error.value)
