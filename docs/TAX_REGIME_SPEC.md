# Tax-regime research spec and validation contract: `tax-regime-research/1.0.0`

**Task 4.1.** Evidence-backed applicability research for the four mandatory
state tax regimes, recorded **before any statutory formula is implemented**
(plan Item 4). Tax mathematics belongs in the underwriting engine; the
harness selects and validates an approved regime/assumption package and
never recalculates tax. This document carries **public authoritative
citations only** — no deal-specific parcels, bills, policies or legal review
belong here; those stay in the private evidence root.

```python
from plat_harness.adapters.tax_regime_spec import (
    CONTRACT_VERSION, MANDATORY_REGIMES, RETRIEVAL_DATE,
    load_regime_registry, validate_research, citation_index,
)

registry = load_regime_registry()      # {'TX': ..., 'CA': ..., 'FL': ..., 'AL': ...}
report = validate_research()           # status 'validated' | 'blocked'
sources = citation_index()             # public citations with retrieval dates
```

- **Retrieval date:** all citations below were retrieved `2026-09-21` from the
  named public hosts. Statutory text drifts; any rule implemented later must
  re-research at implementation time and update this spec first.
- **Engine ownership:** the engine owns every unit, rounding and arithmetic
  decision. This registry records applicability, mechanics, units and
  citations — never a computed tax.
- **Human review:** nothing here is production statutory approval. The
  validation gate reports `requires_competent_human_review` for every regime;
  obtain competent human review before production statutory-policy approval.

## Mandatory applicability questions (answered per regime)

| Key | Question |
|---|---|
| `assessment_mechanics` | How is value determined annually? (appraisal basis, assessment ratio/class) |
| `purchase_price_and_ownership_events` | What do a sale, entity transfer, or foreclosure do to the base? |
| `annual_adjustment_limits` | What caps annual value growth, and for which levies? |
| `levy_structure_and_units` | What are the levy components and their units? |
| `exemptions_and_exclusions` | Which exemptions/exclusions exist, and what is expressly out of scope? |
| `timing_and_remedies` | Key calendar dates and protest/appeal/supplemental timing |
| `new_construction` | How does new construction enter assessed value? |
| `asset_class_applicability` | Which statute/class applies to multifamily, specifically? |

Every answer is either `SUPPORTED: …` (primary sources support the claim) or
`UNCERTAIN …` (uncertainty explicitly blocks the statutory claim). There is
no third state: a question that cannot be answered from primary sources is
recorded as blocking, never guessed.

## Validation contract

As the plan's architecture decision records: tax mathematics belongs in the underwriting engine.
Promoting research into an implemented rule requires, recorded per regime:

1. **`primary_source_support`** — each implemented rule cites a statute- or
   constitution-tier primary source with URL, locator, jurisdiction,
   retrieval date and effective tax year.
2. **`uncertainty_blocks_claims`** — recorded uncertainty (an `UNCERTAIN`
   finding, an expired/sunsetting provision, a per-county input gap) blocks
   the claim rather than shipping a plausible default.
3. **`competent_human_review`** — research alone is never production
   statutory approval; approval requires competent human review outside this
   module (the gate reports `requires_competent_human_review`).
4. **`retrieval_date_recorded`** — every citation records the date it was
   retrieved, and rules re-research statutes at implementation time.

Unknown jurisdictions refuse (`UNSUPPORTED_TAX_REGIME`): there is no
"Standard" fallback state, and no unexplained universal fallback exists.
Unresearched or uncertainty-blocked rule families refuse
(`UNSUPPORTED_TAX_RULE_FAMILY` / `UNCERTAINTY_BLOCKS_STATUTORY_CLAIM`).

## Correction of the prior plan's purchase-price claims

The previous master plan asserted, without statutory support, that "Texas
Ch. 23 resets to 80–100% of purchase price" with a "1-year protest lag", and
that California is a flat "1% base tax", and that Florida has a generic
"non-homestead 10% cap" applying to all levies. Research shows:

- **Texas:** a purchase price is **evidence, not a statutory purchase-price percentage** —
  it is evidence of market value under § 23.013
  comparable-sales rules; § 23.01
  appraises at market value each Jan 1. The "80–100% reset" is not a statutory
  rule. Protest timing (May 15 / 30 days after notice) is a real deadline
  calendar, not a value-lag mechanism.
- **California:** the base is **full cash value at acquisition** with a
  **2 percent annual CPI cap**, and the rate is **1 percent plus voter-approved
  additions — not a single universal rate**. A complete bill requires
  parcel-level local debt data.
- **Florida:** the 10 percent cap applies **for all levies other than school
  district levies**, and **10+-unit multifamily is governed by § 193.1555,
  not § 193.1554** (9-or-fewer units). Insurance escalation is separate from
  tax law and stays a separately approved assumption.
- **Alabama:** no universal fallback; Class II is 20 percent of fair market
  value and millage is jurisdiction-specific.

## TX — Texas (regime `tx_ch23_market_value`)

Market-value regime: Jan-1 appraisal at 100% of appraised value; no
assessment ratio; levy is the sum of overlapping taxing units' adopted
per-$100 rates.

| Citation | Locator | Tier | Key fact |
|---|---|---|---|
| [Tex. Tax Code § 23.01](https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=23.01) | § 23.01(a),(b) | statute | Jan-1 market-value appraisal; generally accepted methods; property-specific evidence considered |
| [Tex. Tax Code § 23.013](https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=23.013) | § 23.013 | statute | Comparable-sales method; 24/36-month windows; a sale is evidence |
| [Tex. Tax Code § 23.231](https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=23.231) | § 23.231(b),(d),(f),(j),(k) | statute | 20% circuit breaker for qualifying non-homestead real property under a CPI-adjusted threshold ($5,320,000 for 2026); **expires Dec 31, 2026** |
| [Tex. Tax Code § 26.02](https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=26.02) | § 26.02 | statute | Assessment ratios prohibited; 100% of appraised value |
| [Tex. Tax Code § 26.04](https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=26.04) | § 26.04(c) | statute | Per-$100 rate structure; no-new-revenue/voter-approval formulas |
| [Tex. Tax Code § 26.09](https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=26.09) | § 26.09(c) | statute | Tax calculation order (engine owns the arithmetic) |
| [Tex. Tax Code § 41.44](https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=41.44) | § 41.44(a)(1) | statute | Protest by May 15 or 30 days after notice delivery, whichever later |
| [TX Comptroller — Valuing Property](https://comptroller.texas.gov/taxes/property-tax/valuing-property.php) | Comptroller guidance | administrative_guidance | Notice deadlines; circuit-breaker threshold publication; approach notes |

Answers to the mandatory questions (findings are quoted in the module
registry; citations per question are listed there as `citation_id`s):
`assessment_mechanics` SUPPORTED (§§ 23.01, 26.02);
`purchase_price_and_ownership_events` SUPPORTED — **evidence, not a
statutory purchase-price percentage** (§§ 23.01, 23.013);
`annual_adjustment_limits` SUPPORTED-with-expiry — the § 23.231 circuit
breaker **expires December 31, 2026**, so post-2026 applicability is UNCERTAIN
and blocks until re-researched (§ 23.231(j),(k));
`levy_structure_and_units` SUPPORTED (§§ 26.04, 26.09);
`exemptions_and_exclusions` SUPPORTED as out-of-scope (§ 23.231(c));
`timing_and_remedies` SUPPORTED (§ 41.44(a)(1); May 1 notices);
`new_construction` SUPPORTED (§ 23.231(a)(3),(d)(2)(C));
`asset_class_applicability` SUPPORTED (ordinary taxable real property).

## CA — California (regime `ca_prop13_base_year`)

Acquisition-value regime: base year value at full cash value on change in
ownership/new construction; ≤2% annual CPI growth; 1% rate cap plus
voter-approved additions; statutory supplemental-roll proration.

| Citation | Locator | Tier | Key fact |
|---|---|---|---|
| [Cal. Const. art. XIII A § 1](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?article=XIII+A&lawCode=CONS&sectionNum=SECTION+1) | § 1(a) | constitution | 1% maximum ad valorem rate; additions for voter-approved debt/school levies |
| [Cal. Const. art. XIII A § 2](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?article=XIII+A&lawCode=CONS&sectionNum=SEC.+2.) | § 2(a),(b) | constitution | Base year value at purchase/new construction/change in ownership; ≤2% annual CPI adjustment |
| [Cal. Rev. & Tax. Code § 64](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=64.&lawCode=RTC) | § 64(a),(c),(d) | statute | Entity transfers excluded except >50% control changes and original-co-owner cumulative >50% transfers |
| [Cal. Rev. & Tax. Code § 75.11](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=75.11&lawCode=RTC) | § 75.11(a),(b) | statute | Two supplemental assessments (Jan 1–May 31 events) vs one (Jun 1–Dec 31) |
| [Cal. Rev. & Tax. Code § 75.41](https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?article=5.&chapter=3.5.&division=1.&lawCode=RTC&part=0.5.&title=) | § 75.41(b),(c) | statute | Month-following presumption; monthly proration factors |
| [BOE — Supplemental Assessment](https://www.boe.ca.gov/proptaxes/supplemental-assessment/) | BOE guidance | administrative_guidance | Supplemental bill timing across the Jul–Jun fiscal year |
| [BOE — Proposition 19](https://boe.ca.gov/prop19/) | BOE guidance (art. XIII A § 2.1; R&TC § 63.2) | administrative_guidance | Intergenerational exclusions narrowed Feb 16, 2021; family home/farm only |
| [Property Tax Rule 462.180](https://www.boe.ca.gov/proptaxes/pdf/rules/Rule462_180.pdf) | Rule 462.180(d) | administrative_guidance | Control = >50% voting stock or partnership/LLC capital-and-profits; JV/LLP applicability |

Answers: `assessment_mechanics` SUPPORTED (art. XIII A § 2);
`purchase_price_and_ownership_events` SUPPORTED (§ 64(c)/(d); Rule 462.180);
`annual_adjustment_limits` SUPPORTED with an input caveat — the annual CPI
factor is BOE-published per year and is parcel input, **not a hardcoded
constant**; `levy_structure_and_units` SUPPORTED — 1% plus voter-approved
additions, **not a single universal rate**;
`exemptions_and_exclusions` SUPPORTED as recorded (Prop 19 narrowing;
others require parcel review); `timing_and_remedies` SUPPORTED (§§ 75.11,
75.41); `new_construction` SUPPORTED (separate base year value for the new
portion); `asset_class_applicability` SUPPORTED (all real property including
multi-unit residential).

## FL — Florida (regime `fl_nonhomestead_10pct_cap`)

Just-value regime with a 10% nonhomestead assessment cap **for all levies
other than school district levies**; permanent since Amendment 2 (2018).
**Multifamily 10+ units is § 193.1555**, not § 193.1554.

| Citation | Locator | Tier | Key fact |
|---|---|---|---|
| [Fla. Const. art. VII § 4](https://www.flsenate.gov/Laws/Constitution?Article=VII&Section=4) | § 4(g) | constitution | 9-or-fewer-unit nonhomestead residential: 10% cap, non-school levies |
| [Fla. Const. art. VII § 4](https://www.flsenate.gov/Laws/Constitution?Article=VII&Section=4) | § 4(h) | constitution | Other nonhomestead real property: 10% cap, non-school levies |
| [Fla. Stat. § 193.1555](https://www.flsenate.gov/Laws/statutes/2026/193.1555) | § 193.1555(2),(3),(5) | statute | **Multifamily 10+ units**; just value at qualifying; 10% cap; ownership/control change reset (incl. >50% entity transfer) |
| [Fla. Stat. § 193.1554](https://www.flsenate.gov/Laws/statutes/2026/193.1554) | § 193.1554(1),(3),(5) | statute | 9-or-fewer units only — cited to prevent misclassification |
| [Fla. Stat. § 200.065](https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0200-0299/0200/Sections/0200.065.html) | § 200.065(1) | statute | Millage per $1,000 of taxable value; TRIM rolled-back rate machinery |
| [FL DOS — Amendment 2 (2018)](https://files.floridados.gov/media/699824/constitutional-amendments-2018-general-election-english.pdf) | art. XII § 27 | constitution | 10% cap made permanent effective Jan 1, 2019 |

Answers: `assessment_mechanics` SUPPORTED (§§ 4(g)/(h), 193.1555);
`purchase_price_and_ownership_events` SUPPORTED (§ 193.1555(5));
`annual_adjustment_limits` SUPPORTED (10% non-school; permanent);
`levy_structure_and_units` SUPPORTED (mills per $1,000; separate school
basis); `exemptions_and_exclusions` SUPPORTED as out-of-scope (homestead/
agricultural classified uses excluded); `timing_and_remedies` SUPPORTED as
procedure (TRIM calendar); `new_construction` SUPPORTED (first Jan 1 after
substantial completion; qualifying improvement ≥25% resets);
`asset_class_applicability` SUPPORTED — unit count is a mandatory
applicability input (10+ → § 193.1555; ≤9 → § 193.1554).

**Insurance is a separate assumption**, outside this spec and outside tax
law; it must never be merged into a tax regime.

## AL — Alabama (regime `al_class_ii_millage`)

Classified property regime: Class II at 20% of fair and reasonable market
value; state 6.5 mills plus jurisdiction-specific county/municipal/school
millages (one mill = $1 per $1,000 of assessed value); tax year begins Oct 1.

| Citation | Locator | Tier | Key fact |
|---|---|---|---|
| [Ala. Code § 40-8-1](https://alison.legislature.state.al.us/code-of-alabama?section=40-8-1) | § 40-8-1(a) Class II | statute | Class II = all property not otherwise classified, 20% of fair and reasonable market value |
| [ADOR — Property (Ad Valorem) Tax](https://www.revenue.alabama.gov/tax-types/property-ad-valorem-tax/) | ADOR guidance | administrative_guidance | State 6.5 mills composition; county millages vary by jurisdiction |
| [ADOR — What is a mill?](https://www.revenue.alabama.gov/faqs/what-is-a-mill/) | ADOR guidance | administrative_guidance | 1 mill = $1 per $1,000 assessed value; Oct 1 due date, Jan 1 delinquent |
| [ADOR — October 2025 Millage Rates](https://www.revenue.alabama.gov/wp-content/uploads/2025/09/2025-Millage-Rates.pdf) | Per-county schedule | local_procedure | Published per-county millage schedules — input data, never a universal default |

Answers: `assessment_mechanics` SUPPORTED (fair market value; annual
reappraisal); `purchase_price_and_ownership_events` SUPPORTED (no statutory
reset; sales are market evidence); `annual_adjustment_limits` SUPPORTED (no
cap; equalization cycle); `levy_structure_and_units` SUPPORTED (6.5 state
mills + county schedules; mills per $1,000 of **assessed** value);
`exemptions_and_exclusions` SUPPORTED as out-of-scope (Class III current-use
application only); `timing_and_remedies` SUPPORTED as procedure (county
board of equalization; Oct 1/Jan 1 calendar); `new_construction` SUPPORTED
(annual reappraisal discovery); `asset_class_applicability` SUPPORTED
(Class II 20% ratio is a mandatory per-parcel input).

## What this spec deliberately does not contain

- **No statutory formulas or rates as defaults.** The researched constants
  (thresholds, ratios, caps) live in the citation records and the future
  engine-owned schedule; this registry stores no `formula`/`rates` keys, and
  the module computes nothing.
- **No deal-specific parcels, bills, policies or legal review.** Those stay
  private in the evidence root, as required.
- **No production statutory approval.** The validation gate reports
  `requires_competent_human_review` for every regime.
- **No universal fallback jurisdiction.** Unknown states refuse.