# Northstar — the OSS an honest REPE would actually run

**Audience:** someone cloning this public repo.

**Not this file:** a pitch deck, or a promise that every acquisitions loop is already wired.

**This file:** the product shape. Certified underwriting + NOI-variance, local-first, rented model optional.

---

## 1. Who this is for

| Seat | What they do on Monday | What the product must not do to them |
|---|---|---|
| **Acquisitions / underwriting** | Screen OM packages, kill bad deals, price to the GP’s CoC hurdle, IC memo | Invent IRR; treat broker market rent as in-place; skip millage |
| **Asset manager** | Monthly NOI vs budget, T12, delinquencies, renewals, PM questions | Blame the PM without a source; auto-send an LP letter |
| **Analyst** | Reconcile T12 vs OM vs house; pull comps; occupancy four counts | Silent zeros; occupancy without a denominator |
| **IC / principal** | Read a cited memo, see kill-log, see blockers | Trust a dashboard “avg IRR 18.4%” with no engine |
| **Ops / IT (other GPs)** | Clone, point at PMS exports, set policy YAML | Be stuck on another GP’s hurdle or unit box |

---

## 2. Jobs to be done

### 2.1 Acquisitions loop

Drop a data room → classify without substring traps → standardize rent roll (`lease_rent` vs `market_rent`) → comps four-pack or `NEEDS_DATA` → three-way recon before price → millage gate → house case from in-place rents → backsolve to the **GP’s** Year-1 CoC (Decimal engine extra) → IC memo from one template, every claim a path → HITL publish.

### 2.2 Asset-management loop

Ingest Standardized exports → data-QA → NOI variance with materiality → occupancy = occupied / (occupied+vacant+down) → delinquency worst-first → call ledger → HITL owner commentary. Never auto-send.

### 2.3 Firm loop

Pipeline stages are funnel SOP states. Every deal shows **certified** metrics or `uncertified_empty`. Policy plugin: another GP changes hurdles without forking math.

---

## 3. Surfaces (cover the job, beat the honesty)

| Job | Honest OSS equivalent | Beat a hosted demo by |
|---|---|---|
| Deal pipeline board | Funnel stages + kill-log | Stages are SOP, not a vanity rollup |
| Underwriting | Decimal engine extra + millage + house policy | CoC from engine; no `IRR = CoC * 0.8` |
| Rent roll | Shared standardizer; `lease_rent` ≠ `market_rent` | In-place is Year-1 default |
| Comps | ETL + optional Playwright; four-pack contract | Evidence only; thin coverage → `NEEDS_DATA` |
| IC memos | One template; Rank 3 HITL | Citations required |
| T-12 / opex delta | Engine + recon bridge | Strip subtotals; missing feed ≠ $0 |

**Refuse to clone:** demo avg IRR with no engine; unattended overnight memo publish; market-data-points as the moat.

---

## 4. Load-bearing extras

Three-way recon before price. Millage as Year-1 tax. In-place vs market rent. Comp coverage gates. Occupancy four counts. Certified metric handshake (CONFLICT / UNCERTIFIED / IMPLICIT_ZERO). Deal room outside git. Policy plugin. Agent-agnostic CLI.

---

## 5. This public repo

```text
plat-harness/
  README.md
  LICENSE          # Apache-2.0
  harness/         # plat_harness CLI, tools, middleware
  engine/          # path-dep stub — bring your Decimal engine
  boxscore/        # sanitized calc notes (no .db, no asset names)
  market_study/    # stub (parsers later, no live keys/PII)
  policies/        # generic GP YAML
  samples/         # synthetic 80-unit deal + synthetic Standardized CSVs
  tests/           # env-stripped
```

GP overlays (hurdle, millage sources, live rooms) stay **out** of this git tree. Mount them with env.

---

## 6. Operator API

```bash
plat-harness ask --metric …
plat-harness scoreboard --asset example_property
plat-harness underwrite --millage-rate …
```

A sentence may *fill a template*. A sentence may not *invent a metric*.

---

## 7. What we will never be

A CoStar replacement. An LP-letter robot. A vibe-coded app factory. A wrapper whose answers change when you switch models. A public dump of anyone’s deal rooms.

**The harness is the product. The model is rented.**
