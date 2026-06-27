#!/usr/bin/env python3
"""Stage 2 Step 2 — similar-case / population subgroup retrieval tests.

Drives the warfarin demo patient through persade_subgroup_risk (stratified
PersADE score tables) and the full graph, asserting the contextual evidence
shape and that Stage 2 probabilities are NOT modified by this step.

First run cold-scans ~2-3GB of PersADE score tables (minutes); subsequent runs
read data/cache/persade_subgroups/ (fast).

    PERSAGENT_USE_LIVE_LLM=false python tests/test_stage2_context.py
"""

from __future__ import annotations

import numbers
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from examples.run_warfarin_demo import build_demo_state
from pertox_agent.tools.real_world_evidence import persade_subgroup_risk

_failures: list[str] = []


def check(condition: bool, label: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def test_tool_direct() -> None:
    print("== persade_subgroup_risk (warfarin, demo subgroups) ==")
    result = persade_subgroup_risk.run(
        {
            "drug": "warfarin",
            "subgroups": {
                "route": "oral",
                "age_group": "60-69YR",
                "sex": "Female",
                "indication_cuis": ["C0004238", "C0023890"],
            },
        }
    )
    evidence = result.get("persade_contextual_evidence", [])
    check(len(evidence) > 0, f"contextual evidence non-empty (got {len(evidence)})")

    first = evidence[0] if evidence else {}
    check(isinstance(first.get("overall", {}).get("total_score"), numbers.Number),
          "each row has overall.total_score")
    check(isinstance(first.get("contextual_risk_shift"), numbers.Number),
          "contextual_risk_shift is numeric")
    check(0.0 < first.get("uncertainty", -1) <= 1.0, "uncertainty in (0, 1]")

    route_hits = [e for e in evidence if e["subgroups"].get("route")]
    check(len(route_hits) > 0, "at least one ADE has a ROUTE subgroup match")
    check(all(e["subgroups"]["route"]["key"] == "ORAL" for e in route_hits),
          "route subgroup key normalized to ORAL")

    age_hits = [e for e in evidence if e["subgroups"].get("age")]
    check(len(age_hits) > 0, "at least one ADE has 60-69YR age distribution parsed")
    check(all(e["subgroups"]["age"]["band"] == "60-69YR" for e in age_hits),
          "age band == 60-69YR")


def test_cli_smoke() -> None:
    print("== CLI smoke ==")
    rc = persade_subgroup_risk.main(["warfarin", "--route", "oral"])
    check(rc == 0, "CLI returns 0")


def test_endtoend_no_probability_change() -> None:
    print("== end-to-end: evidence exposed, Stage 2 probability unchanged ==")
    from pertox_agent.graph import build_graph

    final_state = build_graph().invoke(build_demo_state())
    payload = final_state["final_output"]["json"]["payload"]

    check(payload.get("persade_contextual_evidence") is not None,
          "persade_contextual_evidence exposed in output JSON")
    check(isinstance(payload.get("persade_contextual_evidence"), list)
          and len(payload["persade_contextual_evidence"]) > 0,
          "contextual evidence list is populated end-to-end")

    # Step 2 must NOT alter Stage 2 numbers: liver stays at the Step-1 value 0.61.
    personalized = final_state["personalized_report"].personalized_toxicity
    liver = next((i for i in personalized if i.soc == "Hepatobiliary disorders"), None)
    check(liver is not None and liver.personalized_probability is not None
          and abs(liver.personalized_probability - 0.61) < 0.011,
          "liver personalized_probability still ~0.61 (Step 2 did not modify risk)")


def main() -> int:
    test_tool_direct()
    test_cli_smoke()
    test_endtoend_no_probability_change()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


