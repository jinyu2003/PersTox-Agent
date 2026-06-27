#!/usr/bin/env python3
"""Smoke test + timing harness for the 8 PerTox-agent tools.

Runs every tool on Warfarin (the canonical driver: populated across admetSAR,
DrugBank, PersADE, DDInter2) plus a few extra fixtures, prints formatted
INPUT->OUTPUT, and reports each tool's cold/hot Tool-Call latency. The timing
table is what src/pertox_agent/tools/README.md records.

Run under the perstox conda env (needs rdkit):
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
        conda run -n perstox python tests/test_tools.py

Local-source failures exit non-zero; API outages are tolerated (cache-first
connectors degrade silently). contextual_retrieval is timed cold (streams the
4.5GB patient layer) and hot (cohort cache hit).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from pertox_agent.tools.molecular_evidence import (
    admet_predictor,
    drug_drug_interaction,
    drug_metabolism,
    drug_target_interaction,
    mechanism_evidence,
    pathway_enrichment,
)
from pertox_agent.tools.real_world_evidence import persade_drug_ade_profile, persade_similar_case_retrieval

WARFARIN = "warfarin"


def banner(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def show(tag: str, obj, trunc: int = 1400) -> None:
    s = json.dumps(obj, ensure_ascii=False)
    print(f"  {tag}: {s[:trunc]}{' ...' if len(s) > trunc else ''}")


def run_case(name: str, fn, payload, check) -> dict:
    """Execute one tool, print INPUT/OUTPUT, return a status row with timing."""
    banner(f"[{name}]")
    show("INPUT", payload)
    t0 = time.perf_counter()
    out = fn(payload)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    ok = False
    try:
        ok = check(out)
    except Exception as exc:  # noqa: BLE001
        out.setdefault("_check_error", str(exc))
    show("OUTPUT", out)
    print(f"  -> {ms} ms | local_ok={ok}")
    return {"tool": name, "ms": ms, "ok": ok}


CASES = [
    ("admetsar_predict", admet_predictor.run, WARFARIN,
     lambda o: o["applicability_domain"] and o["n_endpoints"] > 0),
    ("dti_query", drug_target_interaction.run, WARFARIN,
     lambda o: o["n_targets"] > 0 and "VKORC1" in o["on_target"]),
    ("pathway_enrich", pathway_enrichment.run, {"genes": ["VKORC1", "CYP2C9", "GGCX", "CYP2C19", "F2"]},
     lambda o: o["n_pathways_tested"] > 0),
    ("mechanism_query", mechanism_evidence.run, {"drug": WARFARIN, "organ": "Hepatobiliary"},
     lambda o: o["n_triplets"] > 0),
    ("drugbank_metabolism_query", drug_metabolism.run, WARFARIN,
     lambda o: "CYP2C9" in o["enzymes"]["buckets"]["substrate_of"]),
    ("ddi_query", drug_drug_interaction.run, {"drug": WARFARIN, "co_medications": ["aspirin", "clarithromycin", "metformin"]},
     lambda o: o["n_interactions"] >= 2),
    ("persade_drug_profile", persade_drug_ade_profile.run, {"drug": WARFARIN, "top": 30},
     lambda o: o["n_significant"] > 0),
]


def main() -> int:
    rows = [run_case(n, fn, pl, ck) for n, fn, pl, ck in CASES]

    # contextual_retrieval: time cold (clear cache) then hot (cache hit)
    from pertox_agent.tools.real_world_evidence.persade_similar_case_retrieval import COHORT_DIR
    banner("[persade_contextual_retrieval] (cold + hot)")
    profile = {"drug": WARFARIN, "patient": {"age": 72, "sex": "F"}, "k": 100}
    show("INPUT", profile)
    cache_files = list(COHORT_DIR.glob("*.jsonl")) if COHORT_DIR.exists() else []
    for f in cache_files:
        try:
            f.unlink()  # force a cold run when the environment permits it.
        except PermissionError:
            print(f"  WARN: could not clear cache file {f}; using existing cache.")
    t0 = time.perf_counter()
    out_cold = persade_similar_case_retrieval.run(profile)
    cold_ms = round((time.perf_counter() - t0) * 1000, 1)
    t0 = time.perf_counter()
    out_hot = persade_similar_case_retrieval.run(profile)
    hot_ms = round((time.perf_counter() - t0) * 1000, 1)
    show("OUTPUT(hot)", out_hot)
    ctx_ok = out_hot.get("cohort_size", 0) > 0 and out_hot.get("neighbors_used", 0) > 0
    print(f"  -> cold {cold_ms} ms | hot {hot_ms} ms | local_ok={ctx_ok}")
    rows.append({"tool": "persade_contextual_retrieval", "ms": hot_ms,
                 "ms_cold": cold_ms, "ok": ctx_ok})

    banner("TOOL-CALL SPEED SUMMARY")
    for r in rows:
        extra = f" (cold {r['ms_cold']} ms)" if "ms_cold" in r else ""
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] {r['tool']:30} {r['ms']:>9.1f} ms{extra}")
    warm = [r['ms'] for r in rows]
    print(f"\n  mean (warm/hot) over {len(rows)} tools: {sum(warm)/len(warm):.1f} ms")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())



