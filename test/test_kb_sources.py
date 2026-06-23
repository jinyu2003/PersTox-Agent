#!/usr/bin/env python3
"""Exercise every PersTox-Agent knowledge source end-to-end.

For each source this prints a concrete retrieval: the INPUT (the query you'd
issue) and the OUTPUT (what the KB returns). Two families are covered:

  * Local sources  -> read data/normalized/<id>/*.jsonl and answer a lookup.
  * API sources    -> kb_builder.api_cache.cached_get (cache-first; falls back
                      to a live fetch, and degrades gracefully if offline).

Run under the `perstox` conda env:
    conda run -n perstox python test/test_kb_sources.py
    conda run -n perstox python test/test_kb_sources.py --live   # force API refetch

Exit code is non-zero only if a LOCAL source fails; API sources are reported
but never fail the run (upstream outages / no network are expected).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kb_builder.api_cache import api_examples, cached_get

DATA_DIR = PROJECT_ROOT / "data"
NORMALIZED = DATA_DIR / "normalized"
API_CACHE = DATA_DIR / "cache" / "api_responses"


def rule(char: str = "-", width: int = 78) -> str:
    return char * width


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# Each local case: a normalized file + a query described in plain words + a
# predicate selecting the matching record(s) + the fields worth displaying.
LOCAL_CASES = [
    {
        "source_id": "atc",
        "module": "drug_identity",
        "file": NORMALIZED / "atc" / "atc_classes.jsonl",
        "query": "ATC code == 'C01' (cardiac therapy group)",
        "match": lambda r: r.get("atc_code") == "C01",
        "show": ["atc_code", "name", "level"],
    },
    {
        "source_id": "meddra",
        "module": "ade_terms",
        "file": NORMALIZED / "meddra" / "meddra_terms.jsonl",
        "query": "LLT name (EN) contains 'hepatic failure'",
        "match": lambda r: "hepatic failure" in (r.get("llt_name_en") or "").lower(),
        "show": ["llt_code", "llt_name_en", "pt_name_en", "soc_name_en", "primary_soc"],
    },
    {
        "source_id": "ctcae_v5",
        "module": "ade_terms",
        "file": NORMALIZED / "ctcae_v5" / "ctcae_terms.jsonl",
        "query": "CTCAE term == 'Anemia' (show severity grades)",
        "match": lambda r: (r.get("term") or "").lower() == "anemia",
        "show": ["meddra_code", "term", "grade_1", "grade_3", "grade_5"],
    },
    {
        "source_id": "dilirank",
        "module": "organ_liver",
        "file": NORMALIZED / "dilirank" / "dilirank.jsonl",
        "query": "compound_name == 'Abacavir sulfate' (DILI concern class)",
        "match": lambda r: (r.get("compound_name") or "").lower() == "abacavir sulfate",
        "show": ["ltkb_id", "compound_name", "vdili_concern", "severity_class"],
    },
    {
        "source_id": "cpic",
        "module": "personalization",
        "file": NORMALIZED / "cpic" / "cpic_pairs.jsonl",
        "query": "gene == 'CYP2C9' pairs (CPIC level A only)",
        "match": lambda r: r.get("gene") == "CYP2C9" and r.get("cpic_level") == "A",
        "show": ["gene", "drug_id", "cpic_level", "pgx_testing"],
    },
    {
        "source_id": "dpwg",
        "module": "personalization",
        "file": NORMALIZED / "dpwg" / "dpwg_guidelines.jsonl",
        "query": "guideline mentioning gene 'SLCO1B1'",
        "match": lambda r: "SLCO1B1" in (r.get("genes") or []),
        "show": ["guideline_id", "name", "drugs", "genes", "has_recommendation"],
    },
    {
        "source_id": "hgnc",
        "module": "gene_protein",
        "file": NORMALIZED / "hgnc" / "hgnc.jsonl",
        "query": "symbol == 'CYP2C9'",
        "match": lambda r: r.get("symbol") == "CYP2C9",
        "show": ["hgnc_id", "symbol", "name", "entrez_id", "ensembl_gene_id"],
    },
    {
        "source_id": "ncbi_gene",
        "module": "gene_protein",
        "file": NORMALIZED / "ncbi_gene" / "gene_human.jsonl",
        "query": "symbol == 'CYP2C9' (human subset)",
        "match": lambda r: r.get("symbol") == "CYP2C9",
        "show": ["gene_id", "symbol", "description", "chromosome", "hgnc_id"],
    },
    {
        "source_id": "uniprot_swissprot",
        "module": "gene_protein",
        "file": NORMALIZED / "uniprot_swissprot" / "protein_human.jsonl",
        "query": "gene_primary == 'CYP2C9'",
        "match": lambda r: r.get("gene_primary") == "CYP2C9",
        "show": ["primary_accession", "protein_name", "gene_primary", "sequence_length"],
    },
]


def run_local_case(case: dict) -> dict:
    """Scan one normalized .jsonl and return the records matching the query."""
    path = case["file"]
    print(rule())
    print(f"[LOCAL] {case['source_id']}  ({case['module']})")
    print(f"  file : {path.relative_to(PROJECT_ROOT)}")
    print(f"  INPUT: {case['query']}")
    if not path.exists():
        print("  OUTPUT: MISSING normalized file — run scripts/normalize_kb_sources.py")
        return {"source_id": case["source_id"], "status": "missing", "hits": 0}

    scanned = 0
    hits = []
    for rec in load_jsonl(path):
        scanned += 1
        if case["match"](rec):
            hits.append({k: rec.get(k) for k in case["show"]})
            if len(hits) >= 3:  # a couple of examples is enough to prove retrieval
                break

    print(f"  scanned: {scanned} records, matched (showing <=3): {len(hits)}")
    for hit in hits:
        print("  OUTPUT:", json.dumps(hit, ensure_ascii=False))
    status = "ok" if hits else "no_match"
    if not hits:
        print("  OUTPUT: (no record matched the query — check the predicate)")
    return {"source_id": case["source_id"], "status": status, "hits": len(hits)}


# Human-readable intent for each API connector defined in kb_builder.api_cache.
API_INTENT = {
    "rxnorm_by_name": "drug name 'warfarin' -> RxNorm concept id (RxCUI)",
    "atc_by_drug": "drug name 'warfarin' -> ATC classes it belongs to",
    "atc_tree": "fetch the ATC level 1-4 classification tree",
    "pubchem_by_name": "drug name 'warfarin' -> SMILES / InChIKey",
    "chembl_molecule_by_name": "drug name 'warfarin' -> ChEMBL molecule record",
    "reactome_pathways_for_uniprot": "UniProt P03372 -> lower-level Reactome pathways",
    "openfda_label_by_substance": "substance 'warfarin' -> FDA label section(s)",
}


def summarize_json(raw: bytes) -> str:
    """One-line shape summary so the test output stays readable."""
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return f"<{len(raw)} bytes, non-JSON or partial>"
    if isinstance(doc, list):
        return f"list of {len(doc)} items; first keys: {list(doc[0])[:8] if doc and isinstance(doc[0], dict) else doc[:1]}"
    if isinstance(doc, dict):
        return f"object with top-level keys: {list(doc)[:8]}"
    return f"scalar: {doc!r}"


def run_api_case(name: str, example: dict, *, live: bool) -> dict:
    print(rule())
    print(f"[API]   {name}")
    print(f"  INPUT: {API_INTENT.get(name, '(see manifest)')}")
    print(f"  url  : {example['url']}")
    if example.get("params"):
        print(f"  params: {json.dumps(example['params'], ensure_ascii=False)}")
    try:
        response_path = cached_get(
            url=example["url"],
            params=example.get("params") or {},
            cache_dir=API_CACHE / name,
            force=live,
        )
        raw = response_path.read_bytes()
        print(f"  cache: {response_path.relative_to(PROJECT_ROOT)} ({len(raw)} bytes)")
        print(f"  OUTPUT: {summarize_json(raw)}")
        return {"source_id": name, "status": "ok"}
    except Exception as exc:  # noqa: BLE001 — upstream outage / offline is non-fatal
        print(f"  OUTPUT: unavailable ({type(exc).__name__}: {exc})")
        return {"source_id": name, "status": "unavailable"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true",
        help="Force a fresh API fetch (default: use cached responses when present).",
    )
    parser.add_argument(
        "--skip-api", action="store_true",
        help="Exercise only the local normalized sources.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print(rule("="))
    print("PersTox-Agent KB source retrieval test")
    print(f"  local normalized dir: {NORMALIZED.relative_to(PROJECT_ROOT)}")
    print(f"  api cache dir       : {API_CACHE.relative_to(PROJECT_ROOT)}")
    print(f"  mode                : {'LIVE refetch' if args.live else 'cache-first'}")
    print(rule("="))

    print("\n## LOCAL SOURCES (data/normalized/*.jsonl)\n")
    local_results = [run_local_case(case) for case in LOCAL_CASES]

    api_results = []
    if not args.skip_api:
        print("\n## API SOURCES (kb_builder.api_cache)\n")
        for name, example in sorted(api_examples().items()):
            api_results.append(run_api_case(name, example, live=args.live))

    print("\n" + rule("="))
    print("SUMMARY")
    print(rule("="))
    ok_local = sum(r["status"] == "ok" for r in local_results)
    print(f"  local : {ok_local}/{len(local_results)} returned data")
    for r in local_results:
        if r["status"] != "ok":
            print(f"    - {r['source_id']}: {r['status']}")
    if api_results:
        ok_api = sum(r["status"] == "ok" for r in api_results)
        print(f"  api   : {ok_api}/{len(api_results)} reachable")
        for r in api_results:
            if r["status"] != "ok":
                print(f"    - {r['source_id']}: {r['status']}")

    # Only local failures break the run; API outages are expected and tolerated.
    local_failed = [r for r in local_results if r["status"] != "ok"]
    return 1 if local_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
