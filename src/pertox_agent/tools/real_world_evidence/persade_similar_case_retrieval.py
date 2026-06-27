"""persade_contextual_retrieval — similar-patient ADE distribution via k-NN.

临床输入: 药物 (DrugBank ID/SMILES/名称/InChIKey) + 患者画像
          (age / sex / route / outcome / serious 等人口学+用药上下文)
临床输出: 相似人群 ADE 分布 (k-NN 检索患者层报告) + 队列规模 + ADE 频率/相似度。
数据源:
  - 本地: PersADE ADE_report.txt (患者/报告层, 4.5GB; cols: InChIkey, UMLS, Route,
          Sex, Age, Outcome, Serious, SOC, ADE_name ...) [自有,患者层]

策略: 流式扫患者层取该药 InChIKey 的全部个案报告作候选队列(冷调用 ~分钟级,
4.5GB),按 InChIKey 缓存到 data/cache/persade_cohorts/<ik>.jsonl;热调用直接读缓存
(毫秒级)。对每个个案按患者画像算 Gower 距离(年龄分桶差 + 性别/途径/严重性匹配),
取 top-k 相似个案,聚合其 ADE 频率分布。
"""
from __future__ import annotations

import argparse
import json

from pertox_agent.tools.shared.common import CACHE, PERSADE, REPORT, resolve_drug, timed

COHORT_DIR = CACHE / "persade_cohorts"


def _parse_age(raw: str):
    """'45 YR' -> 45.0 (years). Handles DEC/MON/WK/YR units, else None."""
    if not raw:
        return None
    parts = raw.split()
    try:
        val = float(parts[0])
    except (ValueError, IndexError):
        return None
    unit = parts[1].upper() if len(parts) > 1 else "YR"
    factor = {"YR": 1.0, "MON": 1 / 12, "WK": 1 / 52, "DY": 1 / 365, "DEC": 10.0}.get(unit, 1.0)
    return round(val * factor, 2)


def _age_band(age):
    if age is None:
        return None
    for hi, label in [(1, "infant"), (12, "child"), (18, "adolescent"),
                      (45, "adult"), (65, "middle_age"), (200, "elderly")]:
        if age < hi:
            return label
    return "elderly"


def _build_cohort(inchikey: str) -> list[dict]:
    """Stream the 4.5GB patient layer once for this drug's reports, cache as
    JSONL keyed by InChIKey. Returns the cohort (list of report dicts)."""
    COHORT_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = COHORT_DIR / f"{inchikey}.jsonl"
    if cache_path.exists():
        return [json.loads(l) for l in cache_path.open(encoding="utf-8") if l.strip()]
    cohort = []
    with (PERSADE / "ADE_report.txt").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) <= REPORT["ADE_name"] or c[REPORT["InChIkey"]] != inchikey:
                continue
            rec = {"report_id": c[REPORT["Report_ID"]], "umls": c[REPORT["UMLS"]],
                   "route": c[REPORT["Route"]], "sex": c[REPORT["Sex"]],
                   "age": _parse_age(c[REPORT["Age"]]), "outcome": c[REPORT["Outcome"]],
                   "serious": c[REPORT["Serious"]], "soc": c[REPORT["SOC"]],
                   "ade_name": c[REPORT["ADE_name"]]}
            cohort.append(rec)
    with cache_path.open("w", encoding="utf-8") as out:
        for rec in cohort:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return cohort


def _similarity(report: dict, profile: dict) -> float:
    """Gower-style similarity in [0,1]: mean of per-feature matches over the
    features the patient profile actually specifies (missing -> skipped)."""
    sims, n = 0.0, 0
    if profile.get("age") is not None and report.get("age") is not None:
        sims += max(0.0, 1.0 - abs(profile["age"] - report["age"]) / 80.0); n += 1
    elif profile.get("age_band") and _age_band(report.get("age")):
        sims += 1.0 if profile["age_band"] == _age_band(report["age"]) else 0.0; n += 1
    for key in ("sex", "route", "outcome", "serious"):
        if profile.get(key):
            pv = str(profile[key]).upper()
            rv = str(report.get(key) or "").upper()
            sims += 1.0 if pv == rv else 0.0; n += 1
    return sims / n if n else 0.0


@timed
def run(payload) -> dict:
    """payload: {drug:..., patient:{age?,sex?,route?,outcome?,serious?}, k?:int}.
    Returns the k most similar patient reports' ADE distribution."""
    ent = resolve_drug(payload.get("drug") or payload)
    profile = dict(payload.get("patient") or payload.get("profile") or {})
    if profile.get("age") is not None:
        profile["age_band"] = _age_band(profile["age"])
    k = payload.get("k", 100)
    ik = ent.get("inchi_key")
    out = {"tool": "persade_contextual_retrieval",
           "drug": {k2: ent.get(k2) for k2 in ("name", "inchi_key", "drugbank_id")},
           "patient_profile": profile, "cohort_size": 0, "k": k,
           "neighbors_used": 0, "ade_distribution": []}
    if not ik:
        out["error"] = "could not resolve drug to an InChIKey"
        return out
    cohort = _build_cohort(ik)
    out["cohort_size"] = len(cohort)
    if not cohort:
        out["_note"] = "no patient-layer reports for this drug"
        return out
    scored = sorted(cohort, key=lambda r: _similarity(r, profile), reverse=True)
    neighbors = scored[:k]
    out["neighbors_used"] = len(neighbors)
    # aggregate ADE frequency over the neighbor set
    freq: dict[str, dict] = {}
    for r in neighbors:
        name = r.get("ade_name") or r.get("umls")
        e = freq.setdefault(name, {"ade_name": name, "umls": r.get("umls"),
                                   "soc": r.get("soc"), "count": 0})
        e["count"] += 1
    dist = sorted(freq.values(), key=lambda e: e["count"], reverse=True)
    for e in dist:
        e["frequency"] = round(e["count"] / len(neighbors), 4)
    out["ade_distribution"] = dist[:40]
    out["mean_similarity"] = round(sum(_similarity(r, profile) for r in neighbors) / len(neighbors), 3)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="similar-patient ADE distribution (PersADE patient layer)")
    ap.add_argument("drug", help="DrugBank ID, name, SMILES, or InChIKey")
    ap.add_argument("--age", type=float)
    ap.add_argument("--sex", choices=["M", "F"])
    ap.add_argument("--route")
    ap.add_argument("--k", type=int, default=100)
    args = ap.parse_args(argv)
    patient = {kk: vv for kk, vv in (("age", args.age), ("sex", args.sex), ("route", args.route)) if vv is not None}
    print(json.dumps(run({"drug": args.drug, "patient": patient, "k": args.k}),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

