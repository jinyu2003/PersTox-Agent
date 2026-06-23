"""Shared data-access layer for PersTox-Agent tools.

Centralizes: project paths, the SMILES->InChIKey bridge (RDKit), streaming
readers for the GB-scale headerless PersADE TSVs, PersADE column-index maps
(from data/PersADE/Mysql_input.txt), a unified drug resolver (DrugBank +
PersADE drug_all + RDKit), a cache-first API wrapper, and a @timed decorator
used by the tools and the test harness to report tool-call latency.

Raw files under data/raw and data/PersADE are read-only; derived caches are
written under data/cache/.
"""
from __future__ import annotations

import functools
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA = PROJECT_ROOT / "data"
PERSADE = DATA / "PersADE"
ADMETSAR = DATA / "admetsar3_all_endpoints.txt"
NORMALIZED = DATA / "normalized"
RAW = DATA / "raw"
QUARANTINE = DATA / "quarantine_non_t1"
CACHE = DATA / "cache"
API_CACHE_DIR = CACHE / "api_responses"
REACTOME_DIR = RAW / "reactome"
GO_DIR = QUARANTINE / "go"

# --------------------------------------------------------------------------- #
# PersADE column indices (0-based, from data/PersADE/Mysql_input.txt CREATE TABLEs)
# --------------------------------------------------------------------------- #
DRUG = {"InChIkey": 0, "Primary_Name": 1, "Name": 4, "SMILES": 5, "Link": 6,
        "MF": 7, "MW": 8, "Type": 17, "ATC": 18, "Indication": 20}
ASS = {"Reaction_ID": 0, "Drug_ID": 1, "PubMed": 3, "Source": 4, "Case_number": 5,
       "Odds_Ratio": 6, "Adjust_P": 7, "Drug_specific_Frequency": 8, "Overall_Frequency": 9,
       "PRR": 10, "ROR": 11, "ROR_Lower_CI": 12, "ROR_Upper_CI": 13, "total_score": 17,
       "priority": 18, "severity_grade": 20}
ADE = {"UMLS": 0, "Name": 1, "MeSH": 2, "Tree_number": 3, "avg_severity": 6,
       "severity_grade": 7, "PharmGKB": 10}
DTI = {"Uniprot_ID": 0, "InChIkey": 1, "PubMed": 2, "Source": 3, "Affinities": 4, "Affect": 5}
DTA = {"Uniprot_ID": 0, "InChIkey": 1, "UMLS": 2, "Type": 3, "PubMed_DAT": 4}
TARGET = {"Uniprot_ID": 0, "Protein_name": 3, "GeneID": 6, "Gene_name_primary": 18}
TPATH = {"UniProt_ID": 0, "Entrez_ID": 1, "Gene_Symbol": 2, "Pathway_ID": 3, "Pathway_Count": 4}
PATHWAY = {"Source": 0, "Pathway_ID": 1, "Pathway_Name": 2, "Species": 3,
           "Category": 4, "Functional_Category": 8}
# ADE_report.txt (patient/report layer) columns
REPORT = {"Report_ID": 0, "InChIkey": 1, "UMLS": 2, "Route": 3, "Dechal": 4, "Rechal": 5,
          "Event_Date": 8, "Sex": 9, "Age": 10, "Weight": 11, "Outcome": 13, "Serious": 14,
          "SOC": 16, "Drug_name": 17, "ADE_name": 18}


# --------------------------------------------------------------------------- #
# Timing + IO helpers
# --------------------------------------------------------------------------- #
_TIMINGS: list[dict] = []


def timed(fn: Callable) -> Callable:
    """Record wall-clock latency of a tool call into _TIMINGS (for the README
    speed table). The wrapped result dict gets an `_elapsed_ms` field."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        out = fn(*args, **kwargs)
        dt = (time.perf_counter() - t0) * 1000.0
        _TIMINGS.append({"tool": fn.__module__.split(".")[-1], "ms": round(dt, 1)})
        if isinstance(out, dict):
            out.setdefault("_elapsed_ms", round(dt, 1))
        return out
    return wrapper


def get_timings() -> list[dict]:
    return list(_TIMINGS)


def load_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def scan_tsv(path: Path, key_col: int, key_val: str, *, limit: Optional[int] = None) -> Iterator[list]:
    """Stream a (possibly multi-GB) headerless TSV, yielding rows where
    row[key_col] == key_val. PersADE tables have no header line."""
    n = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) > key_col and cols[key_col] == key_val:
                yield cols
                n += 1
                if limit and n >= limit:
                    return


def smiles_to_inchikey(smiles: str) -> Optional[str]:
    """SMILES -> InChIKey via RDKit. This is the ONLY reliable join key between
    admetSAR (its own canonicalization) and PersADE/DrugBank."""
    try:
        from rdkit import Chem, RDLogger
    except ImportError:
        return None
    RDLogger.logger().setLevel(RDLogger.CRITICAL)
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToInchiKey(mol) if mol else None


def is_inchikey(s: str) -> bool:
    return isinstance(s, str) and len(s) == 27 and s[14] == "-" and s[25] == "-"


def parse_link_xrefs(link: str) -> dict:
    """PersADE drug_all `Link` is 'Source$ID|Source$ID|...' e.g. PubChem$5735|CHEMBL$...."""
    xref: dict[str, str] = {}
    for part in (link or "").split("|"):
        if "$" in part:
            src, _, val = part.partition("$")
            xref.setdefault(src, val)
    return xref


# --------------------------------------------------------------------------- #
# Unified drug resolver: any of {name, SMILES, DrugBank ID, InChIKey} -> entity
# --------------------------------------------------------------------------- #
_DB_TOOL = None


def _drugbank():
    """Lazy singleton DrugBankTool (loads the byte-offset index once)."""
    global _DB_TOOL
    if _DB_TOOL is None:
        from tool.drugbank_tool import DrugBankTool
        _DB_TOOL = DrugBankTool()
    return _DB_TOOL


def resolve_drug(query: str | dict) -> dict:
    """Resolve a drug reference to a unified entity. `query` is a string
    (name / SMILES / DrugBank ID / InChIKey) or a dict with any of those keys.

    Returns: {input, inchi_key, smiles, name, drugbank_id, pubchem_id,
              chembl_id, atc, drug_type, _sources}. Missing fields are None.
    Strategy: DrugBank (id/name/inchikey seek) first for identity, then PersADE
    drug_all to fill InChIKey/xrefs, RDKit to derive InChIKey from SMILES."""
    q = {"drug": query} if isinstance(query, str) else dict(query)
    raw = q.get("drug") or q.get("name") or q.get("smiles") or q.get("inchi_key") or ""
    ent = {"input": raw, "inchi_key": q.get("inchi_key"), "smiles": q.get("smiles"),
           "name": q.get("name"), "drugbank_id": q.get("drugbank_id"),
           "pubchem_id": None, "chembl_id": None, "atc": None,
           "drug_type": None, "_sources": []}
    _resolve_via_drugbank(ent, raw, q)
    if ent["smiles"] and not ent["inchi_key"]:
        ent["inchi_key"] = smiles_to_inchikey(ent["smiles"])
    _resolve_via_persade(ent, raw)
    # If a synonym blocked the DrugBank name lookup but PersADE gave us an
    # InChIKey, retry DrugBank by InChIKey to recover the canonical name + DB id.
    if ent.get("inchi_key") and not ent.get("drugbank_id"):
        try:
            rec = _drugbank().get_by_inchikey(ent["inchi_key"])
        except Exception:
            rec = None
        if rec:
            ent["drugbank_id"] = rec.get("drugbank_id")
            ent["name"] = rec.get("name") or ent["name"]
            ent["drug_type"] = ent["drug_type"] or rec.get("type")
            ent["atc"] = ent["atc"] or (rec.get("atc_codes") or [None])[0]
            if "drugbank" not in ent["_sources"]:
                ent["_sources"].append("drugbank")
    return ent


def _resolve_via_drugbank(ent: dict, raw: str, q: dict) -> None:
    """Fill identity from DrugBank by DrugBank ID / InChIKey / exact name."""
    try:
        db = _drugbank()
    except Exception:
        return
    rec = None
    did = q.get("drugbank_id") or (raw if isinstance(raw, str) and raw.upper().startswith("DB") else None)
    if did:
        rec = db.get_record(did)
    if rec is None and (q.get("inchi_key") or (is_inchikey(raw))):
        rec = db.get_by_inchikey(q.get("inchi_key") or raw)
    if rec is None and isinstance(raw, str) and raw and not is_inchikey(raw):
        rec = db.get_by_name(raw)
    if rec is None:
        return
    ent["drugbank_id"] = rec.get("drugbank_id")
    ent["name"] = ent["name"] or rec.get("name")
    ent["drug_type"] = ent["drug_type"] or rec.get("type")
    cp = rec.get("calculated_properties", {})
    ent["smiles"] = ent["smiles"] or cp.get("SMILES")
    ent["inchi_key"] = ent["inchi_key"] or cp.get("InChIKey")
    xr = rec.get("external_ids", {})
    ent["pubchem_id"] = xr.get("PubChem Compound") or xr.get("PubChem")
    ent["chembl_id"] = xr.get("ChEMBL")
    ent["atc"] = (rec.get("atc_codes") or [None])[0]
    ent["_sources"].append("drugbank")


def _resolve_via_persade(ent: dict, raw: str = "") -> None:
    """Fill InChIKey/SMILES/xrefs from PersADE drug_all when still missing.
    `raw` is the original query token, used as a name fallback when ent['name']
    was never set (bare-string input) and the token isn't an InChIKey/SMILES."""
    ik = ent.get("inchi_key")
    row = None
    if ik:
        row = next(scan_tsv(PERSADE / "drug_all.txt", DRUG["InChIkey"], ik, limit=1), None)
    name_q = ent.get("name") or (raw if isinstance(raw, str) and not is_inchikey(raw) else None)
    if row is None and name_q:
        nm = name_q.lower()
        for c in _iter_drug_all_by_name(nm):
            row = c
            break
    if row is None:
        return
    ent["inchi_key"] = ent["inchi_key"] or row[DRUG["InChIkey"]]
    ent["smiles"] = ent["smiles"] or row[DRUG["SMILES"]]
    ent["name"] = ent["name"] or row[DRUG["Primary_Name"]]
    ent["drug_type"] = ent["drug_type"] or (row[DRUG["Type"]] or None)
    ent["atc"] = ent["atc"] or ((row[DRUG["ATC"]] or "").split("|")[0] or None)
    xref = parse_link_xrefs(row[DRUG["Link"]] if len(row) > DRUG["Link"] else "")
    ent["pubchem_id"] = ent["pubchem_id"] or xref.get("PubChem")
    ent["chembl_id"] = ent["chembl_id"] or xref.get("CHEMBL") or xref.get("ChEMBL")
    ent["_sources"].append("persade_drug_all")


def _iter_drug_all_by_name(name_lc: str) -> Iterator[list]:
    """Scan drug_all for a case-insensitive name match. The Name column (idx 4)
    is a '|'-delimited synonym list, so this also matches brand/alt names
    (e.g. 'aspirin' -> the Acetylsalicylic acid row)."""
    with (PERSADE / "drug_all.txt").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) > DRUG["Name"]:
                primary = cols[DRUG["Primary_Name"]].lower()
                synonyms = {s.strip().lower() for s in cols[DRUG["Name"]].split("|")}
                if name_lc == primary or name_lc in synonyms:
                    yield cols


# --------------------------------------------------------------------------- #
# UniProt <-> gene-symbol bridge from the GO GAF (shared by pathway sources that
# key on UniProt, e.g. Reactome). Cached for the process.
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def goa_uniprot_to_symbol() -> dict:
    """{UniProt_acc: gene_symbol} parsed once from goa_human.gaf.gz (col1=acc,
    col2=symbol). Empty dict if the GAF is absent (graceful)."""
    import gzip
    out: dict[str, str] = {}
    gaf = GO_DIR / "goa_human.gaf.gz"
    if not gaf.exists():
        return out
    with gzip.open(gaf, "rt", errors="replace") as f:
        for line in f:
            if line.startswith("!"):
                continue
            c = line.split("\t")
            if len(c) > 2 and c[1] and c[2]:
                out.setdefault(c[1], c[2])
    return out


# --------------------------------------------------------------------------- #
# Cache-first API wrapper (graceful degradation; tools never hard-fail on API)
# --------------------------------------------------------------------------- #
def api_get(url: str, params: Optional[dict] = None, *, cache_subdir: str,
            timeout: int = 30) -> Optional[dict]:
    """Cache-first GET via kb_builder.api_cache. Returns parsed JSON, or None on
    any failure (offline, non-JSON, error) so callers degrade to local-only."""
    try:
        from kb_builder.api_cache import cached_get
        path = cached_get(url=url, params=params or {},
                          cache_dir=API_CACHE_DIR / cache_subdir, timeout=timeout)
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def api_post(url: str, json_body: dict, *, cache_subdir: str,
             timeout: int = 60, retries: int = 3) -> Optional[Any]:
    """Cache-first POST via kb_builder.api_cache. Returns parsed JSON (dict or
    list), or None on any failure (offline, non-JSON, 4xx/5xx, server bug) so
    callers degrade to local-only. Identical payloads hit the disk cache.
    `retries` is low for optional supplements so a flaky upstream can't stall
    the tool call behind exponential backoff."""
    try:
        from kb_builder.api_cache import cached_post
        path = cached_post(url=url, json_body=json_body, retries=retries,
                           cache_dir=API_CACHE_DIR / cache_subdir, timeout=timeout)
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
