"""persade_subgroup_scores — stratified population ADE risk for a patient.

临床输入: 药物 (DrugBank ID/SMILES/名称/InChIKey) + Stage1 候选 ADE (UMLS) +
          患者画像子组 (route / form / age_group / sex / indication UMLS CUIs)。
临床输出: 每个候选 ADE 的「总体 drug-ADE 风险」+「患者分层(途径/剂型/适应症/年龄/性别)证据」
          + contextual_risk_shift(分层 total_score − 总体 total_score) + uncertainty。
数据源 (本地, PersADE 分层打分表; Drug_ID=InChIKey, Reaction_ID=ADE UMLS):
  - CCombined_Results_with_scores.txt        总体 (ROR/total_score/case + sex_dis/age_dis 分布)
  - CCombined_Results_route_with_scores.txt  给药途径分层 total_score/ROR
  - CCombined_Results_form_with_scores.txt   剂型分层
  - CCombined_Results_INDI_with_scores.txt   适应症分层 (INDI_ID=适应症 UMLS CUI)

策略: 每个文件按 Drug_ID==InChIKey 流式过滤一次 (GB 级), 结果按 InChIKey 缓存到
data/cache/persade_subgroups/<ik>.<dim>.jsonl; 热调用直接读缓存。route/form/indi 仅在患者
提供对应子组时才扫。contextual_risk_shift 取最具体的匹配子组 (indication > form > route);
分层病例数少则 uncertainty 升高 —— 本工具只产证据, 不直接修正概率 (留给风险叠加步骤)。
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Dict, List, Optional

from pertox_agent.tools.shared.common import (ASS, ASS_FORM, ASS_INDI, ASS_ROUTE, CACHE, PERSADE,
                         resolve_drug, timed)

SUBGROUP_DIR = CACHE / "persade_subgroups"

_OVERALL_FILE = PERSADE / "CCombined_Results_with_scores.txt"
_ROUTE_FILE = PERSADE / "CCombined_Results_route_with_scores.txt"
_FORM_FILE = PERSADE / "CCombined_Results_form_with_scores.txt"
_INDI_FILE = PERSADE / "CCombined_Results_INDI_with_scores.txt"

_DIST_TOKEN = re.compile(r"([^();]+)\(([\d.]+)%,(\d+)\)")


def _f(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _col(row: List[str], idx: int) -> Optional[str]:
    return row[idx] if len(row) > idx and row[idx] != "" else None


def _parse_distribution(raw: Optional[str]) -> Dict[str, Dict[str, float]]:
    """'Male(66.67%,2);Female(33.33%,1)' -> {'male': {pct, count}, ...}."""
    out: Dict[str, Dict[str, float]] = {}
    for label, pct, count in _DIST_TOKEN.findall(raw or ""):
        out[label.strip().lower()] = {"pct": _f(pct) or 0.0, "count": int(count)}
    return out


# --------------------------------------------------------------------------- #
# Per-InChIKey caches (built by one streaming pass over each GB-scale file)
# --------------------------------------------------------------------------- #
def _cache_path(inchikey: str, dim: str):
    return SUBGROUP_DIR / f"{inchikey}.{dim}.jsonl"


def _load_cache(inchikey: str, dim: str) -> Optional[List[dict]]:
    path = _cache_path(inchikey, dim)
    if not path.exists():
        return None
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def _write_cache(inchikey: str, dim: str, rows: List[dict]) -> None:
    SUBGROUP_DIR.mkdir(parents=True, exist_ok=True)
    with _cache_path(inchikey, dim).open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_overall(inchikey: str) -> Dict[str, dict]:
    cached = _load_cache(inchikey, "overall")
    if cached is None:
        cached = []
        with _OVERALL_FILE.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                c = line.rstrip("\n").split("\t")
                if len(c) <= ASS["age_dis"] or c[ASS["Drug_ID"]] != inchikey:
                    continue
                cached.append({
                    "rid": c[ASS["Reaction_ID"]],
                    "ror": _f(_col(c, ASS["ROR"])),
                    "prr": _f(_col(c, ASS["PRR"])),
                    "total_score": _f(_col(c, ASS["total_score"])),
                    "case_number": _f(_col(c, ASS["Case_number"])),
                    "severity_grade": _col(c, ASS["severity_grade"]),
                    "sex_dis": _col(c, ASS["sex_dis"]),
                    "age_dis": _col(c, ASS["age_dis"]),
                })
        _write_cache(inchikey, "overall", cached)
    return {row["rid"]: row for row in cached}


def _build_stratified(inchikey: str, dim: str, file, cmap: dict, key_field: str) -> Dict[tuple, dict]:
    """Generic per-IK loader for route/form/indi tables -> {(key, rid): row}."""
    cached = _load_cache(inchikey, dim)
    if cached is None:
        cached = []
        with file.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                c = line.rstrip("\n").split("\t")
                if len(c) <= cmap["total_score"] or c[cmap["Drug_ID"]] != inchikey:
                    continue
                cached.append({
                    "key": c[cmap[key_field]].strip().upper(),
                    "rid": c[cmap["Reaction_ID"]],
                    "total_score": _f(_col(c, cmap["total_score"])),
                    "ror": _f(_col(c, cmap["ROR"])),
                    "case_number": _f(_col(c, cmap["Case_number"])),
                })
        _write_cache(inchikey, dim, cached)
    return {(row["key"], row["rid"]): row for row in cached}


# --------------------------------------------------------------------------- #
# Risk-shift + uncertainty
# --------------------------------------------------------------------------- #
def _uncertainty(case_number: Optional[float]) -> float:
    if case_number is None or case_number < 3:
        return 0.5
    if case_number < 10:
        return 0.3
    return 0.1


def _strat_entry(index: Dict[tuple, dict], key: Optional[str], rid: str) -> Optional[dict]:
    if not key:
        return None
    row = index.get((key.strip().upper(), rid))
    if not row:
        return None
    return {"key": row["key"], "total_score": row["total_score"],
            "ror": row["ror"], "case_number": row["case_number"]}


@timed
def run(payload) -> dict:
    """payload: {drug|inchi_key, candidate_ades:[umls|{ade_id,soc}], subgroups:{...}}."""
    payload = payload if isinstance(payload, dict) else {"drug": payload}
    ent = resolve_drug(payload.get("inchi_key") and {"inchi_key": payload["inchi_key"]}
                       or payload.get("drug") or payload)
    ik = ent.get("inchi_key") or payload.get("inchi_key")
    subgroups = dict(payload.get("subgroups") or {})

    out = {
        "tool": "persade_subgroup_scores",
        "drug": {k: ent.get(k) for k in ("name", "inchi_key", "drugbank_id")},
        "subgroups_requested": subgroups,
        "persade_contextual_evidence": [],
    }
    if not ik:
        out["error"] = "could not resolve drug to an InChIKey"
        return out

    # Candidate ADEs (Reaction_IDs) + optional SOC, from Stage 1 (or fall back).
    candidate_soc: Dict[str, Optional[str]] = {}
    for item in payload.get("candidate_ades") or []:
        if isinstance(item, dict):
            rid = item.get("ade_id") or item.get("umls") or item.get("rid")
            if rid:
                candidate_soc[str(rid)] = item.get("soc")
        elif item:
            candidate_soc[str(item)] = None
    if not candidate_soc:
        from pertox_agent.tools.real_world_evidence import persade_drug_ade_profile
        for ade in persade_drug_ade_profile.run({"drug": ik, "top": 40}).get("top_ades", []):
            candidate_soc[str(ade["ade_id"])] = ade.get("soc") or ade.get("organ")

    overall = _build_overall(ik)
    route_idx = _build_stratified(ik, "route", _ROUTE_FILE, ASS_ROUTE, "Route") if subgroups.get("route") else {}
    form_idx = _build_stratified(ik, "form", _FORM_FILE, ASS_FORM, "Form") if subgroups.get("form") else {}
    indi_idx = _build_stratified(ik, "indi", _INDI_FILE, ASS_INDI, "INDI_ID") if subgroups.get("indication_cuis") else {}

    age_group = (subgroups.get("age_group") or "").strip().lower()
    sex = (subgroups.get("sex") or "").strip().lower()

    evidence: List[dict] = []
    for rid, soc in candidate_soc.items():
        base = overall.get(rid)
        if not base:
            continue

        route_hit = _strat_entry(route_idx, subgroups.get("route"), rid)
        form_hit = _strat_entry(form_idx, subgroups.get("form"), rid)
        indi_hit = None
        for cui in subgroups.get("indication_cuis") or []:
            indi_hit = _strat_entry(indi_idx, cui, rid)
            if indi_hit:
                break

        sex_dist = _parse_distribution(base.get("sex_dis"))
        age_dist = _parse_distribution(base.get("age_dis"))
        age_entry = age_dist.get(age_group)
        sex_entry = sex_dist.get(sex)

        # Most specific matching subgroup drives the contextual shift.
        chosen = indi_hit or form_hit or route_hit
        overall_score = base.get("total_score")
        if chosen and chosen["total_score"] is not None and overall_score is not None:
            contextual_risk_shift = round(chosen["total_score"] - overall_score, 4)
            uncertainty = _uncertainty(chosen["case_number"])
        else:
            contextual_risk_shift = 0.0
            uncertainty = 0.5

        evidence.append({
            "ade_id": rid,
            "soc": soc,
            "overall": {
                "ror": base.get("ror"),
                "total_score": overall_score,
                "case_number": base.get("case_number"),
                "prr": base.get("prr"),
                "severity_grade": base.get("severity_grade"),
            },
            "subgroups": {
                "age": ({"band": age_group.upper(), **age_entry} if age_entry else None),
                "sex": ({"label": sex.capitalize(), **sex_entry} if sex_entry else None),
                "route": route_hit,
                "form": form_hit,
                "indication": indi_hit,
            },
            "contextual_risk_shift": contextual_risk_shift,
            "uncertainty": uncertainty,
        })

    evidence.sort(key=lambda e: abs(e["contextual_risk_shift"]), reverse=True)
    out["persade_contextual_evidence"] = evidence
    out["n_candidates"] = len(candidate_soc)
    out["n_with_overall"] = len(evidence)
    if not evidence:
        out["_note"] = "no overall PersADE score rows matched the candidate ADEs for this drug"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stratified population ADE risk (PersADE score tables)")
    ap.add_argument("drug", help="DrugBank ID, name, SMILES, or InChIKey")
    ap.add_argument("--route")
    ap.add_argument("--form")
    ap.add_argument("--age-group", help="e.g. 60-69YR")
    ap.add_argument("--sex", choices=["Male", "Female"])
    ap.add_argument("--indication", action="append", help="indication UMLS CUI (repeatable)")
    args = ap.parse_args(argv)
    subgroups = {
        "route": args.route, "form": args.form, "age_group": args.age_group,
        "sex": args.sex, "indication_cuis": args.indication or [],
    }
    subgroups = {k: v for k, v in subgroups.items() if v}
    print(json.dumps(run({"drug": args.drug, "subgroups": subgroups}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

