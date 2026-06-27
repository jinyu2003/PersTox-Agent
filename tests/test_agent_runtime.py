#!/usr/bin/env python3
"""Smoke tests for the migrated PersAgent multi-agent runtime."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from pertox_agent.graph import build_graph
from pertox_agent.agents.toxicity_orchestrator_agent import ToxicityOrchestratorAgent
from pertox_agent.tools.clinical_input.drug_parser import drug_info_kwargs_from_input, normalize_drug_input
from pertox_agent.agents.knowledge_retrieval_agent import KnowledgeRetrievalAgent
from pertox_agent.schemas import DrugInfo, EvidencePackage, PatientInfo
from pertox_agent.tools.runtime import retrieval_runtime as kb


def test_graph_builds() -> None:
    graph = build_graph()
    assert hasattr(graph, "invoke")


def test_adapter_resolves_warfarin() -> None:
    result = kb.drug_card_lookup(drug_name="warfarin")
    assert result["drug_id"] == "DB00682"
    assert result["canonical_name"].lower() == "warfarin"


def test_cpic_lookup_deduplicates_gene_genotype() -> None:
    result = kb.cpic_lookup(drug_name="warfarin", genotypes={"CYP2C9": "*2/*3"})
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["gene"] == "CYP2C9"


def test_stage1_uses_single_admet_entrypoint() -> None:
    agent = KnowledgeRetrievalAgent()
    plan = agent._plan_tools("universal_toxicity")
    assert "admetsar_predict" in plan
    assert "structure_profile_lookup" not in plan
    assert not hasattr(kb, "structure_profile_lookup")


def test_stage1_uses_single_persade_entrypoint() -> None:
    agent = KnowledgeRetrievalAgent()
    plan = agent._plan_tools("universal_toxicity")
    assert "persade_drug_profile" in plan
    assert "faers_disproportionality" not in plan
    assert not hasattr(kb, "faers_disproportionality")


def test_tool_hard_exception_becomes_evidence_gap() -> None:
    original = kb.admetsar_predict

    def broken_tool(**_kwargs):
        raise RuntimeError("simulated hard tool failure")

    kb.admetsar_predict = broken_tool
    try:
        agent = KnowledgeRetrievalAgent()
        drug = DrugInfo(name="warfarin", drugbank_id="DB00682")
        patient = PatientInfo(patient_id="p-test", age=72)
        result = agent._call_tool(
            tool_name="admetsar_predict",
            drug_info=drug,
            patient_info=patient,
            patient_context={},
            drug_key="warfarin",
        )
        assert result["ok"] is False
        assert result["error_type"] == "RuntimeError"
        assert result["recoverable"] is True

        items = agent._build_evidence_items({"admetsar_predict": result})
        assert len(items) == 1
        assert items[0].tool_name == "admetsar_predict"
        assert items[0].evidence_level == "P5"

        conflicts = agent._detect_conflicts({"admetsar_predict": result}, patient, drug)
        assert any("admetsar_predict failed" in conflict for conflict in conflicts)
    finally:
        kb.admetsar_predict = original


def test_input_parser_uses_current_tool_resolver() -> None:
    normalized = normalize_drug_input({"drug": "warfarin"})
    assert normalized["drugbank_id"] == "DB00682"
    assert normalized["drug_name"].lower() == "warfarin"


def test_input_parser_reads_english_natural_language() -> None:
    raw = "Patient is taking warfarin 5 mg/day by oral route once daily."
    normalized = normalize_drug_input(raw)
    drug_info = drug_info_kwargs_from_input(raw)
    assert normalized["drugbank_id"] == "DB00682"
    assert normalized["drug_name"].lower() == "warfarin"
    assert drug_info["dose"] == "5 mg/day"
    assert drug_info["route"] == "oral"
    assert drug_info["frequency"] == "daily"


def test_input_parser_reads_chinese_natural_language() -> None:
    raw = "患者正在服用华法林，每日5mg，口服。"
    normalized = normalize_drug_input(raw)
    drug_info = drug_info_kwargs_from_input(raw)
    assert normalized["drugbank_id"] == "DB00682"
    assert normalized["drug_name"].lower() == "warfarin"
    assert drug_info["dose"] == "5 mg/day"
    assert drug_info["route"] == "oral"
    assert drug_info["frequency"] == "daily"


def test_input_parser_reads_fenced_json() -> None:
    raw = """```json
{"drug": {"name": "warfarin", "dose": "2.5 mg", "route": "oral", "frequency": "daily"}}
```"""
    normalized = normalize_drug_input(raw)
    drug_info = drug_info_kwargs_from_input(raw)
    assert normalized["drugbank_id"] == "DB00682"
    assert drug_info["dose"] == "2.5 mg"
    assert drug_info["route"] == "oral"


def test_toxicity_orchestrator_agent_uses_llm_drug_parser() -> None:
    def fake_extractor(kind, raw, schema):
        if kind != "drug":
            return None
        return {
            "name": "warfarin",
            "dose": "5 mg/day",
            "route": "oral",
            "frequency": "daily",
            "known_toxicities": ["bleeding"],
        }

    agent = ToxicityOrchestratorAgent(llm_json_extractor=fake_extractor)
    drug = agent.parse_drug_info("The user described an anticoagulant without JSON fields.")
    assert drug.name.lower() == "warfarin"
    assert drug.drugbank_id == "DB00682"
    assert drug.dose == "5 mg/day"
    assert drug.route == "oral"
    assert drug.frequency == "daily"
    assert drug.known_toxicities == ["bleeding"]


def test_toxicity_orchestrator_agent_uses_llm_patient_parser() -> None:
    def fake_extractor(kind, raw, schema):
        if kind != "patient":
            return None
        return {
            "patient_id": "llm-patient-001",
            "age": 72,
            "sex": "female",
            "egfr_ml_min": 45,
            "genotypes": {"CYP2C9": "*2/*3"},
            "medical_history": ["atrial fibrillation"],
            "concomitant_medications": ["amiodarone"],
        }

    agent = ToxicityOrchestratorAgent(llm_json_extractor=fake_extractor)
    patient = agent.parse_patient_info("72-year-old female with AF, eGFR 45, CYP2C9 *2/*3.")
    assert patient.patient_id == "llm-patient-001"
    assert patient.age == 72
    assert patient.sex == "female"
    assert patient.egfr_ml_min == 45
    assert patient.genotypes == {"CYP2C9": "*2/*3"}
    assert patient.concomitant_medications == ["amiodarone"]


def test_toxicity_orchestrator_agent_adds_stage1_attribution_explanation() -> None:
    captured_context = {}
    captured_narrative_context = {}

    def fake_extractor(kind, raw, schema):
        if kind == "attribution_narrative" and raw.get("soc") == "Hepatobiliary disorders":
            captured_narrative_context.update(raw)
            return {
                "attribution_narrative": (
                    "The liver row is mainly supported by the existing DILI attribution result, "
                    "with the retrieved endpoint treated as the probability-driving evidence."
                )
            }
        if kind != "molecular_attribution" or raw.get("soc") != "Hepatobiliary disorders":
            return None
        captured_context.update(raw)
        return {
            "attribution_explanation": "Liver attribution is driven by the retrieved DILI endpoint.",
            "molecular_attribution": [
                {
                    "driver_type": "admet_endpoint",
                    "driver": "label_DILI_t=positive",
                    "contribution_role": "probability_driver",
                    "mechanistic_role": "Retrieved ADMET endpoint increased the hepatobiliary baseline probability.",
                    "direction": "increase",
                    "confidence": 0.72,
                    "evidence_refs": [
                        {
                            "tool_name": "admetsar_predict",
                            "field": "tool_results.admetsar_predict.admet_profile",
                            "summary": "label_DILI_t was positive.",
                        }
                    ],
                    "limitations": "Model endpoint is not atom-level SHAP evidence.",
                }
            ],
            "attribution_limitations": ["No atom-level SHAP evidence."],
        }

    agent = ToxicityOrchestratorAgent(llm_json_extractor=fake_extractor)
    drug = DrugInfo(name="warfarin", drugbank_id="DB00682")
    patient = PatientInfo(patient_id="p-attribution", age=72)
    evidence = EvidencePackage(
        query_id="q-attribution",
        query_purpose="universal_toxicity",
        drug_id="DB00682",
        patient_id=patient.patient_id,
        tool_results={
            "drug_card_lookup": {
                "canonical_name": "warfarin",
                "drug_id": "DB00682",
                "structural_alerts": [],
                "mechanism_chain": "Drug card retrieved.",
            },
            "drugbank_metabolism_query": {
                "metabolism": {"primary_enzymes": ["CYP2C9"], "secondary_enzymes": []}
            },
            "admetsar_predict": {
                "admet_profile": [
                    {
                        "endpoint": "label_DILI_t",
                        "value": "positive",
                        "endpoint_type": "classification",
                        "soc": "Hepatobiliary disorders",
                    }
                ],
                "structure_profile": {
                    "descriptors": {"SlogP": 3.1},
                    "drug_likeness": {},
                    "structural_alerts": [],
                },
                "property_endpoints": [],
                "organ_impacts": [{"organ_system": "liver", "direction": "increase", "magnitude": 1.08}],
            },
            "dti_query": {"targets": []},
            "mechanism_query": {"mechanism_chain": "No mechanism chains."},
            "pathway_enrich": {"pathways": []},
            "persade_drug_profile": {"signals": [], "known_ade_profile": [], "organ_attributions": {}},
            "mechanism_chains_lookup": {"mechanism_chains": []},
        },
    )

    report = agent.build_universal_report(patient, drug, evidence)
    liver = next(item for item in report.general_toxicity if item.soc == "Hepatobiliary disorders")
    assert liver.attribution.attribution_explanation == "Liver attribution is driven by the retrieved DILI endpoint."
    assert liver.attribution.attribution_generation_method == "live_llm"
    assert liver.attribution.attribution_narrative.startswith("The liver row is mainly supported")
    assert liver.attribution.molecular_attribution[0]["driver"] == "label_DILI_t=positive"
    assert liver.attribution.molecular_attribution[0]["contribution_role"] == "probability_driver"
    assert "candidate_drivers" not in captured_context
    assert captured_context["tool_results"]["admetsar_predict"]["admet_profile"][0]["endpoint"] == "label_DILI_t"
    assert captured_narrative_context["molecular_attribution"][0]["driver"] == "label_DILI_t=positive"


def test_toxicity_orchestrator_agent_models_only_liver_and_heart() -> None:
    attribution_calls = []

    def fake_extractor(kind, raw, schema):
        if kind != "molecular_attribution":
            return None
        attribution_calls.append(raw.get("organ_system"))
        return {
            "attribution_explanation": f"{raw.get('organ_system')} attribution from modeled evidence.",
            "molecular_attribution": [
                {
                    "driver_type": "evidence_summary",
                    "driver": f"{raw.get('organ_system')} modeled evidence",
                    "contribution_role": "probability_driver",
                    "mechanistic_role": "Modeled evidence selected from the EvidencePackage.",
                    "direction": "increase",
                    "confidence": 0.5,
                    "evidence_refs": [
                        {
                            "tool_name": "admetsar_predict",
                            "field": "tool_results.admetsar_predict.admet_profile",
                            "summary": "Modeled endpoint evidence.",
                        }
                    ],
                    "limitations": "",
                }
            ],
            "attribution_limitations": [],
        }

    agent = ToxicityOrchestratorAgent(llm_json_extractor=fake_extractor)
    drug = DrugInfo(name="warfarin", drugbank_id="DB00682")
    patient = PatientInfo(patient_id="p-organ-scope", age=72)
    evidence = EvidencePackage(
        query_id="q-organ-scope",
        query_purpose="universal_toxicity",
        drug_id="DB00682",
        patient_id=patient.patient_id,
        tool_results={
            "drug_card_lookup": {
                "canonical_name": "warfarin",
                "drug_id": "DB00682",
                "structural_alerts": [],
                "mechanism_chain": "Drug card retrieved.",
            },
            "drugbank_metabolism_query": {"metabolism": {"primary_enzymes": ["CYP2C9"]}},
            "admetsar_predict": {
                "admet_profile": [
                    {
                        "endpoint": "label_DILI_t",
                        "value": "positive",
                        "endpoint_type": "classification",
                        "soc": "Hepatobiliary disorders",
                    },
                    {
                        "endpoint": "hERG",
                        "value": "positive",
                        "endpoint_type": "classification",
                        "soc": "Cardiac disorders",
                    },
                    {
                        "endpoint": "Nephrotoxicity",
                        "value": "positive",
                        "endpoint_type": "classification",
                        "soc": "Renal and urinary disorders",
                    },
                    {
                        "endpoint": "Bleeding",
                        "value": "positive",
                        "endpoint_type": "classification",
                        "soc": "Blood and lymphatic system disorders",
                    },
                ],
                "structure_profile": {"descriptors": {}, "drug_likeness": {}, "structural_alerts": []},
                "property_endpoints": [],
                "organ_impacts": [
                    {"organ_system": "liver", "direction": "increase", "magnitude": 1.08},
                    {"organ_system": "heart", "direction": "increase", "magnitude": 1.10},
                    {"organ_system": "kidney", "direction": "increase", "magnitude": 1.12},
                    {"organ_system": "hematologic", "direction": "increase", "magnitude": 1.20},
                ],
            },
            "dti_query": {"targets": []},
            "mechanism_query": {"mechanism_chain": "No mechanism chains."},
            "pathway_enrich": {"pathways": []},
            "persade_drug_profile": {"signals": [], "known_ade_profile": [], "organ_attributions": {}},
            "mechanism_chains_lookup": {"mechanism_chains": []},
        },
    )

    report = agent.build_universal_report(patient, drug, evidence)
    by_soc = {item.soc: item for item in report.general_toxicity}

    assert len(report.general_toxicity) == 8
    assert attribution_calls == ["liver", "heart"]
    assert by_soc["Hepatobiliary disorders"].baseline_probability is not None
    assert by_soc["Cardiac disorders"].baseline_probability is not None

    for soc in ("Renal and urinary disorders", "Blood and lymphatic system disorders"):
        item = by_soc[soc]
        assert item.baseline_probability is None
        assert item.attribution.attribution_explanation is None
        assert item.attribution.molecular_attribution == []
        assert item.evidence == []


def test_toxicity_orchestrator_agent_accepts_bare_llm_driver_attribution() -> None:
    def fake_extractor(kind, raw, schema):
        if kind == "attribution_narrative" and raw.get("soc") == "Hepatobiliary disorders":
            return {
                "attribution_narrative": (
                    "The hepatobiliary attribution is explained from the already selected direct DTA driver."
                )
            }
        if kind != "molecular_attribution" or raw.get("soc") != "Hepatobiliary disorders":
            return None
        return {
            "driver_type": "target_pathway",
            "driver": "Direct DTA support via CYP2C9",
            "contribution_role": "probability_driver",
            "mechanistic_role": "Direct target/ADE evidence raised the hepatobiliary baseline probability.",
            "direction": "increase",
            "confidence": 0.68,
            "evidence_refs": [
                {
                    "tool_name": "mechanism_chains_lookup",
                    "field": "tool_results.mechanism_chains_lookup.mechanism_chains",
                    "summary": "Direct DTA support was retrieved.",
                }
            ],
            "limitations": "",
        }

    agent = ToxicityOrchestratorAgent(llm_json_extractor=fake_extractor)
    drug = DrugInfo(name="warfarin", drugbank_id="DB00682")
    patient = PatientInfo(patient_id="p-bare-driver", age=72)
    evidence = EvidencePackage(
        query_id="q-bare-driver",
        query_purpose="universal_toxicity",
        drug_id="DB00682",
        patient_id=patient.patient_id,
        tool_results={
            "drug_card_lookup": {
                "canonical_name": "warfarin",
                "drug_id": "DB00682",
                "structural_alerts": [],
                "mechanism_chain": "Drug card retrieved.",
            },
            "drugbank_metabolism_query": {"metabolism": {"primary_enzymes": ["CYP2C9"]}},
            "admetsar_predict": {
                "admet_profile": [
                    {
                        "endpoint": "label_DILI_t",
                        "value": "positive",
                        "endpoint_type": "classification",
                        "soc": "Hepatobiliary disorders",
                    }
                ],
                "structure_profile": {"descriptors": {}, "drug_likeness": {}, "structural_alerts": []},
                "property_endpoints": [],
                "organ_impacts": [{"organ_system": "liver", "direction": "increase", "magnitude": 1.08}],
            },
            "dti_query": {"targets": []},
            "mechanism_query": {"mechanism_chain": "No mechanism chains."},
            "pathway_enrich": {"pathways": []},
            "persade_drug_profile": {"signals": [], "known_ade_profile": [], "organ_attributions": {}},
            "mechanism_chains_lookup": {"mechanism_chains": []},
        },
    )

    report = agent.build_universal_report(patient, drug, evidence)
    liver = next(item for item in report.general_toxicity if item.soc == "Hepatobiliary disorders")

    assert liver.attribution.attribution_generation_method == "live_llm"
    assert liver.attribution.attribution_narrative.startswith("The hepatobiliary attribution")
    assert liver.attribution.molecular_attribution[0]["driver"] == "Direct DTA support via CYP2C9"
    assert liver.attribution.molecular_attribution[0]["contribution_role"] == "probability_driver"
    assert "bare driver object" in liver.attribution.attribution_limitations[0]


def test_toxicity_orchestrator_agent_does_not_generate_local_narrative_without_llm() -> None:
    agent = ToxicityOrchestratorAgent(llm_json_extractor=lambda kind, raw, schema: None)
    drug = DrugInfo(name="warfarin", drugbank_id="DB00682")
    patient = PatientInfo(patient_id="p-no-narrative", age=72)
    evidence = EvidencePackage(
        query_id="q-no-narrative",
        query_purpose="universal_toxicity",
        drug_id="DB00682",
        patient_id=patient.patient_id,
        tool_results={
            "drug_card_lookup": {
                "canonical_name": "warfarin",
                "drug_id": "DB00682",
                "structural_alerts": [],
                "mechanism_chain": "Drug card retrieved.",
            },
            "drugbank_metabolism_query": {"metabolism": {"primary_enzymes": ["CYP2C9"]}},
            "admetsar_predict": {
                "admet_profile": [
                    {
                        "endpoint": "label_DILI_t",
                        "value": "positive",
                        "endpoint_type": "classification",
                        "soc": "Hepatobiliary disorders",
                    }
                ],
                "structure_profile": {"descriptors": {}, "drug_likeness": {}, "structural_alerts": []},
                "property_endpoints": [],
                "organ_impacts": [{"organ_system": "liver", "direction": "increase", "magnitude": 1.08}],
            },
            "dti_query": {"targets": []},
            "mechanism_query": {"mechanism_chain": "No mechanism chains."},
            "pathway_enrich": {"pathways": []},
            "persade_drug_profile": {"signals": [], "known_ade_profile": [], "organ_attributions": {}},
            "mechanism_chains_lookup": {"mechanism_chains": []},
        },
    )

    report = agent.build_universal_report(patient, drug, evidence)
    liver = next(item for item in report.general_toxicity if item.soc == "Hepatobiliary disorders")

    assert liver.attribution.attribution_generation_method == "deterministic_fallback"
    assert liver.attribution.attribution_narrative is None


def main() -> int:
    test_graph_builds()
    test_adapter_resolves_warfarin()
    test_cpic_lookup_deduplicates_gene_genotype()
    test_stage1_uses_single_admet_entrypoint()
    test_stage1_uses_single_persade_entrypoint()
    test_tool_hard_exception_becomes_evidence_gap()
    test_input_parser_uses_current_tool_resolver()
    test_input_parser_reads_english_natural_language()
    test_input_parser_reads_chinese_natural_language()
    test_input_parser_reads_fenced_json()
    test_toxicity_orchestrator_agent_uses_llm_drug_parser()
    test_toxicity_orchestrator_agent_uses_llm_patient_parser()
    test_toxicity_orchestrator_agent_adds_stage1_attribution_explanation()
    test_toxicity_orchestrator_agent_models_only_liver_and_heart()
    test_toxicity_orchestrator_agent_accepts_bare_llm_driver_attribution()
    test_toxicity_orchestrator_agent_does_not_generate_local_narrative_without_llm()
    print("agent runtime smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


