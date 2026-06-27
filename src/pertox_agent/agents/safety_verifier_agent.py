"""Safety Verifier Agent with deterministic rules and lightweight consistency checks."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from pertox_agent.formatting import to_plain_dict
from pertox_agent.schemas import DrugInfo, EvidencePackage, ORGAN_SYSTEMS, PatientInfo, VerificationIssue, VerificationReport


class SafetyVerifierAgent:
    system_prompt = (
        "You are the Safety Verifier Agent. Independently validate report structure, "
        "content consistency, deterministic safety redlines, and confidence "
        "calibration. Do not generate new clinical reasoning."
    )

    def verify(
        self,
        draft_report: Dict[str, Any],
        patient_info: PatientInfo,
        drug_info: DrugInfo,
        evidence_package: EvidencePackage,
    ) -> VerificationReport:
        issues: List[VerificationIssue] = []
        issues.extend(self._layer1_format_checks(draft_report, drug_info))
        issues.extend(self._layer2_content_checks(draft_report, evidence_package))
        issues.extend(self._layer3_safety_redlines(draft_report, patient_info, drug_info, evidence_package))
        issues.extend(self._layer4_confidence_calibration(draft_report))

        if any(issue.severity == "BLOCKER" for issue in issues):
            status = "BLOCKED"
        elif any(issue.severity in {"WARN", "ERROR"} for issue in issues):
            status = "FLAGGED"
        else:
            status = "PASS"

        summary = {
            "PASS": "All safety verifier layers passed.",
            "FLAGGED": "Safety verifier found non-blocking issues that require review.",
            "BLOCKED": "Safety verifier found a deterministic safety redline or critical missing field.",
        }[status]
        return VerificationReport(status=status, issues=issues, summary=summary)

    def _layer1_format_checks(self, draft_report: Dict[str, Any], drug_info: DrugInfo) -> List[VerificationIssue]:
        issues: List[VerificationIssue] = []
        required = ["metadata", "patient_info", "drug_info", "universal_report", "personalized_report"]
        for field in required:
            if field not in draft_report or draft_report[field] is None:
                issues.append(
                    VerificationIssue(
                        layer=1,
                        severity="BLOCKER",
                        code="MISSING_REQUIRED_SECTION",
                        message=f"Draft report is missing {field}.",
                        recommendation="Regenerate the report with all required sections.",
                        related_field=field,
                    )
                )

        universal = draft_report.get("universal_report")
        personalized = draft_report.get("personalized_report")
        if universal is None or personalized is None:
            return issues

        general_toxicity = getattr(universal, "general_toxicity", [])
        personalized_toxicity = getattr(personalized, "personalized_toxicity", [])

        if not getattr(universal, "drug", None):
            issues.append(
                VerificationIssue(
                    layer=1,
                    severity="BLOCKER",
                    code="MISSING_DRUG_OBJECT",
                    message="Universal report is missing the drug object.",
                    recommendation="Return drug.name, drug.smiles, and drug.drugbank_id.",
                    related_field="universal_report.drug",
                )
            )

        if len(general_toxicity) < len(ORGAN_SYSTEMS):
            issues.append(
                VerificationIssue(
                    layer=1,
                    severity="ERROR",
                    code="INCOMPLETE_GENERAL_TOXICITY",
                    message="Universal report does not include all expected SOC rows.",
                    recommendation="Return Stage 1 general_toxicity entries for all eight SOC rows; only liver and heart are modeled, with other rows as placeholders.",
                    related_field="universal_report.general_toxicity",
                )
            )

        if len(personalized_toxicity) < len(ORGAN_SYSTEMS):
            issues.append(
                VerificationIssue(
                    layer=1,
                    severity="ERROR",
                    code="INCOMPLETE_PERSONALIZED_TOXICITY",
                    message="Personalized report does not include all expected SOC rows.",
                    recommendation="Return Stage 2 personalized_toxicity entries for all eight SOC rows; only liver and heart are modeled, with other rows as placeholders.",
                    related_field="personalized_report.personalized_toxicity",
                )
            )

        for idx, item in enumerate(general_toxicity):
            if item.risk_level is not None and item.risk_level not in {"high", "moderate", "low", "unknown"}:
                issues.append(
                    VerificationIssue(
                        layer=1,
                        severity="ERROR",
                        code="INVALID_RISK_LEVEL",
                        message=f"{item.soc} has an invalid Stage 1 risk level.",
                        recommendation="Use high, moderate, low, or unknown.",
                        related_field=f"universal_report.general_toxicity.{idx}.baseline_risk_level",
                    )
                )
            if item.probability is not None and (item.probability < 0 or item.probability > 1):
                issues.append(
                    VerificationIssue(
                        layer=1,
                        severity="ERROR",
                        code="INVALID_PROBABILITY_RANGE",
                        message=f"{item.soc} probability is outside 0-1.",
                        recommendation="Clamp probability into the 0-1 range.",
                        related_field=f"universal_report.general_toxicity.{idx}",
                    )
                )
            if item.uncertainty is not None and (item.uncertainty < 0 or item.uncertainty > 1):
                issues.append(
                    VerificationIssue(
                        layer=1,
                        severity="ERROR",
                        code="INVALID_UNCERTAINTY_RANGE",
                        message=f"{item.soc} uncertainty is outside 0-1.",
                        recommendation="Clamp uncertainty into the 0-1 range.",
                        related_field=f"universal_report.general_toxicity.{idx}.uncertainty",
                    )
                )
            if item.ctcae_grade_predicted is not None and (
                item.ctcae_grade_predicted < 1 or item.ctcae_grade_predicted > 5
            ):
                issues.append(
                    VerificationIssue(
                        layer=1,
                        severity="ERROR",
                        code="INVALID_BASELINE_CTCAE_GRADE",
                        message=f"{item.soc} Stage 1 CTCAE grade is outside 1-5.",
                        recommendation="Map baseline_probability to CTCAE grade 1-5.",
                        related_field=f"universal_report.general_toxicity.{idx}.ctcae_grade_predicted",
                    )
                )

        for idx, item in enumerate(personalized_toxicity):
            grade = item.ctcae_grade_predicted
            if grade is not None and (grade < 1 or grade > 5):
                issues.append(
                    VerificationIssue(
                        layer=1,
                        severity="ERROR",
                        code="INVALID_CTCAE_GRADE",
                        message=f"{item.soc} CTCAE grade is outside 1-5.",
                        recommendation="Map final score to CTCAE v5.0 grade 1-5.",
                        related_field=f"personalized_report.personalized_toxicity.{idx}.ctcae_grade_predicted",
                    )
                )

        if not re.search(r"\b(mg|mcg|g|unit|units|IU)\b", drug_info.dose, flags=re.IGNORECASE):
            issues.append(
                VerificationIssue(
                    layer=1,
                    severity="WARN",
                    code="DOSE_UNIT_UNCLEAR",
                    message=f"Dose '{drug_info.dose}' lacks a recognized unit.",
                    recommendation="Use explicit dose units such as mg/day.",
                    related_field="drug_info.dose",
                )
            )

        return issues

    def _layer2_content_checks(
        self,
        draft_report: Dict[str, Any],
        evidence_package: EvidencePackage,
    ) -> List[VerificationIssue]:
        issues: List[VerificationIssue] = []
        payload = to_plain_dict(draft_report)
        personalized = payload.get("personalized_report", {})
        rec_text = " ".join(
            (item.get("clinical_recommendation") or {}).get("text", "")
            for item in personalized.get("personalized_toxicity", [])
        ).lower()

        for rec in evidence_package.tool_results.get("cpic_lookup", {}).get("recommendations", []):
            if "lower" in rec.get("recommendation", "").lower() and "dose" not in rec_text:
                issues.append(
                    VerificationIssue(
                        layer=2,
                        severity="ERROR",
                        code="CPIC_RECOMMENDATION_NOT_REFLECTED",
                        message=f"CPIC recommendation for {rec['gene']} was not reflected in clinical advice.",
                        recommendation="Add genotype-guided dose adjustment or explain why it is not used.",
                        related_field="personalized_report.personalized_toxicity.clinical_recommendation",
                    )
                )

        for interaction in evidence_package.tool_results.get("ddi_query", {}).get("interactions", []):
            if interaction.get("severity") in {"major", "contraindicated"}:
                med = interaction.get("co_medication", "").lower()
                if med and med not in rec_text:
                    issues.append(
                        VerificationIssue(
                            layer=2,
                            severity="ERROR",
                            code="DDI_NOT_REFLECTED",
                            message=f"Major DDI with {interaction['co_medication']} was not reflected in advice.",
                            recommendation="Add DDI management instructions.",
                            related_field="personalized_report.personalized_toxicity.clinical_recommendation",
                        )
                    )

        for idx, item in enumerate(personalized.get("personalized_toxicity", [])):
            score = item.get("personalized_probability")
            grade = item.get("ctcae_grade_predicted")
            if score is None or grade is None:
                continue
            expected = self._expected_grade(float(score))
            if grade != expected:
                issues.append(
                    VerificationIssue(
                        layer=2,
                        severity="WARN",
                        code="CTCAE_SCORE_MISMATCH",
                        message=f"{item.get('soc')} probability does not match deterministic CTCAE mapping.",
                        recommendation=f"Expected grade {expected} from current deterministic mapper.",
                        related_field=f"personalized_report.personalized_toxicity.{idx}.ctcae_grade_predicted",
                    )
                )

        for idx, item in enumerate(payload.get("universal_report", {}).get("general_toxicity", [])):
            is_placeholder = item.get("baseline_probability") is None
            evidence = item.get("evidence", [])
            if not is_placeholder and not evidence:
                issues.append(
                    VerificationIssue(
                        layer=2,
                        severity="WARN",
                        code="MISSING_EVIDENCE",
                        message=f"{item.get('soc')} has no evidence objects.",
                        recommendation="Attach at least one source/tier/ref evidence object.",
                        related_field=f"universal_report.general_toxicity.{idx}.evidence",
                    )
                )
            for ev in evidence:
                if not ev.get("source") or not ev.get("ref"):
                    issues.append(
                        VerificationIssue(
                            layer=2,
                            severity="WARN",
                            code="INCOMPLETE_EVIDENCE_REF",
                            message="General toxicity evidence is missing source or ref.",
                            recommendation="Attach complete provenance to every evidence object.",
                            related_field=f"universal_report.general_toxicity.{idx}.evidence",
                        )
                    )
                    break

            attribution = item.get("attribution", {})
            if (
                "structural" not in attribution
                or "property" not in attribution
                or "admet_endpoint" not in attribution
                or "target_pathway" not in attribution
                or "mechanism_summary" not in attribution
            ):
                issues.append(
                    VerificationIssue(
                        layer=2,
                        severity="WARN",
                        code="INCOMPLETE_ATTRIBUTION",
                        message="General toxicity attribution must include structural, property, admet_endpoint, target_pathway, and mechanism_summary fields.",
                        recommendation="Return the full attribution object required by the Stage 1 schema.",
                        related_field=f"universal_report.general_toxicity.{idx}.attribution",
                    )
                )

            target_pathway = attribution.get("target_pathway", [])
            if not is_placeholder and not target_pathway:
                issues.append(
                    VerificationIssue(
                        layer=2,
                        severity="WARN",
                        code="MISSING_TARGET_PATHWAY_ATTRIBUTION",
                        message=f"{item.get('soc')} has no target/pathway attribution summary.",
                        recommendation="Attach a target_pathway summary, using unknown placeholders when evidence is missing.",
                        related_field=f"universal_report.general_toxicity.{idx}.attribution.target_pathway",
                    )
                )

            chains = attribution.get("mechanism_chains", [])
            raw_probability = item.get("baseline_probability", item.get("probability", 0))
            probability = float(raw_probability) if raw_probability is not None else 0.0
            for chain_index, chain in enumerate(chains):
                observed = {node.get("node_type") for node in chain.get("nodes", [])}
                required = set(self._required_chain_nodes())
                missing = sorted(required - observed)
                if missing:
                    issues.append(
                        VerificationIssue(
                            layer=2,
                            severity="WARN",
                            code="MECHANISM_CHAIN_NODE_MISSING",
                            message=f"{item.get('soc')} mechanism chain is missing nodes: {', '.join(missing)}.",
                            recommendation="Represent all six causal nodes, using unknown placeholders when evidence is missing.",
                            related_field=(
                                f"universal_report.general_toxicity.{idx}."
                                f"attribution.mechanism_chains.{chain_index}.nodes"
                            ),
                        )
                    )
                if probability >= 0.65 and not chain.get("chain_complete"):
                    issues.append(
                        VerificationIssue(
                            layer=2,
                            severity="WARN",
                            code="HIGH_RISK_INCOMPLETE_MECHANISM_CHAIN",
                            message=f"{item.get('soc')} is high risk but its mechanism chain is incomplete.",
                            recommendation="Review missing mechanism nodes or raise uncertainty.",
                            related_field=(
                                f"universal_report.general_toxicity.{idx}."
                                f"attribution.mechanism_chains.{chain_index}.missing_nodes"
                            ),
                        )
                    )
                if chain.get("soc") and chain.get("soc") != item.get("soc"):
                    issues.append(
                        VerificationIssue(
                            layer=2,
                            severity="ERROR",
                            code="MECHANISM_CHAIN_SOC_MISMATCH",
                            message=f"Mechanism chain SOC {chain.get('soc')} does not match toxicity row {item.get('soc')}.",
                            recommendation="Attach each mechanism chain only to the matching SOC row.",
                            related_field=(
                                f"universal_report.general_toxicity.{idx}."
                                f"attribution.mechanism_chains.{chain_index}.soc"
                            ),
                        )
                    )

        for idx, item in enumerate(personalized.get("personalized_toxicity", [])):
            for modifier_index, modifier in enumerate(item.get("mechanism_chain_modifiers", [])):
                if not modifier.get("affected_node"):
                    issues.append(
                        VerificationIssue(
                            layer=2,
                            severity="WARN",
                            code="CHAIN_MODIFIER_NODE_MISSING",
                            message=f"{item.get('soc')} personalized modifier is not mapped to a mechanism-chain node.",
                            recommendation="Set affected_node to metabolism, active_or_toxic_species, target_binding, pathway_perturbation, or organ_toxicity_phenotype.",
                            related_field=(
                                f"personalized_report.personalized_toxicity.{idx}."
                                f"mechanism_chain_modifiers.{modifier_index}.affected_node"
                            ),
                        )
                    )

        return issues

    def _layer3_safety_redlines(
        self,
        draft_report: Dict[str, Any],
        patient_info: PatientInfo,
        drug_info: DrugInfo,
        evidence_package: EvidencePackage,
    ) -> List[VerificationIssue]:
        issues: List[VerificationIssue] = []
        drug_lower = drug_info.name.lower()

        for interaction in evidence_package.tool_results.get("ddi_query", {}).get("interactions", []):
            if interaction.get("severity") == "contraindicated":
                issues.append(
                    VerificationIssue(
                        layer=3,
                        severity="BLOCKER",
                        code="CONTRAINDICATED_DDI",
                        message=f"Contraindicated drug interaction: {interaction['co_medication']}.",
                        recommendation=interaction.get("management", "Do not combine."),
                        related_field="patient_info.concomitant_medications",
                    )
                )

        hla_text = " ".join(patient_info.hla_types + list(patient_info.genotypes.values())).upper()
        if drug_lower == "abacavir" and "HLA-B*57:01" in hla_text:
            issues.append(
                VerificationIssue(
                    layer=3,
                    severity="BLOCKER",
                    code="ABACAVIR_HLA_B_5701",
                    message="Abacavir is contraindicated for HLA-B*57:01-positive patients.",
                    recommendation="Do not output a permissive abacavir plan; recommend alternative therapy.",
                    related_field="patient_info.hla_types",
                )
            )

        if drug_lower == "warfarin" and patient_info.pregnancy_status == "pregnant":
            issues.append(
                VerificationIssue(
                    layer=3,
                    severity="BLOCKER",
                    code="WARFARIN_PREGNANCY_REDLINE",
                    message="Warfarin has major fetal toxicity concerns during pregnancy.",
                    recommendation="Escalate to specialist review and consider pregnancy-appropriate alternatives.",
                    related_field="patient_info.pregnancy_status",
                )
            )

        payload = to_plain_dict(draft_report)
        rec_text = " ".join(
            (item.get("clinical_recommendation") or {}).get("text", "")
            for item in payload.get("personalized_report", {}).get("personalized_toxicity", [])
        ).lower()
        black_box = evidence_package.tool_results.get("drug_card_lookup", {}).get("black_box_warning")
        if black_box and drug_lower == "warfarin" and "inr" not in rec_text:
            issues.append(
                VerificationIssue(
                    layer=3,
                    severity="WARN",
                    code="BLACK_BOX_MONITORING_MISSING",
                    message="Warfarin black box bleeding warning is not paired with INR monitoring.",
                    recommendation="Add INR and bleeding monitoring instructions.",
                    related_field="personalized_report.personalized_toxicity.clinical_recommendation",
                )
            )

        if patient_info.age < 18 and drug_info.dose:
            issues.append(
                VerificationIssue(
                    layer=3,
                    severity="WARN",
                    code="PEDIATRIC_DOSE_REVIEW",
                    message="Pediatric patient detected; deterministic dose range table is not implemented.",
                    recommendation="Require pediatric dosing verification before use.",
                    related_field="patient_info.age",
                )
            )

        return issues

    def _layer4_confidence_calibration(self, draft_report: Dict[str, Any]) -> List[VerificationIssue]:
        issues: List[VerificationIssue] = []
        payload = to_plain_dict(draft_report)
        baseline_uncertainty = {
            item.get("soc"): float(item.get("uncertainty"))
            for item in payload.get("universal_report", {}).get("general_toxicity", [])
            if item.get("uncertainty") is not None
        }
        for idx, item in enumerate(payload.get("personalized_report", {}).get("personalized_toxicity", [])):
            soc = item.get("soc")
            raw_score = item.get("personalized_probability")
            if raw_score is None:
                continue
            score = float(raw_score)
            uncertainty = baseline_uncertainty.get(soc, 1.0)
            if score >= 0.65 and uncertainty > 0.45:
                issues.append(
                    VerificationIssue(
                        layer=4,
                        severity="ERROR",
                        code="HIGH_RISK_LOW_CONFIDENCE",
                        message=f"{soc} risk is high but baseline uncertainty is high.",
                        recommendation="Force manual review before final output.",
                        related_field=f"personalized_report.personalized_toxicity.{idx}.personalized_probability",
                    )
                )
            elif score >= 0.80:
                issues.append(
                    VerificationIssue(
                        layer=4,
                        severity="INFO",
                        code="HIGH_RISK_REVIEW_RECOMMENDED",
                        message=f"{soc} risk is high and should be clinically reviewed.",
                        recommendation="Ensure urgent monitoring or specialist review is visible in recommendations.",
                        related_field=f"personalized_report.personalized_toxicity.{idx}.personalized_probability",
                    )
                )
        return issues

    def _expected_grade(self, score: float) -> int:
        if score < 0.20:
            return 1
        if score < 0.40:
            return 2
        if score < 0.65:
            return 3
        return 4

    def _required_chain_nodes(self) -> List[str]:
        return [
            "drug_original",
            "metabolism",
            "active_or_toxic_species",
            "target_binding",
            "pathway_perturbation",
            "organ_toxicity_phenotype",
        ]

