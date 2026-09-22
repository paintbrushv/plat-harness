"""Public audit must keep coverage without shipping private deal identities."""
from pathlib import Path
import re


def test_public_deal_audit_has_thirteen_anonymous_rows_and_private_evidence_boundary():
    text = (Path(__file__).resolve().parents[1] / 'docs/DEAL_INVENTORY_AUDIT.md').read_text()
    section = text.split('## Coverage table', 1)[1].split('## Document inventory', 1)[0]
    aliases = re.findall(r'^\| (Deal-\d\d) \|', section, re.M)
    assert sorted(aliases) == [f'Deal-{i:02d}' for i in range(1, 14)]
    assert 'private identified audit' in text
    assert '/home/' not in text
    assert 'raw_inputs/' not in text
    assert 'Student Housing unverified' in text
    assert 'uncertified_until_classifier_ships' in text
    assert 'SHA256' in text
