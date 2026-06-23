"""Structure-normalize downloaded raw sources into data/normalized/<id>/.

Each normalizer streams its raw input (memory-safe for large files) and writes
newline-delimited JSON (.jsonl) plus a _normalized_metadata.json sidecar that
records source hash linkage, row counts, and the output schema.

Normalized records are derived artifacts; raw files under data/raw stay untouched.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUMAN_TAX_ID = "9606"
UNIPROT_NS = "{http://uniprot.org/uniprot}"
SSML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _raw_dir(data_dir: Path, source_id: str) -> Path:
    return data_dir / "raw" / source_id


def _norm_dir(data_dir: Path, source_id: str) -> Path:
    return data_dir / "normalized" / source_id


def write_jsonl(records: Iterator[dict], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")
            count += 1
    return count


def _read_source_sha(raw_dir: Path, filename: str) -> str | None:
    meta_path = raw_dir / "_source_metadata.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for entry in meta.get("files", []):
        if Path(entry.get("path", "")).name == filename:
            return entry.get("sha256")
    return None


def write_normalized_metadata(
    *,
    source_id: str,
    data_dir: Path,
    inputs: list[dict],
    outputs: list[dict],
    schema: dict,
    notes: str | None = None,
) -> Path:
    out_dir = _norm_dir(data_dir, source_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "source_id": source_id,
        "normalized_at": utc_now(),
        "generator": "kb_builder.normalize",
        "inputs": inputs,
        "outputs": outputs,
        "schema": schema,
        "notes": notes,
    }
    meta_path = out_dir / "_normalized_metadata.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta_path


def _split_list(value: str) -> list[str]:
    return [v for v in value.split("|") if v and v != "-"]


def _parse_dbxrefs(value: str) -> dict[str, list[str]]:
    # dbXrefs like: MIM:138670|HGNC:HGNC:5|Ensembl:ENSG00000121410
    out: dict[str, list[str]] = {}
    for token in _split_list(value):
        prefix, _, ident = token.partition(":")
        if not ident:
            continue
        out.setdefault(prefix, []).append(ident)
    return out


def _xlsx_col_index(ref: str) -> int:
    # Cell ref like "AB12" -> zero-based column index for "AB".
    letters = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - 64)
    return idx - 1


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{SSML_NS}t")) for si in root.findall(f"{SSML_NS}si")]


def iter_xlsx_rows(path: Path, *, sheet: str = "xl/worksheets/sheet1.xml") -> Iterator[list]:
    """Stream rows from an .xlsx sheet, robust to a wrong <dimension> tag.

    openpyxl's read-only path trusts the declared dimension and silently
    under-reads sheets that lie about it (the MedDRA export does). This walks
    the sheet XML directly so every <row> is yielded with shared strings resolved.
    """
    with zipfile.ZipFile(path) as archive:
        shared = _xlsx_shared_strings(archive)
        data = archive.read(sheet)
    for _event, elem in ET.iterparse(io.BytesIO(data), events=("end",)):
        if elem.tag != f"{SSML_NS}row":
            continue
        cells: dict[int, str | None] = {}
        max_col = -1
        for cell in elem.findall(f"{SSML_NS}c"):
            col = _xlsx_col_index(cell.get("r", "A"))
            max_col = max(max_col, col)
            value_el = cell.find(f"{SSML_NS}v")
            text = value_el.text if value_el is not None else None
            if cell.get("t") == "s" and text is not None:
                text = shared[int(text)]
            cells[col] = text
        yield [cells.get(i) for i in range(max_col + 1)]
        elem.clear()


def _gene_info_rows(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header_line = f.readline().lstrip("#").rstrip("\n")
        cols = header_line.split("\t")
        idx = {name: i for i, name in enumerate(cols)}
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if parts[idx["tax_id"]] != HUMAN_TAX_ID:
                continue
            xrefs = _parse_dbxrefs(parts[idx["dbXrefs"]])
            yield {
                "gene_id": parts[idx["GeneID"]],
                "symbol": parts[idx["Symbol"]],
                "synonyms": _split_list(parts[idx["Synonyms"]]),
                "description": parts[idx["description"]].strip() or None,
                "type_of_gene": parts[idx["type_of_gene"]],
                "chromosome": parts[idx["chromosome"]].strip() or None,
                "map_location": parts[idx["map_location"]].strip() or None,
                "hgnc_id": (xrefs.get("HGNC") or [None])[0],
                "ensembl_gene_id": (xrefs.get("Ensembl") or [None])[0],
                "mim_id": (xrefs.get("MIM") or [None])[0],
                "dbxrefs": xrefs,
            }


def normalize_ncbi_gene(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "ncbi_gene") / "gene_info.gz"
    out_path = _norm_dir(data_dir, "ncbi_gene") / "gene_human.jsonl"
    n = write_jsonl(_gene_info_rows(raw), out_path)
    return {"source_id": "ncbi_gene", "rows": n, "output": str(out_path)}


def _hgnc_rows(path: Path) -> Iterator[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for doc in payload.get("response", {}).get("docs", []):
        yield {
            "hgnc_id": doc.get("hgnc_id"),
            "symbol": doc.get("symbol"),
            "name": doc.get("name"),
            "status": doc.get("status"),
            "locus_group": doc.get("locus_group"),
            "locus_type": doc.get("locus_type"),
            "location": doc.get("location"),
            "alias_symbol": doc.get("alias_symbol", []),
            "prev_symbol": doc.get("prev_symbol", []),
            "entrez_id": doc.get("entrez_id"),
            "ensembl_gene_id": doc.get("ensembl_gene_id"),
            "uniprot_ids": doc.get("uniprot_ids", []),
            "refseq_accession": doc.get("refseq_accession", []),
            "omim_id": doc.get("omim_id", []),
        }


def normalize_hgnc(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "hgnc") / "hgnc_complete_set.json"
    out_path = _norm_dir(data_dir, "hgnc") / "hgnc.jsonl"
    n = write_jsonl(_hgnc_rows(raw), out_path)
    return {"source_id": "hgnc", "rows": n, "output": str(out_path)}


def _q(tag: str) -> str:
    return f"{UNIPROT_NS}{tag}"


def _uniprot_protein_name(entry: ET.Element) -> str | None:
    protein = entry.find(_q("protein"))
    if protein is None:
        return None
    for path in ("recommendedName/fullName", "submittedName/fullName"):
        node = protein.find("/".join(_q(p) for p in path.split("/")))
        if node is not None and node.text:
            return node.text
    return None


def _uniprot_function(entry: ET.Element) -> str | None:
    for comment in entry.findall(_q("comment")):
        if comment.get("type") == "function":
            text = comment.find(_q("text"))
            if text is not None and text.text:
                return text.text
    return None


def _uniprot_entry_record(entry: ET.Element) -> dict:
    accessions = [a.text for a in entry.findall(_q("accession")) if a.text]
    genes = entry.find(_q("gene"))
    gene_primary = None
    gene_synonyms: list[str] = []
    if genes is not None:
        for name in genes.findall(_q("name")):
            if name.get("type") == "primary":
                gene_primary = name.text
            elif name.get("type") == "synonym" and name.text:
                gene_synonyms.append(name.text)
    xrefs: dict[str, list[str]] = {}
    for ref in entry.findall(_q("dbReference")):
        rtype, rid = ref.get("type"), ref.get("id")
        if rtype in {"GeneID", "HGNC", "Ensembl", "Reactome", "PDB"} and rid:
            xrefs.setdefault(rtype, []).append(rid)
    seq = entry.find(_q("sequence"))
    name_el = entry.find(_q("name"))
    return {
        "primary_accession": accessions[0] if accessions else None,
        "accessions": accessions,
        "entry_name": name_el.text if name_el is not None else None,
        "protein_name": _uniprot_protein_name(entry),
        "gene_primary": gene_primary,
        "gene_synonyms": gene_synonyms,
        "function": _uniprot_function(entry),
        "keywords": [k.text for k in entry.findall(_q("keyword")) if k.text],
        "xrefs": xrefs,
        "sequence_length": int(seq.get("length")) if seq is not None and seq.get("length") else None,
    }


def _uniprot_rows(path: Path) -> Iterator[dict]:
    entry_tag = _q("entry")
    with gzip.open(path, "rb") as f:
        context = ET.iterparse(f, events=("end",))
        for _event, elem in context:
            if elem.tag != entry_tag:
                continue
            yield _uniprot_entry_record(elem)
            elem.clear()  # free memory; entries are independent


def normalize_uniprot(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "uniprot_swissprot") / "uniprot_sprot_human.xml.gz"
    out_path = _norm_dir(data_dir, "uniprot_swissprot") / "protein_human.jsonl"
    n = write_jsonl(_uniprot_rows(raw), out_path)
    return {"source_id": "uniprot_swissprot", "rows": n, "output": str(out_path)}


def _atc_level(class_id: str) -> int:
    # ATC code length encodes the level: 1 letter=L1, 3 chars=L2, 4=L3, 5=L4.
    mapping = {1: 1, 3: 2, 4: 3, 5: 4}
    return mapping.get(len(class_id), 0)


def _atc_rows(path: Path) -> Iterator[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for concept in payload.get("rxclassMinConceptList", {}).get("rxclassMinConcept", []):
        class_id = concept.get("classId", "")
        if class_id in ("", "0"):  # synthetic root node, not a real ATC code
            continue
        yield {
            "atc_code": class_id,
            "name": concept.get("className"),
            "level": _atc_level(class_id),
            "class_type": concept.get("classType"),
        }


def normalize_atc(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "atc") / "atc1-4_rxclass.json"
    out_path = _norm_dir(data_dir, "atc") / "atc_classes.jsonl"
    n = write_jsonl(_atc_rows(raw), out_path)
    return {"source_id": "atc", "rows": n, "output": str(out_path)}


def _cpic_rows(path: Path) -> Iterator[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for pair in payload:
        yield {
            "pair_id": pair.get("pairid"),
            "gene": pair.get("genesymbol"),
            "drug_id": pair.get("drugid"),
            "guideline_id": pair.get("guidelineid"),
            "cpic_level": pair.get("cpiclevel"),
            "clinpgx_level": pair.get("clinpgxlevel"),
            "pgx_testing": pair.get("pgxtesting"),
            "used_for_recommendation": pair.get("usedforrecommendation"),
            "citations": pair.get("citations", []),
            "removed": pair.get("removed"),
        }


def normalize_cpic(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "cpic") / "cpic_pairs.json"
    out_path = _norm_dir(data_dir, "cpic") / "cpic_pairs.jsonl"
    n = write_jsonl(_cpic_rows(raw), out_path)
    return {"source_id": "cpic", "rows": n, "output": str(out_path)}


def _markdown_text(node: dict | None) -> str | None:
    if not isinstance(node, dict):
        return node if isinstance(node, str) else None
    return node.get("markdown") or node.get("html")


def _dpwg_rows(path: Path) -> Iterator[dict]:
    with zipfile.ZipFile(path) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith(".json"))
        for name in names:
            doc = json.loads(archive.read(name))
            g = doc.get("guideline", {})
            yield {
                "guideline_id": g.get("id"),
                "name": g.get("name"),
                "source": g.get("source"),
                "drugs": [c.get("name") for c in g.get("relatedChemicals", []) if c.get("name")],
                "genes": [x.get("symbol") or x.get("name") for x in g.get("relatedGenes", [])],
                "has_recommendation": bool(g.get("recommendation")),
                "alternate_drug_available": g.get("alternateDrugAvailable"),
                "pediatric": g.get("pediatric"),
                "summary": _markdown_text(g.get("summaryMarkdown")),
                "citation_count": len(doc.get("citations", [])),
            }


def normalize_dpwg(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "dpwg") / "guidelineAnnotations.json.zip"
    out_path = _norm_dir(data_dir, "dpwg") / "dpwg_guidelines.jsonl"
    n = write_jsonl(_dpwg_rows(raw), out_path)
    return {"source_id": "dpwg", "rows": n, "output": str(out_path)}


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").strip()
    return text or None


def _ctcae_rows(path: Path) -> Iterator[dict]:
    # Sheet 1 = "CTCAE v5.0 Clean Copy"; row 0 is the header.
    rows = iter_xlsx_rows(path)
    header = next(rows, None)
    if header is None:
        return
    for row in rows:
        if not any(c is not None for c in row):
            continue
        cells = (list(row) + [None] * 11)[:11]
        yield {
            "meddra_code": _clean(cells[0]),
            "soc": _clean(cells[1]),
            "term": _clean(cells[2]),
            "grade_1": _clean(cells[3]),
            "grade_2": _clean(cells[4]),
            "grade_3": _clean(cells[5]),
            "grade_4": _clean(cells[6]),
            "grade_5": _clean(cells[7]),
            "definition": _clean(cells[8]),
            "navigational_note": _clean(cells[9]),
        }


def normalize_ctcae(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "ctcae_v5") / "CTCAE_v5.0.xlsx"
    out_path = _norm_dir(data_dir, "ctcae_v5") / "ctcae_terms.jsonl"
    n = write_jsonl(_ctcae_rows(raw), out_path)
    return {"source_id": "ctcae_v5", "rows": n, "output": str(out_path)}


def _dilirank_rows(path: Path) -> Iterator[dict]:
    # Sheet 1 = "version 2"; row 0 is a title banner, row 1 is the header.
    rows = iter_xlsx_rows(path)
    for _ in range(2):  # drop banner + header
        next(rows, None)
    for row in rows:
        if not any(c is not None for c in row):
            continue
        cells = (list(row) + [None] * 6)[:6]
        yield {
            "ltkb_id": _clean(cells[0]),
            "compound_name": _clean(cells[1]),
            "severity_class": _clean(cells[2]),
            "label_section": _clean(cells[3]),
            "vdili_concern": _clean(cells[4]),
            "comment": _clean(cells[5]),
        }


def normalize_dilirank(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "dilirank") / "DILIrank_2.0.xlsx"
    out_path = _norm_dir(data_dir, "dilirank") / "dilirank.jsonl"
    n = write_jsonl(_dilirank_rows(raw), out_path)
    return {"source_id": "dilirank", "rows": n, "output": str(out_path)}


def _meddra_rows(path: Path) -> Iterator[dict]:
    # primary_soc export: one row per LLT, EN+CN names, full hierarchy.
    # The file declares a bogus <dimension>A1</dimension>; iter_xlsx_rows ignores it.
    rows = iter_xlsx_rows(path)
    header = next(rows, None)
    if header is None:
        return
    keys = [str(h) for h in header]
    for row in rows:
        if not any(c is not None for c in row):
            continue
        rec = {keys[i]: _clean(row[i]) for i in range(min(len(keys), len(row)))}
        rec["primary_soc"] = rec.get("primary_soc") in ("1", "Y", "true", "True")
        yield rec


def normalize_meddra(data_dir: Path) -> dict:
    raw = _raw_dir(data_dir, "meddra") / "meddra_primary_soc.xlsx"
    out_path = _norm_dir(data_dir, "meddra") / "meddra_terms.jsonl"
    n = write_jsonl(_meddra_rows(raw), out_path)
    return {"source_id": "meddra", "rows": n, "output": str(out_path)}


_DDINTER_SEVERITY = {"Major": 3, "Moderate": 2, "Minor": 1, "Unknown": 0}
_DDINTER_CODES = ["A", "B", "C", "D", "G", "H", "L", "M", "N", "P", "R", "S", "V"]


def _ddinter_rows(raw_dir: Path) -> Iterator[dict]:
    """Collapse the 13 per-ATC-group DDInter2 CSVs into one record per UNORDERED
    drug pair. A pair recurs across groups (a drug has several ATC codes), so we
    accumulate then emit: keep the most severe Level, record which ATC level-1
    groups the pair appeared under. Drug names are kept lowercased for joining
    (the bulk export has no InChIKey; Stage-2 maps DDInterID/name -> drug there)."""
    pairs: dict[tuple[str, str], dict] = {}
    for code in _DDINTER_CODES:
        path = raw_dir / f"ddinter_downloads_code_{code}.csv"
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # header: DDInterID_A,Drug_A,DDInterID_B,Drug_B,Level
            for row in reader:
                if len(row) != 5:
                    continue
                id_a, name_a, id_b, name_b, level = (c.strip() for c in row)
                if not id_a or not id_b:
                    continue
                # order by DDInterID so the pair key is direction-independent
                if id_a > id_b:
                    id_a, name_a, id_b, name_b = id_b, name_b, id_a, name_a
                key = (id_a, id_b)
                rec = pairs.get(key)
                if rec is None:
                    pairs[key] = {
                        "ddinter_id_a": id_a, "drug_a": name_a,
                        "ddinter_id_b": id_b, "drug_b": name_b,
                        "level": level, "atc_groups": [code],
                    }
                else:
                    if code not in rec["atc_groups"]:
                        rec["atc_groups"].append(code)
                    if _DDINTER_SEVERITY.get(level, 0) > _DDINTER_SEVERITY.get(rec["level"], 0):
                        rec["level"] = level
    for rec in pairs.values():
        rec["drug_a_lc"] = rec["drug_a"].lower()
        rec["drug_b_lc"] = rec["drug_b"].lower()
        yield rec


def normalize_ddinter2(data_dir: Path) -> dict:
    raw_dir = _raw_dir(data_dir, "ddinter2")
    out_path = _norm_dir(data_dir, "ddinter2") / "ddinter_pairs.jsonl"
    n = write_jsonl(_ddinter_rows(raw_dir), out_path)
    return {"source_id": "ddinter2", "rows": n, "output": str(out_path)}


# source_id -> (normalizer, input filename, output filename, schema fields)
REGISTRY: dict[str, dict] = {
    "ncbi_gene": {
        "fn": normalize_ncbi_gene,
        "input": "gene_info.gz",
        "output": "gene_human.jsonl",
        "schema": {
            "fields": ["gene_id", "symbol", "synonyms", "description", "type_of_gene",
                       "chromosome", "map_location", "hgnc_id", "ensembl_gene_id", "mim_id", "dbxrefs"],
            "filter": "tax_id == 9606 (human subset)",
        },
        "notes": "Human subset of NCBI gene_info; dbXrefs split into per-prefix lists.",
    },
    "hgnc": {
        "fn": normalize_hgnc,
        "input": "hgnc_complete_set.json",
        "output": "hgnc.jsonl",
        "schema": {
            "fields": ["hgnc_id", "symbol", "name", "status", "locus_group", "locus_type",
                       "location", "alias_symbol", "prev_symbol", "entrez_id",
                       "ensembl_gene_id", "uniprot_ids", "refseq_accession", "omim_id"],
            "filter": "all approved+ records",
        },
        "notes": "Flattened HGNC Solr docs to one record per gene.",
    },
    "uniprot_swissprot": {
        "fn": normalize_uniprot,
        "input": "uniprot_sprot_human.xml.gz",
        "output": "protein_human.jsonl",
        "schema": {
            "fields": ["primary_accession", "accessions", "entry_name", "protein_name",
                       "gene_primary", "gene_synonyms", "function", "keywords", "xrefs", "sequence_length"],
            "filter": "reviewed human (already filtered at download)",
        },
        "notes": "Streaming iterparse over UniProtKB XML; xrefs limited to GeneID/HGNC/Ensembl/Reactome/PDB.",
    },
    "atc": {
        "fn": normalize_atc,
        "input": "atc1-4_rxclass.json",
        "output": "atc_classes.jsonl",
        "schema": {
            "fields": ["atc_code", "name", "level", "class_type"],
            "filter": "ATC levels 1-4 from NLM RxClass; synthetic root '0' dropped",
        },
        "notes": "RxClass ATC1-4 snapshot; level derived from code length (1/3/4/5 chars).",
    },
    "cpic": {
        "fn": normalize_cpic,
        "input": "cpic_pairs.json",
        "output": "cpic_pairs.jsonl",
        "schema": {
            "fields": ["pair_id", "gene", "drug_id", "guideline_id", "cpic_level",
                       "clinpgx_level", "pgx_testing", "used_for_recommendation", "citations", "removed"],
            "filter": "all gene-drug pairs",
        },
        "notes": "CPIC gene-drug pairs from the CPIC API; drug_id keeps the RxNorm: prefix.",
    },
    "dpwg": {
        "fn": normalize_dpwg,
        "input": "guidelineAnnotations.json.zip",
        "output": "dpwg_guidelines.jsonl",
        "schema": {
            "fields": ["guideline_id", "name", "source", "drugs", "genes", "has_recommendation",
                       "alternate_drug_available", "pediatric", "summary", "citation_count"],
            "filter": "one record per guideline annotation JSON in the zip",
        },
        "notes": "PharmGKB guideline-annotation bundle (DPWG + others); summary from summaryMarkdown.",
    },
    "ctcae_v5": {
        "fn": normalize_ctcae,
        "input": "CTCAE_v5.0.xlsx",
        "output": "ctcae_terms.jsonl",
        "schema": {
            "fields": ["meddra_code", "soc", "term", "grade_1", "grade_2", "grade_3",
                       "grade_4", "grade_5", "definition", "navigational_note"],
            "filter": "sheet 'CTCAE v5.0 Clean Copy'",
        },
        "notes": "CTCAE v5.0 adverse-event grading; one record per term with 5 severity grades.",
    },
    "dilirank": {
        "fn": normalize_dilirank,
        "input": "DILIrank_2.0.xlsx",
        "output": "dilirank.jsonl",
        "schema": {
            "fields": ["ltkb_id", "compound_name", "severity_class", "label_section",
                       "vdili_concern", "comment"],
            "filter": "sheet 'version 2'; banner + header rows dropped",
        },
        "notes": "FDA DILIrank 2.0 drug-induced liver injury concern classes.",
    },
    "meddra": {
        "fn": normalize_meddra,
        "input": "meddra_primary_soc.xlsx",
        "output": "meddra_terms.jsonl",
        "schema": {
            "fields": ["llt_code", "llt_name_en", "llt_name_cn", "llt_currency",
                       "pt_code", "pt_name_en", "pt_name_cn", "hlt_code", "hlt_name_en", "hlt_name_cn",
                       "hlgt_code", "hlgt_name_en", "hlgt_name_cn", "soc_code", "soc_name_en",
                       "soc_name_cn", "soc_abbrev", "primary_soc"],
            "filter": "primary_soc export: one row per LLT with full LLT->PT->HLT->HLGT->SOC hierarchy",
        },
        "notes": "MedDRA 28.1 (license-gated, manually placed). Uses the primary_soc workbook "
                 "(EN+CN, one row per LLT) rather than the multi-axial 术语全集 export.",
    },
    "ddinter2": {
        "fn": normalize_ddinter2,
        "input": "ddinter_downloads_code_A.csv",
        "inputs": [f"ddinter_downloads_code_{c}.csv" for c in _DDINTER_CODES],
        "output": "ddinter_pairs.jsonl",
        "schema": {
            "fields": ["ddinter_id_a", "drug_a", "ddinter_id_b", "drug_b", "level",
                       "atc_groups", "drug_a_lc", "drug_b_lc"],
            "filter": "13 per-ATC-group bulk CSVs collapsed to one record per unordered "
                      "drug pair; most-severe Level kept; atc_groups lists the level-1 "
                      "groups the pair appears under",
        },
        "notes": "DDInter 2.0 curated DDIs. Bulk export has no InChIKey — keyed by "
                 "DDInterID + drug name (lowercased *_lc for joining). level in "
                 "{Major,Moderate,Minor,Unknown}. Feeds Stage-2 DDI beside DrugBank.",
    },
}


def normalize_source(source_id: str, data_dir: Path) -> dict:
    if source_id not in REGISTRY:
        raise KeyError(f"No normalizer registered for '{source_id}'. Known: {sorted(REGISTRY)}")
    spec = REGISTRY[source_id]
    raw_dir = _raw_dir(data_dir, source_id)
    result = spec["fn"](data_dir)
    out_path = Path(result["output"])
    input_names = spec.get("inputs", [spec["input"]])
    inputs = [{
        "filename": name,
        "path": str(raw_dir / name),
        "sha256": _read_source_sha(raw_dir, name),
    } for name in input_names]
    outputs = [{
        "filename": out_path.name,
        "path": str(out_path),
        "rows": result["rows"],
        "sha256": sha256_file(out_path) if out_path.exists() else None,
    }]
    meta_path = write_normalized_metadata(
        source_id=source_id, data_dir=data_dir,
        inputs=inputs, outputs=outputs, schema=spec["schema"], notes=spec.get("notes"),
    )
    result["metadata"] = str(meta_path)
    return result
