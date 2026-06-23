"""mechanism_query — reverse ADE -> protein -> pathway mechanism chain.

临床输入: ADE (UMLS ID;也接受 MedDRA PT 名称,内部经 ADE_Information 解析) 或
          药物 (DrugBank ID/SMILES/名称) + 可选器官
临床输出: ADE -> 蛋白(靶点) -> 通路 反向机制链 + 关联严重度 + 证据(PubMed)。
数据源:
  - 本地: PersADE DTA (Drug-Target-ADE 三元组: Uniprot_ID, InChIkey, UMLS, Type, PubMed) [自有];
          Target.tsv (蛋白名/基因) ; uniprot_pathway.txt (蛋白->通路) ;
          ADE_Information.txt (UMLS->名称/MeSH/tree/严重度) [T2,等价 ADReCS/ADReCS-Target]
  - API 补充: Open Targets (靶点-疾病关联, 可选;失败降级)

两种模式:
  (1) 给 ADE(UMLS): DTA 反查关联蛋白 -> 每个蛋白的通路 -> 机制链。
  (2) 给 药物(+器官): 该药 InChIKey 的 DTA 三元组 -> 按器官(MeSH-tree)过滤 ADE ->
      汇总每个 ADE 的介导蛋白/通路。
"""
from __future__ import annotations

import argparse

from tool.common import (ADE, DTA, PERSADE, TARGET, TPATH, resolve_drug, scan_tsv, timed)

# MeSH tree top-level -> coarse organ bucket (fallback; no UMLS->MedDRA SOC crosswalk deployed)
MESH_C_BUCKET = {
    "C01": "Infections", "C04": "Neoplasms", "C06": "Digestive/Hepatobiliary",
    "C14": "Cardiovascular", "C15": "Blood/Lymphatic", "C16": "Congenital",
    "C10": "Nervous system", "C12": "Urogenital", "C13": "Urogenital",
    "C17": "Skin", "C19": "Endocrine", "C20": "Immune", "C23": "Pathological signs",
    "C25": "Chemically-induced",
}


def _organ_bucket(tree_number: str) -> str:
    if not tree_number:
        return "unknown"
    first = tree_number.split("|")[0].strip()
    return MESH_C_BUCKET.get(first[:3], f"MeSH:{first[:3]}")


def _ade_info(umls_ids: set) -> dict:
    """Batch UMLS -> {name, mesh, tree, severity} from ADE_Information.txt."""
    out = {}
    if not umls_ids:
        return out
    with (PERSADE / "ADE_Information.txt").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if c and c[0] in umls_ids:
                out[c[0]] = {"name": (c[ADE["Name"]].split("|")[0] if len(c) > ADE["Name"] else ""),
                             "mesh": c[ADE["MeSH"]] if len(c) > ADE["MeSH"] else "",
                             "tree": c[ADE["Tree_number"]] if len(c) > ADE["Tree_number"] else "",
                             "severity": c[ADE["severity_grade"]] if len(c) > ADE["severity_grade"] else ""}
                if len(out) == len(umls_ids):
                    break
    return out


def _protein_meta(uniprot: str) -> dict:
    trow = next(scan_tsv(PERSADE / "Target.tsv", TARGET["Uniprot_ID"], uniprot, limit=1), None)
    gene = trow[TARGET["Gene_name_primary"]] if trow and len(trow) > TARGET["Gene_name_primary"] else None
    name = trow[TARGET["Protein_name"]] if trow else None
    prow = next(scan_tsv(PERSADE / "uniprot_pathway.txt", TPATH["UniProt_ID"], uniprot, limit=1), None)
    pws = []
    if prow and len(prow) > TPATH["Pathway_ID"]:
        for p in prow[TPATH["Pathway_ID"]].split("|"):
            if p.split(":", 1)[0] in ("KEGG", "Reactome", "MSigDB_Hallmark"):
                pws.append(p)
    return {"gene": gene, "protein": name, "pathways_sample": pws[:5]}


def _by_ade(umls: str) -> dict:
    """Mode 1: ADE(UMLS) -> mediating proteins -> pathways."""
    info = _ade_info({umls})
    proteins = {}
    for c in scan_tsv(PERSADE / "DTA.txt", DTA["UMLS"], umls):
        up = c[DTA["Uniprot_ID"]]
        proteins.setdefault(up, {"uniprot_id": up, "type": c[DTA["Type"]] if len(c) > DTA["Type"] else None,
                                 "pubmed": c[DTA["PubMed_DAT"]] if len(c) > DTA["PubMed_DAT"] else None})
    chains = []
    for up, base in list(proteins.items())[:25]:
        chains.append({**base, **_protein_meta(up)})
    meta = info.get(umls, {})
    return {"mode": "ade", "ade": {"umls": umls, "name": meta.get("name"),
            "organ_bucket": _organ_bucket(meta.get("tree", "")), "severity": meta.get("severity")},
            "n_proteins": len(proteins), "mechanism_chains": chains}


def _by_drug(ent: dict, organ: str | None) -> dict:
    """Mode 2: drug(+organ) -> DTA triplets -> ADEs grouped by mediating protein."""
    ik = ent.get("inchi_key")
    triplets = list(scan_tsv(PERSADE / "DTA.txt", DTA["InChIkey"], ik)) if ik else []
    umls_ids = {c[DTA["UMLS"]] for c in triplets}
    info = _ade_info(umls_ids)
    chains = []
    for c in triplets:
        umls = c[DTA["UMLS"]]
        meta = info.get(umls, {})
        bucket = _organ_bucket(meta.get("tree", ""))
        if organ and organ.lower() not in bucket.lower():
            continue
        up = c[DTA["Uniprot_ID"]]
        pm = _protein_meta(up)
        chains.append({"ade": {"umls": umls, "name": meta.get("name"), "organ_bucket": bucket,
                               "severity": meta.get("severity")},
                       "uniprot_id": up, "gene": pm["gene"], "protein": pm["protein"],
                       "pathways_sample": pm["pathways_sample"],
                       "pubmed": c[DTA["PubMed_DAT"]] if len(c) > DTA["PubMed_DAT"] else None})
    return {"mode": "drug", "drug": {k: ent.get(k) for k in ("name", "inchi_key")},
            "organ_filter": organ, "n_triplets": len(triplets),
            "n_chains": len(chains), "mechanism_chains": chains[:40]}


@timed
def run(payload) -> dict:
    """payload: {umls:...} for ADE mode, or {drug:..., organ?:...} for drug mode.
    A bare string starting with 'C' + digits is treated as a UMLS id."""
    if isinstance(payload, str):
        payload = ({"umls": payload} if payload[:1] == "C" and payload[1:].isdigit()
                   else {"drug": payload})
    out = {"tool": "mechanism_query"}
    if payload.get("umls"):
        out.update(_by_ade(payload["umls"]))
    else:
        ent = resolve_drug(payload)
        out.update(_by_drug(ent, payload.get("organ")))
    return out


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="reverse ADE->protein->pathway mechanism")
    ap.add_argument("query", help="UMLS id (ADE mode) or drug ref (drug mode)")
    ap.add_argument("--organ", help="organ filter (drug mode), e.g. Hepatobiliary")
    args = ap.parse_args(argv)
    pl = {"umls": args.query} if args.query[:1] == "C" and args.query[1:].isdigit() else {"drug": args.query}
    if args.organ:
        pl["organ"] = args.organ
    print(json.dumps(run(pl), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
