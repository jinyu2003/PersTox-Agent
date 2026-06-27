"""Run the warfarin personalized toxicity demo."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

def _fmt_nullable_float(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "null"
    return f"{value:+.2f}" if signed else f"{value:.2f}"


def _fmt_nullable_text(value: object) -> str:
    return "null" if value is None else str(value)


def _fmt_driver_ref(driver: dict) -> str:
    refs = driver.get("evidence_refs") or []
    if not refs:
        return "no ref"
    first = refs[0]
    return f"{first.get('tool_name', 'tool')}:{first.get('field') or first.get('evidence_path', 'field')}"


def _stage1_attribution_method(universal: object) -> str:
    methods = {
        item.attribution.attribution_generation_method
        for item in getattr(universal, "general_toxicity", [])
        if item.attribution.attribution_explanation
    }
    if not methods:
        return "none"
    if len(methods) == 1:
        return next(iter(methods))
    return "mixed"


def build_demo_state() -> dict:
    return {
        "raw_patient_info": {
            "patient_id": "demo-warfarin-001",
            "age": 65,
            "sex": "female",
            "weight_kg": 58,
            "alt_u_l": 68,
            "ast_u_l": 74,
            "bilirubin_mg_dl": 1.8,
            "child_pugh": "B",
            "creatinine_mg_dl": 1.3,
            "egfr_ml_min": 45,
            "genotypes": {"CYP2C9": "*2/*3"},
            "hla_types": [],
            "medical_history": ["K74.6 cirrhosis", "I48 atrial fibrillation"],
            "concomitant_medications": ["amiodarone"],
            "organ_function": {"LVEF": "55%"},
            "exposure": {"route": "oral", "frequency": "daily"},
            "pregnancy_status": "not_pregnant",
        },
        "raw_drug_info": {
            "name": "warfarin",
            "drugbank_id": "DB00682",
            "smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(O)C3=CC=CC=C3OC2=O",
            # "dose": "5 mg/day",
            # "route": "oral",
            # "frequency": "daily",
            # "known_toxicities": ["bleeding", "skin necrosis"],
        },
        "messages": [],
        "trace": [],
    }


def print_demo_summary(final_state: dict) -> None:
    print("\n=== PersAgent Trace ===")
    for item in final_state.get("trace", []):
        print(f"- {item}")

    evidence = final_state["evidence_package"]
    print("\n=== Knowledge Retrieval ===")
    print(f"Evidence package purpose: {evidence.query_purpose}")
    print(f"Tools called: {', '.join(evidence.tool_results.keys())}")
    print(f"Evidence items: {len(evidence.evidence_items)}")
    if evidence.conflicts:
        print("Conflicts:")
        for conflict in evidence.conflicts:
            print(f"- {conflict}")
    else:
        print("Conflicts: none")

    universal = final_state["universal_report"]
    print("\n=== Stage 1 Universal Toxicity ===")
    print(f"Drug: {universal.drug.name} ({universal.drug.drugbank_id})")
    for item in universal.general_toxicity:
        print(
            f"- {item.soc}: baseline={_fmt_nullable_text(item.baseline_risk_level)}, "
            f"probability={_fmt_nullable_float(item.baseline_probability)}, "
            f"uncertainty={_fmt_nullable_float(item.uncertainty)}"
        )

    print(f"\n=== Attribution Explanation [{_stage1_attribution_method(universal)}] ===")
    for item in universal.general_toxicity:
        attribution = item.attribution
        explanation = attribution.attribution_explanation
        if not explanation:
            print(f"- {item.soc}: no attribution explanation available")
            continue
        print(f"- {item.soc}: {explanation}")
        for driver in attribution.molecular_attribution[:3]:
            role = driver.get("contribution_role") or "context"
            print(
                "  - "
                f"{driver.get('driver_type', 'driver')}[{role}]: "
                f"{driver.get('driver', 'retrieved evidence')} "
                f"(confidence={driver.get('confidence', 'n/a')}, ref={_fmt_driver_ref(driver)})"
            )

    narrative_items = [
        item
        for item in universal.general_toxicity
        if item.attribution.attribution_narrative
    ]
    if narrative_items:
        print("\n=== Attribution Narrative ===")
        for item in narrative_items:
            print(f"- {item.soc}: {item.attribution.attribution_narrative}")

    personalized = final_state["personalized_report"]
    print("\n=== Stage 2 Personalized Toxicity ===")
    for item in personalized.personalized_toxicity:
        print(
            f"- {item.soc}: baseline={_fmt_nullable_float(item.baseline.probability)}, "
            f"personalized={_fmt_nullable_float(item.personalized_probability)}, "
            f"shift={_fmt_nullable_float(item.risk_shift, signed=True)}, "
            f"CTCAE grade={_fmt_nullable_text(item.ctcae_grade_predicted)}, "
            f"modifiers={len(item.patient_attribution)}"
        )

    print("\n=== Recommendations ===")
    seen_recommendations = set()
    for item in personalized.personalized_toxicity:
        rec = item.clinical_recommendation
        if rec is None:
            continue
        if rec.text in seen_recommendations:
            continue
        print(f"- [{rec.action}] {rec.text}")
        seen_recommendations.add(rec.text)

    verification = final_state["verification_report"]
    print("\n=== Verification ===")
    print(f"Status: {verification.status}")
    print(f"Summary: {verification.summary}")
    for issue in verification.issues:
        print(f"- L{issue.layer} {issue.severity} {issue.code}: {issue.message}")

    final_output = final_state["final_output"]
    output_dir = Path("results")
    output_path = output_dir / "final_report_warfarin.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_output["json"]["json"], encoding="utf-8")

    print("\n=== Final Output ===")
    schema_payload = final_output["json"]["payload"]
    print(f"JSON report: {output_path.resolve()}")
    print(f"JSON top-level keys: {', '.join(schema_payload.keys())}")
    print(
        "General toxicity rows: "
        f"{len(schema_payload['universal_toxicity_report']['general_toxicity'])}"
    )
    print(
        "Personalized toxicity rows: "
        f"{len(schema_payload['personalized_toxicity_report']['personalized_toxicity'])}"
    )


def main() -> None:
    from pertox_agent.graph import build_graph

    graph = build_graph()
    final_state = graph.invoke(build_demo_state())
    print_demo_summary(final_state)


if __name__ == "__main__":
    main()

