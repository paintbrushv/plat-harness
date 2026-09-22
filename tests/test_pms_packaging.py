"""Packaging contract: optional spreadsheet readers, reproducible test extras."""
from pathlib import Path
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def test_spreadsheet_readers_declared_as_optional_ingest_and_test_dependencies():
    project = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())['project']
    extras = project['optional-dependencies']
    assert extras.get('ingest') == ['openpyxl>=3.1,<4', 'xlrd>=2.0.2,<3']
    assert all(item in extras['dev'] for item in extras['ingest'])
    assert not any(item.startswith(('openpyxl', 'xlrd')) for item in project['dependencies'])
    assert 'tomli>=2; python_version < "3.11"' in extras['dev']
