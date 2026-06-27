"""LangGraph node functions for the PersAgent workflow."""

from __future__ import annotations

from typing import Any, Dict

from pertox_agent.agents.knowledge_retrieval_agent import KnowledgeRetrievalAgent
from pertox_agent.agents.safety_verifier_agent import SafetyVerifierAgent
from pertox_agent.agents.toxicity_orchestrator_agent import ToxicityOrchestratorAgent
from pertox_agent.formatting import format_to_json
from pertox_agent.state import AgentState
from pertox_agent.tools.clinical_input.drug_parser import normalize_drug_input
from pertox_agent.tools.patient_context.standardizer import PatientProfileStandardizer


toxicity_orchestrator_agent = ToxicityOrchestratorAgent()
knowledge_agent = KnowledgeRetrievalAgent()
patient_standardizer = PatientProfileStandardizer()
safety_verifier_agent = SafetyVerifierAgent()


def _trace(state: AgentState, message: str) -> None:
    state.setdefault("trace", [])
    state["trace"].append(message)


def orchestrator_parse_input(state: AgentState) -> AgentState:
    patient = state.get("patient_info") or toxicity_orchestrator_agent.parse_patient_info(state.get("raw_patient_info", {}))
    raw_drug_info = state.get("raw_drug_info", {})
    drug = state.get("drug_info") or toxicity_orchestrator_agent.parse_drug_info(raw_drug_info)
    state["normalized_drug_input"] = normalize_drug_input(
        {
            "name": drug.name,
            "smiles": drug.smiles,
            "drugbank_id": drug.drugbank_id,
            "inchi_key": drug.inchi_key,
        }
    )
    state["patient_info"] = patient
    state["drug_info"] = drug
    state["next_step"] = "stage1_plan_retrieval"
    _trace(state, f"Toxicity Orchestrator parsed patient={patient.patient_id}, drug={drug.name}.")
    return state


def orchestrator_stage1_plan_retrieval(state: AgentState) -> AgentState:
    """Stage 1 orchestrator planning: decide universal toxicity knowledge needs."""
    query = {
        "purpose": "universal_toxicity",
        "drug": state["drug_info"].name,
        "patient_id": state["patient_info"].patient_id,
        "planning_stage": "stage1_universal_toxicity_retrieval",
        "needs": [
            "Drug Card",
            "ADMET endpoints",
            "DTI/mechanism/pathway",
            "PersADE/FAERS population baseline and signal",
        ],
    }
    state["stage1_retrieval_plan"] = {
        "goal": "Retrieve evidence needed to estimate population-level SOC toxicity baseline.",
        "query": query,
    }
    state["pending_knowledge_query"] = query
    state["return_to"] = "orchestrator_stage1_reason"
    state["next_step"] = "knowledge"
    _trace(state, "Toxicity Orchestrator planned Stage 1 universal toxicity retrieval and sent query to Knowledge Retrieval.")
    return state


def knowledge_retrieval_node(state: AgentState) -> AgentState:
    """Knowledge Retrieval runs exactly once for the currently pending orchestrator query."""
    query = state.get("pending_knowledge_query") or {"purpose": "comprehensive"}
    evidence = knowledge_agent.retrieve(
        query=query,
        patient_info=state["patient_info"],
        drug_info=state["drug_info"],
    )
    state["latest_evidence_package"] = evidence

    purpose = query.get("purpose")
    if purpose == "universal_toxicity":
        state["knowledge_stage1_done"] = True
        state["stage1_evidence_package"] = evidence
    elif purpose == "personalized_modifiers":
        state["knowledge_stage2_done"] = True
        state["stage2_evidence_package"] = evidence

    state["pending_knowledge_query"] = None
    state["next_step"] = state.get("return_to", "orchestrator_stage1_reason")
    _trace(
        state,
        f"Knowledge Retrieval returned {len(evidence.evidence_items)} evidence items for {purpose}; returning to Toxicity Orchestrator.",
    )
    return state


def orchestrator_stage1_reason(state: AgentState) -> AgentState:
    """Toxicity Orchestrator receives Stage 1 evidence and produces UniversalToxicityReport."""
    state["evidence_package"] = toxicity_orchestrator_agent.merge_evidence_packages(
        state.get("evidence_package"),
        state["latest_evidence_package"],
    )
    universal_report = toxicity_orchestrator_agent.build_universal_report(
        patient_info=state["patient_info"],
        drug_info=state["drug_info"],
        evidence_package=state["evidence_package"],
    )
    state["universal_report"] = universal_report
    state["next_step"] = "standardize_patient"
    _trace(state, "Toxicity Orchestrator received Stage 1 evidence and generated UniversalToxicityReport.")
    return state


def orchestrator_standardize_patient(state: AgentState) -> AgentState:
    """Stage 2 Step 1: standardize patient input into retrievable features."""
    patient_features = patient_standardizer.standardize(
        patient_info=state["patient_info"],
        drug_info=state["drug_info"],
    )
    state["patient_features"] = patient_features
    state["next_step"] = "stage2_plan_retrieval"
    _trace(
        state,
        "Toxicity Orchestrator standardized patient profile: "
        f"age_group={patient_features.age_group}, "
        f"organ_classes={{renal:{patient_features.organ_function_classes['renal'].klass}, "
        f"hepatic:{patient_features.organ_function_classes['hepatic'].klass}}}, "
        f"pgx={len(patient_features.pgx_phenotypes)}, "
        f"indications_matched={sum(1 for c in patient_features.indication_umls if c.matched)}.",
    )
    return state


def _stage2_subgroups(state: AgentState) -> Dict[str, Any]:
    """Patient subgroup selectors for stratified PersADE retrieval (Step 2)."""
    features = state.get("patient_features")
    if features is None:
        return {}
    exposure = features.exposure_context or {}
    return {
        "route": exposure.get("route"),
        "form": exposure.get("form"),
        "age_group": features.age_group if features.age_group != "unknown" else None,
        "sex": features.sex if features.sex != "Unknown" else None,
        "indication_cuis": [c.umls_cui for c in features.indication_umls if c.matched and c.umls_cui],
    }


def _stage1_candidate_ades(state: AgentState) -> list:
    """ADE ids (with SOC) surfaced by Stage 1 persade_drug_profile, for Step 2."""
    evidence = state.get("evidence_package")
    if evidence is None:
        return []
    top_ades = evidence.tool_results.get("persade_drug_profile", {}).get("top_ades", [])
    return [
        {"ade_id": ade.get("ade_id"), "soc": ade.get("soc")}
        for ade in top_ades
        if ade.get("ade_id")
    ]


def orchestrator_stage2_plan_retrieval(state: AgentState) -> AgentState:
    """Stage 2 orchestrator planning: decide personalized modifier knowledge needs."""
    query = {
        "purpose": "personalized_modifiers",
        "drug": state["drug_info"].name,
        "patient_id": state["patient_info"].patient_id,
        "planning_stage": "stage2_personalized_toxicity_retrieval",
        "baseline_soc_count": len(state["universal_report"].general_toxicity),
        "inchi_key": state["drug_info"].inchi_key,
        "candidate_ades": _stage1_candidate_ades(state),
        "subgroups": _stage2_subgroups(state),
        "needs": [
            "PGx/CPIC",
            "DDI",
            "HLA",
            "patient-context retrieval",
            "population subgroup distribution",
            "similar cases",
            "cohort modifiers",
        ],
    }
    state["stage2_retrieval_plan"] = {
        "goal": "Retrieve patient-specific PGx, DDI, comorbidity, and organ-function modifiers.",
        "query": query,
    }
    state["pending_knowledge_query"] = query
    state["return_to"] = "orchestrator_stage2_reason"
    state["next_step"] = "knowledge"
    _trace(state, "Toxicity Orchestrator planned Stage 2 personalized modifier retrieval and sent query to Knowledge Retrieval.")
    return state


def orchestrator_stage2_reason(state: AgentState) -> AgentState:
    """Toxicity Orchestrator receives Stage 2 evidence and produces PersonalizedToxicityReport."""
    state["evidence_package"] = toxicity_orchestrator_agent.merge_evidence_packages(
        state.get("evidence_package"),
        state["latest_evidence_package"],
    )
    personalized_report = toxicity_orchestrator_agent.build_personalized_report(
        patient_info=state["patient_info"],
        drug_info=state["drug_info"],
        universal_report=state["universal_report"],
        evidence_package=state["evidence_package"],
        patient_features=state["patient_features"],
    )
    state["personalized_report"] = personalized_report
    state["draft_report"] = toxicity_orchestrator_agent.synthesize_draft_report(
        patient_info=state["patient_info"],
        drug_info=state["drug_info"],
        universal_report=state["universal_report"],
        personalized_report=personalized_report,
        evidence_package=state["evidence_package"],
        patient_features=state["patient_features"],
    )
    state["next_step"] = "verify"
    _trace(state, "Toxicity Orchestrator received Stage 2 evidence and generated PersonalizedToxicityReport draft.")
    return state


def safety_verifier_node(state: AgentState) -> AgentState:
    verification = safety_verifier_agent.verify(
        draft_report=state["draft_report"],
        patient_info=state["patient_info"],
        drug_info=state["drug_info"],
        evidence_package=state["evidence_package"],
    )
    state["verification_report"] = verification
    state["next_step"] = "revise"
    _trace(state, f"Safety Verifier completed with status={verification.status}.")
    return state


def orchestrator_revise_output(state: AgentState) -> AgentState:
    revised = toxicity_orchestrator_agent.revise_with_verification(
        draft_report=state["draft_report"],
        verification_report=state["verification_report"],
    )
    state["draft_report"] = revised
    state["personalized_report"] = revised["personalized_report"]
    state["next_step"] = "format"
    _trace(state, "Toxicity Orchestrator integrated safety verifier feedback.")
    return state


def format_output(state: AgentState) -> AgentState:
    report: Dict[str, Any] = state["draft_report"]
    state["final_output"] = {
        "json": format_to_json(report),
    }
    state["next_step"] = "done"
    _trace(state, "Formatted final JSON output using the requested Stage 1 and Stage 2 schemas.")
    return state


def route_after_knowledge(state: AgentState) -> str:
    next_step = state.get("next_step")
    if next_step == "orchestrator_stage2_reason":
        return "stage2_reason"
    return "stage1_reason"

