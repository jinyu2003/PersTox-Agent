#!/usr/bin/env python3
"""End-to-end Stage-1 (general toxicity) reasoning trace for PersTox-Agent.

This drives ONE real admetSAR compound through the full Stage-1 path described
in docs/PersTox-Agent_两阶段推理路径与知识库检索设计.md (Steps 1-8), retrieving
from every knowledge source that is actually deployed, and printing the
formatted INPUT and OUTPUT of each step. It is both a smoke test (does each
source return data?) and a worked example of the reasoning chain.

Driver drug: Warfarin (InChIKey PJVWKTKQMONHTI-UHFFFAOYSA-N) — chosen because it
is populated in admetSAR (DILI=1, CYP2C9 substrate), in PersADE (3457 drug-ADE
associations, 54 targets), and is the canonical drug for the API connectors.

Run under the perstox conda env (needs rdkit for the SMILES->InChIKey bridge):
    conda run -n perstox python tests/test_stage1_pipeline.py

-------------------------------------------------------------------------------
SOURCE COVERAGE for Stage 1 (what the doc asks for vs what is deployed):

  DEPLOYED & USED (local):
    - admetSAR3   data/admetsar3_all_endpoints.txt   (Steps 2,3)
    - PersADE     drug_all / ASS_SCORE / ADE_Information / DTI / Target /
                  uniprot_pathway / Pathway              (Steps 1,4,6)
    - DILIrank    data/normalized/dilirank              (Step 5, hepatic gold)
    - MedDRA, CTCAE, HGNC, UniProt, NCBI gene, ATC, CPIC, DPWG
                  data/normalized/*                      (Steps 1,7 support)
  DEPLOYED & USED (API, cache-first via kb_builder.api_cache):
    - PubChem, ChEMBL, RxNorm, ATC, openFDA label, Reactome  (Steps 1,3,5,6)

  SUBSTITUTED (doc names a source we have not deployed; nearest deployed
  source is used instead, and the substitution is logged):
    - DrugBank (drug identity/targets/DDI)  -> PersADE drug_all `Link` xrefs +
                                               PubChem/ChEMBL API
    - Tox21 / ToxCast (stress pathways)     -> admetSAR NR_*/SR_* endpoints
    - LiverTox / DILIst / LTKB (hepatic)    -> DILIrank (local) + admetSAR DILI
    - BindingDB (binding affinity)          -> PersADE DTI Affinities + ChEMBL
    - ADReCS (ADE mechanism)                -> PersADE DTA/DTI mechanism chains

  MISSING (no deployed substitute — flagged as a gap to procure):
    - CredibleMeds (QT/TdP cardiac gold standard) — Step 5 cardiac arm has no
      gold-standard verifier; only admetSAR hERG + PersADE signal available.
    - A UMLS/MeSH -> MedDRA SOC crosswalk. MedDRA is deployed but keyed by
      LLT/PT; PersADE ADEs are keyed by UMLS+MeSH-tree. Organ grouping below
      falls back to the MeSH tree top-level letter, not a true MedDRA SOC.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

DATA = PROJECT_ROOT / "data"
PERSADE = DATA / "PersADE"
ADMETSAR = DATA / "admetsar3_all_endpoints.txt"
NORMALIZED = DATA / "normalized"


# --------------------------------------------------------------------------- #
# Low-level access helpers (all streaming; the big PersADE tables are GB-scale)
# --------------------------------------------------------------------------- #

def rule(ch: str = "-", n: int = 78) -> str:
    return ch * n


def show(title: str, obj) -> None:
    print(f"  {title}: {json.dumps(obj, ensure_ascii=False)}")


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def smiles_to_inchikey(smiles: str) -> str | None:
    from rdkit import Chem
    from rdkit import RDLogger

    RDLogger.logger().setLevel(RDLogger.CRITICAL)
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToInchiKey(mol) if mol else None


def scan_tsv(path: Path, key_col: int, key_val: str, *, limit: int | None = None):
    """Stream a (possibly multi-GB) headerless TSV, yielding rows where
    row[key_col] == key_val. PersADE files have no header line."""
    n = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) > key_col and cols[key_col] == key_val:
                yield cols
                n += 1
                if limit and n >= limit:
                    return


# --------------------------------------------------------------------------- #
# PersADE column indices (0-based, from data/PersADE/Mysql_input.txt CREATE TABLEs)
# --------------------------------------------------------------------------- #
DRUG = {"InChIkey": 0, "Primary_Name": 1, "Name": 4, "SMILES": 5, "Link": 6,
        "MF": 7, "MW": 8, "Type": 17, "ATC": 18, "Indication": 20}
ASS = {"Reaction_ID": 0, "Drug_ID": 1, "PubMed": 3, "Source": 4, "Case_number": 5,
       "Adjust_P": 7, "PRR": 10, "ROR": 11, "ROR_Lower_CI": 12, "total_score": 17,
       "priority": 18, "severity_grade": 20}
ADE = {"UMLS": 0, "Name": 1, "MeSH": 2, "Tree_number": 3, "avg_severity": 6,
       "severity_grade": 7, "PharmGKB": 10}
DTI = {"Uniprot_ID": 0, "InChIkey": 1, "PubMed": 2, "Source": 3, "Affinities": 4, "Affect": 5}
DTA = {"Uniprot_ID": 0, "InChIkey": 1, "UMLS": 2, "Type": 3, "PubMed_DAT": 4}
TARGET = {"Uniprot_ID": 0, "Protein_name": 3, "GeneID": 6, "Gene_name_primary": 18}
TPATH = {"UniProt_ID": 0, "Gene_Symbol": 2, "Pathway_ID": 3}
PATHWAY = {"Source": 0, "Pathway_ID": 1, "Pathway_Name": 2, "Functional_Category": 8}

# MeSH tree top-level letter -> coarse organ/system bucket. This is the FALLBACK
# noted in the header: a real MedDRA SOC crosswalk is not deployed.
MESH_C_BUCKET = {
    "C01": "Infections", "C04": "Neoplasms", "C06": "Digestive/Hepatobiliary",
    "C14": "Cardiovascular", "C15": "Blood/Lymphatic", "C16": "Congenital",
    "C10": "Nervous system", "C12": "Urogenital (male)", "C13": "Urogenital (female)",
    "C17": "Skin", "C19": "Endocrine", "C20": "Immune", "C23": "Pathological signs",
    "C25": "Chemically-induced",
}


def mesh_tree_bucket(tree_number: str) -> str:
    """Map a MeSH tree number (e.g. 'C06.552.308') to a coarse organ bucket."""
    if not tree_number:
        return "unknown"
    first = tree_number.split("|")[0].strip()
    return MESH_C_BUCKET.get(first[:3], f"MeSH:{first[:3]}")


# admetSAR endpoint -> (mechanism_group, organ bucket). Only the toxicologically
# load-bearing endpoints from the doc's "重点端点" table are mapped; the rest are
# carried through as generic ADMET features.
ENDPOINT_MECHANISM = {
    "label_DILI_t": ("hepatotoxicity", "Digestive/Hepatobiliary"),
    "label_hERG_1": ("hERG/QT", "Cardiovascular"),
    "label_hERG_10": ("hERG/QT", "Cardiovascular"),
    "label_hERG_30": ("hERG/QT", "Cardiovascular"),
    "label_Mito_t": ("mitochondrial", "Digestive/Hepatobiliary"),
    "label_Ames_t": ("mutagenicity", "Chemically-induced"),
    "label_Repro_toxic": ("reproductive", "Urogenital (female)"),
    "label_Skin_sen": ("skin sensitisation", "Skin"),
    "label_Resp_wzy": ("respiratory", "Respiratory"),
}
CYP_PREFIXES = ("label_CYP",)
TRANSPORTER_KEYS = ("P-gp", "BSEP", "OATP", "OCT", "MATE", "BCRP", "OAT")


def admet_mechanism_group(endpoint: str) -> str:
    if endpoint in ENDPOINT_MECHANISM:
        return ENDPOINT_MECHANISM[endpoint][0]
    if endpoint.startswith(CYP_PREFIXES):
        return "CYP metabolism"
    if any(k in endpoint for k in TRANSPORTER_KEYS):
        return "transporter"
    if endpoint.startswith("label_NR") or endpoint.startswith("label_SR"):
        return "Tox21 stress pathway"
    return "general_admet"


def load_admetsar_row(smiles_or_inchikey: str):
    """Find the admetSAR row whose InChIKey matches. admetSAR is keyed only by
    SMILES with its own canonicalisation, so we match via RDKit InChIKey."""
    target_ik = (smiles_or_inchikey if "-" in smiles_or_inchikey and
                 len(smiles_or_inchikey) == 27 else smiles_to_inchikey(smiles_or_inchikey))
    with ADMETSAR.open(encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            cols = line.rstrip("\n").split("\t")
            ik = smiles_to_inchikey(cols[0])
            if ik == target_ik:
                return header, cols, ik
    return header, None, target_ik


# --------------------------------------------------------------------------- #
# STAGE 1 STEP FUNCTIONS — each prints its INPUT and OUTPUT and returns a dict.
# --------------------------------------------------------------------------- #

def parse_link_xrefs(link: str) -> dict:
    """drug_all `Link` is 'Source$ID|Source$ID|...' e.g. PubChem$5735|CHEMBL$..."""
    xref = {}
    for part in (link or "").split("|"):
        if "$" in part:
            src, _, val = part.partition("$")
            xref.setdefault(src, val)
    return xref


def step1_drug_normalize(query: dict) -> dict:
    """Step 1: align the input drug to canonical entities via PersADE drug_all.
    Doc names DrugBank here; not deployed -> substitute = PersADE `Link` xrefs
    (PubChem/ChEMBL) + RDKit-derived InChIKey."""
    print(rule()); print("[STAGE1 · Step 1] 药物标准化与实体对齐")
    show("INPUT", query)
    smiles = query.get("smiles")
    ik = query.get("inchi_key") or (smiles_to_inchikey(smiles) if smiles else None)
    row = next(scan_tsv(PERSADE / "drug_all.txt", DRUG["InChIkey"], ik, limit=1), None) if ik else None
    if not row:
        show("OUTPUT", {"status": "not_found_in_persade", "inchi_key": ik})
        return {"inchi_key": ik, "found": False}
    xref = parse_link_xrefs(row[DRUG["Link"]])
    entity = {
        "primary_name": row[DRUG["Primary_Name"]],
        "inchi_key": ik,
        "smiles": row[DRUG["SMILES"]],
        "pubchem_id": xref.get("PubChem"),
        "chembl_id": xref.get("CHEMBL") or xref.get("ChEMBL"),
        "drugbank_id": xref.get("DrugBank"),
        "atc": (row[DRUG["ATC"]] or "").split("|")[0] or None,
        "drug_type": row[DRUG["Type"]] or "unknown",
        "_substitution": "DrugBank not deployed -> PersADE Link xrefs + RDKit InChIKey",
    }
    show("OUTPUT.drug_entity", entity)
    return {**entity, "found": True}


def step2_3_admetsar(header, row, ik) -> dict:
    """Steps 2+3: structure/physchem descriptors + drug-likeness (Step 2) and
    every non-empty ADMET endpoint mapped to a mechanism group (Step 3)."""
    print(rule()); print("[STAGE1 · Step 2-3] 结构/理化性质 + ADMET endpoint 检索 (admetSAR3)")
    show("INPUT", {"inchi_key": ik, "source": "admetsar3_all_endpoints.txt"})
    if row is None:
        show("OUTPUT", {"status": "compound_not_in_admetsar"})
        return {"found": False}
    idx = {name: i for i, name in enumerate(header)}
    descriptors = {k: row[idx[k]] for k in ("MW", "TPSA", "SlogP", "QED", "HBA", "HBD", "nRot")
                   if k in idx and row[idx[k]] != ""}
    drug_likeness = {k: row[idx[k]] for k in ("Lipinski rule", "Pfizer rule", "GSK rule")
                     if k in idx}
    endpoints = []
    for i in range(14, len(header)):
        val = row[i] if i < len(row) else ""
        if val == "":
            continue  # missing != negative (doc Step 3 rule 3)
        ep = header[i]
        endpoints.append({"endpoint": ep, "value": val,
                          "mechanism_group": admet_mechanism_group(ep)})
    show("OUTPUT.structure_profile", {"descriptors": descriptors, "drug_likeness": drug_likeness})
    print(f"  OUTPUT.admet_profile: {len(endpoints)} non-empty endpoints "
          f"(of {len(header) - 14}); key tox endpoints:")
    for ep in endpoints:
        if ep["mechanism_group"] in ("hepatotoxicity", "hERG/QT", "mutagenicity",
                                     "mitochondrial", "CYP metabolism"):
            show("    ", ep)
    return {"found": True, "descriptors": descriptors, "drug_likeness": drug_likeness,
            "admet_profile": endpoints}


def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def ade_info_lookup(umls_ids: set) -> dict:
    """Batch-resolve ADE UMLS -> {name, mesh, tree, severity} from ADE_Information."""
    out = {}
    if not umls_ids:
        return out
    with (PERSADE / "ADE_Information.txt").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if c and c[0] in umls_ids:
                out[c[0]] = {
                    "name": (c[ADE["Name"]].split("|")[0] if len(c) > ADE["Name"] else ""),
                    "mesh": c[ADE["MeSH"]] if len(c) > ADE["MeSH"] else "",
                    "tree": c[ADE["Tree_number"]] if len(c) > ADE["Tree_number"] else "",
                    "severity": c[ADE["severity_grade"]] if len(c) > ADE["severity_grade"] else "",
                }
                if len(out) == len(umls_ids):
                    break
    return out


def step4_known_ade(ik: str) -> dict:
    """Step 4: known drug-ADE spectrum from PersADE ASS_SCORE, filtered to the
    high-confidence signals (ROR_Lower_CI>1 AND priority in High/Medium), then
    enriched with ADE_Information name/MeSH/tree/severity, organ-bucketed via the
    MeSH-tree fallback. Doc names MedDRA SOC mapping; not crosswalked -> fallback."""
    print(rule()); print("[STAGE1 · Step 4] 已知 drug-ADE 谱检索 (PersADE ASS_SCORE)")
    show("INPUT", {"Drug_ID": ik, "filter": "ROR_Lower_CI>1 AND priority∈{High,Medium}"})
    total = 0
    kept = []
    for c in scan_tsv(PERSADE / "CCombined_Results_with_scores.txt", ASS["Drug_ID"], ik):
        total += 1
        rlci = _float(c[ASS["ROR_Lower_CI"]]) if len(c) > ASS["ROR_Lower_CI"] else None
        prio = c[ASS["priority"]] if len(c) > ASS["priority"] else ""
        if rlci is not None and rlci > 1.0 and prio in ("High", "Medium"):
            kept.append(c)
    kept.sort(key=lambda c: _float(c[ASS["ROR"]]) or 0, reverse=True)
    top = kept[:10]
    info = ade_info_lookup({c[ASS["Reaction_ID"]] for c in top})
    profile = []
    for c in top:
        rid = c[ASS["Reaction_ID"]]
        meta = info.get(rid, {})
        profile.append({
            "ade_id": rid, "ade_name": meta.get("name", rid),
            "organ_bucket": mesh_tree_bucket(meta.get("tree", "")),
            "case_number": c[ASS["Case_number"]], "ror": c[ASS["ROR"]],
            "ror_lower_ci": c[ASS["ROR_Lower_CI"]], "priority": c[ASS["priority"]],
            "severity_grade": meta.get("severity") or c[ASS["severity_grade"]],
            "evidence_level": "signal", "source": "PersADE/FAERS",
        })
    print(f"  OUTPUT.known_ade_profile: {total} total associations -> "
          f"{len(kept)} high-confidence -> top {len(profile)} by ROR:")
    for p in profile:
        show("    ", p)
    return {"total": total, "high_conf": len(kept), "known_ade_profile": profile}


def step5_gold_standard(entity: dict, admet: dict) -> dict:
    """Step 5: high-confidence verifiers. Hepatic = DILIrank (deployed local).
    Cardiac = CredibleMeds (NOT deployed) -> flagged as a gap, only admetSAR hERG
    available. Substitutions: LiverTox/DILIst/LTKB -> DILIrank + admetSAR DILI."""
    print(rule()); print("[STAGE1 · Step 5] 专科金标准检索")
    name = (entity.get("primary_name") or "").lower()
    show("INPUT", {"drug": name})
    checks = []

    # Hepatic gold standard: DILIrank (local, deployed)
    dili_hit = None
    for r in load_jsonl(NORMALIZED / "dilirank" / "dilirank.jsonl"):
        if (r.get("compound_name") or "").lower() == name:
            dili_hit = r
            break
    admet_dili = next((e["value"] for e in admet.get("admet_profile", [])
                       if e["endpoint"] == "label_DILI_t"), None)
    if dili_hit:
        checks.append({"soc": "Digestive/Hepatobiliary", "source": "DILIrank",
                       "verdict": "confirmed", "evidence_level": "T1",
                       "details": dili_hit.get("vdili_concern")})
    else:
        checks.append({"soc": "Digestive/Hepatobiliary", "source": "DILIrank",
                       "verdict": "not_in_gold_standard", "evidence_level": "T1",
                       "details": f"absent from DILIrank; admetSAR label_DILI_t={admet_dili} "
                                  f"is a model PREDICTION (signal, not gold standard)"})

    # Cardiac gold standard: CredibleMeds — NOT DEPLOYED
    herg = [e for e in admet.get("admet_profile", []) if e["mechanism_group"] == "hERG/QT"]
    checks.append({"soc": "Cardiovascular", "source": "CredibleMeds",
                   "verdict": "SOURCE_NOT_DEPLOYED", "evidence_level": "T1",
                   "details": f"QT/TdP gold standard missing; only admetSAR hERG available: "
                              f"{[(e['endpoint'], e['value']) for e in herg] or 'none populated'}"})
    show("OUTPUT.gold_standard_checks", checks)
    return {"gold_standard_checks": checks}


def step6_mechanism(ik: str, top_targets: int = 8) -> dict:
    """Step 6: Drug->Target->Pathway mechanism chains from PersADE DTI + Target +
    uniprot_pathway + Pathway. Substitutes for DrugBank/ChEMBL/BindingDB/ADReCS."""
    print(rule()); print("[STAGE1 · Step 6] 靶点/通路机制检索 (PersADE DTI/Target/Pathway)")
    show("INPUT", {"InChIkey": ik})
    targets = [c[DTI["Uniprot_ID"]] for c in scan_tsv(PERSADE / "DTI.txt", DTI["InChIkey"], ik)]
    dta_n = sum(1 for _ in scan_tsv(PERSADE / "DTA.txt", DTA["InChIkey"], ik))
    chains = []
    for up in targets[:top_targets]:
        trow = next(scan_tsv(PERSADE / "Target.tsv", TARGET["Uniprot_ID"], up, limit=1), None)
        gene = trow[TARGET["Gene_name_primary"]] if trow and len(trow) > TARGET["Gene_name_primary"] else None
        prow = next(scan_tsv(PERSADE / "uniprot_pathway.txt", TPATH["UniProt_ID"], up, limit=1), None)
        pw_ids = (prow[TPATH["Pathway_ID"]].split("|")[:3] if prow and len(prow) > TPATH["Pathway_ID"] else [])
        chains.append({"uniprot": up, "gene": gene,
                       "protein": (trow[TARGET["Protein_name"]] if trow else None),
                       "pathways_sample": pw_ids, "evidence_type": "direct_DTI"})
    print(f"  OUTPUT.mechanism: {len(targets)} targets (DTI), {dta_n} Drug-Target-ADE "
          f"triplets (DTA); first {len(chains)} target chains:")
    for ch in chains:
        show("    ", ch)
    return {"n_targets": len(targets), "n_dta": dta_n, "mechanism_chains": chains}


def step7_8_aggregate(entity, admet, known, gold, mech) -> dict:
    """Steps 7+8: fuse ADMET endpoints, PersADE signals, gold-standard verdicts
    and mechanism into per-organ baseline risk, and emit the Stage-1 output that
    feeds Stage-2. Pure rule-based MVP fusion (doc Step 7 priority order)."""
    print(rule()); print("[STAGE1 · Step 7-8] 器官系统聚合 + 第一阶段输出生成")
    organs = {}
    for ep in admet.get("admet_profile", []):
        _, organ = ENDPOINT_MECHANISM.get(ep["endpoint"], (None, None))
        if organ and str(ep["value"]) not in ("0", "0.0"):
            organs.setdefault(organ, {"admet": [], "signals": [], "gold": []})
            organs[organ]["admet"].append(ep["endpoint"])
    for p in known.get("known_ade_profile", []):
        organs.setdefault(p["organ_bucket"], {"admet": [], "signals": [], "gold": []})
        organs[p["organ_bucket"]]["signals"].append(p["ade_name"])
    for g in gold.get("gold_standard_checks", []):
        organs.setdefault(g["soc"], {"admet": [], "signals": [], "gold": []})
        organs[g["soc"]]["gold"].append(f"{g['source']}:{g['verdict']}")

    general_toxicity = []
    for organ, ev in sorted(organs.items()):
        has_gold = any("confirmed" in g for g in ev["gold"])
        score = 0.5 * bool(ev["admet"]) + 0.3 * bool(ev["signals"]) + 0.2 * has_gold
        level = "high" if score >= 0.7 else "moderate" if score >= 0.4 else "low"
        general_toxicity.append({
            "soc": organ, "baseline_risk_level": level, "baseline_probability": round(score, 2),
            "main_drivers": (ev["admet"][:3] + ev["signals"][:2] + ev["gold"][:1]) or ["weak"],
            "attribution": {"admet_endpoint": ev["admet"][:5], "ade_signals": ev["signals"][:5],
                            "gold": ev["gold"]},
        })
    out = {"drug": {"name": entity.get("primary_name"), "inchi_key": entity.get("inchi_key"),
                    "smiles": entity.get("smiles")},
           "general_toxicity": general_toxicity}
    print(f"  OUTPUT.general_toxicity: {len(general_toxicity)} organ systems")
    show("OUTPUT", out)
    return out


def probe_api_sources(entity: dict) -> None:
    """Cache-first probe of the deployed API connectors (Steps 1/3/5/6 external)."""
    print(rule()); print("[STAGE1 · API sources] kb_builder.api_cache (cache-first)")
    from pertox_agent.kb_builder.api_cache import api_examples, cached_get
    cache = DATA / "cache" / "api_responses"
    for name, ex in sorted(api_examples().items()):
        try:
            path = cached_get(url=ex["url"], params=ex.get("params") or {},
                              cache_dir=cache / name, force=False)
            print(f"  [API] {name}: OK ({len(path.read_bytes())} bytes cached)")
        except Exception as exc:  # noqa: BLE001
            print(f"  [API] {name}: unavailable ({type(exc).__name__})")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
# Formatted Stage-1 INPUT (doc §5.2). Warfarin: populated in admetSAR + PersADE.
STAGE1_INPUT = {
    "drug_name": "Warfarin",
    "smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(C3=CC=CC=C3OC2=O)O",
    "drugbank_id": None,
    "inchi_key": None,   # left blank on purpose -> Step 1 derives it via RDKit
    "route": "ORAL",
    "form": "TABLET",
}


def main() -> int:
    print(rule("=")); print("PersTox-Agent · Stage 1 (general toxicity) end-to-end trace")
    print(f"  driver drug : {STAGE1_INPUT['drug_name']}")
    print(rule("="))

    entity = step1_drug_normalize(STAGE1_INPUT)
    if not entity.get("found"):
        print("\nFATAL: driver drug not found in PersADE; cannot continue.")
        return 1
    ik = entity["inchi_key"]

    header, row, _ = load_admetsar_row(ik)
    admet = step2_3_admetsar(header, row, ik)
    known = step4_known_ade(ik)
    gold = step5_gold_standard(entity, admet)
    mech = step6_mechanism(ik)
    step7_8_aggregate(entity, admet, known, gold, mech)
    probe_api_sources(entity)

    # Summary + explicit local-source health gate (API outages are tolerated).
    print("\n" + rule("=")); print("SUMMARY")
    local_ok = {
        "step1_drug_normalize (PersADE drug_all)": entity.get("found"),
        "step2-3_admetsar (admetsar3)": admet.get("found"),
        "step4_known_ade (PersADE ASS_SCORE)": known.get("total", 0) > 0,
        "step5_gold (DILIrank)": True,  # absence is a valid verdict
        "step6_mechanism (PersADE DTI/Target/Pathway)": mech.get("n_targets", 0) > 0,
    }
    for k, v in local_ok.items():
        print(f"  [{'OK' if v else 'FAIL'}] {k}")
    print(rule("="))
    print("Known deployment gaps surfaced by this trace:")
    print("  - CredibleMeds (cardiac QT/TdP gold standard): NOT deployed")
    print("  - UMLS/MeSH -> MedDRA SOC crosswalk: NOT deployed (MeSH-tree fallback used)")
    return 0 if all(local_ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())




