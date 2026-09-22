"""Model-path/runtime portability tests for the native_qwen host configuration.

The local model path and reviewed tokenizer interpreter are host
configuration, read from PLAT_HARNESS_MODEL_PATH and PLAT_HARNESS_RUNTIME at
call time. When unset, every consumer must refuse fail-closed before touching
the filesystem; no host path is compiled into the source. Tests pin the
configuration with synthetic paths; no model, GPU or network is touched.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError
from plat_harness import native_qwen


@pytest.fixture
def synthetic_host(tmp_path, monkeypatch):
    model_path = tmp_path / 'model'
    model_path.mkdir(mode=0o700)
    runtime = tmp_path / 'runtime' / 'bin' / 'python'
    runtime.parent.mkdir(mode=0o700, parents=True)
    monkeypatch.setenv('PLAT_HARNESS_MODEL_PATH', str(model_path))
    monkeypatch.setenv('PLAT_HARNESS_RUNTIME', str(runtime))
    return model_path, runtime


def test_env_configured_paths_are_returned(synthetic_host):
    model_path, runtime = synthetic_host
    assert native_qwen.model_path() == model_path
    assert native_qwen.runtime_python() == str(runtime)


def test_unset_model_path_refuses_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv('PLAT_HARNESS_MODEL_PATH', raising=False)
    with pytest.raises(HarnessError) as exc:
        native_qwen.model_path()
    assert exc.value.code == 'NATIVE_CONFIG'


def test_empty_model_path_refuses_fail_closed(monkeypatch):
    monkeypatch.setenv('PLAT_HARNESS_MODEL_PATH', '')
    with pytest.raises(HarnessError) as exc:
        native_qwen.model_path()
    assert exc.value.code == 'NATIVE_CONFIG'


def test_unset_runtime_refuses_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv('PLAT_HARNESS_RUNTIME', raising=False)
    with pytest.raises(HarnessError) as exc:
        native_qwen.runtime_python()
    assert exc.value.code == 'NATIVE_CONFIG'


def test_no_host_path_is_compiled_into_source():
    """No consumer module may embed a builtin absolute host path."""
    import plat_harness.native_gate as g
    import plat_harness.native_dynamic_gate as dg
    import plat_harness.native_supervisor as s
    import plat_harness.native_dynamic_supervisor as ds
    import plat_harness.native_qwen_worker as w
    import plat_harness.native_dynamic_worker as dw
    import plat_harness.native_baseline_amendment as ba
    for module in (g, dg, s, ds, w, dw, ba):
        assert not hasattr(module, 'MODEL_PATH'), module.__name__
        assert not hasattr(module, 'RUNTIME'), module.__name__
        source = Path(module.__file__).read_text()
        assert '/home/' not in source, module.__name__