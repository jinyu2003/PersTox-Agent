# 标准化知识库数据

这个目录存放由 `scripts/normalize_kb_sources.py` 从 `data/raw/<id>/` 派生出的结构化数据。

- 原始文件（`data/raw/`）保持只读、不修改。
- 标准化输出为 newline-delimited JSON（`.jsonl`），每行一条记录，便于流式读取大文件。
- 每个源写入 `_normalized_metadata.json`，记录输入文件哈希、输出行数与哈希、字段 schema 和过滤说明，可审计、可复现。

当前已标准化的源：

| 源 | 输入 | 输出 | 行数 | 说明 |
|---|---|---|---|---|
| ncbi_gene | gene_info.gz (全物种) | gene_human.jsonl | 193802 | 仅保留 tax_id=9606 人类子集；dbXrefs 按前缀拆分 |
| hgnc | hgnc_complete_set.json | hgnc.jsonl | 44997 | Solr docs 扁平化，一基因一记录 |
| uniprot_swissprot | uniprot_sprot_human.xml.gz | protein_human.jsonl | 20431 | iterparse 流式解析；xrefs 限 GeneID/HGNC/Ensembl/Reactome/PDB |

重新生成全部：

```bash
python scripts/normalize_kb_sources.py
```

只生成某个源：

```bash
python scripts/normalize_kb_sources.py --source ncbi_gene
```

列出已注册的标准化器：

```bash
python scripts/normalize_kb_sources.py --list
```
