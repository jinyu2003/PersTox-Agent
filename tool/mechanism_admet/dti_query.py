"""dti_query — drug-target interactions for a drug.

临床输入: 药物 (DrugBank ID / SMILES / 名称 / InChIKey)
临床输出: 靶点列表 + 亲和力 (IC50/Ki/Kd, 来自 PersADE Affinities / ChEMBL) +
          作用类型 (DrugBank pharmacological action / PersADE Affect) +
          on-target / off-target 标注 + 证据级别。
数据源:
  - 本地: DrugBank targets (uniprot_id+gene+actions+known_action) [T2-3];
          PersADE DTI (Uniprot_ID<-InChIkey, Affinities, Affect, PubMed) [自有]
  - API 补充: ChEMBL molecule (确证存在性 / 长尾靶点) [实验 T2]

on/off-target 规则: DrugBank known_action=='yes' 或 actions 非空 -> on-target
(治疗性主作用);否则按 off-target (脱靶) 标注。证据级别: DrugBank/实验=high,
PersADE DTI=medium(信号), 仅 ChEMBL 命中=experimental。
"""
from __future__ import annotations

import argparse

from tool.common import (DTI, PERSADE, TARGET, api_get, resolve_drug, scan_tsv, timed)


def _drugbank_targets(drugbank_id: str) -> list[dict]:
    """DrugBank targets with pharmacological action -> on/off-target."""
    from tool.common import _drugbank
    db = _drugbank()
    out = []
    for t in db.get_targets(drugbank_id):
        actions = t.get("actions", [])
        on = bool(actions) or t.get("known_action") == "yes"
        out.append({"uniprot_id": t.get("uniprot_id"), "gene": t.get("gene_name"),
                    "name": t.get("name"), "actions": actions,
                    "target_class": "on-target" if on else "off-target",
                    "affinity": None, "affect": None,
                    "evidence_level": "high", "source": "DrugBank"})
    return out


def _persade_dti(inchikey: str) -> list[dict]:
    """PersADE DTI rows: Uniprot_ID, Affinities, Affect, PubMed. Enriched with
    gene/protein name from Target.tsv. PersADE DTI is a broad drug-gene
    interaction set (much of it indirect/regulatory, CTD-style 'Affect'), so a
    row is only called on-target when it carries a real binding constant
    (Kd/Ki/IC50); otherwise it is off-target (interaction without proven direct
    binding). DrugBank pharmacological action overrides this during merge."""
    out = []
    for c in scan_tsv(PERSADE / "DTI.txt", DTI["InChIkey"], inchikey):
        up = c[DTI["Uniprot_ID"]]
        trow = next(scan_tsv(PERSADE / "Target.tsv", TARGET["Uniprot_ID"], up, limit=1), None)
        gene = trow[TARGET["Gene_name_primary"]] if trow and len(trow) > TARGET["Gene_name_primary"] else None
        affect = c[DTI["Affect"]] if len(c) > DTI["Affect"] else None
        affinity = c[DTI["Affinities"]].strip() if len(c) > DTI["Affinities"] else ""
        has_binding = any(k in affinity for k in ("Kd", "Ki", "IC50", "EC50"))
        out.append({"uniprot_id": up, "gene": gene,
                    "name": (trow[TARGET["Protein_name"]] if trow else None),
                    "actions": [], "target_class": "on-target" if has_binding else "off-target",
                    "affinity": affinity or None,
                    "affect": affect, "evidence_level": "medium", "source": "PersADE/DTI"})
    return out


def _merge(rows: list[dict]) -> list[dict]:
    """Merge target rows by uniprot_id, preferring DrugBank identity but keeping
    PersADE affinity/affect and the strongest evidence level."""
    order = {"high": 3, "medium": 2, "experimental": 1, None: 0}
    by_up: dict[str, dict] = {}
    for r in rows:
        up = r.get("uniprot_id")
        if not up:
            continue
        cur = by_up.get(up)
        if cur is None:
            by_up[up] = dict(r)
            continue
        cur["gene"] = cur.get("gene") or r.get("gene")
        cur["name"] = cur.get("name") or r.get("name")
        cur["affinity"] = cur.get("affinity") or r.get("affinity")
        cur["affect"] = cur.get("affect") or r.get("affect")
        cur["actions"] = cur.get("actions") or r.get("actions")
        if any(a for a in cur["actions"]) or cur["target_class"] == "on-target" or r["target_class"] == "on-target":
            cur["target_class"] = "on-target"
        if order.get(r["evidence_level"], 0) > order.get(cur["evidence_level"], 0):
            cur["evidence_level"] = r["evidence_level"]
        cur["source"] = cur["source"] + "+" + r["source"] if r["source"] not in cur["source"] else cur["source"]
    return list(by_up.values())


@timed
def run(payload) -> dict:
    """payload: str | {drug|drugbank_id|inchi_key|name|smiles, with_api?}."""
    ent = resolve_drug(payload if isinstance(payload, dict) else {"drug": payload})
    out = {"tool": "dti_query",
           "drug": {k: ent.get(k) for k in ("name", "inchi_key", "drugbank_id")},
           "n_targets": 0, "on_target": [], "off_target": [], "targets": []}
    rows = []
    if ent.get("drugbank_id"):
        rows += _drugbank_targets(ent["drugbank_id"])
    if ent.get("inchi_key"):
        rows += _persade_dti(ent["inchi_key"])
    merged = _merge(rows)
    # API supplement: ChEMBL existence/long-tail (cache-first; degrade silently).
    if (isinstance(payload, dict) and payload.get("with_api")) and ent.get("name"):
        js = api_get("https://www.ebi.ac.uk/chembl/api/data/molecule.json",
                     {"pref_name__iexact": ent["name"]}, cache_subdir="chembl_molecule")
        if js and js.get("molecules"):
            out["_chembl"] = {"chembl_id": js["molecules"][0].get("molecule_chembl_id"),
                              "max_phase": js["molecules"][0].get("max_phase")}
    merged.sort(key=lambda r: (r["target_class"] != "on-target", r.get("gene") or "z"))
    out["targets"] = merged
    out["n_targets"] = len(merged)
    out["on_target"] = [r["gene"] or r["uniprot_id"] for r in merged if r["target_class"] == "on-target"]
    out["off_target"] = [r["gene"] or r["uniprot_id"] for r in merged if r["target_class"] == "off-target"]
    return out


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="drug-target interaction query")
    ap.add_argument("drug", help="DrugBank ID, name, SMILES, or InChIKey")
    ap.add_argument("--api", action="store_true", help="also probe ChEMBL (cache-first)")
    args = ap.parse_args(argv)
    print(json.dumps(run({"drug": args.drug, "with_api": args.api}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
