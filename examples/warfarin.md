# Warfarin Personalized Toxicity Demo

本示例展示 PersTox-Agent 如何对 warfarin 进行两阶段个性化毒性推理。示例入口是项目根目录下的
`examples/run_warfarin_demo.py`，输出文件为 `results/final_report_warfarin.json`。

> 本示例仅用于科研复现和方法演示，不构成临床建议。

## 示例目标

给定一个 warfarin 暴露场景和患者画像，系统执行：

1. **Stage 1 通用毒性推理**：检索药物结构、ADMET、代谢、靶点、通路、PersADE/ADE 信号，生成
   SOC 层面的 baseline toxicity。
2. **Stage 2 个性化毒性推理**：叠加患者肝肾功能、PGx、合并疾病和合并用药，生成 personalized
   toxicity、风险偏移和建议。
3. **Safety Verifier 校验**：检查输出结构、证据一致性、重要 DDI/PGx 是否被建议反映，以及确定性安全红线。

## 输入

当前 demo 的输入定义在 `main.py` 的 `build_demo_state()` 中。

### 药物输入

```json
{
  "name": "warfarin",
  "drugbank_id": "DB00682",
  "smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(O)C3=CC=CC=C3OC2=O"
}
```

### 患者输入

```json
{
  "patient_id": "demo-warfarin-001",
  "age": 65,
  "sex": "female",
  "weight_kg": 58,
  "alt_u_l": 68,
  "ast_u_l": 74,
  "bilirubin_mg_dl": 1.8,
  "child_pugh": "B",
  "creatinine_mg_dl": 1.3,
  "egfr_ml_min": 45,
  "genotypes": {
    "CYP2C9": "*2/*3"
  },
  "hla_types": [],
  "medical_history": [
    "K74.6 cirrhosis",
    "I48 atrial fibrillation"
  ],
  "concomitant_medications": [
    "amiodarone"
  ],
  "organ_function": {
    "LVEF": "55%"
  },
  "exposure": {
    "route": "oral",
    "frequency": "daily"
  },
  "pregnancy_status": "not_pregnant"
}
```

## 运行

在项目根目录运行：

```bash
python main.py
```

运行完成后生成：

```text
results/final_report_warfarin.json
```

终端会打印以下摘要：

```text
PersAgent Trace
Knowledge Retrieval
Stage 1 Universal Toxicity
Attribution Explanation
Stage 2 Personalized Toxicity
Recommendations
Verification
Final Output
```

## 工作流

本示例经过以下节点：

```text
orchestrator_parse_input
  -> orchestrator_stage1_plan_retrieval
  -> knowledge_retrieval_node
  -> orchestrator_stage1_reason
  -> orchestrator_standardize_patient
  -> orchestrator_stage2_plan_retrieval
  -> knowledge_retrieval_node
  -> orchestrator_stage2_reason
  -> safety_verifier_node
  -> orchestrator_revise_output
  -> format_output
```

## 知识检索

Stage 1 默认检索：

```text
drug_card_lookup
drugbank_metabolism_query
admetsar_predict
dti_query
mechanism_query
pathway_enrich
persade_drug_profile
mechanism_chains_lookup
```


这些函数由 `src/pertox_agent/tools/runtime/retrieval_runtime.py` 统一适配，再委托到底层工具：

```text
src/pertox_agent/tools/molecular_evidence/
src/pertox_agent/tools/real_world_evidence/
src/pertox_agent/tools/shared/
src/pertox_agent/tools/toxicity_attribution/
```

## 关键标准化结果

示例输出中的药物实体：

```json
{
  "primary_name": "warfarin",
  "inchi_key": "PJVWKTKQMONHTI-UHFFFAOYSA-N",
  "drugbank_id": "DB00682",
  "chembl_id": "CHEMBL1464",
  "pubchem_id": "54678486",
  "atc": "B01AA03",
  "drug_type": "small molecule"
}
```

患者画像标准化结果包括：

| 字段 | 结果 |
|---|---|
| age group | `60-69YR` |
| sex | `Female` |
| renal function | `moderate`, based on eGFR 45 mL/min |
| hepatic function | `moderate`, based on Child-Pugh B |
| cardiac function | `normal_lvef`, based on LVEF 55% |
| PGx | CYP2C9 `*2/*3`, intermediate metabolizer |
| indication | cirrhosis, atrial fibrillation |
| comedication | amiodarone |

## Stage 1 输出摘要

当前 demo 中，主动建模的 SOC 为肝胆和心脏；其他 SOC 行作为 placeholder 保留。

| SOC | baseline risk | baseline probability | uncertainty | CTCAE grade |
|---|---:|---:|---:|---:|
| Hepatobiliary disorders | moderate | 0.42 | 0.555 | 3 |
| Cardiac disorders | moderate | 0.49 | 0.484 | 3 |
| Renal and urinary disorders | null | null | null | null |
| Blood and lymphatic system disorders | null | null | null | null |
| Immune system disorders | null | null | null | null |
| Skin and subcutaneous tissue disorders | null | null | null | null |
| Nervous system disorders | null | null | null | null |
| Gastrointestinal disorders | null | null | null | null |





## 注意事项

- 数值会随本地数据版本、缓存、规则实现和 live LLM 配置变化。
- `FLAGGED` 不等于运行失败，而是表示需要人工复核。
- PersADE / FAERS-derived 信号代表统计关联，不等同于因果证明。
- 本示例不应用于真实患者决策。


