#!/usr/bin/env python3
"""Stage 2 Step 1 — patient profile standardization tests.

Drives the warfarin demo patient through PatientProfileStandardizer and the full
graph, asserting the standardized PatientFeatures and that the refactored
Stage 2 attribution (organ-function / age now sourced from PatientFeatures)
still produces the expected modifiers.

Run deterministically (no live LLM needed):
    PERSAGENT_USE_LIVE_LLM=false python tests/test_patient_profile.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from pertox_agent.agents.toxicity_orchestrator_agent import ToxicityOrchestratorAgent
from examples.run_warfarin_demo import build_demo_state
from pertox_agent.tools.patient_context import indication_resolver, pgx_phenotyper
from pertox_agent.tools.patient_context.standardizer import PatientProfileStandardizer


_failures: list[str] = []


def check(condition: bool, label: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def test_standardizer() -> None:
    print("== PatientProfileStandardizer (warfarin demo patient) ==")
    state = build_demo_state()
    orchestrator = ToxicityOrchestratorAgent()
    patient = orchestrator.parse_patient_info(state["raw_patient_info"])
    drug = orchestrator.parse_drug_info(state["raw_drug_info"])
    features = PatientProfileStandardizer().standardize(patient, drug)

    check(features.age_group == "60-69YR", f"age_group == 60-69YR (got {features.age_group})")
    check(features.elderly is True, "elderly is True")
    check(features.sex == "Female", f"sex == Female (got {features.sex})")

    hepatic = features.organ_function_classes["hepatic"]
    renal = features.organ_function_classes["renal"]
    cardiac = features.organ_function_classes["cardiac"]
    check(hepatic.klass == "moderate", f"hepatic == moderate (got {hepatic.klass})")
    check(renal.klass == "moderate", f"renal == moderate (got {renal.klass})")
    check(cardiac.klass == "normal_lvef", f"cardiac == normal_lvef (got {cardiac.klass})")

    cyp2c9 = next((p for p in features.pgx_phenotypes if p.gene == "CYP2C9"), None)
    check(cyp2c9 is not None and cyp2c9.phenotype == "intermediate_metabolizer",
          "CYP2C9 -> intermediate_metabolizer")
    check(cyp2c9 is not None and cyp2c9.actionable is True, "CYP2C9 actionable is True")

    amiodarone = next((d for d in features.comedication_ids if "amiodarone" in d.input.lower()), None)
    check(amiodarone is not None and amiodarone.matched is True, "amiodarone comedication matched")

    matched_indications = [c for c in features.indication_umls if c.matched]
    check(len(matched_indications) >= 1, "at least one indication resolved to UMLS")

    check(features.exposure_context.get("route") == "oral",
          f"exposure route == oral (got {features.exposure_context.get('route')})")


def test_tool_clis() -> None:
    print("== tool-layer run() smoke ==")
    pgx = pgx_phenotyper.run({"gene": "CYP2C9", "diplotype": "*2/*3"})
    check(pgx["phenotypes"][0]["phenotype"] == "intermediate_metabolizer", "pgx_phenotyper.run CYP2C9 *2/*3")
    indi = indication_resolver.run({"indications": ["K74.6 cirrhosis"]})
    check(indi["results"][0]["matched"] is True and indi["results"][0]["icd10"] == "K74.6",
          "indication_resolver.run strips ICD and matches cirrhosis")
    # uncovered gene degrades, never raises
    check(pgx_phenotyper.classify("UNKNOWNGENE", "*1/*1")["phenotype"] == "indeterminate",
          "uncovered gene -> indeterminate")


def test_stage2_refactor_endtoend() -> None:
    print("== end-to-end: PatientFeatures drives Stage 2 attribution ==")
    from pertox_agent.graph import build_graph

    final_state = build_graph().invoke(build_demo_state())

    check("patient_features" in final_state, "patient_features present in final state")
    payload = final_state["final_output"]["json"]["payload"]
    check(payload.get("patient_features") is not None, "patient_features exposed in output JSON")

    personalized = final_state["personalized_report"].personalized_toxicity
    liver = next((i for i in personalized if i.soc == "Hepatobiliary disorders"), None)
    check(liver is not None, "liver SOC row present")
    if liver is not None:
        hepatic_factor = next(
            (f for f in liver.patient_attribution if f.rule_id.startswith("ORG-HEPATIC")), None
        )
        check(hepatic_factor is not None and abs(hepatic_factor.magnitude - 1.45) < 1e-9,
              "liver hepatic modifier magnitude == 1.45 (Child-Pugh B equivalent)")
        if liver.baseline.probability is not None and liver.personalized_probability is not None:
            check(liver.personalized_probability >= liver.baseline.probability,
                  "liver personalized_probability >= baseline (risk up)")


def main() -> int:
    test_standardizer()
    test_tool_clis()
    test_stage2_refactor_endtoend()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


