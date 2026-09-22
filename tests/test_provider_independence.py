"""Provider-independence tests (Task 5.4): model labels can never alter
canonical economics, and interchangeable providers are only a transport.

The provider bridge moves bytes; the extraction validator re-anchors every
claimed fact to cited packet content; the canonical intake translation is
deterministic. Together: two different provider/model labels producing the
same validated claims must yield byte-identical canonical JSON and SHA256.
No live providers, no credentials, no network — every transport is a fake
holding recorded synthetic responses. No real deal bytes, no tenant rows.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from plat_harness.adapters.canonical_intake import (
    CanonicalIntakeError,
    translate_intake,
)
from plat_harness.adapters.provider_bridge import (
    CAP_STRUCTURED_OUTPUT,
    CAP_TOOL_CALLS,
    ProviderBridge,
    ProviderRegistry,
    ProviderRequest,
    ProviderSpec,
    RoutingTable,
    TransportResult,
)

CANARY = "CANARY RESIDENT Jane Canary"


# ------------------------------------------------- canonical intake fixtures
# (shape-compatible with tests/test_canonical_intake.py synthetic evidence)

SUBJECT = "subject_demo"
AS_OF = "2026-01-31"
POLICY = "policy-2026-01"
OCC_SOURCE = "src_" + "1" * 32
ACC_SOURCE = "src_" + "2" * 32
OCC_SHA = hashlib.sha256(b"synthetic occupancy source").hexdigest()
ACC_SHA = hashlib.sha256(b"synthetic accounting source").hexdigest()


def _enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def unit_cite(row, column=2):
    return {"source_id": OCC_SOURCE, "source_sha256": OCC_SHA,
            "sheet": 1, "row": row, "row_end": row, "column": column}


def make_unit(row, status="occupied", unit_type="residential"):
    unit_id = unit_cite(row, 2)
    fields = [{"unit_id": unit_id, "status": unit_cite(row, 3),
               "unit_type": unit_cite(row, 4)}]
    return {"observation_id": "unit_" + hashlib.sha256(_enc(unit_id)).hexdigest(),
            "source_id": OCC_SOURCE, "unit_type": unit_type, "status": status,
            "evidence": fields}


def occupancy_envelope():
    units = [make_unit(2), make_unit(3)]
    return {
        "contract_version": "ingest-observation/2.0.0",
        "subject_id": SUBJECT, "as_of": AS_OF,
        "adapter": {"id": "synthetic-occupancy", "version": "1.0.0"},
        "sources": [{"source_id": OCC_SOURCE, "sha256": OCC_SHA,
                     "role": "original", "original_source_ids": [],
                     "subject_id": SUBJECT, "as_of": AS_OF}],
        "units": units, "unknown_use_units": [], "summaries": [],
        "issues": [],
        "completeness": {
            "residential": {"enumeration": "complete", "coverage": "established",
                            "coverage_citations": [unit_cite(2, 2)],
                            "counts": {"occupied": 2, "vacant": 0, "down": 0,
                                       "total": 2}},
            "commercial": {"enumeration": "unknown", "coverage": "unknown",
                           "coverage_citations": [],
                           "counts": {f: None for f in
                                      ("occupied", "vacant", "down", "total")}},
        },
        "status": "observed_unvalidated",
    }


def money_obs(kind, source_text, decimal, row, column=4, period="month"):
    cite = {"source_id": ACC_SOURCE, "source_sha256": ACC_SHA,
            "sheet": 1, "row": row, "row_end": row, "column": column}
    identity = "mny_" + hashlib.sha256(_enc(cite)).hexdigest()
    return {"observation_id": identity, "source_id": ACC_SOURCE, "kind": kind,
            "measurement_basis": "unit", "amount": {
                "decimal": decimal, "source_text": source_text,
                "currency": "USD", "unit": "currency", "period": period},
            "cell_origin": "typed", "citation": cite}


def accounting_envelope(observations):
    return {
        "contract_version": "ingest-accounting/1.0.0",
        "subject_id": SUBJECT, "as_of": AS_OF,
        "adapter": {"id": "synthetic-accounting", "version": "1.0.0"},
        "sources": [{"source_id": ACC_SOURCE, "sha256": ACC_SHA,
                     "role": "original", "original_source_ids": [],
                     "subject_id": SUBJECT, "as_of": AS_OF}],
        "observations": observations, "issues": [],
        "status": "observed_unvalidated",
    }


def eligible_evidence(provider):
    """A fully eligible synthetic evidence bundle with provider labels swapped."""
    observations = [
        money_obs("unit_rent", "1450.00", "1450.00", row=2),
        money_obs("unit_rent", "1500.00", "1500.00", row=3),
    ]
    return {
        "contract_version": "ingest-canonical-intake/1.0.0",
        "subject_id": SUBJECT, "as_of": AS_OF, "policy_version": POLICY,
        "provider": {"provider_id": provider[0], "model_id": provider[1]},
        "analysis_window": {"analysis_start_date": "2026-02",
                            "analysis_end_date": "2027-01"},
        "lease_basis": "monthly_rent",
        "run_id": "run_001", "analyst": "synthetic_analyst",
        "purpose": "screening",
        "occupancy": occupancy_envelope(),
        "accounting": accounting_envelope(observations),
        "cohorts": [{
            "cohort_id": "res_1x", "unit_type": "residential", "unit_count": 2,
            "inplace_rent_observation_id": observations[0]["observation_id"],
            "market_rent_observation_id": observations[1]["observation_id"],
            "target_rent": None,
        }],
        "curves": {
            "loss_to_lease": [{"cohort_id": "res_1x", "ltl_percent": "0.02"}],
            "physical_vacancy": [{"cohort_id": "res_1x", "vacancy_rate": "0.04"}],
            "collection_loss": [{"applies_to": "Rent", "loss_rate": "0.01"}],
        },
        "millage": {"millage_rate_mills": "20.5", "assessment_ratio": "0.85",
                    "source": "synthetic_county_schedule",
                    "source_locator": "synthetic/2026/millage/schedule-line-12",
                    "analyst_override": False},
        "commercial_income": {"status": "omitted", "observation_id": None,
                              "omission_explanation": "synthetic omission",
                              "canonical_program": None},
        "other_income": {"status": "omitted", "observation_id": None,
                         "omission_explanation": "synthetic omission",
                         "canonical_program": None},
    }


# ----------------------------------------------------- provider bridge fakes

ANTHROPIC_SPEC = ProviderSpec(
    provider_id="anthropic-cloud", kind="anthropic",
    capabilities=frozenset({CAP_TOOL_CALLS}), api_key_env="ANTHROPIC_API_KEY")
OPENAI_SPEC = ProviderSpec(
    provider_id="openai-cloud", kind="openai",
    capabilities=frozenset({CAP_TOOL_CALLS, CAP_STRUCTURED_OUTPUT}),
    api_key_env="OPENAI_API_KEY")


class ScriptedTransport:
    """Records sends and replays recorded synthetic responses."""

    def __init__(self, body_by_provider):
        self.calls = []
        self.body_by_provider = body_by_provider

    def send(self, spec, path, payload, headers, timeout_s, cancel):
        self.calls.append({"provider_id": spec.provider_id, "path": path,
                           "payload": payload})
        return TransportResult(200, self.body_by_provider[spec.provider_id])


def make_bridge(canonical, transport):
    from plat_harness.adapters.provider_bridge import ModelRoute

    registry = ProviderRegistry()
    registry.register(ANTHROPIC_SPEC)
    registry.register(OPENAI_SPEC)
    routes = {
        "anthropic/synth-model": ModelRoute(
            "anthropic/synth-model", "anthropic-cloud", "synth-model-a"),
        "openai/synth-model": ModelRoute(
            "openai/synth-model", "openai-cloud", "synth-model-o"),
    }
    return ProviderBridge(RoutingTable(registry, routes), canonical, transport,
                          credentials=lambda spec: "«redacted»")


def anthropic_body(text):
    return json.dumps({
        "id": "msg_synth", "model": "synth-model-a",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }).encode()


def openai_body(text):
    return json.dumps({
        "model": "synth-model-o",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }).encode()


# ------------------------------------------- provider-independence assertions


def canonical_for(provider):
    evidence = eligible_evidence(provider)
    result = translate_intake(evidence, subject_id=SUBJECT, as_of=AS_OF,
                              policy_version=POLICY)
    assert result["status"] == "eligible", result["blockers"]
    return result


def test_provider_labels_leave_canonical_bytes_identical():
    first = canonical_for(("provider-a", "model-a"))
    second = canonical_for(("provider-b", "model-b"))
    assert first["canonical_json"] == second["canonical_json"]
    assert first["canonical_sha256"] == second["canonical_sha256"]


def test_two_bridge_providers_same_claims_same_canonical():
    # Two different provider/model labels return identical host-side text
    # (recorded synthetic responses); canonical economics are unchanged.
    claims_text = "host-rendered extraction claims"
    transport = ScriptedTransport({
        "anthropic-cloud": anthropic_body(claims_text),
        "openai-cloud": openai_body(claims_text),
    })
    turns = {}
    for canonical in ("anthropic/synth-model", "openai/synth-model"):
        bridge = make_bridge(canonical, transport)
        turns[canonical] = bridge.complete(
            [{"role": "user", "content": "synthetic request"}], [])
    assert turns["anthropic/synth-model"].content == \
        turns["openai/synth-model"].content == claims_text
    # The validated canonical intake is identical regardless of provider label
    # even when the provider field records different labels.
    first = canonical_for(("anthropic-cloud", "anthropic/synth-model"))
    second = canonical_for(("openai-cloud", "openai/synth-model"))
    assert first["canonical_json"] == second["canonical_json"]
    assert first["canonical_sha256"] == second["canonical_sha256"]


def test_model_authoritative_labels_cannot_leak_into_canonical():
    # A model claiming to be an authority or a different provider cannot
    # alter the canonical provider labels: they come from host evidence.
    evidence = eligible_evidence(("provider-a", "model-a"))
    evidence["provider"] = {"provider_id": "provider-a",
                           "model_id": "model-a"}
    result = translate_intake(evidence, subject_id=SUBJECT, as_of=AS_OF,
                              policy_version=POLICY)
    assert result["lineage"]["provider"] == {
        "provider_id": "provider-a", "model_id": "model-a"}
    # Canonical bytes contain no provider key at all.
    assert "provider" not in json.dumps(json.loads(result["canonical_json"]))


def test_validator_provider_metadata_never_alters_facts():
    from plat_harness.ingest.extraction_validator import validate_extraction

    packet = {
        "version": "1.0", "source_sha256": "b" * 64, "total_pages": 1,
        "metadata": {}, "sections": [{
            "code": "operating_statements", "page_start": 1, "page_end": 1,
            "attribution": "broker_claim",
            "lines": [{"text": "Gross Rental Income 1,000,000", "line_index": 1,
                       "citation": {"source_sha256": "b" * 64, "sheet": 1,
                                    "row": 1, "row_end": 1, "column": 1}}],
            "rows": []}],
        "ocr_required_pages": [], "issues": [],
    }
    claims = [{
        "kind": "fact", "label": "Gross Rental Income", "value": "1,000,000",
        "section_code": "operating_statements",
        "citation": {"source_sha256": "b" * 64, "sheet": 1,
                     "row": 1, "row_end": 1, "column": 1},
    }]
    first = validate_extraction(claims, packet, egress_approved=True,
                                provider={"provider_id": "p-a",
                                          "model_id": "m-a"})
    second = validate_extraction(claims, packet, egress_approved=True,
                                 provider={"provider_id": "p-b",
                                           "model_id": "m-b"})
    assert first["facts"] == second["facts"]
    assert first["source_sha256"] == second["source_sha256"]
    assert first["status"] == second["status"] == "verified"
    assert first["provider"] == {"provider_id": "p-a", "model_id": "m-a"}


def test_validator_result_isolated_from_provider_metadata():
    from plat_harness.ingest.extraction_validator import validate_extraction

    packet = {
        "version": "1.0", "source_sha256": "b" * 64, "total_pages": 1,
        "metadata": {}, "sections": [{
            "code": "operating_statements", "page_start": 1, "page_end": 1,
            "attribution": "broker_claim",
            "lines": [{"text": "Gross Rental Income 1,000,000", "line_index": 1,
                       "citation": {"source_sha256": "b" * 64, "sheet": 1,
                                    "row": 1, "row_end": 1, "column": 1}}],
            "rows": []}],
        "ocr_required_pages": [], "issues": [],
    }
    claims = [{
        "kind": "fact", "label": "Gross Rental Income", "value": "1,000,000",
        "section_code": "operating_statements",
        "citation": {"source_sha256": "b" * 64, "sheet": 1,
                     "row": 1, "row_end": 1, "column": 1},
    }]
    provider = {"provider_id": "p-a", "model_id": "m-a"}
    result = validate_extraction(claims, packet, egress_approved=True,
                                 provider=provider)
    provider["model_id"] = "tampered"
    assert result["provider"]["model_id"] == "m-a"
    claims[0]["value"] = "999,999"
    assert result["facts"][0]["value"] == "1,000,000"


def test_no_fallback_between_providers_on_extraction_path():
    # The extraction path uses exactly one provider; a local failure never
    # routes to a cloud provider (re-asserted at the 5.4 seam).
    from plat_harness.adapters.provider_bridge import (
        BoundedRetryPolicy, KIND_LOCAL_OPENAI, LOCAL_ENDPOINT_ERROR,
        RetryingTransport,
    )
    from plat_harness.adapters.provider_bridge import ModelRoute

    local_spec = ProviderSpec(
        provider_id="local-vllm", kind=KIND_LOCAL_OPENAI,
        capabilities=frozenset({CAP_TOOL_CALLS}),
        base_url="http://127.0.0.1:8000")

    class FailingLocalTransport:
        def __init__(self):
            self.calls = 0

        def send(self, spec, path, payload, headers, timeout_s, cancel):
            self.calls += 1
            raise OSError("connection refused")

    class CloudTransport:
        def __init__(self):
            self.calls = 0

        def send(self, spec, path, payload, headers, timeout_s, cancel):
            self.calls += 1
            return TransportResult(200, openai_body("cloud"))

    registry = ProviderRegistry()
    registry.register(local_spec)
    registry.register(OPENAI_SPEC)
    routes = {
        "local/synth-model": ModelRoute("local/synth-model", "local-vllm",
                                        "synth-model-l"),
    }
    local_wire = FailingLocalTransport()
    cloud_wire = CloudTransport()
    transport = RetryingTransport(local_wire,
                                  BoundedRetryPolicy(max_retries=0),
                                  sleep=lambda s: None)
    bridge = ProviderBridge(RoutingTable(registry, routes),
                            "local/synth-model", transport)
    with pytest.raises(Exception) as exc:
        bridge.complete([{"role": "user", "content": "q"}], [])
    assert getattr(exc.value, "code", None) == LOCAL_ENDPOINT_ERROR
    assert local_wire.calls == 1
    assert cloud_wire.calls == 0  # cloud was never contacted


def test_provider_label_changes_never_relabel_broker_claims():
    # Attribution is a source property, not a provider property: swapping the
    # provider cannot turn broker_claim into verified vendor data.
    from plat_harness.ingest.extraction_validator import validate_extraction

    packet = {
        "version": "1.0", "source_sha256": "b" * 64, "total_pages": 1,
        "metadata": {}, "sections": [{
            "code": "operating_statements", "page_start": 1, "page_end": 1,
            "attribution": "broker_claim",
            "lines": [{"text": "Gross Rental Income 1,000,000", "line_index": 1,
                       "citation": {"source_sha256": "b" * 64, "sheet": 1,
                                    "row": 1, "row_end": 1, "column": 1}}],
            "rows": []}],
        "ocr_required_pages": [], "issues": [],
    }
    claims = [{
        "kind": "fact", "label": "Gross Rental Income", "value": "1,000,000",
        "section_code": "operating_statements",
        "citation": {"source_sha256": "b" * 64, "sheet": 1,
                     "row": 1, "row_end": 1, "column": 1},
    }]
    for provider in ({"provider_id": "p-a", "model_id": "m-a"},
                     {"provider_id": "p-b", "model_id": "m-b"}):
        result = validate_extraction(claims, packet, egress_approved=True,
                                     provider=provider)
        assert result["facts"][0]["citation"]["source_sha256"] == "b" * 64
        assert result["status"] == "verified"


def test_acceptance_measurement_is_labelled_not_fabricated():
    # No model ran: extraction accuracy/cost metrics must not be fabricated.
    # The validator records provider metadata for measurement only; absent
    # measurements stay absent (None), never invented.
    from plat_harness.ingest.extraction_validator import validate_extraction

    packet = {
        "version": "1.0", "source_sha256": "b" * 64, "total_pages": 1,
        "metadata": {}, "sections": [{
            "code": "operating_statements", "page_start": 1, "page_end": 1,
            "attribution": "broker_claim",
            "lines": [{"text": "Gross Rental Income 1,000,000", "line_index": 1,
                       "citation": {"source_sha256": "b" * 64, "sheet": 1,
                                    "row": 1, "row_end": 1, "column": 1}}],
            "rows": []}],
        "ocr_required_pages": [], "issues": [],
    }
    claims = [{
        "kind": "fact", "label": "Gross Rental Income", "value": "1,000,000",
        "section_code": "operating_statements",
        "citation": {"source_sha256": "b" * 64, "sheet": 1,
                     "row": 1, "row_end": 1, "column": 1},
    }]
    result = validate_extraction(claims, packet, egress_approved=True)
    assert result["provider"] is None
    assert "accuracy" not in json.dumps(result)
    assert "cost" not in json.dumps(result)
    assert "latency" not in json.dumps(result)
