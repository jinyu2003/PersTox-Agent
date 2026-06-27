"""indication_resolver — diagnosis/indication text -> UMLS CUI.

临床输入: 诊断/适应症文本 (可带前导 ICD-10 码, 如 "K74.6 cirrhosis")。
临床输出: {input, icd10, name, umls_cui, mesh_id, matched}。
数据源:
  - 本地: PersADE INDI_UMLS.txt (CUI <tab> 同义词1|同义词2|... <tab> MeSH <tab> 树号)。

解析策略: 懒加载并缓存一个 "同义词(小写) -> (CUI, MeSH)" 的反向索引;输入先剥离
前导 ICD-10 码再做精确同义词匹配 (大小写无关)。不做模糊/向量匹配,未命中标
matched=False, 保留 icd10 供下游审计。
"""
from __future__ import annotations

import argparse
import re
from functools import lru_cache
from typing import Dict, Optional, Tuple

from pertox_agent.tools.shared.common import PERSADE, timed

# Leading ICD-10 code, e.g. "K74.6 cirrhosis" / "I48 atrial fibrillation".
_ICD10_PREFIX = re.compile(r"^\s*([A-TV-Z]\d{2}(?:\.\d{1,2})?)\b\s*", re.IGNORECASE)

_INDI_FILE = PERSADE / "INDI_UMLS.txt"


@lru_cache(maxsize=1)
def _synonym_index() -> Dict[str, Tuple[str, Optional[str]]]:
    """Build "synonym(lower) -> (CUI, MeSH)" once. Earlier rows win on clash."""
    index: Dict[str, Tuple[str, Optional[str]]] = {}
    if not _INDI_FILE.exists():
        return index
    with _INDI_FILE.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 2 or not cols[0]:
                continue
            cui = cols[0].strip()
            mesh = cols[2].strip() if len(cols) > 2 and cols[2].strip() else None
            for synonym in cols[1].split("|"):
                key = synonym.strip().lower()
                if key:
                    index.setdefault(key, (cui, mesh))
    return index


def split_icd10(text: str) -> Tuple[Optional[str], str]:
    """Strip a leading ICD-10 code; return (icd10_or_None, remaining_name)."""
    match = _ICD10_PREFIX.match(text or "")
    if not match:
        return None, (text or "").strip()
    return match.group(1).upper(), text[match.end():].strip()


def resolve_indication(text: str) -> Dict[str, object]:
    """Resolve one diagnosis string to a UMLS concept (local INDI_UMLS)."""
    raw = (text or "").strip()
    icd10, name = split_icd10(raw)
    index = _synonym_index()
    hit = index.get(name.lower()) if name else None
    return {
        "input": raw,
        "icd10": icd10,
        "name": name or None,
        "umls_cui": hit[0] if hit else None,
        "mesh_id": hit[1] if hit else None,
        "matched": hit is not None,
    }


@timed
def run(payload) -> dict:
    """payload: str | {text|diagnosis|indication: str} | {indications: [str]}."""
    if isinstance(payload, dict):
        items = payload.get("indications")
        if items is None:
            single = payload.get("text") or payload.get("diagnosis") or payload.get("indication") or ""
            items = [single]
    else:
        items = [payload]

    results = [resolve_indication(str(item)) for item in items if str(item).strip()]
    return {
        "tool": "indication_resolver",
        "source": "PersADE INDI_UMLS",
        "n_matched": sum(1 for r in results if r["matched"]),
        "results": results,
    }


def main(argv=None) -> int:
    import json

    ap = argparse.ArgumentParser(description="diagnosis/indication text -> UMLS CUI")
    ap.add_argument("text", nargs="+", help="diagnosis text, e.g. 'K74.6 cirrhosis'")
    args = ap.parse_args(argv)
    print(json.dumps(run({"indications": args.text}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

