"""Unified clinical input normalization entry point."""

from __future__ import annotations

from typing import Any, Optional

from pertox_agent.settings import get_model_config
from pertox_agent.schemas import DrugInfo, PatientInfo
from pertox_agent.tools.clinical_input.drug_parser import drug_info_kwargs_from_input
from pertox_agent.tools.clinical_input.field_fusion import (
    DRUG_INFO_FIELDS,
    DRUG_INPUT_SCHEMA,
    PATIENT_INFO_FIELDS,
    PATIENT_INPUT_SCHEMA,
    finalize_drug_payload,
    finalize_patient_payload,
    merge_payloads,
)
from pertox_agent.tools.clinical_input.patient_parser import patient_info_kwargs_from_input
from pertox_agent.tools.clinical_input.semantic_extractor import LLMJsonExtractor, SemanticInputExtractor


class ClinicalInputNormalizer:
    """Normalize raw clinical input into ``PatientInfo`` and ``DrugInfo`` models."""

    def __init__(
        self,
        *,
        config: Any = None,
        llm_json_extractor: Optional[LLMJsonExtractor] = None,
    ) -> None:
        self.config = config or get_model_config()
        self.semantic_extractor = SemanticInputExtractor(
            config=self.config,
            llm_json_extractor=llm_json_extractor,
        )

    def normalize_patient(self, raw: Any) -> PatientInfo:
        deterministic_payload = patient_info_kwargs_from_input(raw)
        llm_payload = self.semantic_extractor.extract(
            kind="patient",
            raw=raw,
            schema=PATIENT_INPUT_SCHEMA,
        )
        payload = merge_payloads(
            deterministic_payload,
            llm_payload,
            fields=PATIENT_INFO_FIELDS,
            aliases={
                "diagnoses": "medical_history",
                "diagnosis": "medical_history",
                "comedications": "concomitant_medications",
                "pgx": "genotypes",
            },
        )
        patient = PatientInfo(**finalize_patient_payload(payload))
        missing = set(patient.missing_modalities)
        if not patient.genotypes:
            missing.add("CYP450/PGx genotype")
        if not patient.hla_types and not any("HLA" in key.upper() for key in patient.genotypes):
            missing.add("HLA type")
        if patient.egfr_ml_min is None:
            missing.add("renal function/eGFR")
        if patient.alt_u_l is None and patient.ast_u_l is None and patient.child_pugh is None:
            missing.add("hepatic function")
        patient.missing_modalities = sorted(missing)
        return patient

    def normalize_drug(self, raw: Any) -> DrugInfo:
        deterministic_payload = drug_info_kwargs_from_input(raw)
        llm_payload = self.semantic_extractor.extract(
            kind="drug",
            raw=raw,
            schema=DRUG_INPUT_SCHEMA,
        )
        payload = merge_payloads(
            deterministic_payload,
            llm_payload,
            fields=DRUG_INFO_FIELDS,
            aliases={"drug_name": "name"},
        )
        return DrugInfo(**finalize_drug_payload(raw, payload))

