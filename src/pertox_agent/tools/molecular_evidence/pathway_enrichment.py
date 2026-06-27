"""pathway_enrich — pathway over-representation analysis for a gene/protein set.

临床输入: 基因/蛋白集 (Gene Symbol 列表;也接受 UniProt ID)
临床输出: 富集通路 (KEGG / Reactome / MSigDB ID + 名称) + p 值 (超几何检验) +
          q 值 (Benjamini-Hochberg 校正) + 毒性相关通路标记。
数据源:
  - 本地: PersADE uniprot_pathway.txt (UniProt->pathway 列表, 6917 基因, 背景全集)
          + Pathway.txt (pathway_id->名称/来源/功能分类) [T2]
  - 本地补充: Reactome UniProt2Reactome (data/raw/reactome) [T2] 作通路名称/层级;
              GO goa_human (data/quarantine_non_t1/go) [T2] 可选功能注释。

PersADE 通路全集以 MSigDB_Immunologic 基因表达签名为主(对毒性机制是噪声),故富集
默认仅统计可解释来源 (KEGG/Reactome/MSigDB_Hallmark/WikiPathways/BioCarta);可经
sources 参数放开。超几何检验无 scipy 依赖,用 stdlib math.comb 精确计算。
"""
from __future__ import annotations

import argparse
import math
from functools import lru_cache

from pertox_agent.tools.shared.common import GO_DIR, PERSADE, REACTOME_DIR, TPATH, goa_uniprot_to_symbol, timed

DEFAULT_SOURCES = ("KEGG", "Reactome", "MSigDB_Hallmark", "MSigDB_WikiPathways",
                   "MSigDB_BioCarta", "MSigDB_Canonical", "GO")
# pathway-name substrings flagging toxicologically relevant processes
TOX_KEYWORDS = ("apoptosis", "oxidative", "mitochond", "inflamm", "p53", "DNA damage",
                "necros", "drug metab", "cytochrome", "xenobiotic", "bile", "fibros",
                "cardiac", "arrhythm", "hepat", "lipid", "coagulation", "complement")


@lru_cache(maxsize=1)
def _load_universe() -> tuple[dict, dict]:
    """Build the background universe from uniprot_pathway.txt:
      gene2pw: {gene_symbol_upper: set(pathway_id)}
      pw2genes: {pathway_id: set(gene_symbol_upper)}
    pathway_id is the raw 'Source:LocalID' token. Cached for the process."""
    gene2pw: dict[str, set] = {}
    pw2genes: dict[str, set] = {}
    with (PERSADE / "uniprot_pathway.txt").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) <= TPATH["Pathway_ID"]:
                continue
            gene = (c[TPATH["Gene_Symbol"]] or "").upper()
            if not gene:
                continue
            pws = {p for p in c[TPATH["Pathway_ID"]].split("|") if p}
            gene2pw.setdefault(gene, set()).update(pws)
            for p in pws:
                pw2genes.setdefault(p, set()).add(gene)
    return gene2pw, pw2genes


@lru_cache(maxsize=1)
def _load_reactome() -> tuple[dict, dict, dict]:
    """Reactome human pathways as (gene2pw, pw2genes, names). UniProt accessions
    in UniProt2Reactome are bridged to gene symbols via the GO GAF. pathway_id =
    'Reactome:R-HSA-xxxxx'. Empty if files absent (graceful)."""
    gene2pw: dict[str, set] = {}
    pw2genes: dict[str, set] = {}
    names: dict[str, str] = {}
    fp = REACTOME_DIR / "UniProt2Reactome_All_Levels.txt"
    if not fp.exists():
        return gene2pw, pw2genes, names
    u2s = goa_uniprot_to_symbol()
    with fp.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 6 or c[5] != "Homo sapiens":
                continue
            sym = u2s.get(c[0].split("-")[0])  # strip isoform suffix
            if not sym:
                continue
            pid = f"Reactome:{c[1]}"
            g = sym.upper()
            gene2pw.setdefault(g, set()).add(pid)
            pw2genes.setdefault(pid, set()).add(g)
            names[pid] = c[3]
    return gene2pw, pw2genes, names


@lru_cache(maxsize=1)
def _load_go() -> tuple[dict, dict, dict]:
    """GO human biological annotations as (gene2pw, pw2genes, names) from
    goa_human.gaf (col2=symbol, col4=GO id) + go-basic.obo (id->name).
    pathway_id = 'GO:GO:xxxxxxx'. Empty if files absent (graceful)."""
    import gzip
    gene2pw: dict[str, set] = {}
    pw2genes: dict[str, set] = {}
    names: dict[str, str] = {}
    gaf = GO_DIR / "goa_human.gaf.gz"
    if not gaf.exists():
        return gene2pw, pw2genes, names
    obo = GO_DIR / "go-basic.obo"
    if obo.exists():
        cur = False
        gid = None
        with obo.open(encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line == "[Term]":
                    cur, gid = True, None
                elif cur and line.startswith("id: GO:"):
                    gid = line[4:]
                elif cur and gid and line.startswith("name: "):
                    names[f"GO:{gid}"] = line[6:]
                    cur = False
    with gzip.open(gaf, "rt", errors="replace") as f:
        for line in f:
            if line.startswith("!"):
                continue
            c = line.split("\t")
            if len(c) <= 4 or not c[2] or not c[4].startswith("GO:"):
                continue
            pid = f"GO:{c[4]}"
            g = c[2].upper()
            gene2pw.setdefault(g, set()).add(pid)
            pw2genes.setdefault(pid, set()).add(g)
    return gene2pw, pw2genes, names


@lru_cache(maxsize=None)
def _merged_universe(sources: tuple) -> tuple[dict, dict, dict]:
    """Merge the background universes of the requested sources into a single
    (gene2pw, pw2genes, names). PersADE local-id pathways resolve their names
    lazily in run() via _pathway_names; Reactome/GO carry their own names here."""
    g2p: dict[str, set] = {}
    p2g: dict[str, set] = {}
    names: dict[str, str] = {}
    persade_srcs = {s for s in sources if s not in ("Reactome", "GO")}
    if persade_srcs:
        pg, pp = _load_universe()
        for g, pws in pg.items():
            keep = {p for p in pws if _pathway_source(p) in persade_srcs}
            if keep:
                g2p.setdefault(g, set()).update(keep)
        for p, gs in pp.items():
            if _pathway_source(p) in persade_srcs:
                p2g[p] = set(gs)
    for src, loader in (("Reactome", _load_reactome), ("GO", _load_go)):
        if src in sources:
            sg, sp, sn = loader()
            for g, pws in sg.items():
                g2p.setdefault(g, set()).update(pws)
            p2g.update(sp)
            names.update(sn)
    return g2p, p2g, names


@lru_cache(maxsize=1)
def _pathway_names() -> dict:
    """Pathway local-id -> {name, source, functional_category} from Pathway.txt."""
    from pertox_agent.tools.shared.common import PATHWAY
    out = {}
    with (PERSADE / "Pathway.txt").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) > PATHWAY["Functional_Category"]:
                out[c[PATHWAY["Pathway_ID"]]] = {
                    "name": c[PATHWAY["Pathway_Name"]], "source": c[PATHWAY["Source"]],
                    "functional_category": c[PATHWAY["Functional_Category"]]}
    return out


def _hypergeom_sf(k: int, M: int, n: int, N: int) -> float:
    """P(X >= k) for hypergeometric(M population, n successes, N draws), exact via
    math.comb. k = observed overlap. Clamped/guarded for edge cases."""
    if k <= 0:
        return 1.0
    denom = math.comb(M, N)
    if denom == 0:
        return 1.0
    upper = min(n, N)
    total = sum(math.comb(n, i) * math.comb(M - n, N - i) for i in range(k, upper + 1))
    return min(1.0, total / denom)


def _bh_qvalues(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg FDR. Returns q-values aligned to input order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [0.0] * m
    prev = 1.0
    for rank, idx in enumerate(reversed(order), start=1):
        i = m - rank + 1
        val = min(prev, pvals[idx] * m / i)
        q[idx] = prev = val
    return q


def _pathway_source(pid: str) -> str:
    return pid.split(":", 1)[0] if ":" in pid else "?"


@timed
def run(payload) -> dict:
    """payload: list[str] genes | {genes:[...], sources?:[...], max_results?:int}."""
    if isinstance(payload, dict):
        genes = payload.get("genes") or payload.get("gene_set") or []
        sources = tuple(payload.get("sources") or DEFAULT_SOURCES)
        max_results = payload.get("max_results", 25)
    else:
        genes, sources, max_results = list(payload), DEFAULT_SOURCES, 25
    genes_up = {g.upper() for g in genes if g}
    gene2pw, pw2genes, ext_names = _merged_universe(sources)
    persade_names = _pathway_names()
    M = len(gene2pw)                                  # background population
    hits = genes_up & gene2pw.keys()                  # query genes seen in universe
    N = len(hits)
    out = {"tool": "pathway_enrich", "n_query_genes": len(genes_up),
           "n_genes_in_universe": N, "background_size": M,
           "sources": list(sources), "n_pathways_tested": 0, "pathways": []}
    if N == 0:
        out["_note"] = "no query gene found in the pathway universe for these sources"
        return out
    # candidate pathways = those touched by query genes (universe already source-filtered)
    cand: dict[str, set] = {}
    for g in hits:
        for pid in gene2pw[g]:
            cand.setdefault(pid, set()).add(g)
    rows, pvals = [], []
    for pid, ov in cand.items():
        n_pw = len(pw2genes.get(pid, ()))             # successes in population
        p = _hypergeom_sf(len(ov), M, n_pw, N)
        local = pid.split(":", 1)[1] if ":" in pid else pid
        meta = persade_names.get(local, {})           # PersADE-sourced name/category
        nm = ext_names.get(pid) or meta.get("name") or local  # Reactome/GO carry full-pid names
        rows.append({"pathway_id": pid, "name": nm, "source": _pathway_source(pid),
                     "overlap": sorted(ov), "k": len(ov), "pathway_size": n_pw,
                     "p_value": p, "functional_category": meta.get("functional_category"),
                     "tox_related": any(kw in nm.lower() for kw in TOX_KEYWORDS)})
        pvals.append(p)
    for r, q in zip(rows, _bh_qvalues(pvals)):
        r["q_value"] = q
    rows.sort(key=lambda r: (r["p_value"], -r["k"]))
    out["n_pathways_tested"] = len(rows)
    out["pathways"] = rows[:max_results]
    out["tox_pathways"] = [r["name"] for r in rows[:max_results] if r["tox_related"]]
    return out


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="pathway over-representation analysis")
    ap.add_argument("genes", nargs="+", help="gene symbols")
    ap.add_argument("--all-sources", action="store_true", help="include MSigDB_Immunologic etc.")
    args = ap.parse_args(argv)
    pl = {"genes": args.genes}
    if args.all_sources:
        pl["sources"] = ["KEGG", "Reactome", "GO", "MSigDB_Hallmark", "MSigDB_WikiPathways",
                         "MSigDB_BioCarta", "MSigDB_Canonical", "MSigDB_Immunologic",
                         "MSigDB_Oncogenic"]
    print(json.dumps(run(pl), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

