"""persade_drug_profile — known adverse-event spectrum for a drug.

临床输入: 药物 (DrugBank ID / SMILES / 名称 / InChIKey)
临床输出: 已知 ADE 全谱 (按 MeSH-tree 器官系统组织) + 频率/严重度(若有) + 证据
          (ROR / ROR 下限CI / priority / 报告数 / PubMed)。
数据源:
  - 本地: PersADE CCombined_Results_with_scores (ASS_SCORE: drug-ADE 关联 + ROR/PRR/
          priority/severity) + ADE_Information (UMLS->名称/MeSH/tree/严重度) [自有,可用等级高/中/低]
  - API 补充: OnSIDES (标签 NLP 抽取 ADE, 可选; 当前未本地部署,占位降级) [T2-3]

证据分层: priority(High/Medium/Low) + ROR 下限 CI>1 视为统计显著信号。输出按器官
分组,每组给 ADE 列表(按 ROR 降序)。这是"信号"级证据(FAERS 自发报告挖掘),非金标准。
"""
from __future__ import annotations

import argparse

from tool.common import ASS, PERSADE, resolve_drug, scan_tsv, timed
from tool.mechanism_admet.mechanism_query import _ade_info, _organ_bucket


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


@timed
def run(payload) -> dict:
    """payload: str | {drug|..., min_priority?: 'High'|'Medium'|'Low', top?: int,
    significant_only?: bool}. Aggregates the drug's ADE associations by organ."""
    if isinstance(payload, dict):
        sig_only = payload.get("significant_only", True)
        allowed = {"High", "Medium"} if payload.get("min_priority", "Medium") == "Medium" else {"High"}
        top = payload.get("top", 50)
    else:
        sig_only, allowed, top = True, {"High", "Medium"}, 50
    ent = resolve_drug(payload if isinstance(payload, dict) else {"drug": payload})
    ik = ent.get("inchi_key")
    out = {"tool": "persade_drug_profile",
           "drug": {k: ent.get(k) for k in ("name", "inchi_key", "drugbank_id")},
           "n_total_associations": 0, "n_significant": 0,
           "evidence_level": "signal (FAERS disproportionality, PersADE)",
           "organs": {}, "top_ades": []}
    if not ik:
        out["error"] = "could not resolve drug to an InChIKey"
        return out
    kept = []
    total = 0
    for c in scan_tsv(PERSADE / "CCombined_Results_with_scores.txt", ASS["Drug_ID"], ik):
        total += 1
        rlci = _f(c[ASS["ROR_Lower_CI"]]) if len(c) > ASS["ROR_Lower_CI"] else None
        prio = c[ASS["priority"]] if len(c) > ASS["priority"] else ""
        if sig_only and not (rlci is not None and rlci > 1.0 and prio in allowed):
            continue
        kept.append(c)
    kept.sort(key=lambda c: _f(c[ASS["ROR"]]) or 0, reverse=True)
    info = _ade_info({c[ASS["Reaction_ID"]] for c in kept[:top]})
    organs: dict[str, list] = {}
    for c in kept[:top]:
        rid = c[ASS["Reaction_ID"]]
        meta = info.get(rid, {})
        bucket = _organ_bucket(meta.get("tree", ""))
        rec = {"ade_id": rid, "ade_name": meta.get("name") or rid,
               "organ": bucket, "ror": c[ASS["ROR"]], "ror_lower_ci": c[ASS["ROR_Lower_CI"]],
               "prr": c[ASS["PRR"]] if len(c) > ASS["PRR"] else None,
               "case_number": c[ASS["Case_number"]] if len(c) > ASS["Case_number"] else None,
               "priority": c[ASS["priority"]], "severity_grade": meta.get("severity"),
               "pubmed": c[ASS["PubMed"]] if len(c) > ASS["PubMed"] else None}
        organs.setdefault(bucket, []).append(rec)
        out["top_ades"].append(rec)
    out["n_total_associations"] = total
    out["n_significant"] = len(kept)
    out["organs"] = {k: {"n": len(v), "ades": v} for k, v in
                     sorted(organs.items(), key=lambda kv: -len(kv[1]))}
    return out


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="known ADE spectrum for a drug (PersADE)")
    ap.add_argument("drug", help="DrugBank ID, name, SMILES, or InChIKey")
    ap.add_argument("--top", type=int, default=50)
    args = ap.parse_args(argv)
    print(json.dumps(run({"drug": args.drug, "top": args.top}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
