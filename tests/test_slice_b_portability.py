"""Private-root portability tests for the Slice B adapter's fail-closed path gate.

The private data root is configured per-host via PLAT_HARNESS_PRIVATE_ROOT.
safe_path must accept paths below the configured root and refuse everything
else, with the same fail-closed semantics as the original pinned root:
absolute, no traversal, no symlinks, no group/world bits on root or target,
root itself refused. When the env var is unset the gate must refuse.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from plat_harness.adapters import slice_b
from plat_harness.errors import HarnessError


@pytest.fixture
def private_root(tmp_path, monkeypatch):
    root = tmp_path / 'private-root'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('PLAT_HARNESS_PRIVATE_ROOT', str(root))
    return root


def _file_under(root: Path, name='inputs.json') -> Path:
    p = root / name
    p.write_bytes(b'synthetic')
    p.chmod(0o600)
    return p


def test_env_configured_root_accepts_paths_below_it(private_root):
    p = _file_under(private_root)
    assert slice_b.safe_path(p) == p


def test_env_configured_root_refuses_paths_outside_it(private_root, tmp_path):
    outside = tmp_path / 'elsewhere' / 'inputs.json'
    outside.parent.mkdir(mode=0o700)
    outside.write_bytes(b'synthetic')
    outside.chmod(0o600)
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(outside)
    assert exc.value.code == 'UNSAFE_PATH'


def test_env_root_itself_is_refused(private_root):
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(private_root)
    assert exc.value.code == 'UNSAFE_PATH'


def test_unset_root_refuses_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv('PLAT_HARNESS_PRIVATE_ROOT', raising=False)
    anywhere = tmp_path / 'anywhere'
    anywhere.mkdir(mode=0o700)
    p = _file_under(anywhere)
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(p)
    assert exc.value.code == 'UNSAFE_PATH'


def test_root_reread_per_call(private_root, tmp_path, monkeypatch):
    # The root is re-read at call time: moving the env to a second fresh root
    # immediately changes what safe_path accepts, with no module reload.
    first = _file_under(private_root)
    assert slice_b.safe_path(first) == first
    second_root = tmp_path / 'second-root'
    second_root.mkdir(mode=0o700)
    monkeypatch.setenv('PLAT_HARNESS_PRIVATE_ROOT', str(second_root))
    second = _file_under(second_root, 'other.json')
    assert slice_b.safe_path(second) == second
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(first)
    assert exc.value.code == 'UNSAFE_PATH'


def test_security_semantics_preserved_under_env_root(private_root, tmp_path, monkeypatch):
    # Same fail-closed rules as before, under the configured root.
    # symlink refusal
    target = _file_under(private_root, 'real.json')
    link = private_root / 'link.json'
    link.symlink_to(target)
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(link)
    assert exc.value.code == 'UNSAFE_PATH'
    # traversal refusal
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(str(private_root / '..' / 'escape.json'))
    assert exc.value.code == 'UNSAFE_PATH'
    # relative refusal
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path('relative.json')
    assert exc.value.code == 'UNSAFE_PATH'
    # group/world-accessible target refusal
    loose = private_root / 'loose.json'
    loose.write_bytes(b'synthetic')
    loose.chmod(0o640)
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(loose)
    assert exc.value.code == 'UNSAFE_PATH'
    # group/world-accessible configured root refusal (root mode 0750 -> refuse)
    loose_root = tmp_path / 'loose-root'
    loose_root.mkdir(mode=0o700)
    os.chmod(loose_root, 0o750)
    p = loose_root / 'inputs.json'
    p.write_bytes(b'synthetic')
    p.chmod(0o600)
    monkeypatch.setenv('PLAT_HARNESS_PRIVATE_ROOT', str(loose_root))
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(p)
    assert exc.value.code == 'UNSAFE_PATH'
    monkeypatch.setenv('PLAT_HARNESS_PRIVATE_ROOT', str(private_root))
    # directory=True requires an existing directory
    missing = private_root / 'missing-dir'
    with pytest.raises(HarnessError) as exc:
        slice_b.safe_path(missing, directory=True)
    assert exc.value.code == 'NOT_FOUND'
    present = private_root / 'present-dir'
    present.mkdir(mode=0o700)
    assert slice_b.safe_path(present, directory=True) == present