# PersTox-Agent

**面向药物毒性预测的两阶段 Agent 知识库与工具层。**

PersTox-Agent 把分散的药理/毒理/药物基因组学数据源组织成 Agent 可检索的本地知识库，
并在其上实现一组工具，支撑两阶段推理：

- **Stage 1 — 通用毒性**：分子结构 → ADMET 端点、药物-靶点、机制通路、已知 ADE 全谱。
- **Stage 2 — 个性化毒性**：叠加患者画像与合并用药 → 相似人群 ADE 分布、药物相互作用筛查。

设计强调**本地优先、可复现、可审计**：原始数据只读，派生表带 provenance（输入哈希、行数、
schema），API 响应 cache-first 落盘，跨库统一以 **InChIKey** 为连接键。

---

## 架构

```
config/kb_sources.json     # 数据源登记表（48 源：tier / access / agent_strategy / 许可）
kb_builder/                # 知识库构建层
  downloader.py            #   直接下载（跳过 license-gated / api 源）
  api_cache.py             #   cache-first GET/POST 连接器 + provenance
  normalize.py             #   raw -> data/normalized/<id>/*.jsonl 标准化器（REGISTRY）
  manifest.py              #   运行清单
scripts/                   # CLI 入口
  download_kb_sources.py   #   下载（支持 --strict-t1 / --mvp / --dry-run）
  normalize_kb_sources.py  #   标准化
  query_api_cache.py       #   API 缓存查询示例
tool/                      # Agent 工具层（8 个工具，见 tool/README.md）
  common.py                #   共享：drug 解析 / 流式读 / InChIKey 桥 / API 缓存 / 计时
  drugbank_tool.py         #   DrugBank 流式 XML -> JSONL + 字节偏移索引
  mechanism_admet/         #   机制 / ADMET 组（6）
  ade_profile/             #   ADE 谱组（2）
test/                      # smoke test + 计时
  test_tools.py            #   8 工具 Warfarin 驱动全跑 + 速度汇总
data/                      # 数据（不入 git；见下「数据获取」）
doc/                       # 设计文档
```

详见各子目录 README：[tool/README.md](tool/README.md)（工具 I/O / 数据源 / 速度）、
`data/normalized/README.md`、`data/raw/README.md`。

---

## 安装

需要 **Python ≥ 3.10**（代码使用 `X | Y` 类型注解）。唯一第三方依赖是 **RDKit**
（SMILES → InChIKey 桥）；统计与检索逻辑全部基于标准库，无 scipy/numpy/pandas。

```bash
# 推荐用 conda-forge 装 RDKit（最省心）
conda create -n perstox python=3.10 -c conda-forge rdkit
conda activate perstox
pip install -r requirements.txt   # 仅 rdkit；conda 已装则可跳过
```

> 联网工具（API 补充）若在缺 certifi 的环境运行，需先指向系统 CA：
> `export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`

---

## 数据获取

代码与数据分离：**仓库只含代码**，数据通过单独的压缩包分发（见随附 `perstox-data-*.tar.zst`）。
解压后目录结构应为 `data/normalized/<id>/`、`data/raw/reactome/`、`data/quarantine_non_t1/go/`。

```bash
tar --use-compress-program=unzstd -xf perstox-data-*.tar.zst   # 解压到项目根
```

**用户需自备的源**（体量大或来自独立提供，未随包分发）：

| 源 | 放置位置 | 用途（工具） | 获取方式 |
|---|---|---|---|
| **PersADE** | `data/PersADE/` | dti / mechanism / persade_drug_profile / contextual_retrieval | 自有/提供，已标准化，直接放入 |
| **admetSAR 3.0** | `data/admetsar3_all_endpoints.txt` | admetsar_predict | 从 admetSAR 官方获取 |

放好后无需额外构建：DrugBank 已是 `data/normalized/drugbank/` 的派生 JSONL + 索引；
若你持有 DrugBank 原始 XML 并想自行重建，把它放到 `data/raw/drugbank/` 再跑
`python -m tool.drugbank_tool build`。

---

## 工具用法

8 个工具统一 `run(payload) -> dict` + argparse CLI。完整 I/O / 数据源 / 速度见
**[tool/README.md](tool/README.md)**。

```bash
# 程序内
from tool.mechanism_admet.dti_query import run
run({"drug": "warfarin", "with_api": False})

# 命令行
python -m tool.mechanism_admet.dti_query warfarin
python -m tool.mechanism_admet.pathway_enrich VKORC1 CYP2C9 F2 GGCX
python -m tool.mechanism_admet.admetsar_predict warfarin --with-api   # 补充 ADMETlab 3.0
```

跑全部 8 工具的 smoke test + 速度汇总：

```bash
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt python test/test_tools.py
```

---

## 许可与数据使用

本仓库**代码**可按仓库 LICENSE 使用。**数据**各自遵循原始来源的许可，使用者自行承担合规责任：

- **DrugBank** — 学术非商用许可；原始记录不可再分发，需遵守署名与非商用条款。
- **MedDRA** — MSSO 订阅授权；不可公开再分发。
- **WHOCC ATC/DDD** — 需署名，禁止商业再分发、禁止篡改材料。
- **DDInter2 / Reactome / GO / UniProt / HGNC / NCBI Gene / CTCAE / DILIrank / CPIC / DPWG**
  — 开放或学术许可，使用时注明出处。
- **PersADE / admetSAR** — 由用户自备，遵循各自来源条款。

原始文件一律只读，派生/标准化输出写入独立路径（`data/normalized/`），工具仅读不改、
不再分发原始记录。
