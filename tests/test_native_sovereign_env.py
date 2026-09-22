"""Host-envelope portability tests for both native gates.

The expected host/uid approval envelope is host configuration, read from
PLAT_HARNESS_SOVEREIGN_HOST and PLAT_HARNESS_SOVEREIGN_UID at call time and
refused when unset. The gate semantic is unchanged: authorization happens
only behind an exact host approval envelope — the *expected values* are
simply configured per host instead of compiled in.
"""
from __future__ import annotations

import os

import pytest

from plat_harness.errors import HarnessError


def _refused_host(gate, *args):
    with pytest.raises((HarnessError, OSError)) as exc:
        gate(*args)
    return exc.value


def _is_code(exc, code):
    return getattr(exc, 'code', None) == code


@pytest.mark.parametrize('module_name', ['native_gate', 'native_dynamic_gate'])
def test_unset_sovereign_env_refuses_fail_closed(module_name, tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module(f'plat_harness.{module_name}')
    monkeypatch.delenv('PLAT_HARNESS_SOVEREIGN_HOST', raising=False)
    monkeypatch.delenv('PLAT_HARNESS_SOVEREIGN_UID', raising=False)
    gate = module.authorize if module_name == 'native_gate' else module.authorize_candidate
    exc = _refused_host(gate, tmp_path / 'auth.json', '0' * 64,
                        tmp_path / 'manifest.json', tmp_path / 'out')
    assert _is_code(exc, 'NATIVE_HOST')


@pytest.mark.parametrize('module_name', ['native_gate', 'native_dynamic_gate'])
def test_env_sovereign_env_mismatch_refuses(module_name, tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module(f'plat_harness.{module_name}')
    # Configured to values that are NOT this host: must refuse before anything.
    monkeypatch.setenv('PLAT_HARNESS_SOVEREIGN_HOST', 'not-this-host.example.invalid')
    monkeypatch.setenv('PLAT_HARNESS_SOVEREIGN_UID', str(os.getuid() + 424242))
    gate = module.authorize if module_name == 'native_gate' else module.authorize_candidate
    exc = _refused_host(gate, tmp_path / 'auth.json', '0' * 64,
                        tmp_path / 'manifest.json', tmp_path / 'out')
    assert _is_code(exc, 'NATIVE_HOST')


@pytest.mark.parametrize('module_name', ['native_gate', 'native_dynamic_gate'])
def test_matching_env_sovereign_env_passes_host_envelope(module_name, tmp_path, monkeypatch):
    """Configured to this host's own platform.node()/uid, the host check passes.

    The gate then proceeds to the approval hash check, which refuses on the
    fake zero digest — proving the host envelope was satisfied on any machine.
    """
    import importlib
    import platform
    module = importlib.import_module(f'plat_harness.{module_name}')
    monkeypatch.setenv('PLAT_HARNESS_SOVEREIGN_HOST', platform.node())
    monkeypatch.setenv('PLAT_HARNESS_SOVEREIGN_UID', str(os.getuid()))
    gate = module.authorize if module_name == 'native_gate' else module.authorize_candidate
    exc = _refused_host(gate, tmp_path / 'auth.json', '0' * 64,
                        tmp_path / 'manifest.json', tmp_path / 'out')
    assert not _is_code(exc, 'NATIVE_HOST')  # host envelope passed; later gate refused