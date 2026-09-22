"""Synthetic tests for the evidence-backed tax-regime research registry (Task 4.1).

Self-contained public tests: no real parcels, bills, deal bytes, private asset
paths, tenant rows, models, or network. Every citation is a public
authoritative source; deal-specific evidence stays private. The module under
test performs NO tax arithmetic: it is a research registry and validation
gate. The engine (a sibling repository, out of scope here) owns all tax
mathematics; this module only records applicability questions, regime
findings, citations with retrieval metadata, and refuses unsupported
statutory claims.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError
from plat_harness.adapters import tax_regime_spec as trs

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "TAX_REGIME_SPEC.md"


class TestRegistryContract:
    """Structural facts about the research registry and its citation contract."""

    def test_contract_version_is_frozen(self):
        assert trs.CONTRACT_VERSION == "tax-regime-research/1.0.0"

    def test_mandatory_regimes_are_exactly_the_four_researched(self):
        assert set(trs.MANDATORY_REGIMES) == {"TX", "CA", "FL", "AL"}

    def test_registry_loads_all_four_regimes(self):
        registry = trs.load_regime_registry()
        assert set(registry.keys()) == set(trs.MANDATORY_REGIMES)

    def test_unknown_regime_refuses_rather_than_falling_back(self):
        # The old plan's "Standard" fallback regime is deliberately absent: a
        # jurisdiction without researched applicability must block, not
        # quietly reuse another state's mechanics.
        with pytest.raises(HarnessError) as excinfo:
            trs.load_regime_registry()["GA"]
        assert excinfo.value.code == trs.UNSUPPORTED_REGIME
        assert "no unexplained universal fallback" in excinfo.value.message

    def test_engine_owns_math_is_recorded_per_regime(self):
        for code, regime in trs.load_regime_registry().items():
            assert regime["math_owner"] == "engine", code

    def test_harness_role_is_select_and_validate_only(self):
        for code, regime in trs.load_regime_registry().items():
            assert regime["harness_role"] == "select_and_validate", code
            # A research registry must never ship formulas or rates.
            assert "formula" not in regime, code
            assert "rates" not in regime, code


class TestCitationContract:
    """Every implemented-rule citation carries full retrieval provenance."""

    def test_citation_fields_are_complete(self):
        registry = trs.load_regime_registry()
        for code, regime in registry.items():
            assert len(regime["citations"]) >= 4, code
            for citation in regime["citations"]:
                assert set(citation) == set(trs.CITATION_FIELDS), (code, citation)
                # Public authoritative citations only.
                assert citation["url"].startswith("https://"), citation["url"]
                # Statute sources are primary; guidance is clearly labelled.
                assert citation["authority_tier"] in trs.AUTHORITY_TIERS
                # Retrieval date recorded at implementation time.
                assert citation["retrieved"] == trs.RETRIEVAL_DATE
                # Section/page anchor required for auditability.
                assert citation["locator"], citation
                # Effective tax year is explicit, never assumed.
                assert citation["effective_tax_year"], citation

    def test_each_regime_cites_a_statute_tier_source(self):
        registry = trs.load_regime_registry()
        for code, regime in registry.items():
            tiers = {c["authority_tier"] for c in regime["citations"]}
            assert "statute" in tiers, code

    def test_florida_distinguishes_multifamily_from_nine_or_fewer_units(self):
        # FL multifamily (10+ units) is 193.1555, NOT 193.1554; a registry
        # that cites only the 9-or-fewer statute misroutes garden
        # apartments with 10+ units.
        fl = trs.load_regime_registry()["FL"]
        locators = {c["locator"] for c in fl["citations"]}
        assert any("193.1555" in loc for loc in locators)
        assert any("193.1554" in loc for loc in locators)
        assert fl["classification"] == "nonhomestead_residential_10plus"

    def test_texas_records_purchase_price_is_evidence_not_statutory_reset(self):
        # The prior plan asserted "Ch. 23 resets to 80-100% of purchase
        # price". No such rule exists; 23.01 appraises at market value as of
        # Jan 1 and the sale is evidence. The registry must record the
        # correction explicitly so downstream calculators cannot inherit it.
        tx = trs.load_regime_registry()["TX"]
        assert tx["purchase_price_treatment"] == "evidence_not_statutory_reset"
        assert any(
            "not a statutory purchase-price percentage" in f
            for f in tx["findings"]
        )

    def test_texas_circuit_breaker_records_sunset(self):
        tx = trs.load_regime_registry()["TX"]
        assert any(
            "23.231" in c["locator"] and "December 31, 2026" in c["note"]
            for c in tx["citations"]
        )

    def test_california_records_no_universal_rate(self):
        ca = trs.load_regime_registry()["CA"]
        assert ca["purchase_price_treatment"] == "base_year_reset_on_change_of_ownership"
        assert any(
            "not a single universal rate" in f for f in ca["findings"]
        )

    def test_alabama_records_class_ii_ratio_with_statute(self):
        al = trs.load_regime_registry()["AL"]
        assert any(
            "20 percent" in f and "Class II" in f for f in al["findings"]
        )
        assert any(
            c["locator"] and "40-8-1" in c["locator"] for c in al["citations"]
        )


class TestMandatoryQuestions:
    """The plan's mandatory applicability questions are all answered."""

    def test_mandatory_question_keys_are_the_planned_set(self):
        registry = trs.load_regime_registry()
        for code, regime in registry.items():
            assert set(regime["mandatory_questions"].keys()) == set(
                trs.MANDATORY_QUESTION_KEYS
            ), code

    def test_every_question_answered_with_finding_and_citations(self):
        registry = trs.load_regime_registry()
        for code, regime in registry.items():
            for key, answer in regime["mandatory_questions"].items():
                assert set(answer) == {"finding", "citations"}, (code, key)
                assert answer["finding"], (code, key)
                assert answer["citations"], (code, key)
                for citation_id in answer["citations"]:
                    assert citation_id in trs.CITATION_INDEX, (code, key)

    def test_insurance_economics_separate_from_tax_law(self):
        # The plan forbids merging insurance escalation into tax law.
        fl = trs.load_regime_registry()["FL"]
        assert fl["insurance"] == "separate_assumption_out_of_scope"

    def test_uncertainty_blocks_statutory_claims(self):
        registry = trs.load_regime_registry()
        for code, regime in registry.items():
            for key, answer in regime["mandatory_questions"].items():
                if not answer["finding"].startswith("SUPPORTED"):
                    assert answer["finding"].startswith("UNCERTAIN"), (
                        code,
                        key,
                    )


class TestValidationContract:
    """The validation gate for promoting research into implemented rules."""

    def test_validation_requirements_are_the_planned_four(self):
        assert set(trs.VALIDATION_REQUIREMENTS) == {
            "primary_source_support",
            "uncertainty_blocks_claims",
            "competent_human_review",
            "retrieval_date_recorded",
        }

    def test_validate_research_passes_on_registry(self):
        report = trs.validate_research()
        assert report["status"] == "validated"
        assert report["regimes"] == sorted(trs.MANDATORY_REGIMES)
        assert report["blockers"] == []

    def test_unknown_rule_family_refuses(self):
        with pytest.raises(HarnessError) as excinfo:
            trs.validate_research(rule_families={"TX": {"nonexistent_family"}})
        assert excinfo.value.code == trs.UNSUPPORTED_RULE_FAMILY

    def test_unsupported_regime_family_blocks_validation(self):
        # A regime with an open applicability question must not validate.
        report = trs.validate_research(rule_families={"TX": {"circuit_breaker"}})
        assert report["status"] == "blocked"
        assert report["blockers"], report
        assert all(
            b["code"] == trs.UNCERTAINTY_BLOCK for b in report["blockers"]
        )

    def test_human_review_required_before_statutory_approval(self):
        # No test can approve statutory policy; research alone is never
        # production approval. The gate must say so in every regime status.
        report = trs.validate_research()
        for status in report["regime_status"].values():
            assert status["production_statutory_approval"] == "requires_competent_human_review"

    def test_no_deal_specific_evidence_in_public_registry(self):
        # Deal-specific identifiers, paths and file names stay private; the
        # registry may speak of "parcel evidence" generically but must carry
        # no actual parcel/account identifiers, private paths or bill files.
        blob = json.dumps(trs.load_regime_registry())
        for canary in (
            "/private/data",
            "/private/plat",
            "parcel_number",
            "parcel_id",
            "account_number",
            "R12345",
            "1234-567",
            "tax_bill.pdf",
            "legal_description",
            "PRIVATE",
        ):
            assert canary not in blob, canary


class TestSpecDocument:
    """docs/TAX_REGIME_SPEC.md exists and carries the same contract."""

    def test_doc_exists(self):
        assert DOC_PATH.is_file()

    def test_doc_states_engine_owns_math(self):
        text = DOC_PATH.read_text()
        assert "engine" in text
        assert "tax mathematics belongs in the underwriting engine" in text

    def test_doc_records_retrieval_and_review_gates(self):
        text = DOC_PATH.read_text()
        assert trs.RETRIEVAL_DATE in text
        assert "competent human review" in text
        assert "uncertainty" in text and "blocks" in text

    def test_doc_covers_all_four_regimes_with_citations(self):
        text = DOC_PATH.read_text()
        for code in trs.MANDATORY_REGIMES:
            assert f"## {code} —" in text, code
        assert "statutes.capitol.texas.gov" in text
        assert "leginfo.legislature.ca.gov" in text
        assert "flsenate.gov" in text or "leg.state.fl.us" in text
        assert "revenue.alabama.gov" in text or "alison" in text

    def test_doc_corrects_the_old_plan_purchase_price_claim(self):
        text = DOC_PATH.read_text()
        assert "evidence, not a statutory purchase-price percentage" in text

    def test_doc_keeps_deal_specific_material_private(self):
        text = DOC_PATH.read_text()
        assert "/private/deals" not in text
        assert "no deal-specific parcels, bills, policies or legal review" in text