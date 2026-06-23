# PersTox-Agent 数据包

配套 [PersTox-Agent](https://github.com/) 代码仓库的标准化知识库数据。

## 解压

解压到**项目根目录**（与 `tool/`、`kb_builder/` 同级），即就位：

```bash
cd PersTox-Agent
tar --use-compress-program=unzstd -xf perstox-data-20260623.tar.zst
```

解压后得到 `data/normalized/<id>/`、`data/raw/reactome/`、`data/quarantine_non_t1/go/`。

## 校验

```bash
sha256sum -c perstox-data-20260623.tar.zst.sha256
```

## 包含内容（约 925 MB 解压后 / 56 MB 压缩）

标准化派生表 `data/normalized/`（Agent 工具运行时实际检索的层）：

| 源 | 用途（工具） |
|---|---|
| `drugbank/` | drugbank_metabolism_query / dti_query / ddi_query（JSONL + 字节偏移索引） |
| `ddinter2/` | ddi_query（231k 药对 + severity） |
| `meddra/` | ADE SOC 对齐 |
| `ncbi_gene/` `hgnc/` `uniprot_swissprot/` | 基因/蛋白标识符标准化 |
| `ctcae_v5/` `dilirank/` `cpic/` `dpwg/` `atc/` | 严重度 / PGx / ATC 辅助 |

pathway_enrich 专用本地源：

| 源 | 位置 |
|---|---|
| Reactome（人类通路映射） | `data/raw/reactome/` |
| GO（`goa_human.gaf` + `go-basic.obo`） | `data/quarantine_non_t1/go/` |

## **不包含**（需自备）

| 源 | 放置位置 | 工具 |
|---|---|---|
| **PersADE** | `data/PersADE/` | dti / mechanism / persade_drug_profile / contextual_retrieval |
| **admetSAR 3.0** | `data/admetsar3_all_endpoints.txt` | admetsar_predict |

没有这两个源时，依赖它们的工具会优雅报错（返回 error 字段），其余工具正常。

## 许可

各源遵循原始来源许可。**DrugBank（学术非商用，不可再分发原始记录）、MedDRA（MSSO 订阅）、
WHOCC-ATC（禁止商业再分发/篡改）** 为许可受限源——使用与再分发本数据包者须自行确保已获得相应
授权并遵守各自条款。其余源（Reactome / GO / UniProt / HGNC / NCBI Gene / DDInter2 / CTCAE /
DILIrank / CPIC / DPWG）为开放或学术许可，使用时注明出处。
