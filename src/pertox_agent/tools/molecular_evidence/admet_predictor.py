"""admetsar_predict — ADMET endpoint prediction for a molecule.

临床输入: 分子结构 (SMILES;也接受 name/DrugBank ID/InChIKey,内部解析)
临床输出: 108 个 ADMET 端点预测值 (分类概率 / 回归值) + drug-likeness +
          理化描述符 + 适用域(AD)标志 (是否在 admetSAR 训练表内 = in-domain);
          含肝毒(DILI)、hERG/QT、Ames 致突变、BBB、致癌、线粒体等毒性端点。
数据源:
  - 本地: admetSAR 3.0 (data/admetsar3_all_endpoints.txt, 104651 化合物 x 122 列)
          —— 按 RDKit InChIKey 匹配 (admetSAR 用自有 SMILES 规范化,精确串匹配会漏)
  - API 补充: ADMETlab 3.0 (第二预测器;当前为可选,失败则降级,输出 _api_note)

admetSAR 是预测库:命中=in-domain(已有该分子的预测);未命中=out-of-domain,
工具回退到结构描述符即时计算 (RDKit) 并标注 applicability_domain=False。
"""
from __future__ import annotations

import argparse

from pertox_agent.tools.shared.common import ADMETSAR, api_post, resolve_drug, smiles_to_inchikey, timed

# ADMETlab 3.0 单分子预测端点 (registration-free, CC-BY-NC-SA)。官方后端偶发不稳定
# (对部分请求抛 KeyError:'BSEP' 等 5xx),工具据此优雅降级并在 _api_note 标注。
ADMETLAB_URL = "https://admetlab3.scbdd.com/api/single/admet"

# 关键毒性端点 -> (机制组, 器官系统)。仅标注毒理学承重端点;其余作为通用 ADMET 特征带出。
TOX_ENDPOINTS = {
    "label_DILI_t": ("hepatotoxicity", "Digestive/Hepatobiliary"),
    "label_hERG_1": ("hERG/QT", "Cardiovascular"),
    "label_hERG_10": ("hERG/QT", "Cardiovascular"),
    "label_hERG_30": ("hERG/QT", "Cardiovascular"),
    "label_hERG_1_10": ("hERG/QT", "Cardiovascular"),
    "label_hERG_10_30": ("hERG/QT", "Cardiovascular"),
    "label_Mito_t": ("mitochondrial", "Digestive/Hepatobiliary"),
    "label_Ames_t": ("mutagenicity", "Chemically-induced"),
    "label_Gene_MN_t": ("genotoxicity", "Chemically-induced"),
    "label_Carc_Mouse_C_final": ("carcinogenicity", "Neoplasms"),
    "label_Carc_Rat_C_final": ("carcinogenicity", "Neoplasms"),
    "label_Repro_toxic": ("reproductive", "Urogenital"),
    "label_Skin_sen": ("skin sensitisation", "Skin"),
    "label_Resp_wzy": ("respiratory", "Respiratory"),
    "label_Hemolytic_t": ("hemolytic", "Blood/Lymphatic"),
    "label_Neural_t": ("neurotoxicity", "Nervous system"),
    "label_BBB_gyx_final": ("BBB penetration", "Nervous system"),
}
DESCRIPTOR_COLS = ("MW", "HBA", "HBD", "nRot", "TPSA", "SlogP", "nRing", "nAtom", "nHet", "QED")
DRUGLIKENESS_COLS = ("Lipinski rule", "Pfizer rule", "GSK rule")


def _load_admetsar_row(inchikey: str):
    """Find the admetSAR row whose RDKit InChIKey matches. Returns (header, row)
    or (header, None) if the compound is out-of-domain (absent)."""
    with ADMETSAR.open(encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if smiles_to_inchikey(cols[0]) == inchikey:
                return header, cols
    return header, None


def _classify(endpoint: str) -> dict:
    grp, organ = TOX_ENDPOINTS.get(endpoint, (None, None))
    return {"mechanism_group": grp, "organ": organ, "is_tox_endpoint": grp is not None}


def _admetlab_predict(smiles: str) -> dict:
    """ADMETlab 3.0 second predictor (cache-first POST). Returns {endpoints:{...}}
    on success, or {_api_note: <reason>} when the upstream is unavailable/unstable
    so the caller keeps local admetSAR results and just annotates the gap."""
    if not smiles:
        return {"_api_note": "no SMILES to query ADMETlab"}
    raw = api_post(ADMETLAB_URL, {"data": {"SMILES": smiles}}, cache_subdir="admetlab3",
                   timeout=20, retries=1)
    if raw is None:
        return {"_api_note": "ADMETlab 3.0 unavailable (offline or upstream 5xx); local-only"}
    # successful single-SMILES response is a dict (or [dict]) of endpoint->value
    rec = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(rec, dict) or not rec:
        return {"_api_note": "ADMETlab 3.0 returned an unrecognized payload; local-only"}
    eps = {k: v for k, v in rec.items() if k.lower() not in ("smiles", "index", "taskid")}
    return {"endpoints": eps, "n_endpoints": len(eps)}


@timed
def run(payload) -> dict:
    """payload: str | {drug|smiles|name|inchi_key|drugbank_id, with_api?}.
    with_api=True adds ADMETlab 3.0 as a (down-weighted, predicted-T3) second
    predictor; failures degrade silently to the local admetSAR result."""
    if isinstance(payload, dict):
        with_api = payload.get("with_api", False)
    else:
        with_api = False
    q = {"smiles": payload} if isinstance(payload, str) and " " not in payload and "(" in payload else payload
    ent = resolve_drug(q if isinstance(q, dict) else {"drug": payload})
    ik = ent.get("inchi_key")
    out = {"tool": "admetsar_predict", "drug": {k: ent.get(k) for k in ("name", "inchi_key", "smiles", "drugbank_id")},
           "applicability_domain": False, "descriptors": {}, "drug_likeness": {},
           "n_endpoints": 0, "endpoints": [], "tox_highlights": []}
    if not ik:
        out["error"] = "could not resolve molecule to an InChIKey"
        return out
    header, row = _load_admetsar_row(ik)
    if row is None:
        out["_note"] = "out-of-domain: molecule not in admetSAR3 table; only identity returned"
        if with_api:
            out["admetlab3"] = _admetlab_predict(ent.get("smiles"))
        return out
    out["applicability_domain"] = True
    idx = {h: i for i, h in enumerate(header)}
    out["descriptors"] = {k: row[idx[k]] for k in DESCRIPTOR_COLS if k in idx and row[idx[k]] != ""}
    out["drug_likeness"] = {k: row[idx[k]] for k in DRUGLIKENESS_COLS if k in idx}
    for i in range(14, len(header)):
        val = row[i] if i < len(row) else ""
        if val == "":
            continue  # missing != negative
        rec = {"endpoint": header[i], "value": val, **_classify(header[i])}
        out["endpoints"].append(rec)
        if rec["is_tox_endpoint"]:
            out["tox_highlights"].append(rec)
    out["n_endpoints"] = len(out["endpoints"])
    if with_api:
        out["admetlab3"] = _admetlab_predict(ent.get("smiles"))
    return out


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="admetSAR ADMET prediction")
    ap.add_argument("drug", help="SMILES, name, DrugBank ID, or InChIKey")
    ap.add_argument("--with-api", action="store_true",
                    help="also query ADMETlab 3.0 (second predictor; degrades if upstream down)")
    args = ap.parse_args(argv)
    print(json.dumps(run({"drug": args.drug, "with_api": args.with_api}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

