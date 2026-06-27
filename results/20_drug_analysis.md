# 20 Drug Cardiac Attribution Analysis

## 1. Sulfaisodimidine

Attribution Explanation:

Cardiac disorders attribution is driven mainly by AGO2/PON1 target engagement. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution of cardiac disorders to Sulfaisodimidine is primarily driven by AGO2/PON1 target engagement, as identified in the molecular attribution. Mechanistically, Sulfaisodimidine binds AGO2 (on-target, Kd=4300 nM) and reduces PON1 activity (off-target). PON1 is involved in regulating plasma lipoprotein oxidation and organophosphate catabolism, pathways that may influence cardiovascular homeostasis. This target-pathway evidence contributes to a low baseline risk (12% probability, 78% uncertainty) being increased, though no additional probability drivers beyond this mechanism were identified. The overall confidence is low (0.28), as the cardiac phenotype is not directly specified, and the chain of evidence linking target engagement to cardiac outcomes remains weak. Key limitations include the absence of explicit toxicophore matches, GNN attention, or SHAP-based atom attributions, which would have strengthened the case. The attribution relies solely on the target engagement mechanism, with supporting pathway enrichment data, but high uncertainty and low confidence restrict its conclusiveness.

## 2. Pheniprazine

Attribution Explanation:

No probability drivers identified. Mechanism chain is incomplete (chain_confidence=0.0) and no direct DTI, metabolism, or ADE signals found. Baseline risk remains low (0.12).

Attribution Narrative:

The attribution for Pheniprazine's cardiac toxicity is currently uninformative due to a complete absence of probability drivers. The baseline risk remains low (0.12), unchanged from prior, as no gold-standard, ADMET, ADE population, or target/pathway evidence was identified. Mechanistic reasoning is unsupported: the mechanism chain is incomplete (confidence 0.0), with unknown metabolism, active species, target binding, and downstream pathways linking Pheniprazine to cardiac disorders. General physicochemical properties (molecular weight, hydrogen bond donors/acceptors, rotatable bonds, TPSA, SlogP, ring count, atom count) are not organ-specific (confidence 0.1) and only neurologic impact was noted in ADMET predictions. Key limitations include missing structural alert matching, GNN attention, and SHAP attribution; no cardiac-specific ADMET endpoints or target pathways were found; and no explicit toxicophore match was available. In summary, the current attribution provides only mechanistic and structural context without driving probability, underscoring the need for additional data to assess cardiac risk.

## 3. 6-Cyano-7-nitroquinoxaline-2,3-dione

Attribution Explanation:

Baseline cardiac risk is low (0.12) due to absence of any probability-driving evidence. The drug has no known cardiac targets, no ADMET endpoints, no population signals, and no metabolism data. The only structural alert (nitro group) is associated with liver/hematologic toxicity, not cardiac. The mechanism chain is incomplete with low confidence, and no pathway enrichment was found.

Attribution Narrative:

The molecular attribution for the cardiac risk of 6-Cyano-7-nitroquinoxaline-2,3-dione (CNQX) indicates a low baseline probability (0.12) due to a complete absence of cardiac-relevant evidence. No direct or indirect links to cardiac toxicity were identified: no known cardiac targets, no mechanism chains, no enriched pathways, and no population signals were found. The only structural feature flagged is a nitro group alert, but its contribution (0.12) pertains to liver and hematologic toxicity, not cardiac. This lack of evidence maintains the low baseline risk. However, several limitations weaken the attribution: no GNN or SHAP atom-level attribution was available, structural alert matching using SMARTS was not performed, and the absence of a DrugBank ID and unresolved InChIKey prevented metabolism queries and population signal retrieval. Consequently, while the current evidence does not suggest cardiac liability, the confidence is limited (uncertainty 0.78) due to incomplete mechanistic and population data.

## 4. Pantoyl Adenylate

Attribution Explanation:

No probability drivers identified. The drug Pantoyl Adenylate has a low baseline risk for cardiac disorders. Available evidence includes minimal ADMET endpoints (e.g., CYP3A4 non-substrate), an off-target (panC) with unknown action, and a low-confidence mechanism chain with unknown metabolites and pathways. No population signals or ADE signals were found. Physicochemical properties (high TPSA, negative logP) are contextual but not directly linked to cardiac toxicity. Attributions are limited by missing structural alerts and mechanistic evidence.

Attribution Narrative:

Pantoyl Adenylate has a low baseline risk for cardiac disorders, and the analysis identified no probability drivers that increase that risk. The available evidence is contextual only. A low-confidence mechanism chain via panC (pantothenate synthetase) suggests a possible connection to cardiac disorders but with unknown downstream pathways and low confidence. Off-target binding to panC is noted, but its relevance to human cardiac toxicity is unknown. Physicochemical properties (high TPSA, negative logP) indicate high polarity and low lipophilicity, which may limit cardiac penetration but provide no direct toxicity link. Key limitations include the absence of structural alert matching, no population or ADE signals, no metabolism information, and low confidence in the mechanism chain due to unknown intermediates. Overall, the attribution is weak and remains largely speculative due to insufficient data.

## 5. Isobutyraldehyde

Attribution Explanation:

No probability drivers were identified. The ADMET profile shows no structural alerts or positive toxicity endpoints. The mechanism chain for cardiac toxicity is incomplete with unknown nodes and low confidence. No drug-target interactions or population signals were found. The baseline risk remains low with high uncertainty.

Attribution Narrative:

The baseline risk of cardiac toxicity for isobutyraldehyde is low (12% probability) but with high uncertainty. The molecular attribution does not identify any probability-increasing drivers. Instead, structural alert screening found no recognized toxicophores, and ADMET predictions yielded no positive toxicity endpoints (e.g., carcinogenicity, nuclear receptor, stress response), both decreasing the likelihood of cardiac effects. However, the mechanistic chain for cardiac toxicity is incomplete, with unknown metabolism, species, target, and pathway nodes (confidence 0.0–0.25), preventing definitive attribution and contributing to high uncertainty. Key limitations include the absence of GNN attention or SHAP atom-level evidence, no drug–target interactions or population signals (FAERS), and incomplete metabolism/ADE profiling due to unresolved DrugBank ID. While the available structural and ADMET context suggests low reactivity potential, the incomplete mechanism chain and lack of direct experimental or clinical data leave the attribution with low confidence. No additional drivers were identified that would raise the probability above baseline.

## 6. Ecopipam

Attribution Explanation:

Ecopipam's baseline cardiac risk is low (0.12). The probability_audit reports no main drivers, indicating no strong evidence increased risk. ADMET endpoints show no cardiac alerts; population signals are absent. A low-confidence mechanism chain links dopamine receptor modulation (DRD1, DRD2, DRD4) to cardiac phenotype, but confidence is low (0.25) and lacks direct cardiac mechanistic specification. Thus, no molecular attribution drivers were identified as probability drivers.

Attribution Narrative:

Ecopipam's baseline probability for cardiac disorders is low (0.12) with high uncertainty (0.78). The main drivers of probability are absent; the probability audit did not identify any evidence that increases risk. Although the drug modulates dopamine receptors (DRD1/DRD2/DRD4) and enriches dopaminergic pathways, this mechanism chain has low confidence (0.25) and no direct cardiac toxicity specification. ADMET predictions show no cardiac-specific alerts (e.g., hERG, cardiotoxicity), and the drug's physicochemical properties do not indicate cardiac risk. Population signal analysis from FAERS and PersADE reveals no significant adverse event signals for cardiac disorders, supporting a lack of real-world evidence. Consequently, the molecular attribution drivers are limited to contextual roles: the target pathway provides a mechanistic context but with low confidence, the absence of ADMET alerts and population signals further reduces concern. Key limitations include unavailable structural alert matching, GNN attention, SHAP attribution, metabolism data, and the low-confidence mechanism chain. Overall, no molecular attribution drivers were identified as probability drivers, consistent with the low baseline probability and high uncertainty.

## 7. Clidinium

Attribution Explanation:

Cardiac disorders attribution is driven mainly by Muscarinic M1 antagonism. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution of cardiac disorders to Clidinium is primarily based on its known antagonism of the muscarinic M1 receptor (CHRM1) as a mechanistic context. However, this driver has low confidence (0.3) and is not specific to cardiac disorders, as no direct cardiac endpoint evidence or pathway enrichment was found. The baseline risk for cardiac disorders is low (12% probability), and the overall probability audit did not identify any additional strong drivers from gold standard, ADMET, or population signals, leading to a high uncertainty (0.78). Structural alert matching, GNN attention, and SHAP attributions were not available in the analysis. Overall, while M1 antagonism provides a plausible link via parasympathetic modulation of heart rate, the evidence remains weak and non-specific, and no further molecular support was identified.

## 8. 5-methylpyrazole-3-carboxylic acid

Attribution Explanation:

No probability drivers were identified in the probability audit. The baseline probability for cardiac disorders is low (0.12) with high uncertainty. Drug-target interactions (DAO, HCAR2) and pathway enrichment (e.g., negative regulation of lipid catabolic process) provide mechanistic context but do not directly link to cardiac toxicity. No structural alerts, ADMET toxicity endpoints, or population signals were found. The mechanism chain is incomplete, and metabolism data are unavailable. Therefore, no evidence supports increased cardiac risk.

Attribution Narrative:

The baseline probability for cardiac disorders is low (0.12) with high uncertainty (0.78), and no probability drivers were identified in the audit. The drug binds to DAO and HCAR2, and pathway enrichment suggests perturbation of lipid catabolism, but these provide only mechanistic context without a direct link to cardiac toxicity. No structural alerts or cardiac-specific ADMET endpoints were found, reducing the likelihood of direct toxicity. However, metabolism data are unavailable, and the drug was not resolved in DrugBank or PersADE, limiting assessment of reactive metabolites and population signals. The absence of population-level adverse drug event signals further decreases the probability, though this may reflect a data gap rather than true safety. Overall, the evidence does not support an increased cardiac risk, but confidence is limited by unresolved drug identifiers, missing GNN/SHAP atom-level attribution, and high baseline uncertainty.

## 9. Chlorimuron-ethyl

Attribution Explanation:

Cardiac disorders attribution is driven mainly by ADMET predicted physicochemical properties (SlogP, TPSA, HBA, etc.). The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The cardiac disorder attribution for Chlorimuron-ethyl is primarily based on ADMET-predicted physicochemical properties (SlogP, TPSA, HBA, etc.), which provide structural context but are not directly linked to cardiac toxicity for this compound. These properties influence drug disposition but lack a specific property-toxicity association. The overall probability of cardiac toxicity remains low (baseline risk 12%) with high uncertainty (78%), as no additional gold-standard, population, or pathway evidence increased the probability. The attribution is constrained by the absence of SMARTS-level toxicophore matches, GNN attention interpretations, or SHAP feature attributions in the available tool results. Thus, while physicochemical properties serve as a contextual driver, the evidence is weak and non-specific, limiting confidence in the attribution.

## 10. zwittergent 3-12

Attribution Explanation:

Cardiac disorders attribution is driven mainly by High lipophilicity and flexible chain (SlogP 3.919, nRot 15, QED 0.26). The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The molecular attribution for cardiac disorders is primarily driven by the compound's high lipophilicity and flexible alkyl chain, as indicated by a SlogP of 3.919, 15 rotatable bonds, and a low QED of 0.26. These physicochemical properties are considered structural context, suggesting possible enhanced partitioning into cardiac tissue, but no direct mechanistic link to cardiac toxicity has been established. The probability of cardiac risk is low (0.12) with substantial uncertainty (0.78), reflecting the limited evidence. The contribution confidence of this driver is low (0.08), and it is derived from ADMET predictions that are not specific to cardiac effects. Additional limitations include the absence of structural alert matches, GNN attention attribution, and SHAP feature attribution, which constrain the explanation. Therefore, while the lipophilic nature may be a predisposing factor, the overall attribution remains weak and context-dependent.

## 11. Amenamevir

Attribution Explanation:

No probability drivers were identified for cardiac toxicity. The baseline probability remains low (0.12) because the probability_audit main_drivers and evidence_summary are empty. Retrieved evidence provides only structural and physicochemical context: a coumarin-like lactone structural alert (relevant to hematologic/liver, not cardiac), generic physicochemical descriptors (e.g., MW 482.6, SlogP 3.15), and a low-confidence mechanism chain with unknown steps. These do not elevate risk for Cardiac disorders.

Attribution Narrative:

No probability drivers were identified for amenameavir's cardiac toxicity risk; the baseline probability remains low at 0.12. The attribution relies solely on contextual evidence. A coumarin-like lactone structural alert is present but is linked to hematologic and liver toxicity, not cardiac disorders, and contributes minimally (confidence 0.12). Generic physicochemical properties (e.g., MW 482.6, SlogP 3.15, TPSA 122.47) provide only ADME context with low probabilistic contributions (0.05–0.08) and no cardiac specificity. A low-confidence mechanism chain (score 0.259, confidence 0.0) involves mostly unknown steps—metabolism, toxic species, target, and pathway—offering no established link to cardiac toxicity. Key limitations include absence of GNN attention or SHAP attributions, no SMARTS matching, no drug-target interactions, and no population signals from PersADE. The probability audit found no drivers; thus, the elevated risk is unsupported.

## 12. Bromocriptine mesylate

Attribution Explanation:

No probability drivers were identified for Bromocriptine mesylate in Cardiac disorders. Baseline risk is low (0.12). Evidence from ADMET properties and a low-confidence mechanism chain does not increase probability. Physicochemical features (MW, HBA, HBD, etc.) provide weak structural context. The mechanism chain is incomplete with unknown nodes and no specific cardiac targets. Population signals are absent. Therefore, the universal toxicity probability remains at baseline.

Attribution Narrative:

For Bromocriptine mesylate in Cardiac disorders, the analysis did not identify any specific molecular drivers that increase the probability of cardiac toxicity. The baseline risk remains low at 0.12, and neither ADMET physicochemical properties nor a proposed mechanism chain contribute meaningful evidence. The physicochemical properties (molecular weight, hydrogen bond donors/acceptors, topological polar surface area, etc.) provide only generic structural context, with a confidence of 0.05, and are not cardiac-specific. The mechanism chain, scoring 0.259 but with zero confidence, is incomplete—all nodes (metabolism, toxic species, target, pathway) are unknown, offering no mechanistic linkage. Key limitations include the absence of structural alert matching, GNN attention, SHAP atom-level evidence, and population signals from ADE databases. DrugBank queries for metabolism and drug-target interactions returned no data, and the mechanism chain remains speculative. Consequently, the universal toxicity probability stays at baseline, reflecting no actionable evidence for cardiac risk from this compound.

## 13. Pinacidil

Attribution Explanation:

Cardiac disorders attribution is driven mainly by ABCC9 target and cardiac conduction pathway. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution of Pinacidil to cardiac disorders is primarily driven by its interaction with the ABCC9 target and the cardiac conduction pathway. Pinacidil binds to ABCC9 (sulfonylurea receptor 2), a component of ATP-sensitive potassium channels, with an EC50 of 3540 nM. This binding perturbs potassium channel function, which is critical for cardiac conduction. Pathway enrichment analysis further supports this mechanism, showing significant enrichment of the cardiac conduction pathway (GO:0061337, p=0.00023, q=0.0013). While baseline cardiac risk was low (12%), the probability increased due to this target–pathway evidence. However, confidence in the mechanism chain is modest (0.402), and no direct experimental evidence for cardiac conduction effects in humans is available. Additionally, no SMARTS-based toxicophore matches or GNN/SHAP atom attributions were identified in this analysis. These limitations temper the strength of the attribution, indicating that while the molecular link is plausible, further validation is needed.

## 14. Histidine

Attribution Explanation:

Cardiac disorders attribution is driven mainly by No population signals from PersADE/FAERS. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution of cardiac disorders for histidine is primarily driven by the lack of population signals from PersADE/FAERS. No significant adverse drug event associations for cardiac disorders were reported in these databases, indicating a low population-level signal. This absence of evidence contributes to a contextual role, suggesting no strong epidemiological support for cardiotoxicity. However, this finding is not definitive; FAERS underreporting may obscure real signals. Additionally, no structural alerts or toxicophore matches were available via SMARTS, and no GNN or SHAP attributions were computed in Stage 1, limiting mechanistic insight. The overall probability assessment remains low (baseline 0.12 with high uncertainty), reflecting reliance on population context and lack of direct evidence from other attribution methods. While histidine is an essential amino acid with generally low risk, the cardiac attribution is constrained by sparse data from population sources and the absence of complementary computational toxicology evidence.

## 15. Almotriptan

Attribution Explanation:

Cardiac disorders attribution is driven mainly by MW=335.473; HBA=3.0; HBD=1.0. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The cardiac disorders attribution for Almotriptan is primarily driven by its physicochemical properties: a molecular weight of 335.473, 3 hydrogen bond acceptors, and 1 hydrogen bond donor. These admetSAR-derived features provide contextual, feature-level toxicity context, each with moderate confidence (0.46). Additionally, three population signals from PersADE/FAERS—Botulism, Vith Nerve Paresis, and Ultrasound Uterus Abnormal—support the terminal toxicity phenotype, each with slightly higher confidence (0.52), though they do not identify specific structural toxicophores. The baseline risk for cardiac disorders is low (probability 0.3, uncertainty 0.627). Key limitations include the absence of explicit SMARTS-level toxicophore matches, GNN attention attribution, and SHAP atom/feature attribution in the available tool results, meaning the attribution relies on broad property and population-level signals rather than precise molecular mechanisms.

## 16. 1,2,3,7,8-pentachlorodibenzofuran

Attribution Explanation:

Cardiac disorders attribution is driven mainly by AHR (aryl hydrocarbon receptor) binding. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution of cardiac disorders to 1,2,3,7,8-pentachlorodibenzofuran is primarily driven by binding to the aryl hydrocarbon receptor (AHR), which activates signaling pathways implicated in cardiac toxicity through downstream gene regulation and xenobiotic metabolism. This target interaction was identified via a drug-target interaction query, reporting an EC50 of 74.13 nM. Although the baseline risk for cardiac events is low (probability 0.12), the AHR binding evidence increases the probability, serving as the main probability driver. Contextual limitations include the absence of direct cardiac assay data, as the affinity was derived from literature without functional cardiac outcomes. Additionally, no structural alert matching, GNN attention, or SHAP feature attribution was available in the stage 1 analysis, limiting the depth of mechanistic support. The overall confidence in this driver is moderate (0.6), reflecting the indirect nature of the evidence. The attribution is thus anchored in AHR binding, with the understanding that further experimental validation in cardiac models would strengthen the association.

## 17. 7-Aza-L-tryptophan

Attribution Explanation:

Cardiac disorders attribution is driven mainly by 7-aza-l-tryptophan_heart_mechanism_chain. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution of cardiac disorders to 7-Aza-L-tryptophan is primarily based on a hypothetical mechanistic chain (7-aza-l-tryptophan_heart_mechanism_chain). This chain proposes a pathway from the parent drug through unknown metabolism to a target R and downstream cardiac effects, but all nodes have unknown or low confidence, yielding an overall chain confidence of 0.1. The chain score is 0.355, indicating a weak inferred link. No additional supporting evidence from structural alerts, GNN attention, or SHAP attribution was available. The probability audit shows no increase over baseline (low risk, probability 0.12), as no main drivers from gold standard, ADMET, or population signals contributed. The mechanism chain is speculative and lacks metabolite identification, target binding affinity, or pathway enrichment confirmation. Limitations include the absence of explicit toxicophore matches and the reliance on a low-confidence inferred chain, which does not provide strong grounds for attribution.

## 18. Palomid 529

Attribution Explanation:

Cardiac disorders attribution is driven mainly by MW=406.434; HBA=6.0; HBD=1.0. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution for Palomid 529-associated cardiac disorders is based on physicochemical properties derived from an admetSAR profile. Key drivers include molecular weight (406.434 Da), hydrogen bond acceptors (6), and hydrogen bond donors (1), each contributing with moderate confidence (0.46). These features provide contextual toxicity information but are not definitive. The baseline risk for cardiac adverse events is low (12%) with high uncertainty (78%), and the probability audit did not identify additional drivers that significantly elevate risk. The attribution is constrained by the absence of explicit SMARTS toxicophore matches, GNN attention maps, or SHAP-based feature importance calculations. Consequently, while the identified properties align with general heuristics for cardiotoxicity, the mechanistic link remains indirect, and the overall confidence in attribution is limited by the lack of more granular structural or mechanistic evidence.

## 19. Methazolamide

Attribution Explanation:

No probability drivers were identified from the evidence audit. The baseline probability for cardiac disorders is low (0.12). Methazolamide inhibits carbonic anhydrase (CA1, CA2, CA3) involved in bicarbonate and fluid transport, which may perturb cardiac electrolyte balance, but this mechanism chain has low confidence (0.282) and no direct population signal was found in PersADE. Physicochemical properties (low SlogP, high TPSA) suggest limited cardiac exposure, but no structural alerts or ADMET endpoints directly implicate cardiac toxicity. The attribution is primarily mechanistic context.

Attribution Narrative:

The baseline risk for cardiac disorders with Methazolamide is low (0.12), and no probability drivers were identified from the evidence audit. The attribution is primarily mechanistic and contextual. Mechanistically, Methazolamide is a potent inhibitor of carbonic anhydrases (CA1, CA2, CA3), which may perturb bicarbonate and electrolyte homeostasis, potentially affecting cardiac function. However, the confidence in this mechanism chain is low (0.282) due to missing metabolism and toxic species characterization, and no direct cardiac-specific pathway evidence. Physicochemical properties (low SlogP, high TPSA) suggest limited membrane permeability and cardiac accumulation, potentially mitigating toxicity. No significant cardiac adverse event signals were found in FAERS/PersADE, consistent with low baseline risk. Limitations include the absence of GNN attention or SHAP atom-level attributions, no structural alerts or SMARTS patterns, no ADMET endpoints directly linked to cardiac toxicity, and low mechanism chain confidence. Overall, the attribution is contextual and does not increase the probability above baseline.

## 20. Methyl nicotinate

Attribution Explanation:

Cardiac disorders attribution is driven mainly by MW=137.138; HBA=3.0; HBD=0.0. The explanation is constrained to retrieved tool_results and evidence_items.

Attribution Narrative:

The attribution for cardiac disorders is primarily driven by physicochemical properties, specifically molecular weight (137.138), hydrogen bond acceptors (3.0), and hydrogen bond donors (0.0), as derived from admetSAR predictions. These properties provide contextual feature-level support but are not definitive toxicophores. The confidence in each property is moderate (0.46), and the overall probability of toxicity (0.12) remains low with high uncertainty (0.78). No structural alerts (SMARTS matches) or atom-level attribution (GNN, SHAP) were available, limiting mechanistic specificity. The evidence is constrained to general physicochemical descriptors rather than direct target or pathway interactions. The baseline risk is low, and no additional signals from gold-standard data, ADMET, population, or target/pathway mechanisms were identified. Therefore, the attribution relies on broad property patterns, not on a specific toxic mechanism.
