"""drugbank_metabolism_query — metabolic & transport profile for a drug.

临床输入: 药物 (DrugBank ID / SMILES / 名称 / InChIKey)
临床输出: 代谢酶 (CYP 底物/抑制/诱导分类) + 活性/毒性代谢物 (自由文本) +
          转运体 (P-gp/OATP/BCRP 等 + 作用) + 载体蛋白 + 消除途径 + 代谢自由文本。
数据源:
  - 本地: DrugBank enzymes / transporters / carriers (gene+actions) + metabolism
          自由文本 [T2]
  - API 补充: openFDA drug label (说明书 metabolism/pharmacokinetics 段, 可选,
              失败降级) [T1 标签]

酶按 action 分桶 substrate / inhibitor / inducer (CPIC/DDI 推理直接消费)。转运体
识别 P-gp(ABCB1)/OATP(SLCO*)/BCRP(ABCG2)/OAT/OCT/MATE 等外排/摄取家族。
"""
from __future__ import annotations

import argparse

from pertox_agent.tools.shared.common import api_get, resolve_drug, timed

TRANSPORTER_FAMILIES = {
    "ABCB1": "P-gp", "ABCG2": "BCRP", "ABCC1": "MRP1", "ABCC2": "MRP2",
    "SLCO1B1": "OATP1B1", "SLCO1B3": "OATP1B3", "SLC22A1": "OCT1",
    "SLC22A2": "OCT2", "SLC22A6": "OAT1", "SLC22A8": "OAT3",
    "SLC47A1": "MATE1", "SLC47A2": "MATE2K",
}


def _bucket_enzymes(enzymes: list[dict]) -> dict:
    """Group enzymes by pharmacological role; a gene may be in several buckets."""
    buckets = {"substrate_of": [], "inhibits": [], "induces": [], "other": []}
    detail = []
    for e in enzymes:
        gene = e.get("gene_name") or e.get("name")
        actions = [a.lower() for a in (e.get("actions") or [])]
        detail.append({"gene": gene, "uniprot_id": e.get("uniprot_id"), "actions": e.get("actions") or []})
        if "substrate" in actions:
            buckets["substrate_of"].append(gene)
        if "inhibitor" in actions:
            buckets["inhibits"].append(gene)
        if "inducer" in actions:
            buckets["induces"].append(gene)
        if not actions:
            buckets["other"].append(gene)
    return {"buckets": buckets, "detail": detail}


def _transporters(items: list[dict]) -> list[dict]:
    out = []
    for t in items:
        gene = t.get("gene_name") or t.get("name")
        out.append({"gene": gene, "uniprot_id": t.get("uniprot_id"),
                    "family": TRANSPORTER_FAMILIES.get(gene),
                    "actions": t.get("actions") or []})
    return out


@timed
def run(payload) -> dict:
    """payload: str | {drug|drugbank_id|inchi_key|name|smiles, with_api?}."""
    ent = resolve_drug(payload if isinstance(payload, dict) else {"drug": payload})
    out = {"tool": "drugbank_metabolism_query",
           "drug": {k: ent.get(k) for k in ("name", "inchi_key", "drugbank_id")},
           "enzymes": {}, "transporters": [], "carriers": [],
           "metabolism_text": None, "elimination_routes": []}
    did = ent.get("drugbank_id")
    if not did:
        out["error"] = "drug not resolved to a DrugBank ID; metabolism is DrugBank-sourced"
        return out
    from pertox_agent.tools.shared.common import _drugbank
    db = _drugbank()
    rec = db.get_record(did) or {}
    out["enzymes"] = _bucket_enzymes(rec.get("enzymes", []))
    out["transporters"] = _transporters(rec.get("transporters", []))
    out["carriers"] = _transporters(rec.get("carriers", []))
    out["metabolism_text"] = rec.get("metabolism")
    # crude elimination cues from the free text
    text = (rec.get("metabolism") or "").lower()
    for route in ("renal", "urine", "biliary", "fecal", "hepatic"):
        if route in text:
            out["elimination_routes"].append(route)
    # API supplement: openFDA label PK section (cache-first; degrade silently).
    if isinstance(payload, dict) and payload.get("with_api") and ent.get("name"):
        js = api_get("https://api.fda.gov/drug/label.json",
                     {"search": f'openfda.substance_name:"{ent["name"]}"', "limit": 1},
                     cache_subdir="openfda_label")
        if js and js.get("results"):
            r0 = js["results"][0]
            out["_label_pharmacokinetics"] = (r0.get("pharmacokinetics") or
                                              r0.get("clinical_pharmacology") or [None])[0]
    return out


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="DrugBank metabolism / transport query")
    ap.add_argument("drug", help="DrugBank ID, name, SMILES, or InChIKey")
    ap.add_argument("--api", action="store_true", help="also probe openFDA label")
    args = ap.parse_args(argv)
    print(json.dumps(run({"drug": args.drug, "with_api": args.api}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

