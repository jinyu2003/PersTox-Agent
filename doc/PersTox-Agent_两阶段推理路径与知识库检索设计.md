# PersTox-Agent 两阶段推理路径与知识库检索设计

## 1. 文档目的

本文基于以下材料整理 PersTox-Agent 的两阶段完整推理路径，并明确每一步需要检索哪些知识库、取哪些信息、输入输出是什么。

参考材料：

- `PersTox-Agent_药学视角精简版V2.pdf`
- `admetsar3_all_endpoints.txt`
- `PersADE/PersADE_数据说明.docx`
- PersADE 官网：https://persade.idrblab.net/
- admetSAR 3.0 官网：https://lmmd.ecust.edu.cn/admetsar3/

本文重点回答：

1. 第一阶段“通用毒性预测”如何推理。
2. 第二阶段“个性化毒性预测”如何推理。
3. 每一步需要检索哪些本地知识库或外部补充数据。
4. 每一步的输入、处理逻辑和输出是什么。

---

## 2. 总体设计

PersTox-Agent 采用“两阶段预测 + 双重归因”。

第一阶段回答：

> 这个药物本身是否具有器官毒性风险？风险来自哪些结构、理化性质、ADMET 端点、靶点或通路？

第二阶段回答：

> 在某个具体患者身上，这些风险是否会被放大或降低？风险变化由哪些患者因素造成？

两阶段共享同一条毒性机制链：

```text
药物原型
  -> 代谢 / 毒性代谢物
  -> 靶点结合 / 脱靶作用
  -> 通路扰动
  -> ADE / 器官系统毒性
  -> 患者因素修饰
```

---

## 3. 核心数据资产分工

## 3.1 admetSAR 3.0

本地文件：

```text
/Users/huangziwei/Documents/器官毒性个性化/admetsar3_all_endpoints.txt
```

数据特点：

- 约 104651 个化合物记录。
- 122 列。
- 前 14 列为 SMILES 和理化性质。
- 后 108 列为 ADMET endpoint 标签。
- 标签矩阵稀疏，不是每个化合物都有所有端点。

主要作用：

- 第一阶段主体数据。
- 提供分子结构、理化性质、ADMET 端点。
- 支持通用毒性 baseline 预测。
- 支持结构/性质/端点级归因。

关键字段：

| 字段类型 | 代表字段 | 用途 |
|---|---|---|
| 结构 | `SMILES` | 分子图、分子指纹、GNN 输入 |
| 理化性质 | `MW`、`HBA`、`HBD`、`nRot`、`TPSA`、`SlogP`、`QED` | 药物侧基础特征 |
| 类药性规则 | `Lipinski rule`、`Pfizer rule`、`GSK rule` | 药物性质过滤与解释 |
| 肝毒 | `label_DILI_t` | DILI 风险 |
| 心毒 | `label_hERG_1`、`label_hERG_10`、`label_hERG_30`、`label_hERG_1_10`、`label_hERG_10_30` | hERG/QT 风险 |
| 代谢 | CYP substrate / inhibitor 相关字段 | CYP 介导暴露变化 |
| 转运体 | `P-gp`、`BCRP`、`BSEP`、`OATP`、`OCT`、`MATE` 相关字段 | 转运体介导毒性 |
| 毒性端点 | `label_Ames_t`、`label_Repro_toxic`、`label_Mito_t`、`label_Skin_sen`、`label_Resp_wzy` | 非器官或辅助毒性 |
| Tox21 应激 | `NR_*`、`SR_*` 相关字段 | 核受体/细胞应激机制 |

## 3.2 PersADE

本地目录：

```text
/Users/huangziwei/Documents/器官毒性个性化/PersADE
```

数据特点：

- 药物实体：10235 条。
- ADE 实体：19991 条。
- 原始个案报告：37923034 条。
- 药物-ADE 主打分：2201568 条。
- 分层打分：按 ADE 类别、给药途径、剂型、适应症分层。
- 机制链：药物-靶点-ADE、药物-靶点、ADE-靶点、靶点-通路。

主要作用：

- 第一阶段：提供已知 drug-ADE 谱，辅助通用毒性 baseline 和监督标签。
- 第二阶段：作为个性化毒性预测的主体数据源。
- 机制解释：连接 Drug -> Target -> Pathway -> ADE。
- 证据链：提供 Cases、PubMed、Source、PRR、ROR、priority、severity 等字段。

关键文件：

| 文件 | 作用 |
|---|---|
| `drug_all.txt` | 药物实体、SMILES、InChIkey、ATC、给药途径、剂型、剂量分布 |
| `ADE_Information.txt` | ADE 实体、UMLS、MeSH、树状分类、严重度、相似 ADE |
| `ADE_report.txt` | 原始个案报告，含年龄、性别、剂量、途径、剂型、结局 |
| `CCombined_Results_with_scores.txt` | 药物-ADE 主关联打分 |
| `CCombined_Results_ADE_with_scores.txt` | 按 ADE 类别聚合的关联打分 |
| `CCombined_Results_route_with_scores.txt` | 按给药途径分层的关联打分 |
| `CCombined_Results_form_with_scores.txt` | 按剂型分层的关联打分 |
| `CCombined_Results_INDI_with_scores.txt` | 按适应症分层的关联打分 |
| `DTA.txt` | 药物-靶点-ADE 三元组 |
| `DTI.txt` | 药物-靶点关系 |
| `AT.csv` | ADE-靶点关系 |
| `Target.tsv` | 蛋白靶点实体 |
| `Pathway.txt` | 通路实体 |
| `uniprot_pathway.txt` | 蛋白-通路映射 |

## 3.3 外部补充数据

外部知识源不建议全部在线临时搜索，应分为本地核心库和在线补充源。

| 数据源 | 建议方式 | 主要作用 | 证据定位 |
|---|---|---|---|
| DrugBank | 本地化 | 药物基础、靶点、代谢、DDI、适应症 | T2 |
| ChEMBL | 本地化 | DTI 活性、hERG、ADME assay | T2 |
| BindingDB | 本地化或按需检索 | 靶点结合亲和力 | T2-T3 |
| UniProt | 本地化 | 蛋白标准化、功能、组织表达 | T1-T2 |
| Reactome / KEGG / GO | 本地化 | 通路和功能注释 | T2 |
| ADReCS | 本地化 | ADE 机制链 | T2 |
| MedDRA | 本地化 | ADE PT 到 SOC 映射 | T1 |
| CTCAE v5.0 | 本地化 | 严重度和临床分级 | T1 |
| DILIrank / LiverTox / DILIst / LTKB | 本地化 | 肝毒金标准 | T1-T2 |
| CredibleMeds | 本地化 | QT/TdP 心毒金标准 | T1 |
| CPIC / DPWG | 本地化 | PGx 临床规则 | T1 |
| PharmGKB / ClinVar | 本地化或按需更新 | PGx 证据与变异解释 | T1-T2 |
| DDInter / DrugBank DDI | 本地化 | 合并用药相互作用 | T1-T2 |
| openFDA label | 在线或定期同步 | 最新说明书和警示 | T1 |
| FAERS / openFDA FAERS | 在线或定期同步 | 最新群体信号 | T4 |
| PubMed | 在线检索 | 最新文献、个案、机制补充 | T3-T5 |

---

## 4. 全局输入与标准化

## 4.1 Agent 全局输入

```json
{
  "drug": {
    "name": "...",
    "smiles": "...",
    "drugbank_id": "...",
    "inchi_key": "...",
    "route": "...",
    "dose": "...",
    "frequency": "...",
    "form": "..."
  },
  "patient": {
    "patient_id": "...",
    "age": 56,
    "sex": "female",
    "diagnoses_icd10": ["..."],
    "indications": ["..."],
    "comedications": ["..."],
    "pgx": {
      "CYP2D6": "*4/*4",
      "HLA-B": "*15:02"
    },
    "organ_function": {
      "eGFR": 75,
      "ALT": 35,
      "AST": 30,
      "LVEF": 62
    }
  }
}
```

## 4.2 标准化输出

在进入两阶段推理前，需要先把输入映射到统一主键。

| 实体 | 推荐主键 | 辅助 ID |
|---|---|---|
| 药物 | DrugBank ID / InChIkey | SMILES、PubChem CID、ChEMBL ID、RxNorm、ATC |
| ADE | MedDRA PT / UMLS | MeSH、CTCAE term |
| 蛋白 | UniProt ID | HGNC、NCBI Gene、Ensembl |
| 通路 | Reactome ID / KEGG ID | GO、WikiPathways |
| 适应症/共病 | ICD-10 / UMLS | MeSH、MONDO |
| 基因型 | HGNC + allele | PharmGKB、ClinVar |

---

## 5. 第一阶段：通用毒性预测完整推理路径

## 5.1 阶段目标

第一阶段面向“药物本身”，预测该药在群体/化合物层面的器官毒性 baseline risk。

核心问题：

```text
这个药本身危险吗？
危险主要体现在哪些器官系统？
风险来自结构、理化性质、ADMET 端点、靶点还是通路？
有哪些证据支持？
```

## 5.2 第一阶段总输入

```json
{
  "drug_name": "...",
  "smiles": "...",
  "drugbank_id": "...",
  "inchi_key": "...",
  "route": "...",
  "form": "..."
}
```

患者信息在第一阶段不参与风险修正，只保留给第二阶段。

## 5.3 第一阶段总输出

```json
{
  "drug": {
    "name": "...",
    "smiles": "...",
    "inchi_key": "...",
    "drugbank_id": "..."
  },
  "general_toxicity": [
    {
      "soc": "Hepatobiliary disorders",
      "ade_terms": ["..."],
      "risk_level": "high|moderate|low|unknown",
      "probability": 0.0,
      "uncertainty": 0.0,
      "ctcae_grade_predicted": 1,
      "attribution": {
        "structural": [],
        "property": [],
        "admet_endpoint": [],
        "target_pathway": [],
        "mechanism_summary": "..."
      },
      "evidence": []
    }
  ]
}
```

---

## 6. 第一阶段逐步推理

## Step 1：药物标准化与实体对齐

### 目的

把输入药物统一到可检索的标准实体，解决药名、SMILES、DrugBank ID、InChIkey 不一致的问题。

### 输入

```json
{
  "drug_name": "...",
  "smiles": "...",
  "drugbank_id": "...",
  "inchi_key": "..."
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| PersADE `drug_all.txt` | `Primary_Name`、`Name`、`SMILES`、`InChIkey`、`Link` | InChIkey、SMILES、药名、外部 ID、ATC、Route/Form/Dose 分布 |
| admetSAR3 `admetsar3_all_endpoints.txt` | `SMILES` | 分子描述符和 ADMET endpoint |
| DrugBank | drug name、DrugBank ID、SMILES | DrugBank ID、靶点、代谢、转运体、DDI、适应症 |
| PubChem / ChEMBL | SMILES、InChIkey、名称 | 结构和外部 ID 对齐 |

### 处理逻辑

1. 若输入有 InChIkey，优先用 InChIkey 匹配 PersADE。
2. 若输入有 DrugBank ID，从 PersADE `Link` 或 DrugBank 映射到 InChIkey。
3. 若只有药名，用同义名表匹配 PersADE `Name` 和 DrugBank aliases。
4. 若只有 SMILES，计算 InChIkey 并匹配 PersADE/admetSAR3。

### 输出

```json
{
  "drug_entity": {
    "primary_name": "...",
    "inchi_key": "...",
    "smiles": "...",
    "drugbank_id": "...",
    "chembl_id": "...",
    "pubchem_id": "...",
    "atc": "...",
    "drug_type": "small_molecule|biologic|unknown"
  }
}
```

## Step 2：结构与理化性质检索

### 目的

获取药物的分子结构、基础理化性质和类药性规则，用于第一阶段模型输入和归因。

### 输入

```json
{
  "smiles": "...",
  "inchi_key": "..."
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| admetSAR3 | `SMILES` | `MW`、`HBA`、`HBD`、`nRot`、`TPSA`、`SlogP`、`nRing`、`nAtom`、`nHet`、`QED`、类药性规则 |
| PersADE `drug_all.txt` | `InChIkey` | `MF`、`MW`、`LogP`、`HBA`、`HBD`、`TPSA`、FP2/FP3/FP4/MACCS |
| RDKit/结构警示库 | `SMILES` | 分子指纹、SMARTS 警示、toxicophore |

### 处理逻辑

1. 用 admetSAR3 的数值描述符作为结构基础特征。
2. 用 PersADE 的分子指纹和外部链接补充结构信息。
3. 对 SMILES 做结构警示匹配，例如反应性基团、芳香胺、硝基、醌类、亲电基团等。

### 输出

```json
{
  "structure_profile": {
    "descriptors": {
      "MW": 0.0,
      "TPSA": 0.0,
      "SlogP": 0.0,
      "QED": 0.0
    },
    "drug_likeness": {
      "lipinski": "Accept|Not accept",
      "pfizer": "Accept|Not accept",
      "gsk": "Accept|Not accept"
    },
    "structural_alerts": [
      {
        "alert": "...",
        "smarts": "...",
        "matched_atoms": [],
        "toxicity_relevance": "hepatotoxicity|cardiotoxicity|mutagenicity|..."
      }
    ]
  }
}
```

## Step 3：ADMET endpoint 检索与端点解释

### 目的

获取药物在吸收、分布、代谢、排泄、毒性方面的端点标签，形成通用毒性模型的核心输入。

### 输入

```json
{
  "smiles": "...",
  "structure_profile": {}
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| admetSAR3 | `SMILES` | 全部非空 `label_*` endpoint |
| ChEMBL ADME assays | ChEMBL ID / SMILES | 实验 ADME assay |
| Tox21 / ToxCast | SMILES / PubChem CID | 核受体、细胞应激、毒性通路 |

### 重点端点

| 机制类型 | admetSAR3 代表字段 | 解释 |
|---|---|---|
| 肝毒 | `label_DILI_t` | DILI 风险 |
| 心毒 | `label_hERG_*` | hERG 阻滞和 QT/TdP 风险 |
| 代谢 | CYP substrate/inhibitor 字段 | 暴露、DDI、活性代谢物风险 |
| 转运体 | `P-gp`、`BSEP`、`OATP`、`OCT`、`MATE` | 胆汁淤积、肾排泄、药物蓄积 |
| 线粒体毒性 | `label_Mito_t` | 线粒体损伤相关毒性 |
| 致突变 | `label_Ames_t` | Ames 阳性 |
| 生殖毒性 | `label_Repro_toxic` | 生殖/发育毒性 |
| 皮肤致敏 | `label_Skin_sen` | 皮肤过敏/致敏 |
| 应激通路 | `NR_*`、`SR_*` | 核受体和应激反应 |

### 处理逻辑

1. 提取该化合物所有非空 endpoint。
2. 将 endpoint 映射到机制类别和器官 SOC。
3. 对缺失 endpoint 标记为 `unknown`，不要视为阴性。
4. 将数值型 endpoint 和分类 endpoint 分开处理。

### 输出

```json
{
  "admet_profile": [
    {
      "endpoint": "label_DILI_t",
      "value": "1",
      "endpoint_type": "classification",
      "mechanism_group": "hepatotoxicity",
      "soc": "Hepatobiliary disorders",
      "evidence_role": "model_feature"
    }
  ]
}
```

## Step 4：已知 drug-ADE 谱检索

### 目的

从真实世界药物警戒数据中获取该药已知 ADE 谱，用作通用毒性 baseline 的外部支持和校验。

### 输入

```json
{
  "inchi_key": "..."
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| PersADE `CCombined_Results_with_scores.txt` | `Drug_ID = InChIkey` | Reaction_ID、Case_number、PRR、ROR、ROR_Lower_CI、Adjust_P、total_score、priority、severity_grade、sex_dis、age_dis、Cases、PubMed、Source |
| PersADE `ADE_Information.txt` | `UMLS = Reaction_ID` | ADE 名称、MeSH、Tree_number、定义、severity_grade、相似 ADE |
| PersADE `CCombined_Results_ADE_with_scores.txt` | `Drug_ID` | ADE 类别层面的聚合风险 |
| MedDRA / MeSH | UMLS / MeSH / Tree_number | ADE 到 SOC 的映射 |

### 处理逻辑

1. 取该药所有 drug-ADE 关联。
2. 优先筛选：
   - `ROR_Lower_CI > 1`
   - `priority = High|Medium`
   - `Case_number` 足够
   - `severity_grade = Severe|Critical`
3. 将 UMLS ADE 映射到 MedDRA SOC 或器官系统。
4. 将 FAERS/PersADE 信号标记为 `signal evidence`，不能直接作为因果真值。

### 输出

```json
{
  "known_ade_profile": [
    {
      "ade_id": "Cxxxxxxx",
      "ade_name": "...",
      "soc": "Cardiac disorders",
      "case_number": 0,
      "ror": 0.0,
      "ror_lower_ci": 0.0,
      "adjust_p": 0.0,
      "total_score": 0.0,
      "priority": "High|Medium|Low",
      "severity_grade": "Minimal|Mild|Moderate|Severe|Critical",
      "evidence_level": "signal",
      "source": "PersADE/FAERS"
    }
  ]
}
```

## Step 5：专科金标准检索

### 目的

用高可信来源对通用毒性风险进行校验，尤其是 MVP 阶段的肝毒和心毒。

### 输入

```json
{
  "drug_entity": {},
  "known_ade_profile": []
}
```

### 检索知识库

| 毒性方向 | 数据源 | 返回信息 |
|---|---|---|
| 肝毒 | DILIrank | Most-DILI-concern、Less-DILI-concern、No-DILI-concern 等标签 |
| 肝毒 | LiverTox | likelihood category、临床描述、机制、参考文献 |
| 肝毒 | DILIst / LTKB | DILI 标签、结构/机制支持 |
| 心毒 | CredibleMeds | Known/Possible/Conditional risk of TdP |
| 心毒 | ChEMBL hERG | hERG 活性、IC50/Ki、assay 信息 |
| 标签级 | FDA / EMA / NMPA label | Boxed warning、Warnings and Precautions、Adverse Reactions |

### 处理逻辑

1. 如果高可信来源支持该毒性，则提升证据等级。
2. 如果只有 PersADE/FAERS 信号而无高可信来源，则标记为 `unverified signal`。
3. 如果高可信来源明确负控，则降低风险或标记冲突。

### 输出

```json
{
  "gold_standard_checks": [
    {
      "soc": "Hepatobiliary disorders",
      "source": "DILIrank|LiverTox|CredibleMeds|FDA label",
      "verdict": "confirmed|not_supported|conflicting|unknown",
      "evidence_level": "T1|T2|T4",
      "details": "..."
    }
  ]
}
```

## Step 6：靶点、脱靶和通路机制检索

### 目的

解释药物毒性可能通过哪些靶点、脱靶或通路发生。

### 输入

```json
{
  "inchi_key": "...",
  "drugbank_id": "...",
  "chembl_id": "...",
  "known_ade_profile": []
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| PersADE `DTA.txt` | `InChIkey` + `UMLS` | Drug-Target-ADE 三元组、Type、PubMed_DAT、PubMed_DT、PubMed_DA、PubMed_AT |
| PersADE `DTI.txt` | `InChIkey` | Uniprot_ID、PubMed、Source、Affinities、Affect |
| PersADE `AT.csv` | `UMLS` | ADE-Target 文献 |
| PersADE `Target.tsv` | `Uniprot_ID` | 蛋白名、基因名、功能分类、组织表达 |
| PersADE `uniprot_pathway.txt` | `UniProt_ID` | Pathway_ID 列表 |
| PersADE `Pathway.txt` | `Pathway_ID` | 通路名、来源、功能类别 |
| DrugBank / ChEMBL / BindingDB | Drug ID / target | 已知靶点、亲和力、作用类型 |
| Reactome / KEGG / GO | gene / UniProt | 通路和功能注释 |
| ADReCS | ADE / target | ADE 机制链补充 |

### 处理逻辑

1. 对每个高风险 ADE，检索是否存在 DTA 三元组。
2. 若 DTA 不存在，尝试通过 DTI + AT 间接构建：

```text
Drug -> Target
ADE -> Target
Target -> Pathway
```

3. 对 hERG、BSEP、线粒体、CYP、HLA 等关键机制设置优先级。
4. 标记机制证据类型：
   - 直接三元组支持
   - 药物-靶点 + ADE-靶点间接支持
   - 仅通路推断

### 输出

```json
{
  "mechanism_chains": [
    {
      "ade_id": "...",
      "soc": "...",
      "chain": [
        {
          "node_type": "Drug",
          "id": "InChIkey",
          "name": "..."
        },
        {
          "node_type": "Target",
          "id": "Pxxxx",
          "gene": "KCNH2",
          "protein": "..."
        },
        {
          "node_type": "Pathway",
          "id": "hsa...",
          "name": "..."
        },
        {
          "node_type": "ADE",
          "id": "Cxxxx",
          "name": "QT prolongation"
        }
      ],
      "evidence_type": "direct_DTA|indirect_DTI_AT|pathway_inferred",
      "pubmed": ["..."]
    }
  ]
}
```

## Step 7：器官系统聚合和通用风险融合

### 目的

把零散 ADMET endpoint、ADE 信号、金标准证据和机制链聚合成八大器官系统的 baseline risk。

### 输入

```json
{
  "admet_profile": [],
  "known_ade_profile": [],
  "gold_standard_checks": [],
  "mechanism_chains": []
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| MedDRA | ADE PT / UMLS 映射 | SOC |
| CTCAE v5.0 | ADE term | CTCAE grade 描述 |
| PersADE `ADE_Information.txt` | UMLS / Tree_number | ADE 严重度、MeSH 类别 |
| 医生定义的金标准 rubric | soc / endpoint | 证据裁决规则 |

### 处理逻辑

按器官系统聚合证据：

```text
器官风险 = f(
  admet endpoint,
  PersADE drug-ADE signal,
  gold standard evidence,
  target/pathway mechanism,
  severity,
  uncertainty
)
```

建议优先级：

1. L1-L3 金标准证据。
2. admetSAR3 实验/预测 endpoint。
3. PersADE 高强度信号。
4. 机制链支持。
5. FAERS/PubMed 弱信号。

### 输出

```json
{
  "baseline_organ_risk": [
    {
      "soc": "Cardiac disorders",
      "risk_level": "high",
      "probability": 0.82,
      "uncertainty": 0.18,
      "main_ade_terms": ["QT prolongation", "Torsade de pointes"],
      "main_drivers": [
        "hERG positive",
        "CredibleMeds Known Risk",
        "PersADE high ROR signal"
      ],
      "evidence_summary": []
    }
  ]
}
```

## Step 8：第一阶段输出生成

### 目的

生成可供第二阶段使用的 baseline toxicity profile。

### 输入

```json
{
  "baseline_organ_risk": [],
  "mechanism_chains": [],
  "structure_profile": {},
  "admet_profile": []
}
```

### 输出

第一阶段输出是第二阶段输入的一部分。

```json
{
  "general_toxicity": [
    {
      "soc": "...",
      "baseline_risk_level": "high|moderate|low|unknown",
      "baseline_probability": 0.0,
      "uncertainty": 0.0,
      "ctcae_grade_predicted": 1,
      "attribution": {
        "structural": [],
        "property": [],
        "admet_endpoint": [],
        "target_pathway": [],
        "mechanism_summary": "..."
      },
      "evidence": []
    }
  ]
}
```

---

## 7. 第二阶段：个性化毒性预测完整推理路径

## 7.1 阶段目标

第二阶段面向“药物 + 患者画像”，在第一阶段 baseline risk 基础上进行个体化修正。

核心问题：

```text
这个患者相对于普通人群风险是否更高？
风险升高或降低来自哪些患者因素？
是否需要监测、调剂量、换药或避免使用？
```

## 7.2 第二阶段总输入

```json
{
  "drug_entity": {},
  "general_toxicity": [],
  "patient": {
    "age": 56,
    "sex": "female",
    "diagnoses_icd10": ["..."],
    "indications": ["..."],
    "comedications": ["..."],
    "pgx": {},
    "organ_function": {},
    "route": "...",
    "form": "...",
    "dose": "...",
    "frequency": "..."
  }
}
```

## 7.3 第二阶段总输出

```json
{
  "personalized_toxicity": [
    {
      "soc": "Cardiac disorders",
      "baseline": {
        "risk_level": "moderate",
        "probability": 0.45
      },
      "personalized_risk_level": "high",
      "personalized_probability": 0.72,
      "risk_shift": 0.27,
      "ctcae_grade_predicted": 3,
      "patient_attribution": [],
      "clinical_recommendation": {}
    }
  ]
}
```

---

## 8. 第二阶段逐步推理

## Step 1：患者画像标准化

### 目的

把患者输入转成可检索、可规则匹配的结构化特征。

### 输入

```json
{
  "age": 56,
  "sex": "female",
  "diagnoses": ["breast cancer"],
  "comedications": ["..."],
  "pgx": {
    "CYP2D6": "*4/*4"
  },
  "organ_function": {
    "eGFR": 75,
    "ALT": 35,
    "LVEF": 62
  },
  "route": "IV",
  "form": "injection",
  "dose": "..."
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| ICD-10 / UMLS / MeSH | diagnosis text | 标准疾病/适应症 ID |
| PersADE `INDI_UMLS.txt` / `diseases_progress.csv` | 疾病名 / UMLS | 适应症 UMLS |
| DrugBank / RxNorm | comedication name | 合并用药标准 ID |
| PharmGKB / CPIC | gene + allele | PGx phenotype |
| 临床规则库 | eGFR、ALT、LVEF 等 | 器官功能分层 |

### 输出

```json
{
  "patient_features": {
    "age_group": "50-59YR",
    "sex": "Female",
    "indication_umls": ["..."],
    "comedication_ids": ["..."],
    "pgx_phenotypes": [
      {
        "gene": "CYP2D6",
        "diplotype": "*4/*4",
        "phenotype": "poor metabolizer"
      }
    ],
    "organ_function_classes": {
      "renal": "normal|mild_impairment|moderate_impairment|severe_impairment",
      "hepatic": "...",
      "cardiac": "normal_lvef"
    },
    "exposure_context": {
      "route": "...",
      "form": "...",
      "dose": "..."
    }
  }
}
```

## Step 2：相似病例和人群分布检索

### 目的

从 PersADE 的真实世界报告和分层打分中检索与患者相似的 ADE 风险分布。

### 输入

```json
{
  "inchi_key": "...",
  "patient_features": {},
  "general_toxicity": []
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| PersADE `CCombined_Results_with_scores.txt` | `Drug_ID` + candidate `Reaction_ID` | 总体 drug-ADE baseline、sex_dis、age_dis、severity |
| PersADE `CCombined_Results_route_with_scores.txt` | `Drug_ID` + `Route` + `Reaction_ID` | 给药途径分层 PRR/ROR/score |
| PersADE `CCombined_Results_form_with_scores.txt` | `Drug_ID` + `Form` + `Reaction_ID` | 剂型分层 PRR/ROR/score |
| PersADE `CCombined_Results_INDI_with_scores.txt` | `INDI_ID` + `Drug_ID` + `Reaction_ID` | 适应症分层 PRR/ROR/score |
| PersADE `ADE_report.txt` | Report_ID / drug / ADE | 年龄、性别、结局、剂量、途径、剂型、严重性 |

### 处理逻辑

1. 对第一阶段筛出的候选 ADE/SOC 检索总体药物-ADE 风险。
2. 按患者的年龄组、性别、适应症、给药途径、剂型寻找分层证据。
3. 比较分层风险与总体风险：

```text
risk_shift_context = subgroup_score - overall_score
```

4. 若分层病例数太少，增加不确定性，不直接大幅修正风险。

### 输出

```json
{
  "persade_contextual_evidence": [
    {
      "ade_id": "...",
      "soc": "...",
      "overall": {
        "ror": 0.0,
        "total_score": 0.0,
        "case_number": 0
      },
      "subgroups": {
        "age": "...",
        "sex": "...",
        "route": "...",
        "form": "...",
        "indication": "..."
      },
      "contextual_risk_shift": 0.0,
      "uncertainty": 0.0
    }
  ]
}
```

## Step 3：PGx 规则检索

### 目的

判断患者基因型是否改变药物暴露、药效或免疫介导毒性风险。

### 输入

```json
{
  "drug_entity": {},
  "patient_pgx": {
    "CYP2D6": "*4/*4",
    "HLA-B": "*15:02"
  }
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| CPIC | drug + gene + diplotype | phenotype、recommendation、evidence level |
| DPWG | drug + gene | dose recommendation、avoidance recommendation |
| PharmGKB | drug + gene + variant | clinical annotation、level of evidence |
| ClinVar | variant | clinical significance |
| PersADE `ADE_Information.txt` | ADE UMLS | Increase_Var、Decrease_Var、PharmGKB |
| HLA 预测工具 / NetMHCpan | drug/metabolite + HLA | HLA 结合风险，主要用于 SJS/TEN 等 |

### 处理逻辑

PGx 风险分为三类：

1. PK 型：CYP/UGT/转运体改变药物暴露。
2. PD 型：靶点变异改变药效或毒性敏感性。
3. 免疫型：HLA 等位基因增加超敏反应风险。

### 输出

```json
{
  "pgx_risk_modifiers": [
    {
      "factor_type": "PGx",
      "gene": "CYP2D6",
      "genotype": "*4/*4",
      "phenotype": "poor metabolizer",
      "affected_mechanism": "metabolism/exposure",
      "direction": "up|down",
      "magnitude": 0.0,
      "related_soc": ["..."],
      "related_ade": ["..."],
      "rule_id": "CPIC-...",
      "evidence": {
        "source": "CPIC",
        "tier": "T1",
        "grade": "A|B|C|D"
      }
    }
  ]
}
```

## Step 4：合并用药与 DDI 检索

### 目的

判断合并用药是否通过 CYP、转运体、药效叠加或同器官毒性叠加改变风险。

### 输入

```json
{
  "index_drug": "...",
  "comedications": ["..."]
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| DrugBank DDI | drug pair | 相互作用描述、机制 |
| DDInter | drug pair | DDI 类型、严重程度、证据 |
| admetSAR3 | index drug + comedications | CYP inhibitor/substrate、P-gp/BCRP/OATP 等 |
| ChEMBL / DrugBank | drug-target | 共同靶点或脱靶 |
| CredibleMeds | all drugs | QT/TdP 风险叠加 |
| LiverTox / DILIrank | all drugs | 肝毒叠加 |

### 处理逻辑

DDI 修正分四类：

1. 暴露升高：抑制代谢酶或转运体。
2. 暴露降低：诱导代谢酶或转运体。
3. 药效叠加：例如多药均延长 QT。
4. 器官毒性叠加：例如多个肝毒/肾毒药物合用。

### 输出

```json
{
  "ddi_risk_modifiers": [
    {
      "factor_type": "comedication",
      "comedication": "...",
      "interaction_type": "CYP inhibition|QT additive|organ toxicity additive|...",
      "direction": "up|down",
      "magnitude": 0.0,
      "affected_soc": ["Cardiac disorders"],
      "mechanism": "...",
      "evidence": {
        "source": "DrugBank|DDInter|CredibleMeds",
        "tier": "T1|T2"
      }
    }
  ]
}
```

## Step 5：共病与适应症修正

### 目的

判断患者基础疾病或用药适应症是否改变 ADE 风险。

### 输入

```json
{
  "drug_entity": {},
  "patient_indications": ["..."],
  "diagnoses_icd10": ["..."]
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| PersADE `CCombined_Results_INDI_with_scores.txt` | INDI_ID + Drug_ID + Reaction_ID | 适应症分层 ADE 风险 |
| PersADE `INDI_UMLS.txt` | disease name | UMLS |
| Open Targets / CTD | disease + gene/target | 疾病-靶点/毒性易感性 |
| 临床指南 / 禁忌证 | disease + drug | 禁忌、慎用、监测要求 |
| FDA label | disease state | Warnings / contraindications |

### 处理逻辑

1. 将患者疾病映射到 UMLS/ICD。
2. 检索该适应症下药物-ADE 是否更高。
3. 检查疾病是否本身影响器官易感性，例如：
   - 基础肝病增加 DILI 风险。
   - 心衰或低 LVEF 增加心毒风险。
   - 慢性肾病增加蓄积和肾毒风险。

### 输出

```json
{
  "comorbidity_risk_modifiers": [
    {
      "factor_type": "comorbidity",
      "factor": "...",
      "direction": "up|down",
      "magnitude": 0.0,
      "affected_soc": ["..."],
      "mechanism": "...",
      "evidence": {
        "source": "PersADE_INDI|FDA label|guideline",
        "tier": "T1|T2|T4"
      }
    }
  ]
}
```

## Step 6：器官功能修正

### 目的

根据肝、肾、心等器官功能指标修正毒性风险。

### 输入

```json
{
  "organ_function": {
    "eGFR": 75,
    "ALT": 35,
    "AST": 30,
    "bilirubin": 1.0,
    "LVEF": 62
  },
  "drug_entity": {}
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| FDA label / DrugBank | drug + renal/hepatic impairment | 剂量调整、禁忌、警示 |
| CPIC / DPWG | drug + organ impairment if available | 推荐 |
| 临床指南 | organ function class | 监测和调整建议 |
| admetSAR3 | clearance、PPB、VDss、CYP、transporter endpoints | 蓄积倾向 |
| PersADE | age_dis、severity、outcome | 高风险人群和结局支持 |

### 处理逻辑

1. 将 eGFR、ALT/AST、bilirubin、LVEF 转成临床分层。
2. 若药物经肾排泄且 eGFR 下降，则上调肾毒或全身暴露相关风险。
3. 若药物有 DILI 信号且基线肝功能异常，则上调肝毒风险。
4. 若药物有心毒或 hERG 风险且 LVEF 低/心脏病史，则上调心毒风险。

### 输出

```json
{
  "organ_function_modifiers": [
    {
      "factor_type": "organ_function",
      "factor": "LVEF 45%",
      "direction": "up",
      "magnitude": 0.25,
      "affected_soc": ["Cardiac disorders"],
      "rule_id": "CARDIAC-LVEF-...",
      "evidence": {
        "source": "FDA label|guideline|DrugBank",
        "tier": "T1|T2"
      }
    }
  ]
}
```

## Step 7：暴露信息修正

### 目的

根据给药途径、剂型、剂量、频率和疗程修正 ADE 风险。

### 输入

```json
{
  "route": "...",
  "form": "...",
  "dose": "...",
  "frequency": "...",
  "duration": "..."
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| PersADE `CCombined_Results_route_with_scores.txt` | Drug_ID + Route + Reaction_ID | 给药途径分层风险 |
| PersADE `CCombined_Results_form_with_scores.txt` | Drug_ID + Form + Reaction_ID | 剂型分层风险 |
| PersADE `drug_all.txt` | InChIkey | Route_Distribution、Form_Distribution、Dose_Distribution |
| FDA label / DrugBank | drug + dose | 推荐剂量、最大剂量、剂量相关警示 |
| PK 数据库 / PK-DB | drug + dose | 暴露参数 |

### 处理逻辑

1. 判断患者给药途径/剂型是否对应 PersADE 高风险分层。
2. 判断剂量是否超过常用范围或标签推荐范围。
3. 对剂量依赖毒性加权，例如 QT、肝毒、骨髓抑制、肾毒。

### 输出

```json
{
  "exposure_modifiers": [
    {
      "factor_type": "exposure",
      "factor": "IV route",
      "direction": "up",
      "magnitude": 0.0,
      "affected_soc": ["..."],
      "evidence": {
        "source": "PersADE_route|FDA label",
        "tier": "T1|T4"
      }
    }
  ]
}
```

## Step 8：个体风险融合

### 目的

把第一阶段 baseline risk 和第二阶段所有个体化修正因素融合，得到最终个体风险。

### 输入

```json
{
  "baseline_organ_risk": [],
  "persade_contextual_evidence": [],
  "pgx_risk_modifiers": [],
  "ddi_risk_modifiers": [],
  "comorbidity_risk_modifiers": [],
  "organ_function_modifiers": [],
  "exposure_modifiers": []
}
```

### 处理逻辑

建议采用可解释规则加权作为 MVP：

```text
personalized_risk = baseline_risk
  + PersADE context shift
  + PGx shift
  + DDI shift
  + comorbidity shift
  + organ function shift
  + exposure shift
```

同时计算不确定性：

```text
uncertainty = f(
  evidence tier,
  case number,
  missingness,
  conflict,
  model calibration
)
```

证据优先级：

1. CPIC/DPWG/FDA label/专科指南。
2. DILIrank/LiverTox/CredibleMeds。
3. admetSAR3/ChEMBL/DrugBank/Reactome 等机制证据。
4. PersADE/FAERS 信号。
5. PubMed 个案或弱文献。

### 输出

```json
{
  "personalized_risk_profile": [
    {
      "soc": "Cardiac disorders",
      "baseline_probability": 0.45,
      "personalized_probability": 0.72,
      "risk_shift": 0.27,
      "personalized_risk_level": "high",
      "uncertainty": 0.18,
      "dominant_modifiers": [
        "QT-risk comedication",
        "low LVEF",
        "CredibleMeds known risk"
      ]
    }
  ]
}
```

## Step 9：CTCAE 对齐与临床建议生成

### 目的

将最终风险转换为临床可读的严重度、监测和处置建议。

### 输入

```json
{
  "personalized_risk_profile": [],
  "patient_attribution": [],
  "evidence": []
}
```

### 检索知识库

| 知识库 | 检索字段 | 返回信息 |
|---|---|---|
| CTCAE v5.0 | ADE / SOC | grade 定义 |
| FDA label | drug + ADE | 监测、停药、减量建议 |
| CPIC / DPWG | drug + genotype | PGx 建议 |
| ASCO/ESMO/ESC cardio-oncology | toxicity type | 专科管理建议 |
| 肝病/DILI 指南 | DILI risk | 肝毒监测和处置 |
| CredibleMeds | QT/TdP risk | QT 风险管理 |

### 处理逻辑

输出建议分为：

- `monitor`：建议监测。
- `dose_adjust`：建议剂量调整。
- `avoid`：建议避免使用或换药。
- `consult_specialist`：建议专科会诊。
- `no_action`：暂无额外措施。

### 输出

```json
{
  "clinical_recommendation": {
    "action": "monitor|dose_adjust|avoid|consult_specialist|no_action",
    "text": "...",
    "ctcae_aligned": true,
    "monitoring_items": ["ALT/AST", "ECG", "LVEF"],
    "trigger_threshold": "...",
    "evidence": []
  }
}
```

---

## 9. 两阶段之间的数据衔接

第一阶段输出不是最终结论，而是第二阶段的先验。

```text
第一阶段 baseline risk
  -> 第二阶段 patient modifiers
  -> personalized risk
```

衔接字段建议如下：

| 第一阶段字段 | 第二阶段用途 |
|---|---|
| `soc` | 确定个性化修正的器官系统 |
| `baseline_probability` | 个体风险计算起点 |
| `baseline_risk_level` | 个体化风险等级比较 |
| `main_ade_terms` | 检索 PersADE 分层表、CTCAE、label |
| `mechanism_chains` | 判断 PGx/DDI/共病是否命中同一机制 |
| `admet_endpoint` | 判断暴露、代谢、转运体修正方向 |
| `evidence` | 证据冲突裁决和不确定性计算 |

---

## 10. 推荐工具接口

## 10.1 第一阶段工具

| 工具名 | 输入 | 输出 |
|---|---|---|
| `drug_normalize` | drug name / SMILES / DrugBank ID | InChIkey、SMILES、DrugBank ID、ChEMBL ID |
| `admetsar_profile_query` | SMILES | descriptors + ADMET endpoints |
| `structural_alert_query` | SMILES | toxicophore、SMARTS、matched atoms |
| `persade_drug_profile` | InChIkey | drug-ADE signals |
| `gold_toxicity_lookup` | drug ID + SOC | DILIrank、LiverTox、CredibleMeds、label |
| `mechanism_chain_query` | InChIkey + UMLS | Drug-Target-Pathway-ADE chains |
| `soc_mapping` | UMLS / MeSH / MedDRA | SOC、CTCAE term |

## 10.2 第二阶段工具

| 工具名 | 输入 | 输出 |
|---|---|---|
| `patient_normalize` | raw patient profile | age group、sex、ICD/UMLS、PGx phenotype |
| `persade_contextual_retrieval` | InChIkey + ADE + patient context | age/sex/route/form/indication evidence |
| `cpic_lookup` | drug + gene + genotype | PGx recommendation |
| `ddi_query` | index drug + comedications | DDI mechanism and severity |
| `organ_function_rule_match` | drug + eGFR/ALT/LVEF | organ function risk shift |
| `exposure_rule_match` | drug + route/form/dose | exposure risk shift |
| `ctcae_recommendation` | ADE + risk + patient context | CTCAE grade and clinical recommendation |

---

## 11. MVP 推荐路径

MVP 阶段建议先做肝毒和心毒。

## 11.1 肝毒路径

核心检索链：

```text
SMILES
  -> admetSAR3 DILI / CYP / UGT / BSEP / mitochondrial endpoints
  -> PersADE drug-ADE liver-related signals
  -> LiverTox / DILIrank / DILIst / LTKB validation
  -> DrugBank metabolism / CYP / transporter
  -> patient liver function + comedication + PGx
  -> personalized hepatotoxicity risk
```

重点知识源：

- admetSAR3：`label_DILI_t`、CYP、BSEP、Mito、Ames、Tox21 endpoints。
- PersADE：药物-肝胆相关 ADE 信号、严重度、人群分布。
- LiverTox/DILIrank：金标准裁决。
- DrugBank/CPIC/DPWG：代谢和 PGx。
- FDA label：肝功能异常、禁忌、监测。

## 11.2 心毒路径

核心检索链：

```text
SMILES
  -> admetSAR3 hERG endpoints
  -> ChEMBL hERG assay
  -> PersADE cardiac ADE signals
  -> CredibleMeds QT/TdP category
  -> DTA/DTI target chain such as KCNH2
  -> patient LVEF + QT drugs + electrolyte/comorbidity context
  -> personalized cardiotoxicity risk
```

重点知识源：

- admetSAR3：`label_hERG_*`、CYP、transporter、Mito endpoints。
- ChEMBL：hERG IC50/Ki。
- CredibleMeds：QT/TdP 金标准。
- PersADE：心脏 ADE 信号、route/form/indication 分层。
- DrugBank/DDInter：QT 叠加和 CYP DDI。
- ESC cardio-oncology / FDA label：监测和处置建议。

---

## 12. 证据分级和裁决规则

Agent 输出时必须区分“事实确认”和“信号提示”。

| 来源 | 用法 |
|---|---|
| FDA/EMA/NMPA label | 可作为强证据 |
| CPIC/DPWG/专科指南 | 可作为临床规则 |
| LiverTox/DILIrank/CredibleMeds | 可作为专科金标准 |
| DrugBank/ChEMBL/UniProt/Reactome | 可作为机制支持 |
| admetSAR3 | 可作为模型特征和 ADMET 证据 |
| PersADE/FAERS | 可作为真实世界信号，不单独判因果 |
| PubMed 个案 | 可作为补充证据，不单独判定 |

裁决原则：

```text
若 L1-L3 支持：可判定 confirmed / supported。
若只有 PersADE/FAERS：标记 signal / unverified。
若高可信来源与信号冲突：保留冲突并提高不确定性。
若缺失关键数据：标记 unknown，不把缺失当阴性。
```

---

## 13. 最终端到端流程图

```text
输入：药物 + 患者画像
        |
        v
药物标准化
        |
        v
第一阶段：通用毒性预测
        |
        +--> admetSAR3：结构、理化性质、ADMET endpoints
        +--> PersADE：drug-ADE baseline signals
        +--> DILIrank / LiverTox / CredibleMeds：专科金标准
        +--> DrugBank / ChEMBL / UniProt / Pathway：机制证据
        |
        v
输出 baseline organ toxicity risk
        |
        v
第二阶段：个性化毒性预测
        |
        +--> PersADE：age/sex/route/form/indication 分层风险
        +--> CPIC / DPWG / PharmGKB：PGx 修正
        +--> DrugBank / DDInter：DDI 修正
        +--> FDA label / 指南：器官功能和禁忌修正
        +--> CTCAE：严重度和临床建议
        |
        v
输出 personalized toxicity risk + attribution + recommendation
```

---

## 14. 一句话总结

PersTox-Agent 的第一阶段应以 admetSAR3 为主体，结合 PersADE、专科金标准和机制库，得到药物本身的器官毒性 baseline；第二阶段应以 PersADE 的患者上下文和分层风险为主体，再叠加 PGx、DDI、共病、器官功能和暴露规则，得到个体化风险、患者因素归因和 CTCAE 对齐的临床建议。
