"""pgx_phenotype — pharmacogenomic genotype (diplotype) -> phenotype.

把基因型翻译成表型

临床输入: 基因 + 二倍型 (如 CYP2C9 "*2/*3") 或 {genotypes: {gene: diplotype}}。
临床输出: {gene, diplotype, phenotype, actionable, source} 列表。

LIMITATION / 覆盖说明: 项目 normalized 数据里没有标准的 CPIC allele-function /
diplotype->phenotype 表, 因此本工具用**规则化**映射, 仅覆盖常见药物基因:
  CYP2C9 / CYP2C19 / CYP2D6 / VKORC1 / TPMT / NUDT15 / DPYD / UGT1A1 / SLCO1B1 / HLA-*。
代谢酶基因走 "等位基因功能分值求和 -> 表型" 的简化 activity-score 思路; 非酶基因
(VKORC1 / SLCO1B1 / HLA) 走专门规则。actionable 用 normalized/cpic/cpic_pairs.jsonl
里 cpic_level in {A,B} 的基因集合交叉标注。未覆盖基因返回 phenotype="indeterminate"。

"""
from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from typing import Dict, List, Optional, Set

from pertox_agent.tools.shared.common import NORMALIZED, timed

# Allele -> function score (CPIC-style: no=0, decreased=0.5, normal=1, increased=1.5).
# Coverage is intentionally limited to common, well-characterized alleles.
_NO_FUNCTION: Dict[str, Set[str]] = {
    "CYP2C9": {"*3", "*6", "*8", "*11", "*13", "*15"},
    "CYP2C19": {"*2", "*3", "*4", "*5", "*6", "*7", "*8"},
    "CYP2D6": {"*3", "*4", "*5", "*6", "*7", "*8"},
    "TPMT": {"*2", "*3A", "*3B", "*3C", "*4"},
    "NUDT15": {"*2", "*3"},
    "DPYD": {"*2A", "*13"},
}
_DECREASED_FUNCTION: Dict[str, Set[str]] = {
    "CYP2C9": {"*2", "*5", "*9", "*12"},
    "CYP2D6": {"*9", "*10", "*17", "*29", "*41"},
    "DPYD": {"c.2846A>T"},
    "UGT1A1": {"*6", "*28", "*37"},
    "SLCO1B1": {"*5", "*15", "*17"},
}
_INCREASED_FUNCTION: Dict[str, Set[str]] = {
    "CYP2C19": {"*17"},
}

_METABOLIZER_GENES = {"CYP2C9", "CYP2C19", "CYP2D6", "TPMT", "NUDT15", "DPYD"}

_CPIC_FILE = NORMALIZED / "cpic" / "cpic_pairs.jsonl"


@lru_cache(maxsize=1)
def _actionable_genes() -> Set[str]:
    """Genes with a CPIC level A/B pair in the normalized CPIC table."""
    genes: Set[str] = set()
    if not _CPIC_FILE.exists():
        return genes
    with _CPIC_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("cpic_level", "")).upper() in {"A", "B"}:
                gene = str(row.get("gene", "")).upper()
                if gene:
                    genes.add(gene)
    return genes


def _allele_score(gene: str, allele: str) -> float:
    allele = allele.strip()
    if allele in _NO_FUNCTION.get(gene, set()):
        return 0.0
    if allele in _DECREASED_FUNCTION.get(gene, set()):
        return 0.5
    if allele in _INCREASED_FUNCTION.get(gene, set()):
        return 1.5
    return 1.0  # *1 and uncharacterized alleles default to normal function.


def _metabolizer_phenotype(total: float) -> str:
    if total == 0.0:
        return "poor_metabolizer"
    if total <= 1.0:
        return "intermediate_metabolizer"
    if total <= 2.0:
        return "normal_metabolizer"
    if total <= 2.5:
        return "rapid_metabolizer"
    return "ultrarapid_metabolizer"


def _split_alleles(diplotype: str) -> List[str]:
    parts = re.split(r"[/|]", diplotype.strip())
    return [p.strip() for p in parts if p.strip()]


def classify(gene: str, diplotype: str) -> Dict[str, object]:
    """Map one (gene, diplotype) to a phenotype with an actionability flag."""
    gene_u = (gene or "").strip().upper()
    diplotype = (diplotype or "").strip()
    actionable = gene_u in _actionable_genes()

    if gene_u in _METABOLIZER_GENES:
        alleles = _split_alleles(diplotype)
        if len(alleles) >= 2:
            total = sum(_allele_score(gene_u, a) for a in alleles[:2])
            phenotype = _metabolizer_phenotype(total)
        else:
            phenotype = "indeterminate"
    elif gene_u == "VKORC1":
        text = diplotype.upper().replace("-1639", "").replace("G>A", "")
        if "AA" in text:
            phenotype = "warfarin_sensitive"
        elif "AG" in text or "GA" in text:
            phenotype = "intermediate_sensitivity"
        elif "GG" in text:
            phenotype = "normal_sensitivity"
        else:
            phenotype = "indeterminate"
    elif gene_u == "SLCO1B1":
        alleles = _split_alleles(diplotype)
        score = sum(_allele_score("SLCO1B1", a) for a in alleles[:2]) if len(alleles) >= 2 else 2.0
        phenotype = (
            "poor_function" if score == 0.0
            else "decreased_function" if score <= 1.0
            else "normal_function"
        )
    elif gene_u == "UGT1A1":
        alleles = _split_alleles(diplotype)
        score = sum(_allele_score("UGT1A1", a) for a in alleles[:2]) if len(alleles) >= 2 else 2.0
        phenotype = (
            "poor_metabolizer" if score == 0.0
            else "intermediate_metabolizer" if score <= 1.0
            else "normal_metabolizer"
        )
    elif gene_u.startswith("HLA"):
        allele = diplotype.lstrip("*") or diplotype
        phenotype = f"risk_allele_carrier:{allele}" if allele else "indeterminate"
        actionable = True
    else:
        phenotype = "indeterminate"

    return {
        "gene": gene_u,
        "diplotype": diplotype,
        "phenotype": phenotype,
        "actionable": actionable,
        "source": "rule-based",
    }


def classify_genotypes(genotypes: Dict[str, str]) -> List[Dict[str, object]]:
    """Map a {gene: diplotype} dict to a list of phenotype records."""
    return [classify(gene, diplotype) for gene, diplotype in (genotypes or {}).items()]


@timed
def run(payload) -> dict:
    """payload: {gene, diplotype} | {genotypes: {gene: diplotype}}."""
    if isinstance(payload, dict) and "genotypes" in payload:
        results = classify_genotypes(payload["genotypes"])
    elif isinstance(payload, dict):
        results = [classify(payload.get("gene", ""), payload.get("diplotype", ""))]
    else:
        results = [classify(str(payload), "")]
    return {
        "tool": "pgx_phenotype",
        "coverage": "rule-based; limited gene set",
        "phenotypes": results,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pharmacogenomic diplotype -> phenotype (rule-based)")
    ap.add_argument("gene", help="gene symbol, e.g. CYP2C9")
    ap.add_argument("diplotype", help="diplotype, e.g. '*2/*3'")
    args = ap.parse_args(argv)
    print(json.dumps(run({"gene": args.gene, "diplotype": args.diplotype}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

