"""Knowledge Retrieval Agent.

This agent owns query rewriting, tool orchestration, evidence packaging,
conflict marking, and session-level drug cache. It does not make clinical
decisions.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from pertox_agent.settings import get_model_config
from pertox_agent.schemas import DrugInfo, EvidenceCitation, EvidenceItem, EvidencePackage, PatientInfo
from pertox_agent.formatting import to_plain_dict
from pertox_agent.tools.runtime import retrieval_runtime as kb


class KnowledgeRetrievalAgent:
    system_prompt = (
        "You are the Knowledge Retrieval Agent. Rewrite structured clinical "
        "queries into deterministic tool calls, call external knowledge tools, "
        "and return structured evidence packages with provenance. Do not make "
        "patient-specific clinical decisions."
    )

    def __init__(self) -> None:
        self.config = get_model_config()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.tool_catalog: Dict[str, Callable[..., Dict[str, Any]]] = {
            "drug_card_lookup": kb.drug_card_lookup,
            "admetsar_predict": kb.admetsar_predict,
            "dti_query": kb.dti_query,
            "mechanism_query": kb.mechanism_query,
            "mechanism_chains_lookup": kb.mechanism_chains_lookup,
            "pathway_enrich": kb.pathway_enrich,
            "persade_drug_profile": kb.persade_drug_profile,
            "cpic_lookup": kb.cpic_lookup,
            "ddi_query": kb.ddi_query,
            "hla_peptide_score": kb.hla_peptide_score,
            "drugbank_metabolism_query": kb.drugbank_metabolism_query,
            "persade_contextual_retrieval": kb.persade_contextual_retrieval,
            "persade_subgroup_scores": kb.persade_subgroup_scores,
            "similar_case_retrieval": kb.similar_case_retrieval,
            "cohort_outcomes_query": kb.cohort_outcomes_query,
        }

    def retrieve(
        self,
        query: Dict[str, Any],
        patient_info: PatientInfo,
        drug_info: DrugInfo,
    ) -> EvidencePackage:
        purpose = query.get("purpose", "comprehensive")
        patient_context = to_plain_dict(patient_info)
        drug_key = (drug_info.drugbank_id or drug_info.name).lower()

        tool_results: Dict[str, Any] = {}
        tool_plan = self._plan_tools(purpose)
        for tool_name in tool_plan:
            tool_results[tool_name] = self._call_tool(
                tool_name=tool_name,
                drug_info=drug_info,
                patient_info=patient_info,
                patient_context=patient_context,
                drug_key=drug_key,
                query=query,
            )

        evidence_items = self._build_evidence_items(tool_results)
        conflicts = self._detect_conflicts(tool_results, patient_info, drug_info)
        attribution_chain = self._build_attribution_chain(tool_results)
        drug_card = tool_results.get("drug_card_lookup") or self._call_tool(
            "drug_card_lookup", drug_info, patient_info, patient_context, drug_key
        )

        return EvidencePackage(
            query_id=str(uuid4()),
            query_purpose=purpose,
            drug_id=drug_card.get("drug_id") or drug_info.drugbank_id or "UNKNOWN",
            patient_id=patient_info.patient_id,
            tool_results=tool_results,
            evidence_items=evidence_items,
            conflicts=conflicts,
            attribution_chain=attribution_chain,
        )

    def _plan_tools(self, purpose: str) -> List[str]:
        common = ["drug_card_lookup", "drugbank_metabolism_query"]
        universal = [
            "admetsar_predict",
            "dti_query",
            "mechanism_query",
            "pathway_enrich",
            "persade_drug_profile",
            "mechanism_chains_lookup",
        ]
        personalized = [
            "cpic_lookup",
            "ddi_query",
            "hla_peptide_score",
            "persade_contextual_retrieval",
            "persade_subgroup_scores",
            "similar_case_retrieval",
            "cohort_outcomes_query",
        ]
        if purpose == "universal_toxicity":
            return common + universal
        if purpose == "personalized_modifiers":
            return common + personalized
        return common + universal + personalized

    def _call_tool(
        self,
        tool_name: str,
        drug_info: DrugInfo,
        patient_info: PatientInfo,
        patient_context: Dict[str, Any],
        drug_key: str,
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cacheable = {
            "drug_card_lookup",
            "admetsar_predict",
            "dti_query",
            "mechanism_query",
            "mechanism_chains_lookup",
            "pathway_enrich",
            "persade_drug_profile",
            "drugbank_metabolism_query",
        }
        cache_key = f"{drug_key}:{tool_name}"
        if tool_name in cacheable and cache_key in self._cache:
            return self._cache[cache_key]

        try:
            result = self._dispatch_tool(
                tool_name=tool_name,
                drug_info=drug_info,
                patient_info=patient_info,
                patient_context=patient_context,
                query=query or {},
            )
            if not isinstance(result, dict):
                raise TypeError(f"{tool_name} returned {type(result).__name__}, expected dict")
            result.setdefault("tool", tool_name)
            result.setdefault("ok", True)
        except Exception as exc:  # noqa: BLE001 - tool failures become evidence gaps.
            result = self._tool_error_result(tool_name, exc)

        if tool_name in cacheable and result.get("ok", True):
            self._cache[cache_key] = result
        return result

    def _dispatch_tool(
        self,
        *,
        tool_name: str,
        drug_info: DrugInfo,
        patient_info: PatientInfo,
        patient_context: Dict[str, Any],
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query = query or {}
        common_kwargs = {
            "drug_name": drug_info.name,
            "drugbank_id": drug_info.drugbank_id,
            "smiles": drug_info.smiles,
            "inchi_key": drug_info.inchi_key,
        }

        if tool_name == "drug_card_lookup":
            result = kb.drug_card_lookup(**common_kwargs)
        elif tool_name == "admetsar_predict":
            result = kb.admetsar_predict(**common_kwargs)
        elif tool_name == "dti_query":
            result = kb.dti_query(**common_kwargs)
        elif tool_name == "mechanism_query":
            result = kb.mechanism_query(**common_kwargs)
        elif tool_name == "mechanism_chains_lookup":
            result = kb.mechanism_chains_lookup(**common_kwargs)
        elif tool_name == "pathway_enrich":
            result = kb.pathway_enrich(**common_kwargs)
        elif tool_name == "persade_drug_profile":
            result = kb.persade_drug_profile(**common_kwargs)
        elif tool_name == "cpic_lookup":
            result = kb.cpic_lookup(genotypes=patient_info.genotypes, **common_kwargs)
        elif tool_name == "ddi_query":
            result = kb.ddi_query(
                concomitant_medications=patient_info.concomitant_medications,
                **common_kwargs,
            )
        elif tool_name == "hla_peptide_score":
            hla_values = list(patient_info.hla_types)
            hla_values.extend(patient_info.genotypes.values())
            result = kb.hla_peptide_score(hla_types=hla_values, **common_kwargs)
        elif tool_name == "drugbank_metabolism_query":
            result = kb.drugbank_metabolism_query(**common_kwargs)
        elif tool_name == "persade_contextual_retrieval":
            result = kb.persade_contextual_retrieval(
                patient_context=patient_context,
                **common_kwargs,
            )
        elif tool_name == "persade_subgroup_scores":
            result = kb.persade_subgroup_scores(
                candidate_ades=query.get("candidate_ades") or [],
                subgroups=query.get("subgroups") or {},
                **common_kwargs,
            )
        elif tool_name == "similar_case_retrieval":
            result = kb.similar_case_retrieval(patient_context=patient_context, **common_kwargs)
        elif tool_name == "cohort_outcomes_query":
            result = kb.cohort_outcomes_query(patient_context=patient_context, **common_kwargs)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

        return result

    def _tool_error_result(self, tool_name: str, exc: Exception) -> Dict[str, Any]:
        error_type = type(exc).__name__
        message = str(exc) or error_type
        return {
            "tool": tool_name,
            "ok": False,
            "matched": False,
            "error": message,
            "error_type": error_type,
            "recoverable": True,
        }

    def _build_evidence_items(self, tool_results: Dict[str, Any]) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []

        def citation(result: Dict[str, Any]) -> EvidenceCitation:
            allowed_levels = {"P1", "P2", "P3", "P4", "P5", "DrugCard", "ADMET"}
            payload = dict(result.get("citation") or {})
            payload.setdefault("source", result.get("tool") or result.get("source") or "unknown tool")
            payload.setdefault("version", "PersAgent-local")
            payload.setdefault("year", 2026)
            level = payload.get("evidence_level") or result.get("evidence_level") or "P5"
            payload["evidence_level"] = level if level in allowed_levels else "P5"
            payload.setdefault(
                "summary",
                result.get("error") or "Tool result returned without explicit citation metadata.",
            )
            return EvidenceCitation(**payload)

        for tool_name, result in tool_results.items():
            if result.get("ok", True):
                continue
            items.append(
                EvidenceItem(
                    tool_name=tool_name,
                    evidence_level="P5",
                    finding=(
                        f"{tool_name} failed with {result.get('error_type', 'Exception')}: "
                        f"{result.get('error', 'unknown error')}"
                    ),
                    strength="low",
                    payload=result,
                    citations=[citation(result)],
                )
            )

        if "drug_card_lookup" in tool_results and tool_results["drug_card_lookup"].get("ok", True):
            result = tool_results["drug_card_lookup"]
            items.append(
                EvidenceItem(
                    tool_name="drug_card_lookup",
                    evidence_level="DrugCard",
                    finding=result.get("mechanism_chain", "Drug card retrieved."),
                    strength="high",
                    payload=result,
                    citations=[citation(result)],
                )
            )

        if "admetsar_predict" in tool_results and tool_results["admetsar_predict"].get("ok", True):
            result = tool_results["admetsar_predict"]
            admet_profile = result.get("admet_profile", [])
            known_count = sum(1 for item in admet_profile if item.get("value") != "unknown")
            unknown_count = len(admet_profile) - known_count
            items.append(
                EvidenceItem(
                    tool_name="admetsar_predict",
                    evidence_level="ADMET",
                    finding=(
                        f"ADMET endpoint profile contains {known_count} observed endpoints "
                        f"and {unknown_count} unknown endpoints."
                    ),
                    strength="moderate" if known_count else "low",
                    payload=result,
                    citations=[citation(result)],
                )
            )

        if "persade_drug_profile" in tool_results and tool_results["persade_drug_profile"].get("ok", True):
            result = tool_results["persade_drug_profile"]
            for signal in result.get("signals", []):
                items.append(
                    EvidenceItem(
                        tool_name="persade_drug_profile",
                        evidence_level="P1",
                        finding=f"PersADE/FAERS signal: {signal['event']} (ROR {signal['ror']}).",
                        strength=signal.get("strength", "moderate"),
                        payload=signal,
                        citations=[citation(result)],
                    )
                )

        if "mechanism_chains_lookup" in tool_results and tool_results["mechanism_chains_lookup"].get("ok", True):
            result = tool_results["mechanism_chains_lookup"]
            counts = result.get("evidence_counts", {})
            items.append(
                EvidenceItem(
                    tool_name="mechanism_chains_lookup",
                    evidence_level="DrugCard",
                    finding=(
                        f"Mechanism-chain lookup returned {result.get('mechanism_chain_count', 0)} chains "
                        f"for {result.get('candidate_ade_count', 0)} candidate ADEs "
                        f"(direct={counts.get('direct_DTA', 0)}, "
                        f"indirect={counts.get('indirect_DTI_AT', 0)}, "
                        f"pathway={counts.get('pathway_inferred', 0)})."
                    ),
                    strength="high" if counts.get("direct_DTA", 0) else "moderate" if result.get("matched") else "low",
                    payload=result,
                    citations=[citation(result)],
                )
            )

        if "cpic_lookup" in tool_results and tool_results["cpic_lookup"].get("ok", True):
            result = tool_results["cpic_lookup"]
            for rec in result.get("recommendations", []):
                items.append(
                    EvidenceItem(
                        tool_name="cpic_lookup",
                        evidence_level="P3",
                        finding=f"{rec['gene']} {rec['genotype']}: {rec['recommendation']}",
                        strength="high",
                        payload=rec,
                        citations=[citation(result)],
                    )
                )

        if "ddi_query" in tool_results and tool_results["ddi_query"].get("ok", True):
            result = tool_results["ddi_query"]
            for interaction in result.get("interactions", []):
                severity = interaction.get("severity")
                items.append(
                    EvidenceItem(
                        tool_name="ddi_query",
                        evidence_level="P4",
                        finding=(
                            f"{severity} DDI with {interaction['co_medication']}: "
                            f"{interaction['clinical_effect']}"
                        ),
                        strength="high" if severity in {"major", "contraindicated"} else "moderate",
                        payload=interaction,
                        citations=[citation(result)],
                    )
                )

        if "persade_contextual_retrieval" in tool_results and tool_results["persade_contextual_retrieval"].get("ok", True):
            result = tool_results["persade_contextual_retrieval"]
            for snippet in result.get("snippets", []):
                items.append(
                    EvidenceItem(
                        tool_name="persade_contextual_retrieval",
                        evidence_level=snippet.get("evidence_level", "P2"),
                        finding=snippet["finding"],
                        strength="moderate",
                        payload=snippet,
                        citations=[citation(result)],
                    )
                )

        if "persade_subgroup_scores" in tool_results and tool_results["persade_subgroup_scores"].get("ok", True):
            result = tool_results["persade_subgroup_scores"]
            for row in result.get("persade_contextual_evidence", [])[:40]:
                shift = row.get("contextual_risk_shift", 0.0)
                items.append(
                    EvidenceItem(
                        tool_name="persade_subgroup_scores",
                        evidence_level="P2",
                        finding=(
                            f"Subgroup ADE risk for {row.get('ade_id')} "
                            f"(SOC {row.get('soc') or 'n/a'}): contextual_risk_shift={shift:+.3f}, "
                            f"uncertainty={row.get('uncertainty')}."
                        ),
                        strength="moderate" if abs(shift) >= 0.5 and row.get("uncertainty", 1.0) <= 0.3 else "low",
                        payload=row,
                        citations=[citation(result)],
                    )
                )

        if "similar_case_retrieval" in tool_results and tool_results["similar_case_retrieval"].get("ok", True):
            result = tool_results["similar_case_retrieval"]
            for case in result.get("cases", []):
                items.append(
                    EvidenceItem(
                        tool_name="similar_case_retrieval",
                        evidence_level="P2",
                        finding=f"Similar case {case['case_id']}: {case['outcome']}",
                        strength="moderate",
                        payload=case,
                        citations=[citation(result)],
                    )
                )

        if "cohort_outcomes_query" in tool_results and tool_results["cohort_outcomes_query"].get("ok", True):
            result = tool_results["cohort_outcomes_query"]
            for finding in result.get("findings", []):
                items.append(
                    EvidenceItem(
                        tool_name="cohort_outcomes_query",
                        evidence_level="P5",
                        finding=f"Cohort finding: {finding['finding']}",
                        strength="moderate",
                        payload=finding,
                        citations=[citation(result)],
                    )
                )

        return items

    def _detect_conflicts(
        self,
        tool_results: Dict[str, Any],
        patient_info: PatientInfo,
        drug_info: DrugInfo,
    ) -> List[str]:
        conflicts: List[str] = []
        for tool_name, result in tool_results.items():
            if not result.get("ok", True):
                conflicts.append(
                    f"{tool_name} failed with {result.get('error_type', 'Exception')}: "
                    f"{result.get('error', 'unknown error')}"
                )

        drug_card = tool_results.get("drug_card_lookup", {})
        if drug_card.get("drug_id") == "UNKNOWN":
            conflicts.append("Drug could not be standardized to a known DrugBank-like ID.")

        if drug_info.smiles is None and not drug_info.target_description:
            conflicts.append("No SMILES or biologic target description was provided.")

        cpic = tool_results.get("cpic_lookup", {})
        if patient_info.genotypes and not cpic.get("recommendations") and "cpic_lookup" in tool_results:
            conflicts.append("Genotype data were supplied, but no actionable local CPIC rule matched or was configured.")

        ddi = tool_results.get("ddi_query", {})
        for interaction in ddi.get("interactions", []):
            if interaction.get("severity") == "contraindicated":
                conflicts.append(f"Contraindicated DDI detected with {interaction['co_medication']}.")

        return conflicts

    def _build_attribution_chain(self, tool_results: Dict[str, Any]) -> List[str]:
        chain: List[str] = []
        mechanism = tool_results.get("mechanism_query") or tool_results.get("drug_card_lookup")
        if mechanism and mechanism.get("mechanism_chain"):
            chain.append(mechanism["mechanism_chain"])

        metabolism = tool_results.get("drugbank_metabolism_query", {}).get("metabolism", {})
        enzymes = metabolism.get("primary_enzymes") or []
        if enzymes:
            chain.append(f"Primary metabolism enzymes: {', '.join(enzymes)}")

        pathways = tool_results.get("pathway_enrich", {}).get("pathways") or []
        if pathways:
            chain.append(f"Pathways: {', '.join(pathways)}")

        mechanism_chains = tool_results.get("mechanism_chains_lookup", {}).get("mechanism_chains") or []
        if mechanism_chains:
            chain.append(f"Mechanism chains: {len(mechanism_chains)} Drug-Target-Pathway-ADE chains")

        return chain

