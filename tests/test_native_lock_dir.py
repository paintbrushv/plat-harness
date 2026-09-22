"""Native lock-dir portability tests for both supervisor mains.

The global resident-model lock directory is host configuration, read from
PLAT_HARNESS_NATIVE_LOCK_DIR at call time. When unset, the supervisor mains
must refuse before touching the filesystem; when set, the lock is created
inside the configured directory with the same 0600 owner-only semantics.
No test reaches a real model, GPU or network.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError


def _run_main(main, argv, env_changes, tmp_path):
    """Run a supervisor main in-process with a controlled environment."""
    import inspect
    for key, value in env_changes.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        if list(inspect.signature(main).parameters):
            return main(argv)
        # native_supervisor.main() reads sys.argv; splice ours in.
        import sys
        old_argv = sys.argv
        sys.argv = ['supervisor'] + list(argv)
        try:
            return main()
        finally:
            sys.argv = old_argv
    finally:
        for key in env_changes:
            os.environ.pop(key, None)


@pytest.mark.parametrize('module_name', ['native_supervisor', 'native_dynamic_supervisor'])
def test_unset_lock_dir_refuses_fail_closed(module_name, tmp_path):
    import importlib
    module = importlib.import_module(f'plat_harness.{module_name}')
    argv = ['--authorization', str(tmp_path / 'auth.json'),
            '--authorization-sha256', '0' * 64,
            '--manifest', str(tmp_path / 'manifest.json'),
            '--output', str(tmp_path / 'out')]
    if module_name == 'native_dynamic_supervisor':
        argv = ['native'] + argv
    # The gate must fail closed before any model load; the store records the
    # typed refusal. main() returns 2 on gate failure in both supervisors.
    assert _run_main(module.main, argv, {'PLAT_HARNESS_NATIVE_LOCK_DIR': None}, tmp_path) == 2


@pytest.mark.parametrize('module_name', ['native_supervisor', 'native_dynamic_supervisor'])
def test_env_lock_dir_is_used_and_unset_refuses(module_name, tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module(f'plat_harness.{module_name}')
    lock_dir = tmp_path / 'lock-root'
    lock_dir.mkdir(mode=0o700)
    monkeypatch.setenv('PLAT_HARNESS_NATIVE_LOCK_DIR', str(lock_dir))
    argv_base = ['--authorization', str(tmp_path / 'auth.json'),
                 '--authorization-sha256', '0' * 64,
                 '--manifest', str(tmp_path / 'manifest.json'),
                 '--output', str(tmp_path / 'out')]
    argv = argv_base if module_name == 'native_supervisor' else ['native'] + argv_base
    out = tmp_path / 'out'
    # Store(args.output) creates the output dir itself; do not pre-create it.
    code = _run_main(module.main, argv, {}, tmp_path)
    # Authorization is fake, so the gate must fail AFTER the lock was created
    # in the configured directory — proving the env dir is actually used.
    assert code == 2
    lock = lock_dir / '.native-qwen.lock'
    assert lock.is_file(), 'lock must be created inside PLAT_HARNESS_NATIVE_LOCK_DIR'
    mode = stat.S_IMODE(lock.stat().st_mode)
    assert mode == 0o600, oct(mode)
    gate_failure = out / 'gate_failure.json'
    assert gate_failure.is_file(), 'typed gate failure must be recorded'


def test_empty_lock_dir_refuses_fail_closed(tmp_path, monkeypatch):
    from plat_harness import native_supervisor
    monkeypatch.setenv('PLAT_HARNESS_NATIVE_LOCK_DIR', '')
    argv = ['--authorization', str(tmp_path / 'auth.json'),
            '--authorization-sha256', '0' * 64,
            '--manifest', str(tmp_path / 'manifest.json'),
            '--output', str(tmp_path / 'out')]
    # Empty env is treated as unset: fail closed, no lock directory touched.
    assert _run_main(native_supervisor.main, argv, {}, tmp_path) == 2