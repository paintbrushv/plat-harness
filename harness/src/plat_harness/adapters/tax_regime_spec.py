"""Evidence-backed tax-regime research registry and validation gate.

Task 4.1 of the harness product plan. Tax mathematics belongs in the
underwriting engine (a sibling repository); this module is NOT a tax
calculator. It records, per mandatory regime, the applicability research
the plan requires: statutory mechanics, purchase-price treatment, levy
structure, protest/supplemental timing, citation provenance (public
authoritative sources only, with retrieval dates), and the validation
contract that governs promoting research into implemented rules.

No deal-specific parcels, bills, policies or legal review appear here;
those stay private. Nothing in this module computes a tax.
"""
from __future__ import annotations

from copy import deepcopy

from plat_harness.errors import HarnessError

CONTRACT_VERSION = "tax-regime-research/1.0.0"

# Refusal codes (module-local, closed set).
UNSUPPORTED_REGIME = "UNSUPPORTED_TAX_REGIME"
UNSUPPORTED_RULE_FAMILY = "UNSUPPORTED_TAX_RULE_FAMILY"
UNCERTAINTY_BLOCK = "UNCERTAINTY_BLOCKS_STATUTORY_CLAIM"

# All citations were retrieved on this date, at implementation time, from
# the public authoritative hosts named in each citation URL.
RETRIEVAL_DATE = "2026-09-21"

MANDATORY_REGIMES = ("TX", "CA", "FL", "AL")

CITATION_FIELDS = (
    "citation_id",
    "url",
    "locator",
    "authority_tier",
    "jurisdiction",
    "retrieved",
    "effective_tax_year",
    "note",
)

AUTHORITY_TIERS = ("statute", "constitution", "administrative_guidance", "local_procedure")

MANDATORY_QUESTION_KEYS = (
    "assessment_mechanics",
    "purchase_price_and_ownership_events",
    "annual_adjustment_limits",
    "levy_structure_and_units",
    "exemptions_and_exclusions",
    "timing_and_remedies",
    "new_construction",
    "asset_class_applicability",
)

VALIDATION_REQUIREMENTS = (
    "primary_source_support",
    "uncertainty_blocks_claims",
    "competent_human_review",
    "retrieval_date_recorded",
)

_ENGINE_OWNS_MATH = "Tax mathematics belongs in the underwriting engine; the harness selects and validates an approved regime package and never recalculates tax."
_HARNESS_ROLE = "select_and_validate"
_NO_FALLBACK = "Refused: no researched applicability for this jurisdiction, and no unexplained universal fallback exists."

# Rule families recognized by the validation gate. A family not listed here
# has not been researched; claiming it refuses with UNSUPPORTED_RULE_FAMILY.
SUPPORTED_RULE_FAMILIES = {
    "TX": frozenset({
        "market_value_appraisal",
        "levy_per_100",
        "protest_timing",
        "sale_price_evidence",
        "circuit_breaker",
    }),
    "CA": frozenset({
        "base_year_value",
        "one_percent_rate_cap",
        "supplemental_proration",
        "change_in_ownership_rules",
        "annual_two_percent_limit",
    }),
    "FL": frozenset({
        "nonhomestead_ten_percent_cap",
        "millage_per_1000",
    }),
    "AL": frozenset({
        "class_ii_ratio",
        "millage_per_assessed_dollar",
    }),
}
# Families with a known open applicability question at retrieval time.
# TX circuit_breaker: § 23.231 expires December 31, 2026 (subsection (k)), so
# any tax year after 2026 has unresearched applicability — the family is
# researched but blocked until re-research. (CA's 2-percent limit is solid
# statute; the BOE-published annual CPI factor is per-year input data, not
# legal uncertainty.)
_UNCERTAIN_FAMILIES = {
    "TX": frozenset({"circuit_breaker"}),
}

_CITATIONS = {
    "TX": (
        {
            "citation_id": "tx_23.01",
            "url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=23.01",
            "locator": "Tex. Tax Code § 23.01(a),(b)",
            "authority_tier": "statute",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026 (statutes current through 89th 2nd C.S. 2025)",
            "note": "All taxable property appraised at market value as of January 1; generally accepted appraisal methods; individual characteristics and all property-specific evidence taken into account.",
        },
        {
            "citation_id": "tx_23.231",
            "url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=23.231",
            "locator": "Tex. Tax Code § 23.231(b),(d),(f),(j),(k)",
            "authority_tier": "statute",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2024-2026",
            "note": "Circuit breaker: appraised-value increases capped at 20% over prior year plus new improvements for qualifying non-homestead real property; eligibility threshold $5,000,000 (2024), CPI-adjusted and published by the Comptroller ($5,160,000 for 2025, $5,320,000 for 2026 per Comptroller guidance); the section expires December 31, 2026.",
        },
        {
            "citation_id": "tx_26.02",
            "url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=26.02",
            "locator": "Tex. Tax Code § 26.02",
            "authority_tier": "statute",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Assessment ratios prohibited: all property assessed on the basis of 100 percent of appraised value.",
        },
        {
            "citation_id": "tx_26.04",
            "url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=26.04",
            "locator": "Tex. Tax Code § 26.04(c)",
            "authority_tier": "statute",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Taxing-unit rates are expressed in dollars (cents) per $100 of taxable value; no-new-revenue and voter-approval rate formulas defined here.",
        },
        {
            "citation_id": "tx_26.09",
            "url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=26.09",
            "locator": "Tex. Tax Code § 26.09(c)",
            "authority_tier": "statute",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Calculation of tax: net appraised value minus exemptions, times assessment ratio, minus exemptions, times tax rate. The engine owns this arithmetic.",
        },
        {
            "citation_id": "tx_41.44",
            "url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=41.44",
            "locator": "Tex. Tax Code § 41.44(a)(1)",
            "authority_tier": "statute",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Protest deadline: May 15 or the 30th day after the notice of appraised value is delivered, whichever is later.",
        },
        {
            "citation_id": "tx_comptroller_valuing",
            "url": "https://comptroller.texas.gov/taxes/property-tax/valuing-property.php",
            "locator": "Texas Comptroller, 'Valuing Property'",
            "authority_tier": "administrative_guidance",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Administrative guidance confirming Jan. 1 market-value appraisal, three common appraisal approaches, notice-of-appraised-value deadlines (Apr. 1 residence / May 1 other property) and circuit-breaker eligibility amounts. Guidance annotates statutes; it is not itself the statute.",
        },
        {
            "citation_id": "tx_23.013",
            "url": "https://statutes.capitol.texas.gov/GetStatute.aspx?Code=TX&Value=23.013",
            "locator": "Tex. Tax Code § 23.013",
            "authority_tier": "statute",
            "jurisdiction": "Texas",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Market data comparison method: comparable-sales use, 24-month window (36 months for residential in counties over 150,000 population), time adjustment. A recent arm's-length sale is strong evidence of market value, not a statutory purchase-price percentage.",
        },
    ),
    "CA": (
        {
            "citation_id": "ca_xiiia_1",
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?article=XIII+A&lawCode=CONS&sectionNum=SECTION+1",
            "locator": "Cal. Const. art. XIII A, § 1(a)",
            "authority_tier": "constitution",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Maximum ad valorem tax on real property: 1 percent of full cash value, collected by counties; voter-approved bonded indebtedness and specified school levies are additions outside the 1 percent. A complete bill is 1 percent plus voter-approved additions — not a single universal rate.",
        },
        {
            "citation_id": "ca_xiiia_2",
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?article=XIII+A&lawCode=CONS&sectionNum=SEC.+2.",
            "locator": "Cal. Const. art. XIII A, § 2(a),(b)",
            "authority_tier": "constitution",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Full cash value means the appraised value when purchased, newly constructed, or after a change in ownership; base-year value may increase annually by the CPI inflationary rate not to exceed 2 percent.",
        },
        {
            "citation_id": "ca_rtc_64",
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=64.&lawCode=RTC",
            "locator": "Cal. Rev. & Tax. Code § 64(a),(c),(d)",
            "authority_tier": "statute",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Entity-interest transfers are generally not changes in ownership, except: obtaining more-than-50-percent control (direct or indirect) of the entity, or original co-owners cumulatively transferring more than 50 percent of previously excluded property interests.",
        },
        {
            "citation_id": "ca_rtc_75.11",
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=75.11&lawCode=RTC",
            "locator": "Cal. Rev. & Tax. Code § 75.11(a),(b)",
            "authority_tier": "statute",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Supplemental assessment: events Jan 1-May 31 produce two supplemental assessments (current roll difference plus roll-being-prepared difference); events Jun 1-Dec 31 produce one (current roll difference).",
        },
        {
            "citation_id": "ca_rtc_75.41",
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?article=5.&chapter=3.5.&division=1.&lawCode=RTC&part=0.5.&title=",
            "locator": "Cal. Rev. & Tax. Code § 75.41(b),(c)",
            "authority_tier": "statute",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Supplemental proration: event presumed to occur the first day of the month following the change in ownership or new construction completion; monthly proration factors (e.g., July 1 -> 1.00, Jan 1 -> 0.50, Dec 1 -> 0.58) apply the current roll's rate to the net supplemental assessment. Engine owns the arithmetic.",
        },
        {
            "citation_id": "ca_boe_supplemental",
            "url": "https://www.boe.ca.gov/proptaxes/supplemental-assessment/",
            "locator": "CA BOE, 'Supplemental Assessment' (R&TC §§ 75-75.72 summary)",
            "authority_tier": "administrative_guidance",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Administrative guidance: supplemental bills cover the first day of the month following the event through fiscal-year end (Jul 1-Jun 30); one bill for Jun 1-Dec 31 events, two for Jan 1-May 31 events.",
        },
        {
            "citation_id": "ca_boe_prop19",
            "url": "https://boe.ca.gov/prop19/",
            "locator": "CA BOE, Proposition 19 (art. XIII A § 2.1; R&TC § 63.2)",
            "authority_tier": "administrative_guidance",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Former Prop 58/193 parent-child and $1,000,000 other-property exclusions ended for transfers on/after Feb 16, 2021; current intergenerational exclusion covers family homes/farms only, capped at factored base year value plus an inflation-adjusted $1,000,000 ($1,044,586 for Feb 16 2025-Feb 15 2027). Relevant to ownership-event classification, never to market-rate acquisitions.",
        },
        {
            "citation_id": "ca_rule_462.180",
            "url": "https://www.boe.ca.gov/proptaxes/pdf/rules/Rule462_180.pdf",
            "locator": "18 Cal. Code Regs. tit. 18, Property Tax Rule 462.180",
            "authority_tier": "administrative_guidance",
            "jurisdiction": "California",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Rule 462.180 (Change in Ownership—Legal Entities): control means more than 50 percent of voting stock or of total partnership/LLC capital AND profits interests; applies directly to multifamily JV/LLP structures.",
        },
    ),
    "FL": (
        {
            "citation_id": "fl_const_4g",
            "url": "https://www.flsenate.gov/Laws/Constitution?Article=VII&Section=4",
            "locator": "Fla. Const. art. VII, § 4(g)",
            "authority_tier": "constitution",
            "jurisdiction": "Florida",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Nonhomestead residential property containing nine units or fewer: annual assessment changes may not exceed 10 percent of the prior year's assessment, for all levies other than school district levies; no assessment exceeds just value; reset to just value after change of ownership or control.",
        },
        {
            "citation_id": "fl_const_4h",
            "url": "https://www.flsenate.gov/Laws/Constitution?Article=VII&Section=4",
            "locator": "Fla. Const. art. VII, § 4(h)",
            "authority_tier": "constitution",
            "jurisdiction": "Florida",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Other nonhomestead real property (including multifamily 10+ units): same 10 percent annual cap for all levies other than school district levies; no assessment exceeds just value; the legislature must require just-value assessment after a qualifying improvement and may require it after change of ownership or control.",
        },
        {
            "citation_id": "fl_193.1555",
            "url": "https://www.flsenate.gov/Laws/statutes/2026/193.1555",
            "locator": "Fla. Stat. § 193.1555(2),(3),(5)",
            "authority_tier": "statute",
            "jurisdiction": "Florida",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "The statute that governs 10+-unit multifamily: nonhomestead residential and nonresidential real property assessed at just value as of January 1 of the qualifying year, then 10 percent annual cap for all levies other than school district levies; change of ownership or control (including cumulative transfer of more than 50 percent of the owning legal entity) resets to just value.",
        },
        {
            "citation_id": "fl_193.1554",
            "url": "https://www.flsenate.gov/Laws/statutes/2026/193.1554",
            "locator": "Fla. Stat. § 193.1554(1),(3),(5)",
            "authority_tier": "statute",
            "jurisdiction": "Florida",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Nine-or-fewer-unit nonhomestead residential property only. Cited to prevent misclassification: 10+-unit multifamily belongs under § 193.1555, not here.",
        },
        {
            "citation_id": "fl_200.065",
            "url": "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0200-0299/0200/Sections/0200.065.html",
            "locator": "Fla. Stat. § 200.065(1)",
            "authority_tier": "statute",
            "jurisdiction": "Florida",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Method of fixing millage: millage rates are levied per $1,000 of taxable value (a mill is $1 per $1,000); TRIM rolled-back rate and hearing machinery. Levy units and TRIM procedure, not tax arithmetic, which the engine owns.",
        },
        {
            "citation_id": "fl_dor_amendment2_2018",
            "url": "https://files.floridados.gov/media/699824/constitutional-amendments-2018-general-election-english.pdf",
            "locator": "Fla. Dept. of State, Proposed Constitutional Amendments 2018 General Election, Amendment 2 (art. XII § 27)",
            "authority_tier": "constitution",
            "jurisdiction": "Florida",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "Voters permanently retained the 10 percent nonhomestead assessment-increase cap (approved Nov 6, 2018; effective Jan 1, 2019), removing its scheduled 2019 repeal. Confirms the cap is current law for the 2026 tax year.",
        },
    ),
    "AL": (
        {
            "citation_id": "al_40-8-1",
            "url": "https://alison.legislature.state.al.us/code-of-alabama?section=40-8-1",
            "locator": "Ala. Code § 40-8-1(a) (Class II)",
            "authority_tier": "statute",
            "jurisdiction": "Alabama",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026 (tax year beginning Oct 1)",
            "note": "Classification of property: Class II is all property not otherwise classified, assessed at 20 percent of fair and reasonable market value (or current use value where provided by law). Multifamily real property is Class II; there is no unexplained universal fallback class.",
        },
        {
            "citation_id": "al_ador_property",
            "url": "https://www.revenue.alabama.gov/tax-types/property-ad-valorem-tax/",
            "locator": "Alabama Department of Revenue, 'Property (Ad Valorem) Tax'",
            "authority_tier": "administrative_guidance",
            "jurisdiction": "Alabama",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "State rate is 6.5 mills (3 public school, 1 soldier relief per the ADOR millage schedules, 2.5 general fund; the ADOR property-tax page currently labels the third mill 'Human Resources Fund'); county and municipal millages vary by taxing jurisdiction; property appraised at fair and reasonable market value except Class III current-use on application.",
        },
        {
            "citation_id": "al_ador_millage",
            "url": "https://www.revenue.alabama.gov/faqs/what-is-a-mill/",
            "locator": "Alabama Department of Revenue, 'What is a mill?'",
            "authority_tier": "administrative_guidance",
            "jurisdiction": "Alabama",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026",
            "note": "One mill is one-tenth of one cent ($1 per $1,000 of assessed value); county commissions and other taxing agencies set millage; taxes due October 1, delinquent January 1.",
        },
        {
            "citation_id": "al_2025_millage",
            "url": "https://www.revenue.alabama.gov/wp-content/uploads/2025/09/2025-Millage-Rates.pdf",
            "locator": "Alabama Department of Revenue, 'October 2025 Millage Rates'",
            "authority_tier": "local_procedure",
            "jurisdiction": "Alabama (per county)",
            "retrieved": RETRIEVAL_DATE,
            "effective_tax_year": "2026 (Oct 2025 rate publication)",
            "note": "Published per-county state/county/school/municipal millage schedules. County-specific input data for the engine's levy schedule — jurisdiction-specific, never a universal default.",
        },
    ),
}

CITATION_INDEX = {
    citation["citation_id"]: citation
    for regime_citations in _CITATIONS.values()
    for citation in regime_citations
}

_REGIMES = {
    "TX": {
        "regime_id": "tx_ch23_market_value",
        "math_owner": "engine",
        "harness_role": _HARNESS_ROLE,
        "classification": "market_value_full_appraisal",
        "purchase_price_treatment": "evidence_not_statutory_reset",
        "insurance": "separate_assumption_out_of_scope",
        "findings": (
            "Texas appraises taxable property at market value as of January 1 each year using generally accepted appraisal methods (Tex. Tax Code § 23.01); assessment ratios are prohibited and all property is assessed at 100 percent of appraised value (§ 26.02).",
            "A recent arm's-length purchase price is evidence of market value under the market-data-comparison and income methods, not a statutory purchase-price percentage; there is no statutory '80-100 percent of purchase price' reset. The prior plan's purchase-price percentages are not self-validating statutory rules.",
            "Taxing units (ISD, county, city, college, special district) each adopt a rate in cents (dollars) per $100 of taxable value under chapter 26 truth-in-taxation; the total levy is the sum of the overlapping units' adopted rates (§§ 26.04, 26.05, 26.09).",
            "A non-homestead circuit breaker caps the appraised-value increase at 20 percent (plus new-improvement value) for qualifying real property under a Comptroller-published value threshold ($5,320,000 for 2026), first effective the tax year after the owner's first Jan 1 ownership year, and § 23.231 expires December 31, 2026 — eligibility must be re-researched for any later tax year.",
            "Protest timing: a written notice of protest must be filed by May 15 or within 30 days after the notice of appraised value is delivered, whichever is later (§ 41.44(a)(1)); notices of appraised value are due May 1 (or as soon thereafter as practicable) for non-residence-homestead property. This is a timing/cash-flow fact, not a statutory one-year value 'lag'.",
        ),
        "mandatory_questions": {
            "assessment_mechanics": {
                "finding": "SUPPORTED: annual Jan-1 market-value appraisal with mass/individual appraisal methods; no assessment ratio (100 percent of appraised value).",
                "citations": ("tx_23.01", "tx_26.02"),
            },
            "purchase_price_and_ownership_events": {
                "finding": "SUPPORTED: ownership change updates the owner of record but does not itself reset value; the district appraises to market value and a sale is comparable-sales evidence (§ 23.013). Purchase-price percentage resets are NOT statutory rules.",
                "citations": ("tx_23.01", "tx_23.013", "tx_comptroller_valuing"),
            },
            "annual_adjustment_limits": {
                "finding": "SUPPORTED with an expiry: non-homestead real property may be capped at prior appraised value + 20 percent + new improvements under the circuit breaker (§ 23.231) when under the Comptroller's published threshold; the section expires December 31, 2026, so any year-after-2026 applicability is UNCERTAIN and must block until re-researched.",
                "citations": ("tx_23.231", "tx_comptroller_valuing"),
            },
            "levy_structure_and_units": {
                "finding": "SUPPORTED: per-$100 adopted rates summed across all overlapping taxing units; no-new-revenue and voter-approval rate machinery governs adoption (§§ 26.04, 26.05, 26.09).",
                "citations": ("tx_26.04", "tx_26.09"),
            },
            "exemptions_and_exclusions": {
                "finding": "SUPPORTED as out-of-scope: residence-homestead exemptions and special-appraisal subchapters are excluded from the circuit breaker and from multifamily underwriting scope here; no exemption is assumed without parcel evidence.",
                "citations": ("tx_23.231",),
            },
            "timing_and_remedies": {
                "finding": "SUPPORTED: notice of appraised value May 1 (non-homestead); protest by May 15 or 30 days after notice delivery, whichever is later (§ 41.44(a)(1)).",
                "citations": ("tx_41.44", "tx_comptroller_valuing"),
            },
            "new_construction": {
                "finding": "SUPPORTED: new improvements (not repairs/ordinary maintenance) are appraised at market value and added outside any circuit-breaker cap (§ 23.231(a)(3),(d)(2)(C)).",
                "citations": ("tx_23.231",),
            },
            "asset_class_applicability": {
                "finding": "SUPPORTED: multifamily is ordinary taxable real property appraised at market value (frequently via the income method); no special statutory class applies to apartments.",
                "citations": ("tx_23.01", "tx_comptroller_valuing"),
            },
        },
    },
    "CA": {
        "regime_id": "ca_prop13_base_year",
        "math_owner": "engine",
        "harness_role": _HARNESS_ROLE,
        "classification": "acquisition_value_base_year",
        "purchase_price_treatment": "base_year_reset_on_change_of_ownership",
        "insurance": "separate_assumption_out_of_scope",
        "findings": (
            "California reassesses real property at full cash value only on purchase, new construction, or certain changes in ownership (Cal. Const. art. XIII A § 2(a)); an acquisition establishes a new base year value which then grows annually by the lesser of CPI or 2 percent (§ 2(b)).",
            "The constitutional rate cap is 1 percent of full cash value (art. XIII A § 1(a)); voter-approved bonded indebtedness and specified school levies are added outside the cap, so a complete bill is 1 percent plus local voter-approved additions — not a single universal rate.",
            "Entity transfers are not changes in ownership except on more-than-50-percent control changes or cumulative original-co-owner transfers of previously excluded interests (R&TC § 64); Property Tax Rule 462.180 applies this directly to corporate stock and partnership/LLC capital-and-profits interests — the standard multifamily acquisition structure.",
            "A change in ownership between Jan 1 and May 31 yields two supplemental assessments (current roll and roll-being-prepared differences); Jun 1-Dec 31 yields one (R&TC § 75.11). Supplemental taxes are prorated from the first day of the month following the event using statutory factors (§ 75.41).",
            "Intergenerational (parent-child) exclusions were narrowed by Proposition 19 for transfers on/after Feb 16, 2021: family homes/farms only, capped at factored base year value plus an inflation-adjusted $1,000,000. Never relevant to market-rate multifamily acquisitions, and never an analyst-usable exclusion without parcel-level review.",
        ),
        "mandatory_questions": {
            "assessment_mechanics": {
                "finding": "SUPPORTED: acquisition-value system — new base year value at full cash value on purchase/change in ownership; annual adjustment thereafter (art. XIII A § 2).",
                "citations": ("ca_xiiia_2",),
            },
            "purchase_price_and_ownership_events": {
                "finding": "SUPPORTED: purchase resets the base year value to full cash value; entity-interest transfers trigger reassessment only through § 64(c) control changes or § 64(d) original-co-owner cumulative transfers.",
                "citations": ("ca_xiiia_2", "ca_rtc_64", "ca_rule_462.180"),
            },
            "annual_adjustment_limits": {
                "finding": "SUPPORTED: base year value adjusts annually by the CPI inflation factor, not to exceed 2 percent (art. XIII A § 2(b)); BOE publishes the factor each year, so the exact factor for a given tax year is parcel-input data the analyst supplies, not a hardcoded constant.",
                "citations": ("ca_xiiia_2", "ca_boe_supplemental"),
            },
            "levy_structure_and_units": {
                "finding": "SUPPORTED: 1 percent constitutional maximum plus voter-approved bonded indebtedness and specified school levies; local debt/assessment additions are parcel-specific input, never hardcoded into a universal rate.",
                "citations": ("ca_xiiia_1",),
            },
            "exemptions_and_exclusions": {
                "finding": "SUPPORTED as recorded: Prop 19 narrowed intergenerational exclusions (family home/farm only, factored-base-year-plus-$1,000,000-adjusted cap, operative Feb 16, 2021); all other exclusions (§ 62 transfers, charitable, etc.) require parcel-level review and are not assumed.",
                "citations": ("ca_boe_prop19", "ca_rtc_64"),
            },
            "timing_and_remedies": {
                "finding": "SUPPORTED: supplemental assessments and monthly proration factors are statutory (R&TC §§ 75.11, 75.41); escape/appeal timing is procedural and recorded as timing input, not a value claim.",
                "citations": ("ca_rtc_75.11", "ca_rtc_75.41", "ca_boe_supplemental"),
            },
            "new_construction": {
                "finding": "SUPPORTED: new construction adds a separate base year value for the constructed portion at completion; the remainder keeps its existing base year value.",
                "citations": ("ca_xiiia_2", "ca_rtc_75.11"),
            },
            "asset_class_applicability": {
                "finding": "SUPPORTED: Prop 13 applies to all real property including multi-unit residential (BOE Pub 800-10 lists 'multi-unit residential building'); no multifamily carve-out exists.",
                "citations": ("ca_xiiia_2", "ca_boe_supplemental"),
            },
        },
    },
    "FL": {
        "regime_id": "fl_nonhomestead_10pct_cap",
        "math_owner": "engine",
        "harness_role": _HARNESS_ROLE,
        "classification": "nonhomestead_residential_10plus",
        "purchase_price_treatment": "just_value_reset_on_change_of_ownership_or_qualifying_improvement",
        "insurance": "separate_assumption_out_of_scope",
        "findings": (
            "Florida assesses nonhomestead real property at just value, then caps annual assessment increases at 10 percent of the prior year's assessed value for all levies other than school district levies (Fla. Const. art. VII § 4(g),(h); permanent since Amendment 2, approved Nov 2018).",
            "Multifamily with 10 or more units is governed by § 193.1555 (certain residential and nonresidential real property); the § 193.1554 nine-or-fewer-unit statute does NOT apply. A regime model that cites only 193.1554 misroutes every garden apartment community with 10+ units.",
            "A change of ownership or control — including a cumulative transfer of more than 50 percent of the owning legal entity — resets assessment to just value (§ 193.1555(5)); a qualifying improvement (value increase of at least 25 percent) does as well.",
            "Millage is levied per $1,000 of taxable value under ch. 200 TRIM machinery (Fla. Stat. § 200.065); school district levies apply to just value outside the cap, so a correct first-year model needs separate school and non-school bases.",
            "Insurance economics (including windstorm) is a separate assumption with its own approval path; it is NOT part of tax law and must never be merged into a tax regime.",
        ),
        "mandatory_questions": {
            "assessment_mechanics": {
                "finding": "SUPPORTED: Jan-1 just-value assessment subject to the 10 percent nonhomestead cap for non-school levies; school levies assess on just value with no cap.",
                "citations": ("fl_const_4g", "fl_const_4h", "fl_193.1555"),
            },
            "purchase_price_and_ownership_events": {
                "finding": "SUPPORTED: change of ownership or control (sale, foreclosure, legal/beneficial title transfer, or cumulative >50 percent transfer of the owning legal entity) resets to just value as of the next Jan 1.",
                "citations": ("fl_193.1555", "fl_const_4g"),
            },
            "annual_adjustment_limits": {
                "finding": "SUPPORTED: 10 percent annual cap on assessment changes for all levies other than school district levies; no assessment may exceed just value; cap is permanent (2018 Amendment 2).",
                "citations": ("fl_const_4h", "fl_193.1555", "fl_dor_amendment2_2018"),
            },
            "levy_structure_and_units": {
                "finding": "SUPPORTED: millage per $1,000 of taxable value per taxing authority under § 200.065; county/school/municipal/special-district rates are parcel-input data.",
                "citations": ("fl_200.065",),
            },
            "exemptions_and_exclusions": {
                "finding": "SUPPORTED as out-of-scope: homestead exemptions (§ 196.031) exclude property from this cap regime; agricultural/high-water-recharge classified uses are outside § 4(h). Nonhomestead multifamily has no exemption assumed.",
                "citations": ("fl_193.1554", "fl_const_4h"),
            },
            "timing_and_remedies": {
                "finding": "SUPPORTED as procedure: TRIM notices and two-hearing adoption calendar (§ 200.065) are recorded as procedure context; VAB appeal timing is local procedure, not a value rule.",
                "citations": ("fl_200.065",),
            },
            "new_construction": {
                "finding": "SUPPORTED: changes, additions, or improvements are assessed at just value as of the first Jan 1 after substantial completion; a qualifying improvement (>= 25 percent value increase) is a cap reset event.",
                "citations": ("fl_193.1555",),
            },
            "asset_class_applicability": {
                "finding": "SUPPORTED: 10+ unit multifamily is 'certain residential' property under § 193.1555 (DOR use code 03); 9-or-fewer units fall under § 193.1554 — the unit count is a mandatory applicability input.",
                "citations": ("fl_193.1555", "fl_193.1554"),
            },
        },
    },
    "AL": {
        "regime_id": "al_class_ii_millage",
        "math_owner": "engine",
        "harness_role": _HARNESS_ROLE,
        "classification": "classified_property_class_ii",
        "purchase_price_treatment": "market_value_appraisal_no_statutory_reset",
        "insurance": "separate_assumption_out_of_scope",
        "findings": (
            "Alabama classifies all taxable property into four constitutional classes; multifamily real property is Class II ('all property not otherwise classified') assessed at 20 percent of fair and reasonable market value (Ala. Code § 40-8-1(a)).",
            "The state levies 6.5 mills (3 public school, 1 soldier relief, 2.5 general fund); county commissions, municipalities and school boards levy additional jurisdiction-specific millages — the ADOR publishes per-county schedules (e.g., 'October 2025 Millage Rates'). Rates are inputs, never universal defaults.",
            "One mill is one-tenth of one cent — $1 per $1,000 of assessed value; the tax computation (assessed value × total mills × 0.001) belongs to the engine. Appraised value is fair and reasonable market value (annual reappraisal program), with Class III current-use only on owner application.",
            "There is no unexplained universal fallback: a jurisdiction outside the researched county millage schedule blocks the calculation rather than borrowing another county's rates.",
        ),
        "mandatory_questions": {
            "assessment_mechanics": {
                "finding": "SUPPORTED: fair and reasonable market value appraisal under the annual reappraisal program, classified by constitutional class.",
                "citations": ("al_40-8-1", "al_ador_property"),
            },
            "purchase_price_and_ownership_events": {
                "finding": "SUPPORTED: no statutory purchase-price reset; sales are market-value evidence within annual reappraisal, and ownership changes update the taxpayer of record.",
                "citations": ("al_ador_property", "al_ador_millage"),
            },
            "annual_adjustment_limits": {
                "finding": "SUPPORTED: no constitutional assessment-increase cap; values move with the annual reappraisal/equalization cycle (quarter of the county reviewed each year).",
                "citations": ("al_ador_millage",),
            },
            "levy_structure_and_units": {
                "finding": "SUPPORTED: state 6.5 mills plus county/municipal/school millages per the published county schedule; mills are per $1,000 of assessed value. Tax year begins October 1; taxes due October 1, delinquent January 1.",
                "citations": ("al_ador_property", "al_ador_millage", "al_2025_millage"),
            },
            "exemptions_and_exclusions": {
                "finding": "SUPPORTED as out-of-scope: current-use valuation applies to Class III on owner application only; Class II multifamily has no assumed exemption. All exemptions require parcel evidence.",
                "citations": ("al_40-8-1", "al_ador_property"),
            },
            "timing_and_remedies": {
                "finding": "SUPPORTED as procedure: taxpayers may appear before the county board of equalization concerning valuation; taxes due Oct 1, delinquent Jan 1, probate-court collection thereafter.",
                "citations": ("al_ador_property", "al_ador_millage"),
            },
            "new_construction": {
                "finding": "SUPPORTED: new construction enters appraised value through the annual reappraisal/assessment program as discovered improvements; no separate base-year regime exists.",
                "citations": ("al_ador_millage",),
            },
            "asset_class_applicability": {
                "finding": "SUPPORTED: multifamily apartments are Class II at the 20 percent assessment ratio; the class and ratio are mandatory applicability inputs per parcel.",
                "citations": ("al_40-8-1",),
            },
        },
    },
}


def load_regime_registry() -> dict:
    """Return the four mandatory researched regimes.

    The registry is a deep copy: callers cannot mutate shared research
    state at any nesting depth (findings, citations, mandatory questions).
    An unknown regime key refuses (KeyError is never used) — there is no
    "Standard" fallback jurisdiction in this product.
    """
    class _Registry(dict):
        def __missing__(self, key):  # pragma: no cover - exercised via tests
            raise HarnessError(UNSUPPORTED_REGIME, _NO_FALLBACK, details={"regime": key})

    registry = _Registry()
    for code in MANDATORY_REGIMES:
        regime = deepcopy({key: (tuple(value) if isinstance(value, list) else value)
                           for key, value in _REGIMES[code].items()})
        regime["citations"] = deepcopy(_CITATIONS[code])
        regime["math_owner"] = "engine"
        registry[code] = regime
    return registry


def validate_research(rule_families: dict | None = None) -> dict:
    """Validate the research contract before any statutory rule ships.

    ``rule_families`` optionally scopes the validation to specific
    ``{regime: {family}}`` selections; each family must be one of the
    researched, supported families. A family with a recorded open
    applicability question blocks with UNCERTAINTY_BLOCK instead of
    validating. The report never grants production statutory approval —
    that requires competent human review outside this module.
    """
    if rule_families is None:
        scoped = {
            code: set(SUPPORTED_RULE_FAMILIES[code]) - _UNCERTAIN_FAMILIES.get(code, frozenset())
            for code in MANDATORY_REGIMES
        }
    else:
        for regime, families in rule_families.items():
            if regime not in SUPPORTED_RULE_FAMILIES:
                raise HarnessError(
                    UNSUPPORTED_REGIME,
                    _NO_FALLBACK,
                    details={"regime": regime},
                )
            unknown = set(families) - set(SUPPORTED_RULE_FAMILIES[regime])
            if unknown:
                raise HarnessError(
                    UNSUPPORTED_RULE_FAMILY,
                    "Refused: these rule families have not been researched for this regime.",
                    details={"regime": regime, "families": sorted(unknown)},
                )
        scoped = {code: set(fams) for code, fams in rule_families.items()}

    blockers = []
    regime_status = {}
    for code, families in sorted(scoped.items()):
        uncertain = {f for f in families if f in _UNCERTAIN_FAMILIES.get(code, frozenset())}
        citations_ok = bool(_CITATIONS[code])
        if uncertain:
            blockers.extend(
                {
                    "code": UNCERTAINTY_BLOCK,
                    "regime": code,
                    "family": family,
                    "message": "Uncertainty explicitly blocks the statutory claim: re-research at implementation time before implementing this family.",
                }
                for family in sorted(uncertain)
            )
        regime_status[code] = {
            "families": sorted(families),
            "citations_present": citations_ok,
            "production_statutory_approval": "requires_competent_human_review",
        }

    status = "blocked" if blockers else "validated"
    return {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "regimes": sorted(scoped),
        "validation_requirements": list(VALIDATION_REQUIREMENTS),
        "regime_status": regime_status,
        "blockers": blockers,
        "engine_owns_math": _ENGINE_OWNS_MATH,
    }


def citation_index() -> dict:
    """Return a copy of the public citation index for audit/reporting."""
    return {cid: dict(c) for cid, c in CITATION_INDEX.items()}