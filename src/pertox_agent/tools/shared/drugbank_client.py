#!/usr/bin/env python3
"""DrugBank local query client for PersAgent.

Mirrors the public API of OpenClaw-Medical-Skills/skills/drugbank-database
(DrugBankHelper) but is built for THIS project's constraints:

  * The raw export is 1.6 GB / 17,430 drugs (v5.1, 2025-01-02) — far larger than
    the skill assumes. The skill loads the whole ElementTree into RAM; we instead
    STREAM it once (ET.iterparse + elem.clear()) into a normalized JSONL, and
    serve queries by seeking to a byte offset. RAM stays flat regardless of size.
  * Raw file stays read-only. Derived data goes to data/normalized/drugbank/
    (drugbank.jsonl + drugbank_index.json), consistent with the normalize layer.

Two phases:
    # 1. one-time build (streams the XML; a few minutes)
    conda run -n perstox python -m tool.shared.drugbank_client build
    # 2. query (O(1) seeks; no XML, no full-file scan)
    conda run -n perstox python -m tool.shared.drugbank_client info DB00006
    conda run -n perstox python -m tool.shared.drugbank_client name "warfarin"
    conda run -n perstox python -m tool.shared.drugbank_client interactions DB00682
    conda run -n perstox python -m tool.shared.drugbank_client targets DB00682

Programmatic:
    from pertox_agent.tools.shared.drugbank_client import DrugBankTool
    db = DrugBankTool()                 # opens the prebuilt index
    info = db.get_drug_info("DB00682")  # warfarin
    ddis = db.get_interactions("DB00682")

Each normalized record carries: identity (ids, name, type, groups, cas, unii),
external xrefs (PubChem/ChEMBL/UniProtKB/KEGG/PharmGKB/RxCUI/...), calculated +
experimental properties (incl. SMILES/InChI/InChIKey/logP), ATC codes,
categories, free-text pharmacology (indication/MoA/pharmacodynamics/metabolism/
toxicity), targets/enzymes/transporters/carriers (UniProt id + gene + actions),
and drug-drug interactions. These feed Stage-1 drug identity/mechanism and
Stage-2 DDI/PGx reasoning.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_XML = PROJECT_ROOT / "data" / "raw" / "drugbank" / "drugbank_full_database.xml"
OUT_DIR = PROJECT_ROOT / "data" / "normalized" / "drugbank"
JSONL = OUT_DIR / "drugbank.jsonl"
INDEX = OUT_DIR / "drugbank_index.json"
META = OUT_DIR / "_normalized_metadata.json"

NS = "{http://www.drugbank.ca}"


# --------------------------------------------------------------------------- #
# XML extraction helpers (operate on a single <drug> element)
# --------------------------------------------------------------------------- #

def _tag(elem) -> str:
    return elem.tag.replace(NS, "")


def _text(parent, child: str) -> Optional[str]:
    el = parent.find(f"{NS}{child}")
    if el is not None and el.text is not None:
        t = el.text.strip()
        return t or None
    return None


def _texts(parent, path: str) -> list[str]:
    return [e.text.strip() for e in parent.findall(path) if e.text and e.text.strip()]


def _ids(drug) -> tuple[str, list[str]]:
    primary, secondary = None, []
    for el in drug.findall(f"{NS}drugbank-id"):
        if el.get("primary") == "true":
            primary = el.text
        elif el.text:
            secondary.append(el.text)
    return primary, secondary


def _external_ids(drug) -> dict:
    out = {}
    ext = drug.find(f"{NS}external-identifiers")
    if ext is not None:
        for ei in ext.findall(f"{NS}external-identifier"):
            res = _text(ei, "resource")
            idv = _text(ei, "identifier")
            if res and idv:
                out[res] = idv
    return out


def _properties(drug, container: str) -> dict:
    out = {}
    cont = drug.find(f"{NS}{container}")
    if cont is not None:
        for prop in cont.findall(f"{NS}property"):
            kind = _text(prop, "kind")
            value = _text(prop, "value")
            if kind and value:
                out[kind] = value
    return out


def _protein_group(drug, container: str) -> list[dict]:
    """Extract targets/enzymes/transporters/carriers. Each carries the UniProt
    accession + gene-name from its polypeptide, plus pharmacological action."""
    out = []
    cont = drug.find(f"{NS}{container}")
    if cont is None:
        return out
    singular = container[:-1]  # targets -> target
    for node in cont.findall(f"{NS}{singular}"):
        actions = _texts(node.find(f"{NS}actions") or node, f"{NS}action") \
            if node.find(f"{NS}actions") is not None else []
        rec = {
            "id": _text(node, "id"),
            "name": _text(node, "name"),
            "organism": _text(node, "organism"),
            "known_action": _text(node, "known-action"),
            "actions": actions,
        }
        poly = node.find(f"{NS}polypeptide")
        if poly is not None:
            rec["uniprot_id"] = poly.get("id")
            rec["gene_name"] = _text(poly, "gene-name")
        out.append({k: v for k, v in rec.items() if v not in (None, [], "")})
    return out


def _interactions(drug) -> list[dict]:
    out = []
    ddi = drug.find(f"{NS}drug-interactions")
    if ddi is not None:
        for it in ddi.findall(f"{NS}drug-interaction"):
            out.append({
                "partner_id": _text(it, "drugbank-id"),
                "partner_name": _text(it, "name"),
                "description": _text(it, "description"),
            })
    return out


def drug_to_record(drug) -> dict:
    """Flatten one <drug> element into the normalized PersTox record."""
    primary, secondary = _ids(drug)
    atc = [c.get("code") for c in drug.findall(f"{NS}atc-codes/{NS}atc-code") if c.get("code")]
    categories = _texts(drug, f"{NS}categories/{NS}category/{NS}category")
    return {
        "drugbank_id": primary,
        "secondary_ids": secondary,
        "name": _text(drug, "name"),
        "type": drug.get("type"),
        "groups": _texts(drug, f"{NS}groups/{NS}group"),
        "cas_number": _text(drug, "cas-number"),
        "unii": _text(drug, "unii"),
        "atc_codes": atc,
        "categories": categories,
        "external_ids": _external_ids(drug),
        "calculated_properties": _properties(drug, "calculated-properties"),
        "experimental_properties": _properties(drug, "experimental-properties"),
        "indication": _text(drug, "indication"),
        "pharmacodynamics": _text(drug, "pharmacodynamics"),
        "mechanism_of_action": _text(drug, "mechanism-of-action"),
        "metabolism": _text(drug, "metabolism"),
        "toxicity": _text(drug, "toxicity"),
        "targets": _protein_group(drug, "targets"),
        "enzymes": _protein_group(drug, "enzymes"),
        "transporters": _protein_group(drug, "transporters"),
        "carriers": _protein_group(drug, "carriers"),
        "drug_interactions": _interactions(drug),
    }


# --------------------------------------------------------------------------- #
# BUILD: stream raw XML -> normalized JSONL + byte-offset index
# --------------------------------------------------------------------------- #

def build(raw_xml: Path = RAW_XML) -> dict:
    """Stream the raw export once, writing one JSON record per top-level <drug>
    to drugbank.jsonl, and an index mapping lookups -> byte offsets. Only the
    root's direct <drug> children are emitted (nested <drug> inside <salts>,
    <mixtures>, <reactions> are skipped via a depth counter)."""
    if not raw_xml.exists():
        raise FileNotFoundError(f"raw DrugBank XML not found: {raw_xml}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    by_id: dict[str, int] = {}
    name_to_id: dict[str, str] = {}
    inchikey_to_id: dict[str, str] = {}
    n = 0
    depth = 0

    with JSONL.open("w", encoding="utf-8") as out:
        offset = 0
        for ev, elem in ET.iterparse(str(raw_xml), events=("start", "end")):
            if _tag(elem) != "drug":
                continue
            if ev == "start":
                depth += 1
                continue
            # ev == "end"
            if depth == 1:
                rec = drug_to_record(elem)
                if rec["drugbank_id"]:
                    line = json.dumps(rec, ensure_ascii=False) + "\n"
                    out.write(line)
                    by_id[rec["drugbank_id"]] = offset
                    offset += len(line.encode("utf-8"))
                    if rec["name"]:
                        name_to_id.setdefault(rec["name"].lower(), rec["drugbank_id"])
                    ik = rec["calculated_properties"].get("InChIKey")
                    if ik:
                        inchikey_to_id.setdefault(ik, rec["drugbank_id"])
                    n += 1
                    if n % 2000 == 0:
                        print(f"  ... {n} drugs", file=sys.stderr)
                elem.clear()
            depth -= 1

    index = {"by_id": by_id, "name_to_id": name_to_id, "inchikey_to_id": inchikey_to_id}
    INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    meta = {
        "source": "drugbank", "raw_file": str(raw_xml.relative_to(PROJECT_ROOT)),
        "drugbank_version": "5.1", "rows": n,
        "outputs": {"jsonl": str(JSONL.relative_to(PROJECT_ROOT)),
                    "index": str(INDEX.relative_to(PROJECT_ROOT))},
        "note": "Raw read-only; streamed via ET.iterparse. Only top-level <drug> "
                "emitted. Index maps drugbank_id/name/InChIKey -> JSONL byte offset.",
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"built {n} drugs -> {JSONL.relative_to(PROJECT_ROOT)}", file=sys.stderr)
    return meta


# --------------------------------------------------------------------------- #
# QUERY: O(1) byte-offset seeks into the prebuilt JSONL
# --------------------------------------------------------------------------- #

class DrugBankTool:
    """Query the prebuilt DrugBank JSONL by seeking to byte offsets. Mirrors the
    skill's DrugBankHelper API (get_drug_info/get_interactions/get_targets/
    get_properties/get_smiles/search_by_name) plus InChIKey lookup, but never
    loads the full database into memory."""

    def __init__(self, jsonl: Path = JSONL, index: Path = INDEX):
        if not jsonl.exists() or not index.exists():
            raise FileNotFoundError(
                f"DrugBank index not built. Run: python {Path(__file__).name} build")
        self.jsonl = jsonl
        idx = json.loads(index.read_text(encoding="utf-8"))
        self.by_id: dict[str, int] = idx["by_id"]
        self.name_to_id: dict[str, str] = idx["name_to_id"]
        self.inchikey_to_id: dict[str, str] = idx["inchikey_to_id"]

    def _read_at(self, offset: int) -> dict:
        with self.jsonl.open(encoding="utf-8") as f:
            f.seek(offset)
            return json.loads(f.readline())

    def get_record(self, drugbank_id: str) -> Optional[dict]:
        off = self.by_id.get(drugbank_id)
        return self._read_at(off) if off is not None else None

    def get_by_name(self, name: str) -> Optional[dict]:
        did = self.name_to_id.get(name.lower())
        return self.get_record(did) if did else None

    def get_by_inchikey(self, inchikey: str) -> Optional[dict]:
        did = self.inchikey_to_id.get(inchikey)
        return self.get_record(did) if did else None

    def get_drug_info(self, drugbank_id: str) -> dict:
        r = self.get_record(drugbank_id)
        if not r:
            return {}
        keys = ("drugbank_id", "name", "type", "groups", "cas_number", "unii",
                "atc_codes", "indication", "pharmacodynamics", "mechanism_of_action",
                "metabolism", "toxicity", "external_ids")
        return {k: r.get(k) for k in keys}

    def get_interactions(self, drugbank_id: str) -> list[dict]:
        r = self.get_record(drugbank_id)
        return r.get("drug_interactions", []) if r else []

    def get_targets(self, drugbank_id: str) -> list[dict]:
        r = self.get_record(drugbank_id)
        return r.get("targets", []) if r else []

    def get_enzymes(self, drugbank_id: str) -> list[dict]:
        r = self.get_record(drugbank_id)
        return r.get("enzymes", []) if r else []

    def get_transporters(self, drugbank_id: str) -> list[dict]:
        r = self.get_record(drugbank_id)
        return r.get("transporters", []) if r else []

    def get_properties(self, drugbank_id: str) -> dict:
        r = self.get_record(drugbank_id)
        if not r:
            return {"calculated": {}, "experimental": {}}
        return {"calculated": r.get("calculated_properties", {}),
                "experimental": r.get("experimental_properties", {})}

    def get_smiles(self, drugbank_id: str) -> Optional[str]:
        return self.get_properties(drugbank_id)["calculated"].get("SMILES")

    def get_inchikey(self, drugbank_id: str) -> Optional[str]:
        return self.get_properties(drugbank_id)["calculated"].get("InChIKey")

    def search_by_name(self, name: str, exact: bool = False) -> list[dict]:
        term = name.lower()
        hits = []
        for nm, did in self.name_to_id.items():
            if (nm == term) if exact else (term in nm):
                hits.append({"id": did, "name": nm})
                if len(hits) >= 50:
                    break
        return hits

    def check_interaction(self, drug1_id: str, drug2_id: str) -> Optional[dict]:
        for it in self.get_interactions(drug1_id):
            if it.get("partner_id") == drug2_id:
                return it
        for it in self.get_interactions(drug2_id):
            if it.get("partner_id") == drug1_id:
                return it
        return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="DrugBank local query tool for PersTox-Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="stream raw XML -> normalized JSONL + index (one-time)")
    p_info = sub.add_parser("info", help="drug identity + pharmacology by DrugBank ID")
    p_info.add_argument("drugbank_id")
    p_name = sub.add_parser("name", help="look up a drug by exact name")
    p_name.add_argument("name")
    p_ik = sub.add_parser("inchikey", help="look up a drug by InChIKey")
    p_ik.add_argument("inchikey")
    p_ddi = sub.add_parser("interactions", help="drug-drug interactions by DrugBank ID")
    p_ddi.add_argument("drugbank_id")
    p_tgt = sub.add_parser("targets", help="targets/enzymes/transporters by DrugBank ID")
    p_tgt.add_argument("drugbank_id")
    p_pair = sub.add_parser("check", help="check whether two drugs interact")
    p_pair.add_argument("drug1_id")
    p_pair.add_argument("drug2_id")
    p_search = sub.add_parser("search", help="substring name search")
    p_search.add_argument("query")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        build()
        return 0

    db = DrugBankTool()
    if args.cmd == "info":
        _print(db.get_drug_info(args.drugbank_id))
    elif args.cmd == "name":
        rec = db.get_by_name(args.name)
        _print(db.get_drug_info(rec["drugbank_id"]) if rec else {"error": "not found"})
    elif args.cmd == "inchikey":
        rec = db.get_by_inchikey(args.inchikey)
        _print(db.get_drug_info(rec["drugbank_id"]) if rec else {"error": "not found"})
    elif args.cmd == "interactions":
        ddi = db.get_interactions(args.drugbank_id)
        print(f"{len(ddi)} interactions", file=sys.stderr)
        _print(ddi[:25])
    elif args.cmd == "targets":
        _print({"targets": db.get_targets(args.drugbank_id),
                "enzymes": db.get_enzymes(args.drugbank_id),
                "transporters": db.get_transporters(args.drugbank_id)})
    elif args.cmd == "check":
        _print(db.check_interaction(args.drug1_id, args.drug2_id) or {"interaction": None})
    elif args.cmd == "search":
        _print(db.search_by_name(args.query, exact=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



