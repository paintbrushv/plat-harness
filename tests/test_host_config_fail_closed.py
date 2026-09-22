"""Direct fail-closed probes for every host-configuration seam.

Each configuration reader must refuse when its env var is unset or empty —
a call with NO other arguments, before any path or host comparison. This
kills builtin-default mutations that survive path-level tests (a builtin
fallback can look identical to a refusal when the probe path happens to sit
outside the fallback directory).
"""
from __future__ import annotations

import pytest

from plat_harness.errors import HarnessError


READERS = [
    ('plat_harness.adapters.slice_b', 'private_root'),
    ('plat_harness.native_gate', 'sovereign_host_envelope'),
    ('plat_harness.native_supervisor', 'native_lock_path'),
    ('plat_harness.native_qwen', 'model_path'),
    ('plat_harness.native_qwen', 'runtime_python'),
    ('plat_harness.native_runtime_candidate', 'site_packages'),
]


@pytest.mark.parametrize('module_name,func_name', READERS)
def test_unset_config_refuses_before_any_comparison(module_name, func_name, monkeypatch):
    import importlib
    module = importlib.import_module(module_name)
    env_names = [n for n in dir(module) if isinstance(getattr(module, n), str) and n.startswith('PLAT_HARNESS_')]
    # strip every PLAT_HARNESS_* env var the module could read
    for key in list(__import__('os').environ):
        if key.startswith('PLAT_HARNESS_'):
            monkeypatch.delenv(key, raising=False)
    with pytest.raises(HarnessError):
        getattr(module, func_name)()


@pytest.mark.parametrize('module_name,func_name', READERS)
def test_empty_config_refuses_before_any_comparison(module_name, func_name, monkeypatch, tmp_path):
    import importlib
    module = importlib.import_module(module_name)
    for key in list(__import__('os').environ):
        if key.startswith('PLAT_HARNESS_'):
            monkeypatch.setenv(key, '')
    with pytest.raises(HarnessError):
        getattr(module, func_name)()