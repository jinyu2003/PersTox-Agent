# PerTox-agent 工具层 (`src/pertox_agent/tools/`)

本目录提供 Agent 推理使用的工具。当前工具按职责分层：临床输入、患者上下文、分子证据、
真实世界证据、毒性归因、共享数据访问和运行时适配。工具返回 payload 中的历史 `tool`
字段名暂时保持不变，以兼容现有 evidence package 和报告逻辑。

## 目录结构

```
src/pertox_agent/tools/
  clinical_input/
    normalizer.py
    drug_parser.py
    patient_parser.py
    semantic_extractor.py
    field_fusion.py
  patient_context/
    standardizer.py
    indication_resolver.py
    pgx_phenotyper.py
  molecular_evidence/
    admet_predictor.py
    drug_target_interaction.py
    drug_drug_interaction.py
    drug_metabolism.py
    mechanism_evidence.py
    pathway_enrichment.py
  real_world_evidence/
    persade_drug_ade_profile.py
    persade_similar_case_retrieval.py
    persade_subgroup_risk.py
  toxicity_attribution/
    toxicity_chain_builder.py
  shared/
    common.py
    drugbank_client.py
  runtime/
    retrieval_runtime.py
```

## 统一调用约定

每个工具暴露 `run(payload) -> dict`，并带一个 argparse CLI：

```bash
# 程序内
from pertox_agent.tools.molecular_evidence.drug_target_interaction import run
run({"drug": "warfarin", "with_api": False})

# 命令行
conda run -n perstox python -m pertox_agent.tools.molecular_evidence.drug_target_interaction warfarin
```

**药物解析**：所有工具经 `tool.shared.common.resolve_drug()` 接受 名称 / 商品名同义词 / DrugBank ID /
SMILES / InChIKey 任一形式，融合 DrugBank + PersADE `drug_all`(含同义词) + RDKit，
统一以 **InChIKey** 为跨库连接键（admetSAR 与 PersADE/DrugBank 的 SMILES 规范化不同，
精确串匹配会漏，InChIKey 是唯一可靠桥）。

**数据策略**：本地优先 + API 补充（cache-first）。带 `with_api=True` 时工具会经
`kb_builder.api_cache` 查询并缓存到 `data/cache/api_responses/`；API 失败时**优雅降级**
为仅本地，绝不阻断。原始数据只读，派生缓存写 `data/cache/`。

## 工具清单（输入 / 输出 / 数据源）

### 机制 / ADMET 组

**`admetsar_predict`** — 分子 ADMET 端点预测
- 输入：SMILES（或名称/DrugBank ID/InChIKey，内部解析）
- 输出：ADMET 端点预测（分类概率/回归）+ drug-likeness + 理化描述符 + 适用域(AD)标志；含 DILI/hERG/Ames/BBB/致癌/线粒体等毒性端点
- 数据源：🟢 本地 admetSAR 3.0（`data/admetsar3_all_endpoints.txt`，按 RDKit InChIKey 匹配）；🔵 API 补充 ADMETlab 3.0（`with_api=True`，`POST /api/single/admet`，cache-first，结果存 `data/cache/api_responses/admetlab3/`；作降权的第二预测器，上游故障时降级标注 `admetlab3._api_note`）

**`dti_query`** — 药物-靶点相互作用
- 输入：药物
- 输出：靶点列表 + 亲和力(IC50/Ki/Kd) + 作用类型 + on/off-target 标注 + 证据级别
- 数据源：🟢 本地 DrugBank targets + PersADE DTI；🔵 API 补充 ChEMBL（`with_api`）。on-target = DrugBank 药理作用 或 存在结合常数；其余 off-target

**`pathway_enrich`** — 通路富集分析
- 输入：基因/蛋白集（Gene Symbol）
- 输出：富集通路(ID+名) + p 值(超几何) + q 值(BH) + 毒性通路标记
- 数据源：🟢 本地 PersADE `uniprot_pathway`+`Pathway` + 🟢 Reactome(`data/raw/reactome`，人类 2822 通路，经 GO GAF 把 UniProt 桥到 Gene Symbol) + 🟢 GO(`data/quarantine_non_t1/go`，`goa_human.gaf`+`go-basic.obo`)。三源合并为统一背景全集（~38.8k 基因），默认统计 KEGG/Reactome/GO/Hallmark/WikiPathways/BioCarta（MSigDB_Immunologic 噪声排除，可经 `sources` 放开）

**`mechanism_query`** — ADE→蛋白→通路 反向机制链
- 输入：ADE(UMLS ID) 或 药物 + 可选器官
- 输出：ADE→蛋白→通路 机制链 + 关联严重度 + PubMed 证据
- 数据源：🟢 本地 PersADE DTA + Target + uniprot_pathway + ADE_Information；🔵 Open Targets（占位，可选）

**`drugbank_metabolism_query`** — 代谢/转运谱
- 输入：药物
- 输出：代谢酶(CYP 底物/抑制/诱导分桶) + 活性/毒性代谢物文本 + 转运体(P-gp/OATP/BCRP…) + 载体 + 消除途径
- 数据源：🟢 本地 DrugBank enzymes/transporters/carriers/metabolism；🔵 API 补充 openFDA drug label（`with_api`）

**`ddi_query`** — 药物相互作用筛查
- 输入：目标药 + 合并用药列表
- 输出：DDI 对 + 机制(PK/PD 归类) + 严重度(Major/Moderate/Minor) + 处置建议
- 数据源：🟢 本地 DrugBank DDI（机制描述）+ DDInter2（`data/normalized/ddinter2`，严重度）；🔵 FAERS 信号（辅助，可选）

### ADE 谱组

**`persade_drug_profile`** — 已知 ADE 全谱
- 输入：药物
- 输出：已知 ADE 全谱(按 MeSH-tree 器官分组) + 频率/严重度 + 证据(ROR/priority/报告数/PubMed)
- 数据源：🟢 本地 PersADE `CCombined_Results_with_scores`(ASS_SCORE) + ADE_Information；🔵 OnSIDES（未本地部署，占位降级）。证据为"信号"级(FAERS 失衡挖掘)

**`persade_contextual_retrieval`** — 相似人群 ADE 分布（k-NN）
- 输入：药物 + 患者画像（age/sex/route/outcome/serious）
- 输出：相似人群 ADE 频率分布 + 队列规模 + 平均相似度
- 数据源：🟢 本地 PersADE `ADE_report.txt`（患者层，4.5GB）。Gower 相似度 k-NN；按 InChIKey 缓存队列到 `data/cache/persade_cohorts/`（冷调用流式过全表，热调用读缓存）

## 平均 Tool Call 速度

由 `tests/test_tools.py` 实测（Warfarin 驱动，perstox 环境，单次冷启动进程；数据源均为本地，
未启用 API 补充）。本地源各表大小差异大，故速度差异主要来自被检索文件体量。

| 工具 | 速度 | 说明 |
|---|---|---|
| `drugbank_metabolism_query` | **9 ms** | DrugBank 字节偏移索引，O(1) seek |
| `admetsar_predict` | **213 ms** | 流式扫 104k 行 admetSAR 表 + RDKit InChIKey 计算（`with_api=True` 加 ADMETlab 联网，见下） |
| `persade_contextual_retrieval` (热) | **257 ms** | 队列缓存命中后读 13MB JSONL + k-NN |
| `mechanism_query` | **419 ms** | 流式扫 DTA(31MB) + 逐蛋白富集 |
| `dti_query` | **757 ms** | DrugBank targets + 流式扫 DTI(16MB)，逐靶点查 Target.tsv |
| `ddi_query` | **986 ms** | DrugBank DDI + 加载 DDInter2 索引(231k 药对)一次 |
| `persade_drug_profile` | **2603 ms** | 流式扫 ASS_SCORE(1.1GB)取该药全部关联 |
| `pathway_enrich` (冷) | **4120 ms** | 首次构建合并背景：PersADE + Reactome(15.4万行) + GO(GAF+obo)；同进程热调 ~1 ms |
| `persade_contextual_retrieval` (冷) | **16370 ms** | 首次流式过 4.5GB 患者层并建队列缓存 |

- **8 工具 warm/hot 平均：~1170 ms**（pathway_enrich 冷加载 Reactome/GO 全集后升高；热调回落到毫秒级）。
- 重复调用会更快：`pathway_enrich`/`ddi_query` 的背景索引带 `lru_cache`，同进程内二次调用仅毫秒级；
  `persade_contextual_retrieval` 冷→热加速约 32×（16.4s → 0.26s）。
- `admetsar_predict` 启用 `with_api=True` 时联网查 ADMETlab 3.0（单次重试、20s 超时）：命中
  `data/cache/api_responses/admetlab3/` 后毫秒级；官方后端当前不稳定（对请求抛 5xx）时
  ~8s 后优雅降级为仅本地，并在输出 `admetlab3._api_note` 标注。

## 运行测试

```bash
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    conda run -n perstox python tests/test_tools.py
```

每个工具打印格式化 INPUT→OUTPUT 与计时，末尾汇总速度表。本地源失败则退出码非零；
API 离线被容忍（cache-first 连接器静默降级）。注：perstox 环境无 certifi，联网前需设
`SSL_CERT_FILE` 指向系统 CA bundle。

## 许可

DrugBank（学术非商用）与 PersADE（自有/提供）原始数据只读，工具仅读不改、不再分发原始记录；
所有派生缓存写入 `data/cache/`。



