"""ddi_query — drug-drug interaction screen for a target drug + co-medications.

临床输入: 目标药 + 合并用药列表 (每个均可为 DrugBank ID / 名称 / SMILES / InChIKey)
临床输出: DDI 对 + 机制 (PK: CYP/转运体; PD: 协同/拮抗, 从 DrugBank 描述启发式归类) +
          严重度 (DDInter2 Major/Moderate/Minor) + 后果 + 处置建议。
数据源:
  - 本地: DrugBank drug_interactions (partner_id+name+description, 机制自由文本) [T2];
          DDInter2 归一化 (data/normalized/ddinter2/ddinter_pairs.jsonl, 严重度 Level) [T2]
  - API 补充: FAERS/openFDA (共报告信号, 仅辅助; 可选) [T4]

每对药取 DrugBank 机制描述 + DDInter2 严重度,合并为一条 DDI 记录;机制按描述里的
关键词归类 PK(CYP/transporter/absorption)或 PD(additive/antagon/synerg)。处置建议
按严重度模板给出 (Major=避免/监测, Moderate=监测剂量, Minor=留意)。
"""
from __future__ import annotations

import argparse
from functools import lru_cache

from tool.common import NORMALIZED, load_jsonl, resolve_drug, timed

SEVERITY_ADVICE = {
    "Major": "避免联用或仅在密切监测下使用;考虑替代药物",
    "Moderate": "可联用但需监测疗效/毒性,必要时调整剂量",
    "Minor": "临床意义有限,留意即可",
    "Unknown": "证据不足,谨慎评估",
}


@lru_cache(maxsize=1)
def _ddinter_index() -> dict:
    """Load DDInter2 normalized pairs into {frozenset(name_a_lc,name_b_lc): level}.
    ~231k rows / 50MB; loaded once per process and cached."""
    idx = {}
    path = NORMALIZED / "ddinter2" / "ddinter_pairs.jsonl"
    if not path.exists():
        return idx
    for r in load_jsonl(path):
        key = frozenset((r["drug_a_lc"], r["drug_b_lc"]))
        idx[key] = r["level"]
    return idx


def _classify_mechanism(description: str) -> str:
    d = (description or "").lower()
    if any(k in d for k in ("cyp", "metabolism", "p-glycoprotein", "transporter", "absorption", "excretion", "serum concentration")):
        return "PK"
    if any(k in d for k in ("additive", "synerg", "antagon", "qt", "cns depress", "bleeding", "hypotens", "serotonin")):
        return "PD"
    return "unspecified"


def _pair(target: dict, comed: dict, db) -> dict:
    """Resolve one target-comedication pair: DrugBank DDI description (by ID) +
    DDInter2 severity (by name)."""
    rec = {"co_drug": comed.get("name") or comed.get("input"),
           "co_drugbank_id": comed.get("drugbank_id"),
           "interaction": False, "severity": None, "mechanism_class": None,
           "description": None, "advice": None, "sources": []}
    # DrugBank DDI: scan target's interactions for this partner id
    if target.get("drugbank_id") and comed.get("drugbank_id"):
        hit = db.check_interaction(target["drugbank_id"], comed["drugbank_id"])
        if hit:
            rec["interaction"] = True
            rec["description"] = hit.get("description")
            rec["mechanism_class"] = _classify_mechanism(hit.get("description"))
            rec["sources"].append("DrugBank")
    # DDInter2 severity by name pair
    tn, cn = (target.get("name") or "").lower(), (comed.get("name") or "").lower()
    if tn and cn:
        level = _ddinter_index().get(frozenset((tn, cn)))
        if level:
            rec["interaction"] = True
            rec["severity"] = level
            rec["sources"].append("DDInter2")
    rec["advice"] = SEVERITY_ADVICE.get(rec["severity"] or "Unknown")
    return rec


@timed
def run(payload) -> dict:
    """payload: {drug: target, co_medications: [refs...]} (also accepts
    'target'/'comeds'). Each ref = name / DrugBank ID / SMILES / InChIKey."""
    target_ref = payload.get("drug") or payload.get("target")
    comeds = payload.get("co_medications") or payload.get("comeds") or []
    target = resolve_drug(target_ref)
    from tool.common import _drugbank
    db = _drugbank()
    pairs = [_pair(target, resolve_drug(c), db) for c in comeds]
    interacting = [p for p in pairs if p["interaction"]]
    sev_rank = {"Major": 3, "Moderate": 2, "Minor": 1, None: 0, "Unknown": 0}
    interacting.sort(key=lambda p: sev_rank.get(p["severity"], 0), reverse=True)
    return {"tool": "ddi_query",
            "target_drug": {k: target.get(k) for k in ("name", "drugbank_id", "inchi_key")},
            "n_comedications": len(comeds), "n_interactions": len(interacting),
            "interactions": interacting,
            "no_interaction_found": [p["co_drug"] for p in pairs if not p["interaction"]]}


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="drug-drug interaction screen")
    ap.add_argument("target", help="target drug ref")
    ap.add_argument("comeds", nargs="+", help="co-medication refs")
    args = ap.parse_args(argv)
    print(json.dumps(run({"drug": args.target, "co_medications": args.comeds}),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
