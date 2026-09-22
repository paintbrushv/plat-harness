# Deal Inventory Audit — public anonymized view

> Local-source coverage audit, not underwriting, certification, or investment approval.

## Privacy and evidence boundary

The private identified audit retains the exact folder names, source paths, SHA256 pins,
page/line/cell/JSON citations, and evidence markers for every field. It is stored
as `DEAL_INVENTORY_AUDIT.private.md` in the operator’s private audit evidence bundle,
alongside `audit_evidence.json`, `inventory.json`, and `public_alias_map.json`.
It must not be copied into this public repository. Public aliases below map one-to-one
onto the privately verified folder set; source locators resolve through that bundle.

Coverage: **13 folders**, **366 files**, **70 supplied raw-input files**, **296 derivative/working files**.
**8 folders lack original source documents locally.** One root-level scratch workbook is separately inventoried and excluded from deal counts.

`original` means inspected supplied bytes, not authenticated broker truth. Broker thesis,
derivative claims, conflicts, and analyst inferences remain separately labelled.
All strategy candidates are `uncertified_until_classifier_ships`. Container type is
not PMS identity. Student Housing is not verified. No financial forecasts were produced.

## Coverage table

| Alias | Strategy | Subtype | Vintage | Geography (state only) | PMS / format |
|---|---|---|---|---|---|
| Deal-01 | value-add (broker_thesis) [E001] | Garden / low-rise (inferred_from_original) [E002] [E003] | 1998 (original) [E004] | AL (original) [E005] | Unknown vendor; Yardi-like current/notice/vacant layout; PDF (inferred_format) [E006] [E007] |
| Deal-02 | Unknown (unknown) [E008] | High-Rise; mixed-use residential/office/retail (original) [E009] [E010] [E011] | 2021 (original) [E012] | TX (original) [E013] | Unknown vendor; Yardi-like current/notice/vacant layout; PDF (inferred_format) [E014] [E015] |
| Deal-03 | value-add (inferred_from_original) [E016] | High-Rise; residential with retail (original) [E017] [E018] | 2018 (original) [E019] | IN (original) [E020] | OneSite explicit on detailed reports; OLE/BIFF XLS; compact export vendor unconfirmed (original_partial) [E021] [E022] [E023] [E024] [E100] [E101] [E102] [E103] [E104] |
| Deal-04 | value-add (inferred_from_original) [E025] | Garden; three separate assets (original) [E026] [E027] [E028] [E034] [E035] [E036] | 2007; 2008; 2009 (component mapping private) (original) [E031] [E029] [E030] [E034] [E035] [E036] | TX (original) [E032] [E033] | Unknown vendor; Yardi-like lease-charge XLSX reports plus distinct roll-up XLSX (inferred_format) [E037] [E038] [E039] [E040] [E041] [E042] [E043] [E105] |
| Deal-05 | value-add (broker_thesis) [E044] | Mid-Rise inferred; residential/retail (inferred_from_original) [E045] [E046] | 2010 (original) [E047] | AL (original) [E048] | Unknown vendor; ledger/charge-detail XLSX with Report Parameters sheet (unknown_vendor) [E049] [E050] [E051] [E052] [E053] [E054] [E106] |
| Deal-06 | Unknown (unknown) [E055] | Garden (derivative_claim) [E056] | 2014 (derivative_claim) [E057] | TX (derivative_claim) [E058] | Unknown vendor; standardized CSV only; original XLS referenced but absent (derivative_only) [E059] [E060] |
| Deal-07 | value-add (derivative_claim) [E061] | Garden (derivative_claim) [E062] | 1982 (derivative_claim) [E063] | TX (derivative_claim) [E064] | Unverified RealPage claim in prior audit; standardized CSV only (derivative_claim) [E065] [E066] |
| Deal-08 | lease-up (inferred_from_derivative) [E067] | Unknown building form; residential/commercial evidence claimed (derivative_claim) [E068] | 2025 (derivative_claim) [E069] | IN (derivative_claim) [E070] | Unknown vendor; RedIQ XLSX filename is derivative provenance only (derivative_only) [E071] |
| Deal-09 | opportunistic (inferred_from_derivative) [E073] | Multifamily; building form unknown (derivative_claim) [E074] | 1968 (derivative_claim) [E075] | TX (derivative_claim) [E076] | Unknown vendor; standardized CSV only (derivative_only) [E077] [E078] |
| Deal-10 | value-add (derivative_claim) [E079] | Garden inferred; Student Housing unverified (inferred_from_derivative) [E080] [E081] [E085] | 1979 (derivative_claim) [E082] | TX (derivative_claim) [E083] | Unknown vendor; standardized CSV only (derivative_only) [E084] |
| Deal-11 | Unknown (unknown) [E086] | Townhome (derivative_claim) [E087] | 2022 (derivative_claim) [E088] | TX; city conflict unresolved (derivative_conflict) [E089] [E090] [E091] | Unknown vendor; standardized CSV only (derivative_only) [E092] |
| Deal-12 | Unknown (unknown) [E093] | Unknown (unknown) [E093] | Unknown (unknown) [E093] | Unknown (unknown) [E093] | Unknown; no RR source or normalized RR present (unknown) [E093] |
| Deal-13 | core+ (derivative_claim) [E094] | Unknown building form (unknown) [E094] | 2019 (derivative_claim) [E095] | TX (derivative_claim) [E096] | Unknown vendor; standardized CSV; absent XLS references (derivative_only) [E097] [E098] [E099] |

## Document inventory

| Alias | All files | Supplied raw | Derivative / working | File extensions |
|---|---:|---:|---:|---|
| Deal-01 | 12 | 8 | 4 | .json: 1; .log: 3; .pdf: 5; .xlsm: 1; .xlsx: 2 |
| Deal-02 | 9 | 5 | 4 | .json: 1; .log: 3; .pdf: 4; .xlsx: 1 |
| Deal-03 | 33 | 27 | 6 | .docx: 1; .json: 2; .log: 4; .pdf: 15; .xls: 7; .xlsx: 4 |
| Deal-04 | 37 | 23 | 14 | .json: 6; .log: 8; .pdf: 7; .png: 3; .xlsm: 4; .xlsx: 9 |
| Deal-05 | 13 | 7 | 6 | .json: 2; .log: 4; .pdf: 4; .xlsx: 2; .zip: 1 |
| Deal-06 | 13 | 0 | 13 | .csv: 2; .json: 7; .md: 2; .pdf: 1; .xlsm: 1 |
| Deal-07 | 115 | 0 | 115 | .csv: 23; .json: 66; .md: 19; .txt: 3; .xlsm: 4 |
| Deal-08 | 40 | 0 | 40 | (none): 6; .json: 25; .md: 7; .pdf: 1; .xlsx: 1 |
| Deal-09 | 24 | 0 | 24 | .csv: 4; .ipynb: 1; .json: 11; .md: 4; .py: 3; .xlsx: 1 |
| Deal-10 | 28 | 0 | 28 | .csv: 6; .ipynb: 2; .json: 11; .md: 2; .py: 4; .xlsx: 3 |
| Deal-11 | 10 | 0 | 10 | .csv: 2; .json: 4; .md: 2; .pdf: 1; .xlsm: 1 |
| Deal-12 | 1 | 0 | 1 | .json: 1 |
| Deal-13 | 31 | 0 | 31 | .csv: 5; .json: 18; .md: 4; .pdf: 1; .py: 1; .xlsm: 2 |

## Document types and gaps

### Deal-01

OM; PDF RR; XLSX T12; supplied XLSM underwriting workbook; debt indications/quote/sizer; sales-comps workbook. Parser output/logs are derivatives.

- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-02

Offering summary; PDF residential RR; XLSX income statement; concession-burnoff and rentable-items PDFs. T12 parser output/logs are derivatives.

- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-03

OM; seven XLS RR snapshots; three XLSX T12s; tax bill and tax opinion; financing material; leases/amendment/ground-lease termination; service agreements/scopes; aging report; PDF/XLSX lease trade-outs. Ingest JSON/logs are derivatives.

- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-04

Portfolio OM; three asset-level XLSX RR/T12 pairs; additional RR roll-up/aggregate operating statement/asset summary; three tax PDFs and three tax PNGs; three debt quotes; four supplied XLSM workbooks. Per-asset ingest JSON/logs are derivatives.

- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-05

OM; XLSX RR and T12; debt quote; tax bill; confidentiality agreement; ZIP archive. Ingest JSON/logs are derivatives.

- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-06

Manifest/assumptions; standardized RR/T12 CSV and summary JSON; canonical/model JSON, generated XLSM/PDF; cost-model outputs. No supplied originals on disk.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-07

Manifest/assumptions; extracted OM text (derivative, not PDF original); standardized RR charges/units/change/summary and T12 CSVs; model/cost/market-study outputs; review/handoff memos. No supplied originals on disk.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-08

Intake/canonical/positioning/reconciliation/CRM/comps JSON and Markdown; generated underwriting XLSX and memo PDF; lifecycle markers. No supplied originals on disk.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-09

Manifest and handoffs; normalized RR/T12/floor-plan CSV and summary JSON; canonical/pro-forma/validation JSON; generated XLSX; Python/notebook working artifacts. No supplied originals on disk.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-10

Manifest/implementation plan/config; standardized RR/T12/floor-plan CSV and summary JSON; engine/test/pro-forma JSON/CSV; generated underwriting XLSX variants; Python/notebook working artifacts. No supplied originals on disk.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-11

Manifest/assumptions; standardized RR/T12 CSV and summary JSON; canonical/model JSON; generated XLSM/PDF. No supplied originals on disk.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-12

One generated underwriting deal_summary.json only. No manifest, original OM/RR/T12 or normalized source tables.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

### Deal-13

Manifest/assumptions; standardized RR/T12 CSV and summary JSON; canonical/model JSON and generated XLSM/PDF; market-study/comp-tracking/submarket/supply-demand/sweep outputs; memo and parser script. No supplied originals on disk.

- Original OM/RR/T12 absent locally; derivative metadata is not source validation.
- Strategy, subtype, vintage, geography and PMS evidence status are shown above; consult private per-deal gap records before using any field.
- Scope, chronology, source authenticity and required underwriting approvals are not established by this inventory.

## Citation integrity

Public evidence IDs in the coverage table resolve to the private identified audit.
That bundle contains **106 citations** across **44 SHA256-pinned sources**.
Exact identified paths and source quotes are deliberately not reproduced here.

| Private artifact | SHA256 |
|---|---|
| `DEAL_INVENTORY_AUDIT.private.md` | `6a0183d27d123508644a6f1f0c86b69fea8b21ef411d6c93fd85dc5c7a41479c` |
| `audit_evidence.json` | `be44b92c8f8f9414bd6e05ebbb2b5bd6bcd1136ab104111a69a1f143c50589e6` |
| `inventory.json` | `02bf136ffd1dcec9765cb2d79bcaa140853fc847108e30999d1805889f9ace20` |
| `public_alias_map.json` | `01023ce1b3544742bdd5717518b4cc8d321363464b22505f67a3a7dfd6ca671c` |

## Format backlog

- PDF current/notice/vacant layouts need page-aware parsing; vendor identity is unconfirmed.
- Detailed OneSite BIFF and compact XLS exports are distinct shapes; do not treat branding as universal support.
- Lease-charge XLSX exports include split headers and asset-level versus roll-up scope.
- Ledger/charge-detail XLSX exports have ancillary rows and report-parameter sheets.
- Derived CSVs are parser outputs, not proof of the original PMS.
- Tax PNGs were inventoried without OCR; ZIP contents were not expanded.
- Missing Down/use evidence must remain missing, not inferred zero.
