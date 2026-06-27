"""Patient Profile Standardizer (Stage 2 Step 1).

Turns a parsed ``PatientInfo`` into a standardized, retrievable ``PatientFeatures``
object: the single source of truth for all downstream personalized-toxicity
rules. Fully deterministic and local-first — KB lookups degrade to
``matched=False`` / ``unresolved`` rather than raising, matching the project's
reproducible/auditable style.
"""

from __future__ import annotations

import re
from typing import Optional

from pertox_agent.schemas import (
    DrugInfo,
    OrganFunctionClass,
    PatientFeatures,
    PatientInfo,
    PgxPhenotype,
    ResolvedConcept,
    ResolvedDrug,
)
from pertox_agent.tools.shared.common import resolve_drug
from pertox_agent.tools.patient_context import indication_resolver, pgx_phenotyper

# Upper limit of normal used for transaminase-based hepatic staging (U/L).
_ALT_ULN = 40.0


class PatientProfileStandardizer:
    """Stage 2 Step 1: PatientInfo -> PatientFeatures (deterministic, local)."""

    def standardize(self, patient_info: PatientInfo, drug_info: DrugInfo) -> PatientFeatures:
        unresolved: list[str] = []
        return PatientFeatures(
            patient_id=patient_info.patient_id,
            age=patient_info.age,
            age_group=self._age_group(patient_info.age),
            elderly=patient_info.age >= 65,
            sex=self._sex(patient_info.sex),
            indication_umls=self._indications(patient_info, unresolved),
            comedication_ids=self._comedications(patient_info, unresolved),
            pgx_phenotypes=self._pgx(patient_info),
            organ_function_classes=self._organ_function(patient_info),
            exposure_context=self._exposure(patient_info, drug_info),
            unresolved=unresolved,
        )

    # --- demographics ----------------------------------------------------- #
    @staticmethod
    def _age_group(age: int) -> str:
        if age <= 0:
            return "unknown"
        low = (age // 10) * 10
        return f"{low}-{low + 9}YR"

    @staticmethod
    def _sex(sex: str) -> str:
        return {"female": "Female", "male": "Male", "other": "Other"}.get(sex, "Unknown")

    # --- indications / comedications -------------------------------------- #
    @staticmethod
    def _indications(patient_info: PatientInfo, unresolved: list[str]) -> list[ResolvedConcept]:
        concepts: list[ResolvedConcept] = []
        for diagnosis in patient_info.medical_history:
            resolved = indication_resolver.resolve_indication(diagnosis)
            concepts.append(
                ResolvedConcept(
                    input=resolved["input"],
                    name=resolved["name"],
                    umls_cui=resolved["umls_cui"],
                    mesh_id=resolved["mesh_id"],
                    icd10=resolved["icd10"],
                    matched=bool(resolved["matched"]),
                )
            )
            if not resolved["matched"]:
                unresolved.append(f"indication:{diagnosis}")
        return concepts

    @staticmethod
    def _comedications(patient_info: PatientInfo, unresolved: list[str]) -> list[ResolvedDrug]:
        drugs: list[ResolvedDrug] = []
        for name in patient_info.concomitant_medications:
            try:
                entity = resolve_drug({"drug": name})
            except Exception:
                entity = {}
            matched = bool(entity.get("drugbank_id") or entity.get("inchi_key"))
            drugs.append(
                ResolvedDrug(
                    input=name,
                    name=entity.get("name") or name,
                    drugbank_id=entity.get("drugbank_id"),
                    inchi_key=entity.get("inchi_key"),
                    matched=matched,
                )
            )
            if not matched:
                unresolved.append(f"comedication:{name}")
        return drugs

    # --- pharmacogenomics ------------------------------------------------- #
    @staticmethod
    def _pgx(patient_info: PatientInfo) -> list[PgxPhenotype]:
        records = pgx_phenotyper.classify_genotypes(patient_info.genotypes)
        return [
            PgxPhenotype(
                gene=record["gene"],
                diplotype=record["diplotype"],
                phenotype=record["phenotype"],
                actionable=bool(record["actionable"]),
                source=record["source"],
            )
            for record in records
        ]

    # --- organ function stratification ------------------------------------ #
    def _organ_function(self, patient_info: PatientInfo) -> dict:
        return {
            "renal": self._renal_class(patient_info),
            "hepatic": self._hepatic_class(patient_info),
            "cardiac": self._cardiac_class(patient_info),
        }

    @staticmethod
    def _renal_class(patient_info: PatientInfo) -> OrganFunctionClass:
        egfr = patient_info.egfr_ml_min
        if egfr is None:
            return OrganFunctionClass(klass="unknown", basis="eGFR not provided")
        if egfr >= 90:
            klass = "normal"
        elif egfr >= 60:
            klass = "mild"
        elif egfr >= 30:
            klass = "moderate"
        elif egfr >= 15:
            klass = "severe"
        else:
            klass = "kidney_failure"
        return OrganFunctionClass(klass=klass, basis=f"eGFR {egfr:g} mL/min (KDIGO)")

    @staticmethod
    def _hepatic_class(patient_info: PatientInfo) -> OrganFunctionClass:
        if patient_info.child_pugh in {"A", "B", "C"}:
            klass = {"A": "mild", "B": "moderate", "C": "severe"}[patient_info.child_pugh]
            return OrganFunctionClass(klass=klass, basis=f"Child-Pugh {patient_info.child_pugh}")
        transaminase = patient_info.alt_u_l if patient_info.alt_u_l is not None else patient_info.ast_u_l
        if transaminase is not None:
            ratio = transaminase / _ALT_ULN
            if ratio < 1.0:
                klass = "normal"
            elif ratio < 3.0:
                klass = "mild"
            else:
                klass = "moderate"
            return OrganFunctionClass(klass=klass, basis=f"ALT/AST {transaminase:g} U/L ({ratio:.1f}x ULN)")
        return OrganFunctionClass(klass="unknown", basis="no Child-Pugh or transaminase provided")

    @staticmethod
    def _cardiac_class(patient_info: PatientInfo) -> OrganFunctionClass:
        lvef = PatientProfileStandardizer._parse_lvef(patient_info.organ_function.get("LVEF"))
        if lvef is None:
            return OrganFunctionClass(klass="unknown", basis="LVEF not provided")
        if lvef >= 50:
            klass = "normal_lvef"
        elif lvef >= 40:
            klass = "mildly_reduced"
        else:
            klass = "reduced"
        return OrganFunctionClass(klass=klass, basis=f"LVEF {lvef:g}%")

    @staticmethod
    def _parse_lvef(value: object) -> Optional[float]:
        if value is None:
            return None
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None

    # --- exposure --------------------------------------------------------- #
    @staticmethod
    def _exposure(patient_info: PatientInfo, drug_info: DrugInfo) -> dict:
        exposure = patient_info.exposure or {}

        def pick(drug_value: Optional[str], *exposure_keys: str) -> Optional[str]:
            if drug_value and drug_value not in ("unspecified", "unknown"):
                return drug_value
            for key in exposure_keys:
                if exposure.get(key):
                    return str(exposure[key])
            return drug_value if drug_value not in ("unspecified", "unknown") else None

        return {
            "route": pick(drug_info.route, "route"),
            "form": pick(drug_info.form, "form"),
            "dose": pick(drug_info.dose, "dose"),
            "frequency": pick(drug_info.frequency, "frequency"),
        }

