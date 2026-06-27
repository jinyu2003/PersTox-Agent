"""Shared LangGraph state definition."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

try:
    from langchain_core.messages import BaseMessage
except Exception:  # Allows type checking and fallback execution without deps.
    BaseMessage = Any  # type: ignore

from pertox_agent.schemas import (
    DrugInfo,
    EvidencePackage,
    PatientFeatures,
    PatientInfo,
    PersonalizedToxicityReport,
    UniversalToxicityReport,
    VerificationReport,
)


class AgentState(TypedDict, total=False):
    messages: List[BaseMessage]
    raw_patient_info: Any
    raw_drug_info: Any
    normalized_drug_input: Dict[str, Any]
    patient_info: PatientInfo
    drug_info: DrugInfo
    patient_features: Optional[PatientFeatures]
    universal_report: Optional[UniversalToxicityReport]
    personalized_report: Optional[PersonalizedToxicityReport]
    evidence_package: Optional[EvidencePackage]
    latest_evidence_package: Optional[EvidencePackage]
    stage1_evidence_package: Optional[EvidencePackage]
    stage2_evidence_package: Optional[EvidencePackage]
    verification_report: Optional[VerificationReport]
    draft_report: Optional[Dict[str, Any]]
    final_output: Optional[Dict[str, Any]]
    pending_knowledge_query: Optional[Dict[str, Any]]
    stage1_reasoning: Optional[Dict[str, Any]]
    stage2_reasoning: Optional[Dict[str, Any]]
    next_step: str
    return_to: str
    trace: List[str]
    knowledge_stage1_done: bool
    knowledge_stage2_done: bool

