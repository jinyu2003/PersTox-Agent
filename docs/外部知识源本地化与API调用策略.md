# PersTox-Agent 外部知识源本地化与 API 调用策略

## 1. 目标

本文档记录 PersTox-Agent 外部知识源在 Agent 检索场景下的接入策略，回答两个工程问题：

1. 哪些数据源适合下载到本地，作为稳定、可复现、可审计的知识库。
2. 哪些数据源适合通过 API 或在线检索调用，作为长尾补充、最新核查或动态证据来源。

PersTox-Agent 的知识库不是普通数据仓库，而是服务于 Agent 检索、证据裁决、机制解释和个性化风险修正。因此推荐采用：

```text
Local-first Hybrid Knowledge Base

本地结构化知识库 / 本地索引
    -> 本地 evidence cache
        -> 外部 API 补缺 / 最新核查
            -> 标准化为 evidence object 后写回缓存
```

核心原则是：

- 高频、核心、参与推理/评测/训练/事实裁决的数据，优先本地化。
- 更新频繁、许可受限、长尾补充、临时核查的数据，优先 API 或在线检索。
- 既高频又持续更新的数据，采用“本地快照 + API 增量更新”。
- 所有 API 结果都必须缓存为 evidence object，不能直接裸进 Agent 最终推理上下文。

---

## 2. 推荐分类

### 2.1 全部本地化

适合下载或人工导入成本地版本快照，供 Agent 高频检索、训练、评测、证据裁决和机制推理使用。

特点：

- 高频调用。
- 需要版本冻结。
- 需要可复现实验。
- 是 gold standard、规则库、标准术语或核心机制链的一部分。
- Agent 应优先查询本地结构化表、图谱边或文本索引。

### 2.2 部分本地化 + API

适合保留核心字段、MVP 子集或版本快照；长尾内容、最新更新、缺失字段通过 API 或在线检索补充。

特点：

- 数据重要，但全量下载成本较高。
- 更新较频繁。
- 存在许可或再分发限制。
- 对 MVP 只需要肝毒、心毒、PGx、DDI 等相关子集。

### 2.3 API 为主 + 本地缓存

适合按需查询，不建议在 MVP 阶段全量下载。

特点：

- 主要用于药物标准化、结构补全、最新标签、文献检索、长尾查询。
- 每次 API 查询后应保存原始响应、访问日期、query、source、hash、license note。
- 缓存结果再转成内部 claim / evidence object。

### 2.4 许可受限 / 人工处理

这类数据源即使适合本地化，也不应由脚本绕过许可自动下载。

处理方式：

- 获得授权后人工下载。
- 放入 `data/raw/<source_id>/`。
- 保存版本、来源、许可说明和下载日期。
- 再由标准化脚本导入本地知识库。

---

## 3. 总览表

| 数据源 | 可信度 | 推荐策略 | 是否优先用于 MVP | 说明 |
|---|---:|---|---|---|
| RxNorm | T1 | 部分本地化 + API | 是 | 药物标准命名和别名归一化。常用药可缓存，本地查不到时调用 API。 |
| ATC | T1 | 全部本地化 | 是 | 药物分类体系体量较小、更新低频，适合本地分类检索。 |
| DrugBank | T2 | 部分本地化 + 许可受控导入 | 是 | 药物、代谢、DDI、靶点、适应症重要，但受许可限制。 |
| PubChem | T2.5 | API 为主 + 本地缓存 | 是 | 适合按药名/结构查询 CID、SMILES、InChIKey；不建议 MVP 全量下载。 |
| ChEMBL | T2.6 / T2 | 部分本地化 + API | 是 | hERG、DTI、ADME assay 可本地化子集；长尾 bioactivity 走 API。 |
| admetSAR 3.0 | 实验值 T2，预测值 T3 | 全部本地化 | 是 | 第一阶段通用毒性预测主体数据，必须版本冻结。 |
| UniProt Swiss-Prot | T1 | 全部本地化 | 是 | 蛋白标准实体、功能和 xref，建议至少本地化 reviewed human subset。 |
| HGNC | T1 | 全部本地化 | 是 | 基因命名标准，体量小，必须本地化。 |
| NCBI Gene | T1 | 部分本地化 + API | 是 | 基因基础信息本地化 human subset；长尾更新可 API。 |
| Ensembl | T2 | 部分本地化 + API | 否 | 基因坐标、转录本、xref 有用，但 MVP 可先只接 API 或 human subset。 |
| PDB | T2 | API 为主 + 本地缓存 | 否 | 实验结构不是每次推理必查，按靶点需要时查询。 |
| AlphaFold DB | T3 | API 为主 + 本地缓存 | 否 | 预测结构仅作弱机制补充，不作为强证据。 |
| Reactome | T2 | 全部本地化 + API 增量 | 是 | 开放 curated 通路，适合本地构建 protein-pathway 机制链。 |
| KEGG | T2 | 部分本地化 + 许可/API | 否 | 通路与药物映射有价值，但需注意许可。 |
| Gene Ontology | T2 | 全部本地化 | 是 | 生物过程、分子功能、蛋白功能解释，适合本地索引。 |
| WikiPathways | T2-T3 | 部分本地化 + API | 否 | 社区通路补充，适合毒性相关子集。 |
| MSigDB | T2-T3 | 部分本地化 | 否 | 更适合富集分析和机制补充，不作为核心事实源。 |
| MedDRA | T1 | 全部本地化，许可合规 | 是 | ADE 标准术语与 SOC 映射，Agent 输出必需。 |
| CTCAE v5.0 | T1 | 全部本地化 | 是 | 严重度分级标准，体量小，必须本地化。 |
| ADReCS | T2 | 全部或核心子集本地化 | 是 | ADR 本体与机制链，支持 ADE 到蛋白/通路解释。 |
| OnSIDES | T2-T3 | 部分本地化 | 否 | 标签抽取结果适合辅助，不作为真值。 |
| SIDER | T3 | 可选本地化 | 否 | 已冻结，不建议作为核心源。 |
| DILIrank | T1 | 全部本地化 | 是 | 肝毒 MVP gold standard，必须版本冻结。 |
| LiverTox | T1 | 部分本地化 + 本地文本索引 | 是 | 临床专论和 likelihood 重要；建议结构化核心字段 + 文本索引。 |
| DILIst | T1-T2 | 全部本地化 | 是 | 结构化 DILI 数据，可用于肝毒 benchmark。 |
| LTKB | T1-T2 | 全部本地化 | 是 | FDA 肝毒知识库，适合本地权威源。 |
| Open TG-GATEs | T2 | 部分本地化 | 是 | 表达与病理机制证据体量大，MVP 可导入肝/肾相关子集。 |
| ToxCast/Tox21 | T2 | 部分本地化 | 是 | 体外机制支持，导入器官相关 assay 子集。 |
| CredibleMeds | T1 | 全部本地化，许可合规 | 是 | QT/TdP 风险金标准，需版本冻结。 |
| ChEMBL hERG 数据集 | T2 | 全部或 curated subset 本地化 | 是 | hERG 阻滞是心毒机制归因核心。 |
| 说明书/标签信息 | T1 | 部分本地化 + API 最新核查 | 是 | 标签是 L1 证据，但会更新。建议本地 warning snapshot + API 核查。 |
| IUPHAR/Guide to Pharmacology | T1-T2 | 全部或核心 snapshot 本地化 | 是 | 专家策展药理关系，适合高可信 DTI。 |
| BindingDB | T2-T3 | 部分本地化 | 否 | assay 条件异质，适合作 DTI 补充。 |
| STITCH | T3 | API 或可选本地化 | 否 | 含预测和文本挖掘，只作弱证据。 |
| PharmGKB pathways | T1-T2 | 全部本地化 + API 更新 | 是 | PGx/PK/PD pathway 对个性化解释很重要。 |
| SMPDB | T2 | 部分本地化，注意许可 | 否 | 药物作用和代谢通路补充。 |
| CPIC | T1 | 全部本地化 + 更新核查 | 是 | PGx 到临床建议的核心规则库。 |
| DPWG | T1 | 全部本地化 + 更新核查 | 是 | PGx 规则补充，适合规则化入库。 |
| PharmGKB | T1-T2 | 部分本地化 + API | 是 | clinical annotation、pathway、drug-gene evidence 本地 snapshot；长尾更新 API。 |
| ClinVar | T1-T2 | 部分本地化 + API | 是 | 变异临床意义更新频繁，建议 PGx/药物相关子集本地化。 |
| Open Targets | T2 | 部分本地化 + API | 否 | 基因-疾病证据，适合机制和共病解释补充。 |
| CTD | T2-T3 | 部分本地化 | 否 | 化学-基因-疾病推断关系要标为弱证据。 |
| DDI 规则库 | T1-T2 | 全部本地化 + 标签/API 更新 | 是 | 合并用药风险修正是第二阶段核心。 |
| Beers/器官功能规则 | T1-T2 | 全部本地化 + 指南更新 | 是 | 老年、肝肾功能、器官功能修正规则，需结构化。 |

---

## 4. 按接入策略分组

### 4.1 建议全部本地化的数据源

这些数据源应作为 Agent 的核心本地知识底座。

| 数据源 | 主要用途 | 备注 |
|---|---|---|
| ATC | 药物分类 | 体量小，适合作本地分类字段。 |
| admetSAR 3.0 | ADMET 标签与性质端点 | 第一阶段通用毒性预测主体数据。 |
| UniProt Swiss-Prot | 蛋白标准实体 | 建议至少本地化 reviewed human subset。 |
| HGNC | 基因命名标准 | 用于 gene symbol / HGNC ID 统一。 |
| Reactome | 通路映射 | 支撑 target-pathway-mechanism 检索。 |
| Gene Ontology | 功能/生物过程 | 支撑机制解释。 |
| MedDRA | ADE 术语与 SOC 映射 | 许可合规后本地化。 |
| CTCAE v5.0 | 严重度分级 | 本地化用于输出和评测。 |
| DILIrank | 肝毒金标准 | 肝毒 MVP 必需。 |
| DILIst | 结构化 DILI 数据 | 用于 benchmark 和 gold cases。 |
| LTKB | FDA 肝毒知识库 | 用于肝毒权威证据。 |
| CredibleMeds | QT/TdP 风险金标准 | 许可合规后本地化。 |
| ChEMBL hERG curated subset | hERG 实验依据 | 心毒 MVP 核心机制证据。 |
| CPIC | PGx 临床建议 | 规则化本地存储。 |
| DPWG | PGx 规则补充 | 规则化本地存储。 |
| DDI 规则库 | 合并用药风险修正 | 第二阶段个性化预测核心。 |
| Beers/器官功能规则 | 老年与器官功能修正 | 第二阶段个性化预测核心。 |
| PersADE | 个性化 drug-ADE 主体数据 | 项目自有核心资产，应本地化。 |
| gold cases | 评测与裁决 | 必须本地版本冻结。 |

### 4.2 建议部分本地化 + API 的数据源

这些数据源适合保留 MVP 子集或核心字段，并通过 API/在线检索补充长尾。

| 数据源 | 本地化内容 | API/在线检索内容 |
|---|---|---|
| RxNorm | 常用药名、别名、RxCUI 缓存 | 新药、长尾商品名、实时标准化 |
| DrugBank | 许可允许字段、靶点、代谢、DDI、适应症摘要 | 缺失字段、更新核查 |
| ChEMBL | hERG、DTI、ADME assay 子集 | 长尾 bioactivity 查询 |
| NCBI Gene | human gene/xref subset | 长尾基因更新 |
| Ensembl | human xref 或关键转录本 | 长尾转录本、坐标查询 |
| KEGG | 许可允许的通路/药物映射 | 最新通路或 drug-pathway 查询 |
| WikiPathways | 毒性相关通路子集 | 社区通路补充 |
| MSigDB | 相关 gene set 子集 | 新 gene set 或富集分析补充 |
| ADReCS | ADR 机制链核心子集 | 新版本或缺失机制 |
| OnSIDES | 标签 drug-ADE 子集 | 新标签抽取版本 |
| LiverTox | MVP 药物专论文本索引、likelihood 字段 | 最新条目核查 |
| Open TG-GATEs | 肝/肾机制相关子集 | 更大范围毒理基因组查询 |
| ToxCast/Tox21 | 器官相关 assay 子集 | 新 assay 或长尾化合物 |
| 说明书/标签信息 | warning/precaution 本地快照 | 最新 FDA/EMA/NMPA 标签核查 |
| IUPHAR/Guide to Pharmacology | 核心药物-靶点关系 | 新 target/ligand 更新 |
| BindingDB | MVP target 子集 | 长尾 DTI 查询 |
| PharmGKB pathways | PK/PD pathway snapshot | 新 pathway 更新 |
| SMPDB | 药物作用/代谢通路子集 | 长尾通路补充 |
| PharmGKB | clinical annotation、gene-drug、variant annotation snapshot | 最新 PGx annotation |
| ClinVar | PGx/药物相关变异子集 | 最新变异临床意义 |
| Open Targets | 共病相关 gene-disease 子集 | 长尾 gene-disease evidence |
| CTD | curated 子集 | 长尾化学-基因-疾病补充 |

### 4.3 建议 API 为主 + 本地缓存的数据源

这些数据源适合按需调用，不建议在 MVP 阶段全量下载。

| 数据源 | API 用途 | 缓存要求 |
|---|---|---|
| PubChem | CID、SMILES、InChIKey、同义名、结构补全 | 缓存 query、response、retrieved_at、hash |
| PDB | 靶点实验结构元数据 | 缓存结构 ID、链、分辨率、xref |
| AlphaFold DB | 预测结构辅助解释 | 标记为弱证据，不参与强裁决 |
| openFDA label | 最新说明书标签核查 | 缓存标签版本、访问日期、匹配字段 |
| openFDA FAERS | 群体信号查询 | 只能作为 signal，不能单独判真 |
| PubMed / 文献检索 | 长尾机制、新证据、病例补充 | 缓存 PMID、摘要、检索式、访问日期 |
| Reactome Content Service | 最新 pathway 补充 | 本地没有时再调用 |
| ChEMBL API | 长尾活性数据补充 | 本地 hERG/DTI 子集没有时再调用 |
| PharmGKB / ClinPGx API | 最新 PGx 规则或 annotation | 进入 evidence cache 后再给 Agent 使用 |

---

## 5. 按 PersTox-Agent 模块组织

### 5.1 药物基础信息模块

| 数据源 | 策略 |
|---|---|
| RxNorm | 部分本地化 + API |
| ATC | 全部本地化 |
| DrugBank | 部分本地化 + 许可受控导入 |
| PubChem | API 为主 + 本地缓存 |
| ChEMBL | 部分本地化 + API |
| admetSAR 3.0 | 全部本地化 |

### 5.2 蛋白/基因模块

| 数据源 | 策略 |
|---|---|
| UniProt Swiss-Prot | 全部本地化 |
| HGNC | 全部本地化 |
| NCBI Gene | 部分本地化 + API |
| Ensembl | 部分本地化 + API |
| PDB | API 为主 + 本地缓存 |
| AlphaFold DB | API 为主 + 本地缓存 |

### 5.3 通路模块

| 数据源 | 策略 |
|---|---|
| Reactome | 全部本地化 + API 增量 |
| KEGG | 部分本地化 + 许可/API |
| Gene Ontology | 全部本地化 |
| WikiPathways | 部分本地化 + API |
| MSigDB | 部分本地化 |

### 5.4 ADE 与术语模块

| 数据源 | 策略 |
|---|---|
| MedDRA | 全部本地化，许可合规 |
| CTCAE v5.0 | 全部本地化 |
| ADReCS | 全部或核心子集本地化 |
| OnSIDES | 部分本地化 |
| SIDER | 可选本地化，不作为核心源 |

### 5.5 器官毒性专病模块

| 数据源 | 策略 |
|---|---|
| DILIrank | 全部本地化 |
| LiverTox | 部分本地化 + 本地文本索引 |
| DILIst | 全部本地化 |
| LTKB | 全部本地化 |
| Open TG-GATEs | 部分本地化 |
| ToxCast/Tox21 | 部分本地化 |
| CredibleMeds | 全部本地化，许可合规 |
| ChEMBL hERG 数据集 | 全部或 curated subset 本地化 |
| 说明书/标签信息 | 部分本地化 + API 最新核查 |

### 5.6 药物-靶点-通路机制模块

| 数据源 | 策略 |
|---|---|
| IUPHAR/Guide to Pharmacology | 全部或核心 snapshot 本地化 |
| ChEMBL | 部分本地化 + API |
| BindingDB | 部分本地化 |
| DrugBank | 部分本地化 + 许可受控导入 |
| STITCH | API 或可选本地化 |
| PharmGKB pathways | 全部本地化 + API 更新 |
| KEGG/Reactome/SMPDB | 部分本地化 + API/许可 |
| Reactome/KEGG/GO | 本地化优先 |
| STRING | 部分本地化或 API，弱证据 |

### 5.7 个性化修饰模块

| 数据源 | 策略 |
|---|---|
| CPIC | 全部本地化 + 更新核查 |
| DPWG | 全部本地化 + 更新核查 |
| PharmGKB | 部分本地化 + API |
| ClinVar | 部分本地化 + API |
| Open Targets | 部分本地化 + API |
| CTD | 部分本地化 |
| DDI 规则库 | 全部本地化 + 标签/API 更新 |
| Beers/器官功能规则 | 全部本地化 + 指南更新 |

---

## 6. Agent 检索建议

### 6.1 通用毒性预测阶段

推荐检索顺序：

```text
1. 药物标准化：本地 drug_master / RxNorm cache
2. ADMET 查询：本地 admetSAR 3.0
3. 已知毒性谱：本地 DILIrank / CredibleMeds / LiverTox index / PersADE
4. DTI 查询：本地 ChEMBL hERG / IUPHAR / DrugBank subset
5. 通路查询：本地 Reactome / GO
6. 机制查询：本地 ADReCS / pathway mechanism
7. 标签核查：本地 label snapshot，不足时 API
8. 证据组装：统一 evidence object
```

### 6.2 个性化毒性预测阶段

推荐检索顺序：

```text
1. 读取第一阶段 baseline risk
2. PGx 规则查询：本地 CPIC / DPWG / PharmGKB snapshot
3. DDI 查询：本地 DDI 规则库 / DrugBank subset
4. 共病规则查询：本地规则 + Open Targets/CTD subset
5. 器官功能规则查询：本地 Beers / 肝肾功能 / LVEF 规则
6. 最新指南/标签核查：必要时 API
7. 输出 personalized risk shift 和患者因素归因
```

---

## 7. API 检索结果的证据缓存要求

所有 API 查询结果都应进入 evidence cache，而不是直接进入 Agent 最终回答。

建议保存字段：

```json
{
  "query_id": "Q_000001",
  "source_name": "openFDA",
  "source_url": "...",
  "api_endpoint": "...",
  "query": {
    "drug": "warfarin"
  },
  "retrieved_at": "2026-06-16",
  "source_version": "if_available",
  "raw_response_hash": "...",
  "normalized_claims": ["CLAIM_0001"],
  "evidence_ids": ["EV_0001"],
  "license_note": "...",
  "cache_ttl_days": 30
}
```

---

## 8. 当前阶段建议

当前阶段建议先只处理严格 T1 数据源，T2/T3 数据源暂不下载。

优先级建议：

1. 先本地化 CTCAE v5.0、MedDRA、DILIrank、CredibleMeds、CPIC、DPWG 等 T1 证据和规则源。
2. 暂停 UniProt Swiss-Prot、HGNC、NCBI Gene 时，其他 T1 源中多数需要许可、landing page 确认或 API 缓存。
3. T1-T2、T2、T3 数据源先记录策略，不进入当前下载队列。
4. 等 T1 层跑通后，再逐步引入 T1-T2 和 T2 的机制补充数据。

---

## 9. 一句话结论

PersTox-Agent 的外部知识库应采用：

```text
核心 T1 证据和规则本地化；
T1-T2/T2 机制知识按 MVP 子集本地化；
长尾、最新、许可受限内容通过 API 或人工授权导入；
所有在线检索结果必须证据化、缓存化、可回放。
```

