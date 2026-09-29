# Synthetic September 2026 manager handoff and correction

This public fixture is invented. It contains no CCAR or TC export, account ID,
resident, credential, or real grant. The only real scenario boundaries used are
the dates and PMS directions supplied by the owner: TC changes on September 23
from Yardi to ResMan; CCAR changes on September 25 from Yardi to Yardi under a
new manager. The source namespaces and all amounts are synthetic.

The executable case is
`tests/test_synthetic_september_handoff.py`, using
`samples/operations/synthetic_september_handoff.json`. Run it with:

```text
pytest -q tests/test_synthetic_september_handoff.py
```

| Property | Synthetic source boundary | Invented October 1 issued September NOI | Invented October 3 restated September NOI | Invented budget NOI |
|---|---|---:|---:|---:|
| TC | outgoing Yardi through September 22; incoming ResMan from September 23 | $45,000 | $44,500 | $47,000 |
| CCAR | outgoing Yardi through September 24; incoming Yardi from September 25, with distinct feed namespace | $44,000 | $44,000 | $46,000 |

The exercise books balanced debit and credit GL transactions against independent
opening and closing trial balance expectations. It checks rental revenue,
operating expense, cash, receivables, payables, and deposits, then derives
income statement NOI and budget variance. Both TC feeds reuse transaction IDs;
two separate $5,000 deposit receipts remain distinct. An exact retry keeps the
same event IDs and stream version, while changed content under the same source
revision is refused. A synthetic host rule refuses a feed whose effective date
falls outside its manager tenure.

The September books are **fictionally** issued October 1. On October 3 a late
outgoing Yardi revision changes TC's September 22 expense from $18,000 to
$18,500. The as-known September close restates by $500, while the issued report
retains $45,000. The correction links to each original posting. The case copies
the local ledger with SQLite's backup API, reopens it, verifies the restated
view and frozen report, and confirms that revoked outgoing readers still cannot
read historical rows or the report. It also checks cross-property refusal.

## Evidence boundary

This is a component rehearsal against `TemporalLedger`, with a test-only
source-date rule, synthetic grants, and a generic balanced GL packet. It is not
a Boxscore close test, a ResMan adapter, a Yardi adapter, or a production host
policy. The ledger does not make each multi-posting GL transaction atomic; the
case does not prove interrupted packet recovery. It does not reconcile a real
source TB, income statement, budget, GL, deposit ledger, or receivables aging.
No real CCAR/TC period has been closed, corrected, replayed, or restored by
this test. Section 13 case 4 and the real-host/migration gates remain blocked
until approved exports, stable ID mapping, a host-owned grant source, and
read-only test identity are available and the product paths are exercised.
