"""Toxicity Orchestrator Agent: supervisor, two-stage reasoning, and report synthesis."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from pertox_agent.settings import get_model_config
from pertox_agent.tools.clinical_input.normalizer import ClinicalInputNormalizer
from pertox_agent.tools.clinical_input.semantic_extractor import LLMJsonExtractor
from pertox_agent.formatting import to_plain_dict
from pertox_agent.schemas import (
    BaselineRisk,
    ClinicalRecommendationOutput,
    DrugInfo,
    DrugOutput,
    EvidenceCitation,
    EvidencePackage,
    GeneralToxicityEvidence,
    GeneralToxicityItem,
    MechanismChain,
    MechanismChainModifier,
    MechanismEvidence,
    ORGAN_SYSTEMS,
    PatientAttribution,
    PatientFactorEvidence,
    PatientFeatures,
    PatientInfo,
    PersonalizedToxicityItem,
    PersonalizedToxicityReport,
    PropertyAttribution,
    StructuralAlert,
    ToxicityAttribution,
    UniversalToxicityReport,
    VerificationReport,
)
from pertox_agent.tools.toxicity_attribution.toxicity_chain_builder import ToxicityChainBuilder


SOC_BY_ORGAN = {
    "liver": "Hepatobiliary disorders",
    "heart": "Cardiac disorders",
    "kidney": "Renal and urinary disorders",
    "hematologic": "Blood and lymphatic system disorders",
    "immune": "Immune system disorders",
    "skin": "Skin and subcutaneous tissue disorders",
    "neurologic": "Nervous system disorders",
    "gastrointestinal": "Gastrointestinal disorders",
}

ORGAN_BY_SOC = {soc: organ for organ, soc in SOC_BY_ORGAN.items()}
# Only liver and heart are actively modeled; all other SOC rows remain placeholders.
MODELED_ORGANS = {"liver", "heart"}

EVIDENCE_TIER_BY_LEVEL = {
    "P1": 1,
    "P2": 2,
    "DrugCard": 2,
    "ADMET": 3,
    "P3": 1,
    "P4": 2,
    "P5": 5,
    "clinical_rule": 4,
}

BASELINE_ORGAN_PRIOR = 0.12

_EMPTY_LLM_VALUES = {"", "unknown", "unspecified", "not provided", "n/a", "na", "none", "null"}
_MOLECULAR_DRIVER_TYPES = {
    "structural_alert",
    "physchem_property",
    "admet_endpoint",
    "metabolism_reactivity",
    "target_pathway",
    "population_signal",
    "evidence_summary",
}


class ToxicityOrchestratorAgent:
    system_prompt = (
        "You are the Toxicity Orchestrator Agent. Parse inputs, decide what knowledge is needed, "
        "perform two-stage toxicity reasoning, synthesize reports, and revise "
        "drafts using safety verifier feedback. All external knowledge must come from "
        "the Knowledge Retrieval Agent."
    )

    def __init__(self, llm_json_extractor: Optional[LLMJsonExtractor] = None) -> None:
        self.config = get_model_config()
        self.toxicity_chain_builder = ToxicityChainBuilder()
        self._llm_json_extractor = llm_json_extractor
        self.input_normalizer = ClinicalInputNormalizer(
            config=self.config,
            llm_json_extractor=llm_json_extractor,
        )

    def parse_patient_info(self, raw: Any) -> PatientInfo:
        return self.input_normalizer.normalize_patient(raw)

    def parse_drug_info(self, raw: Any) -> DrugInfo:
        return self.input_normalizer.normalize_drug(raw)

    # JSON parsing helpers for live LLM outputs.
    @staticmethod
    def _json_object_from_text(text: str) -> Optional[Dict[str, Any]]:
        stripped = text.strip()
        fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            stripped = fence_match.group(1).strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for idx, char in enumerate(stripped):
                if char != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(stripped[idx:])
                except json.JSONDecodeError:
                    continue
                break
            else:
                return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _coerce_llm_payload(payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, Mapping):
            return None
        return dict(payload)

    @staticmethod
    def _string_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            value = next((item for item in value if item not in (None, "")), None)
            if value is None:
                return None
        if isinstance(value, Mapping):
            return None
        text = str(value).strip().strip('"').strip("'")
        if not text or text.lower() in _EMPTY_LLM_VALUES:
            return None
        return re.sub(r"\s+", " ", text)

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _string_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = re.split(r"[,;|]", value)
        elif isinstance(value, Mapping):
            return []
        else:
            parts = list(value) if isinstance(value, (list, tuple, set)) else [value]
        return [str(item).strip() for item in parts if str(item).strip()]

    def merge_evidence_packages(
        self,
        existing: Optional[EvidencePackage],
        new_package: EvidencePackage,
    ) -> EvidencePackage:
        """Orchestrator-owned accumulation of evidence across serial retrieval stages."""
        if existing is None:
            return new_package

        tool_results = dict(existing.tool_results)
        tool_results.update(new_package.tool_results)

        seen_conflicts = set(existing.conflicts)
        conflicts = list(existing.conflicts)
        for conflict in new_package.conflicts:
            if conflict not in seen_conflicts:
                conflicts.append(conflict)
                seen_conflicts.add(conflict)

        return EvidencePackage(
            query_id=f"{existing.query_id}+{new_package.query_id}",
            query_purpose="merged",
            drug_id=new_package.drug_id or existing.drug_id,
            patient_id=new_package.patient_id or existing.patient_id,
            tool_results=tool_results,
            evidence_items=existing.evidence_items + new_package.evidence_items,
            conflicts=conflicts,
            attribution_chain=existing.attribution_chain + [
                item for item in new_package.attribution_chain if item not in existing.attribution_chain
            ],
        )


    
    def build_universal_report(
        self,
        patient_info: PatientInfo,
        drug_info: DrugInfo,
        evidence_package: EvidencePackage,
    ) -> UniversalToxicityReport:
        tool_results = evidence_package.tool_results
        drug_card = tool_results.get("drug_card_lookup", {})
        persade = tool_results.get("persade_drug_profile", {})
        mechanism = tool_results.get("mechanism_query", {})
        admet = tool_results.get("admetsar_predict", {})
        admet_profile = admet.get("admet_profile", [])

        drug = self._drug_output(drug_info, drug_card)
        baseline_scores = persade.get("baseline_organ_scores", {})
        attributions = persade.get("organ_attributions", {})
        baseline_organ_risk = self._build_baseline_organ_risk(tool_results)
        baseline_by_soc = {item["soc"]: item for item in baseline_organ_risk}
        admet_impacts = self._impact_map(admet.get("organ_impacts", []))
        population_signal_impacts = self._population_signal_map(persade)
        raw_mechanism_chains = tool_results.get("mechanism_chains_lookup", {}).get("mechanism_chains", [])
        mechanism_chain = mechanism.get("mechanism_chain") or drug_card.get("mechanism_chain") or (
            drug_info.target_description or "Mechanism unavailable."
        )

        toxicity_rows: List[Any] = []
        for organ in ORGAN_SYSTEMS:
            soc = SOC_BY_ORGAN[organ]
            if organ not in MODELED_ORGANS:
                toxicity_rows.append(self._general_toxicity_placeholder(soc))
                continue

            mechanism_chains = self.toxicity_chain_builder.build_for_organ(
                drug_info=drug_info,
                tool_results=tool_results,
                organ=organ,
                soc=soc,
            )
            fused_baseline = baseline_by_soc.get(soc, {})
            probability = self._clamp(
                float(fused_baseline.get("probability", baseline_scores.get(organ, BASELINE_ORGAN_PRIOR)))
            )

            confidence = self._clamp(1.0 - float(fused_baseline.get("uncertainty", 0.60)))

            organ_attribution = attributions.get(
                organ,
                self._default_organ_attribution(organ, drug_info, drug_card),
            )
            local_summary = self._local_mechanism_summary(raw_mechanism_chains, organ)
            if local_summary:
                mechanism_text = f"{local_summary} Drug-level mechanism context: {mechanism_chain}; {organ_attribution}"
            else:
                mechanism_text = (
                    f"{self.toxicity_chain_builder.summarize(mechanism_chains)} "
                    f"Drug-level mechanism context: {mechanism_chain}; {organ_attribution}"
                )
            if organ in admet_impacts:
                mechanism_text += " ADMET endpoints support one or more mechanism-chain nodes."
            if organ in population_signal_impacts:
                mechanism_text += " PersADE/FAERS population signals support the terminal organ-toxicity phenotype."
            if fused_baseline.get("main_drivers"):
                mechanism_text += " Baseline fusion drivers: " + "; ".join(fused_baseline["main_drivers"][:4]) + "."

            structural_attribution = self._structural_alerts_for_organ(tool_results, organ)
            property_attribution = self._property_attributions_for_organ(admet, organ)
            admet_endpoint_attribution = self._admet_endpoints_for_organ(admet_profile, organ)
            target_pathway_attribution = self._target_pathway_for_organ(
                mechanism_chains,
                raw_mechanism_chains,
                organ,
            )
            organ_evidence = self._evidence_for_organ(evidence_package, organ)
            molecular_context = self._build_molecular_attribution_context(
                drug_info=drug_info,
                drug=drug,
                organ=organ,
                soc=soc,
                fused_baseline=fused_baseline,
                tool_results=tool_results,
                evidence_package=evidence_package,
                structural=structural_attribution,
                property_attribution=property_attribution,
                admet_endpoint=admet_endpoint_attribution,
                target_pathway=target_pathway_attribution,
                mechanism_chains=mechanism_chains,
                mechanism_summary=mechanism_text,
            )

            toxicity_rows.append(
                {
                    "soc": soc,
                    "probability": probability,
                    "confidence": confidence,
                    "structural_attribution": structural_attribution,
                    "property_attribution": property_attribution,
                    "admet_endpoint_attribution": admet_endpoint_attribution,
                    "target_pathway_attribution": target_pathway_attribution,
                    "mechanism_text": mechanism_text,
                    "mechanism_chains": mechanism_chains,
                    "organ_evidence": organ_evidence,
                    "molecular_context": molecular_context,
                }
            )

        modeled_rows = [row for row in toxicity_rows if isinstance(row, dict)]
        molecular_outputs = self._resolve_molecular_attributions(
            [row["molecular_context"] for row in modeled_rows]
        )
        for row, molecular_output in zip(modeled_rows, molecular_outputs):
            row["molecular_attribution"] = molecular_output

        toxicity_items: List[GeneralToxicityItem] = []
        for row in toxicity_rows:
            if isinstance(row, GeneralToxicityItem):
                toxicity_items.append(row)
                continue

            molecular_attribution = row["molecular_attribution"]
            toxicity_items.append(
                GeneralToxicityItem(
                    soc=row["soc"],
                    baseline_risk_level=self._risk_level(row["probability"]),
                    baseline_probability=row["probability"],
                    uncertainty=self._clamp(1.0 - row["confidence"]),
                    ctcae_grade_predicted=self._risk_to_ctcae_grade(row["probability"]),
                    attribution=ToxicityAttribution(
                        structural=row["structural_attribution"],
                        property=row["property_attribution"],
                        admet_endpoint=row["admet_endpoint_attribution"],
                        target_pathway=row["target_pathway_attribution"],
                        mechanism_summary=row["mechanism_text"],
                        attribution_explanation=molecular_attribution.get("attribution_explanation"),
                        attribution_narrative=molecular_attribution.get("attribution_narrative"),
                        attribution_generation_method=molecular_attribution.get(
                            "attribution_generation_method",
                            "deterministic_fallback",
                        ),
                        molecular_attribution=molecular_attribution.get("molecular_attribution", []),
                        attribution_limitations=molecular_attribution.get("attribution_limitations", []),
                        mechanism_chains=row["mechanism_chains"],
                    ),
                    evidence=row["organ_evidence"],
                )
            )

        return UniversalToxicityReport(drug=drug, general_toxicity=toxicity_items)

    def _general_toxicity_placeholder(self, soc: str) -> GeneralToxicityItem:
        return GeneralToxicityItem(
            soc=soc,
            baseline_risk_level=None,
            baseline_probability=None,
            uncertainty=None,
            ctcae_grade_predicted=None,
            attribution=ToxicityAttribution(
                structural=[],
                property=[],
                admet_endpoint=[],
                target_pathway=[],
                mechanism_summary=None,
                mechanism_chains=[],
            ),
            evidence=[],
        )

    def _build_molecular_attribution_context(
        self,
        *,
        drug_info: DrugInfo,
        drug: DrugOutput,
        organ: str,
        soc: str,
        fused_baseline: Dict[str, Any],
        tool_results: Dict[str, Any],
        evidence_package: EvidencePackage,
        structural: List[StructuralAlert],
        property_attribution: List[PropertyAttribution],
        admet_endpoint: List[Dict[str, Any]],
        target_pathway: List[Dict[str, Any]],
        mechanism_chains: List[MechanismChain],
        mechanism_summary: str,
    ) -> Dict[str, Any]:
        evidence_items = []
        all_evidence_items = []
        for item in evidence_package.evidence_items:
            compact_item = {
                "tool_name": item.tool_name,
                "evidence_level": item.evidence_level,
                "strength": item.strength,
                "finding": item.finding,
                "payload": self._compact_for_llm(item.payload),
                "citations": [
                    {
                        "source": citation.source,
                        "evidence_level": citation.evidence_level,
                        "summary": citation.summary,
                    }
                    for citation in item.citations[:3]
                ],
            }
            if len(all_evidence_items) < 40:
                all_evidence_items.append(compact_item)
            if not self._evidence_item_matches_organ(item.payload, item.finding, item.tool_name, organ):
                continue
            evidence_items.append(compact_item)
            if len(evidence_items) >= 12:
                break

        persade = tool_results.get("persade_drug_profile", {})
        population_signals = [
            self._compact_for_llm(signal)
            for signal in persade.get("signals", [])
            if signal.get("organ_system") == organ
        ][:8]

        chain_summaries = []
        for chain in mechanism_chains[:6]:
            plain_chain = to_plain_dict(chain)
            chain_summaries.append(
                {
                    "chain_id": plain_chain.get("chain_id"),
                    "summary": plain_chain.get("summary"),
                    "chain_score": plain_chain.get("chain_score"),
                    "chain_confidence": plain_chain.get("chain_confidence"),
                    "nodes": self._compact_for_llm(plain_chain.get("nodes", []), max_items=6),
                }
            )

        admet_result = tool_results.get("admetsar_predict", {})
        metabolism = tool_results.get("drugbank_metabolism_query", {}).get("metabolism", {})
        return {
            "task": "molecular_attribution_for_universal_toxicity",
            "allowed_tool_names": sorted(tool_results.keys()),
            "drug": {
                "name": drug.name or drug_info.name,
                "smiles": drug.smiles or drug_info.smiles,
                "drugbank_id": drug.drugbank_id or drug_info.drugbank_id,
            },
            "organ_system": organ,
            "soc": soc,
            "baseline_risk": {
                "risk_level": fused_baseline.get("risk_level"),
                "probability": fused_baseline.get("probability"),
                "uncertainty": fused_baseline.get("uncertainty"),
                "main_drivers": fused_baseline.get("main_drivers", []),
                "evidence_summary": fused_baseline.get("evidence_summary", []),
            },
            "probability_audit": {
                "calculation": (
                    "baseline_probability = prior + capped support from gold_standard, ADMET, "
                    "ADE population signal, and target/pathway mechanism evidence."
                ),
                "main_drivers": fused_baseline.get("main_drivers", []),
                "evidence_summary": fused_baseline.get("evidence_summary", []),
                "priority_instruction": (
                    "Drivers listed here explain why probability increased; structural and "
                    "physchem evidence should be marked as context unless it directly appears "
                    "in this audit."
                ),
            },
            "evidence_package": {
                "query_id": evidence_package.query_id,
                "query_purpose": evidence_package.query_purpose,
                "drug_id": evidence_package.drug_id,
                "patient_id": evidence_package.patient_id,
                "conflicts": evidence_package.conflicts,
                "attribution_chain": evidence_package.attribution_chain,
            },
            "tool_results": self._compact_for_llm(tool_results, max_items=40, max_string=600),
            "organ_tool_result_focus": {
                "admetsar_predict": {
                    "admet_profile": self._compact_for_llm(admet_endpoint),
                    "property_attribution": self._compact_for_llm(property_attribution),
                    "organ_impacts": self._compact_for_llm(admet_result.get("organ_impacts", [])),
                },
                "mechanism_chains_lookup": {"mechanism_chains": chain_summaries},
                "persade_drug_profile": {
                    "population_signals": population_signals,
                    "organ_attribution": persade.get("organ_attributions", {}).get(organ),
                },
                "drugbank_metabolism_query": self._compact_for_llm(metabolism),
            },
            "prepared_attribution": {
                "structural_alerts": self._compact_for_llm(structural),
                "physchem_or_property": self._compact_for_llm(property_attribution),
                "admet_endpoint": self._compact_for_llm(admet_endpoint),
                "target_pathway": self._compact_for_llm(target_pathway),
                "mechanism_summary": mechanism_summary,
            },
            "evidence_items": evidence_items,
            "all_evidence_items": all_evidence_items,
            "method_availability": {
                "structural_alert_matching": bool(structural),
                "smarts": any(bool(item.get("smarts")) for item in to_plain_dict(structural)),
                "gnn_attention": False,
                "shap": False,
            },
        }

    def _molecular_attribution_for_organ(self, context: Dict[str, Any]) -> Dict[str, Any]:
        llm_payload = self._generate_molecular_attribution_with_llm(context)
        normalized = self._normalize_molecular_attribution(llm_payload, context)
        if normalized is not None:
            return self._with_attribution_narrative(context, normalized)
        return self._with_attribution_narrative(context, self._deterministic_molecular_attribution(context))

    def _resolve_molecular_attributions(self, contexts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not contexts:
            return []

        should_parallelize = (
            self.config.use_live_llm
            and bool(self.config.api_key)
            and self.config.attribution_parallelism > 1
            and len(contexts) > 1
        )
        if not should_parallelize:
            return [self._molecular_attribution_for_organ(context) for context in contexts]

        results: List[Optional[Dict[str, Any]]] = [None] * len(contexts)
        max_workers = min(self.config.attribution_parallelism, len(contexts))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(self._molecular_attribution_for_organ, context): index
                for index, context in enumerate(contexts)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception:
                    results[index] = self._deterministic_molecular_attribution(contexts[index])

        return [
            result if result is not None else self._deterministic_molecular_attribution(contexts[index])
            for index, result in enumerate(results)
        ]

    def _llm_molecular_attribution_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """EvidencePackage-derived context for live LLM attribution.

        The local prepared_attribution block is intentionally excluded so the
        LLM judges drivers from retrieved evidence rather than local candidate
        ordering rules.
        """
        return {
            "task": context.get("task"),
            "allowed_tool_names": context.get("allowed_tool_names", []),
            "drug": context.get("drug", {}),
            "organ_system": context.get("organ_system"),
            "soc": context.get("soc"),
            "baseline_risk": context.get("baseline_risk", {}),
            "probability_audit": context.get("probability_audit", {}),
            "evidence_package": context.get("evidence_package", {}),
            "tool_results": context.get("tool_results", {}),
            "organ_tool_result_focus": context.get("organ_tool_result_focus", {}),
            "evidence_items": context.get("evidence_items", []),
            "all_evidence_items": context.get("all_evidence_items", []),
            "method_availability": context.get("method_availability", {}),
        }

    def _generate_molecular_attribution_with_llm(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        schema = self._molecular_attribution_schema()
        llm_context = self._llm_molecular_attribution_context(context)
        if self._llm_json_extractor is not None:
            return self._coerce_llm_payload(
                self._llm_json_extractor("molecular_attribution", llm_context, schema)
            )
        if not self.config.use_live_llm or not self.config.api_key:
            return None
        try:
            for _ in range(3):
                payload = self._call_live_llm_molecular_attribution(context=llm_context, schema=schema)
                if payload:
                    return payload
            return None
        except Exception:
            return None

    def _with_attribution_narrative(
        self,
        context: Dict[str, Any],
        attribution: Dict[str, Any],
    ) -> Dict[str, Any]:
        if attribution.get("attribution_narrative"):
            return attribution
        narrative = self._generate_attribution_narrative_with_llm(context, attribution)
        if not narrative:
            return attribution
        enriched = dict(attribution)
        enriched["attribution_narrative"] = narrative
        return enriched

    def _generate_attribution_narrative_with_llm(
        self,
        context: Dict[str, Any],
        attribution: Dict[str, Any],
    ) -> Optional[str]:
        narrative_context = self._attribution_narrative_context(context, attribution)
        schema = self._attribution_narrative_schema()
        if self._llm_json_extractor is not None:
            payload = self._coerce_llm_payload(
                self._llm_json_extractor("attribution_narrative", narrative_context, schema)
            )
            return self._string_value((payload or {}).get("attribution_narrative"))
        if not self.config.use_live_llm or not self.config.api_key:
            return None
        try:
            for _ in range(2):
                payload = self._call_live_llm_attribution_narrative(
                    context=narrative_context,
                    schema=schema,
                )
                narrative = self._string_value((payload or {}).get("attribution_narrative"))
                if narrative:
                    return narrative
            return None
        except Exception:
            return None

    def _attribution_narrative_context(
        self,
        context: Dict[str, Any],
        attribution: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "drug": context.get("drug", {}),
            "organ_system": context.get("organ_system"),
            "soc": context.get("soc"),
            "baseline_risk": context.get("baseline_risk", {}),
            "probability_audit": context.get("probability_audit", {}),
            "method_availability": context.get("method_availability", {}),
            "attribution_explanation": attribution.get("attribution_explanation"),
            "molecular_attribution": attribution.get("molecular_attribution", []),
            "attribution_limitations": attribution.get("attribution_limitations", []),
        }

    def _attribution_narrative_schema(self) -> Dict[str, Any]:
        return {
            "attribution_narrative": (
                "one human-readable paragraph, 120-220 words, explaining the existing attribution; "
                "do not add, remove, reorder, or contradict molecular_attribution drivers; mention "
                "main probability drivers, supporting context, and key limitations"
            )
        }

    def _call_live_llm_attribution_narrative(
        self,
        *,
        context: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        try:
            from openai import OpenAI
        except ImportError:
            return None

        client_kwargs: Dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url:
            client_kwargs["base_url"] = self.config.base_url
        client = OpenAI(**client_kwargs)

        completion = client.chat.completions.create(
            model=self.config.brain_model,
            temperature=self.config.temperature,
            max_tokens=min(self.config.max_tokens, 900),
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write a human-readable narrative for an already-computed molecular "
                        "attribution. Do not change the attribution result, do not introduce new "
                        "drivers, and do not invent evidence. Use only the supplied attribution, "
                        "probability_audit, and limitations. Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"schema": schema, "context": context}, ensure_ascii=False, default=str),
                },
            ],
        )
        content = completion.choices[0].message.content or "{}"
        return self._coerce_llm_payload(self._json_object_from_text(content))

    def _call_live_llm_molecular_attribution(
        self,
        *,
        context: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        try:
            from openai import OpenAI
        except ImportError:
            return None

        client_kwargs: Dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url:
            client_kwargs["base_url"] = self.config.base_url
        client = OpenAI(**client_kwargs)

        completion = client.chat.completions.create(
            model=self.config.brain_model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are PersAgent's molecular attribution analyst. Read the supplied "
                        "EvidencePackage-derived context and decide which retrieved evidence explains "
                        "the universal toxicity row. Use only tool_results, evidence_items, "
                        "all_evidence_items, organ_tool_result_focus, and probability_audit. Do not "
                        "invent toxicophores, SMARTS, GNN attention, SHAP, assays, references, targets, "
                        "or endpoints. Prioritize probability_audit.main_drivers and "
                        "probability_audit.evidence_summary for drivers whose contribution_role is "
                        "probability_driver. Structural alerts and physicochemical properties may be "
                        "included only as structural_context or mechanistic_context unless the audit "
                        "says they raised probability. The top-level JSON object must contain soc, "
                        "attribution_explanation, molecular_attribution, and attribution_limitations. "
                        "Do not return a bare driver object. Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"schema": schema, "context": context}, ensure_ascii=False, default=str),
                },
            ],
        )
        content = completion.choices[0].message.content or "{}"
        return self._coerce_llm_payload(self._json_object_from_text(content))

    def _molecular_attribution_schema(self) -> Dict[str, Any]:
        return {
            "soc": "string; must match the input SOC",
            "attribution_explanation": (
                "brief evidence-bound explanation, <= 120 words; distinguish probability drivers "
                "from structural or physicochemical context"
            ),
            "molecular_attribution": [
                {
                    "driver_type": (
                        "one of structural_alert, physchem_property, admet_endpoint, "
                        "metabolism_reactivity, target_pathway, population_signal, evidence_summary"
                    ),
                    "driver": "short label copied or summarized from retrieved evidence",
                    "contribution_role": (
                        "one of probability_driver, mechanistic_context, structural_context, "
                        "population_context, limitation"
                    ),
                    "mechanistic_role": "how this evidence links molecule/target/property to the SOC toxicity",
                    "direction": "increase, decrease, contextual, or unknown",
                    "confidence": "number from 0 to 1 based only on retrieved evidence strength",
                    "evidence_refs": [
                        {
                            "tool_name": "must be one of allowed_tool_names",
                            "field": "dot path such as tool_results.admetsar_predict.admet_profile",
                            "summary": "short evidence summary",
                        }
                    ],
                    "limitations": "driver-specific limitation, or empty string",
                }
            ],
            "attribution_limitations": [
                "missing or unavailable molecular attribution methods, e.g. no GNN/SHAP atom-level evidence"
            ],
        }

    def _normalize_molecular_attribution(
        self,
        payload: Optional[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        bare_driver = self._is_llm_driver_object(payload)
        driver_payload = [payload] if bare_driver else payload.get("molecular_attribution")
        drivers, ref_limitations = self._normalize_llm_molecular_drivers(
            driver_payload,
            context,
        )
        explanation = self._string_value(payload.get("attribution_explanation"))
        if not explanation and isinstance(payload.get("molecular_attribution"), str):
            explanation = self._string_value(payload.get("molecular_attribution"))
        if not explanation and drivers:
            explanation = self._driver_sentence(context.get("soc", "toxicity"), drivers)
        if not explanation:
            return None
        limitations = self._string_list(payload.get("attribution_limitations"))
        if bare_driver:
            limitations.append("Live LLM returned a bare driver object; local code wrapped it as molecular_attribution.")
        elif "molecular_attribution" not in payload:
            limitations.append("Live LLM did not return a molecular_attribution driver list.")
        limitations.extend(ref_limitations)
        limitations.extend(self._method_limitations(context))
        return {
            "attribution_generation_method": "live_llm",
            "attribution_explanation": explanation,
            "molecular_attribution": drivers,
            "attribution_limitations": self._unique_strings(limitations),
        }

    def _is_llm_driver_object(self, payload: Mapping[str, Any]) -> bool:
        return any(key in payload for key in ("driver", "driver_type", "evidence_refs", "contribution_role")) and not any(
            key in payload for key in ("attribution_explanation", "molecular_attribution", "attribution_limitations")
        )

    def _normalize_llm_molecular_drivers(
        self,
        value: Any,
        context: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        if value is None:
            return [], []
        if isinstance(value, Mapping):
            raw_drivers = [value]
        elif isinstance(value, str):
            raw_drivers = [{"driver": value, "driver_type": "evidence_summary"}]
        elif isinstance(value, list):
            raw_drivers = value
        else:
            return [], ["LLM molecular_attribution had an unsupported shape."]

        drivers: List[Dict[str, Any]] = []
        limitations: List[str] = []
        for raw in raw_drivers[:8]:
            if isinstance(raw, str):
                raw = {"driver": raw, "driver_type": "evidence_summary"}
            if not isinstance(raw, Mapping):
                limitations.append("Skipped a non-object molecular attribution driver from the LLM output.")
                continue

            driver = self._string_value(
                raw.get("driver")
                or raw.get("name")
                or raw.get("endpoint")
                or raw.get("feature")
                or raw.get("summary")
            )
            if not driver:
                limitations.append("Skipped an LLM molecular attribution driver without a driver label.")
                continue

            evidence_refs, ref_limitations = self._normalize_llm_evidence_refs(raw, context)
            limitations.extend(ref_limitations)
            confidence = self._float_or_none(raw.get("confidence"))
            if confidence is None:
                confidence = 0.5

            drivers.append(
                {
                    "driver_type": self._normalize_driver_type(raw.get("driver_type")),
                    "driver": driver,
                    "contribution_role": self._normalize_contribution_role(raw.get("contribution_role")),
                    "mechanistic_role": self._string_value(raw.get("mechanistic_role"))
                    or self._string_value(raw.get("rationale"))
                    or "Evidence selected by the live LLM from the retrieved EvidencePackage.",
                    "direction": self._normalize_direction(raw.get("direction")),
                    "confidence": round(self._clamp(float(confidence)), 3),
                    "evidence_refs": evidence_refs,
                    "limitations": self._string_value(raw.get("limitations")) or "",
                }
            )

        return drivers, self._unique_strings(limitations)

    def _normalize_driver_type(self, value: Any) -> str:
        text = (self._string_value(value) or "evidence_summary").lower()
        text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
        aliases = {
            "admet": "admet_endpoint",
            "admet_model": "admet_endpoint",
            "target": "target_pathway",
            "pathway": "target_pathway",
            "mechanism": "target_pathway",
            "physchem": "physchem_property",
            "property": "physchem_property",
            "population": "population_signal",
        }
        normalized = aliases.get(text, text)
        return normalized if normalized in _MOLECULAR_DRIVER_TYPES else "evidence_summary"

    def _normalize_contribution_role(self, value: Any) -> str:
        text = (self._string_value(value) or "mechanistic_context").lower()
        text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
        aliases = {
            "probability": "probability_driver",
            "driver": "probability_driver",
            "baseline_driver": "probability_driver",
            "mechanism": "mechanistic_context",
            "context": "mechanistic_context",
            "structural": "structural_context",
            "population": "population_context",
        }
        normalized = aliases.get(text, text)
        allowed = {
            "probability_driver",
            "mechanistic_context",
            "structural_context",
            "population_context",
            "limitation",
        }
        return normalized if normalized in allowed else "mechanistic_context"

    def _normalize_llm_evidence_refs(
        self,
        raw_driver: Mapping[str, Any],
        context: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        raw_refs = raw_driver.get("evidence_refs")
        if raw_refs is None:
            raw_refs = [
                {
                    "tool_name": raw_driver.get("tool_name") or raw_driver.get("evidence_source"),
                    "field": raw_driver.get("field") or raw_driver.get("evidence_path"),
                    "summary": raw_driver.get("evidence_summary") or raw_driver.get("summary"),
                }
            ]
        elif isinstance(raw_refs, Mapping):
            raw_refs = [raw_refs]
        elif not isinstance(raw_refs, list):
            raw_refs = []

        allowed_tools = set(context.get("allowed_tool_names", []))
        refs: List[Dict[str, Any]] = []
        limitations: List[str] = []
        for raw_ref in raw_refs[:4]:
            if not isinstance(raw_ref, Mapping):
                limitations.append("Skipped a non-object evidence reference from the LLM output.")
                continue
            field = self._string_value(
                raw_ref.get("field")
                or raw_ref.get("evidence_path")
                or raw_driver.get("field")
                or raw_driver.get("evidence_path")
            )
            tool_name = self._string_value(
                raw_ref.get("tool_name")
                or raw_ref.get("evidence_source")
                or raw_driver.get("tool_name")
                or raw_driver.get("evidence_source")
            )
            if not tool_name and field:
                match = re.match(r"tool_results\.([A-Za-z0-9_]+)", field)
                if match:
                    tool_name = match.group(1)
            if not tool_name or tool_name not in allowed_tools:
                limitations.append(
                    f"Skipped evidence reference with unknown tool_name={tool_name or 'missing'}."
                )
                continue

            if not field:
                field = f"tool_results.{tool_name}"
            if not self._evidence_path_exists(context, field):
                limitations.append(f"LLM evidence path could not be verified: {field}.")
                field = f"tool_results.{tool_name}"
            refs.append(
                {
                    "tool_name": tool_name,
                    "field": field,
                    "summary": self._string_value(raw_ref.get("summary") or raw_driver.get("summary"))
                    or "Evidence reference selected by the live LLM.",
                }
            )

        return refs, limitations

    def _evidence_path_exists(self, context: Dict[str, Any], path: str) -> bool:
        cleaned = path.strip().strip("$")
        cleaned = cleaned[1:] if cleaned.startswith(".") else cleaned
        if not cleaned:
            return False
        parts = [part for part in re.split(r"\.|\[|\]", cleaned) if part not in {"", "'"}]
        current: Any = context
        for part in parts:
            part = part.strip("\"'")
            if isinstance(current, Mapping):
                if part not in current:
                    return False
                current = current[part]
                continue
            if isinstance(current, list):
                if part.isdigit():
                    index = int(part)
                    if index >= len(current):
                        return False
                    current = current[index]
                    continue
                return bool(current)
            return False
        return True

    def _deterministic_molecular_attribution(self, context: Dict[str, Any]) -> Dict[str, Any]:
        prepared = context.get("prepared_attribution", {})
        drivers: List[Dict[str, Any]] = []

        for alert in prepared.get("structural_alerts", [])[:3]:
            label = alert.get("alert") or "structural alert"
            smarts = alert.get("smarts")
            drivers.append(
                self._driver(
                    "structural_alert",
                    f"{label} ({smarts})" if smarts else label,
                    "Matched structural alert that can contribute molecular-level toxicity context.",
                    "increase",
                    0.56,
                    "drug_card_lookup",
                    "tool_results.drug_card_lookup.structural_alerts",
                    "Structural alert retrieved from local drug/structure tools.",
                    "" if smarts else "SMARTS pattern was not available for this alert.",
                )
            )

        for endpoint in prepared.get("admet_endpoint", [])[:4]:
            endpoint_name = endpoint.get("endpoint") or "ADMET endpoint"
            drivers.append(
                self._driver(
                    "admet_endpoint",
                    f"{endpoint_name}={endpoint.get('value')}",
                    "ADMET model endpoint supports this organ toxicity baseline.",
                    "increase",
                    0.64,
                    "admetsar_predict",
                    "tool_results.admetsar_predict.admet_profile",
                    "Organ-mapped ADMET endpoint from Stage 1 retrieval.",
                    "Model endpoint is not an atom-level GNN/SHAP attribution.",
                )
            )

        for prop in prepared.get("physchem_or_property", [])[:3]:
            drivers.append(
                self._driver(
                    "physchem_property",
                    f"{prop.get('feature', 'property')}={prop.get('value')}",
                    "Physicochemical or ADMET property provides feature-level toxicity context.",
                    "contextual",
                    0.46,
                    "admetsar_predict",
                    "tool_results.admetsar_predict.property_attribution",
                    "Feature-level property attribution from admetSAR-derived profile.",
                    "Feature attribution is not atom-level SHAP.",
                )
            )

        for target in prepared.get("target_pathway", [])[:3]:
            if target.get("evidence_type") == "fallback_unknown":
                continue
            summary = target.get("summary") or self._target_pathway_label(target)
            drivers.append(
                self._driver(
                    "target_pathway",
                    str(summary),
                    "Target/pathway evidence links molecular pharmacology to the terminal ADE phenotype.",
                    "increase",
                    0.58,
                    "mechanism_chains_lookup",
                    "tool_results.mechanism_chains_lookup.mechanism_chains",
                    "Drug-target-pathway-ADE chain from Stage 1 retrieval.",
                    "",
                )
            )

        persade_profile = context.get("tool_results", {}).get("persade_drug_profile", {})
        population_signals = persade_profile.get("population_signals") or persade_profile.get("signals") or []
        for signal in population_signals[:3]:
            if signal.get("organ_system") and signal.get("organ_system") != context.get("organ_system"):
                continue
            event = signal.get("event") or signal.get("ade_name") or "population ADE signal"
            drivers.append(
                self._driver(
                    "population_signal",
                    str(event),
                    "Population ADE signal supports the terminal toxicity phenotype but is not molecular by itself.",
                    "increase",
                    0.52,
                    "persade_drug_profile",
                    "tool_results.persade_drug_profile.signals",
                    "PersADE/FAERS signal retrieved for this organ system.",
                    "Disproportionality signal does not identify a structural toxicophore.",
                )
            )

        metabolism_result = context.get("tool_results", {}).get("drugbank_metabolism_query", {})
        metabolism = metabolism_result.get("metabolism", metabolism_result)
        for enzyme in metabolism.get("primary_enzymes", [])[: max(0, 3 - len(drivers))]:
            drivers.append(
                self._driver(
                    "metabolism_reactivity",
                    f"Primary metabolism enzyme {enzyme}",
                    "Metabolism context may alter parent-drug or metabolite exposure relevant to toxicity.",
                    "contextual",
                    0.44,
                    "drugbank_metabolism_query",
                    "tool_results.drugbank_metabolism_query.metabolism.primary_enzymes",
                    "Metabolism evidence retrieved locally.",
                    "No reactive metabolite or atom-level metabolism attribution was retrieved.",
                )
            )

        drivers = drivers[:8]
        if not drivers:
            drivers.append(
                self._driver(
                    "evidence_summary",
                    "baseline prior with sparse molecular evidence",
                    "No organ-specific molecular attribution evidence was retrieved for this row.",
                    "unknown",
                    0.2,
                    "drug_card_lookup",
                    "tool_results.drug_card_lookup",
                    "Drug identity was retrieved, but molecular attribution evidence was sparse.",
                    "No SMARTS, GNN attention, SHAP, ADMET, or target/pathway driver was available.",
                )
            )

        return {
            "attribution_generation_method": "deterministic_fallback",
            "attribution_explanation": self._driver_sentence(context.get("soc", "toxicity"), drivers),
            "molecular_attribution": drivers,
            "attribution_limitations": self._method_limitations(context),
        }

    def _target_pathway_label(self, target_pathway: Dict[str, Any]) -> str:
        target = target_pathway.get("target") or {}
        pathway = target_pathway.get("pathway") or {}
        ade = target_pathway.get("ade") or {}
        parts = [
            target.get("gene") or target.get("protein") or target.get("id"),
            pathway.get("name") or pathway.get("id"),
            ade.get("name") or ade.get("id"),
        ]
        label = " -> ".join(str(part) for part in parts if part)
        return label or "target/pathway evidence"

    def _driver(
        self,
        driver_type: str,
        driver: str,
        mechanistic_role: str,
        direction: str,
        confidence: float,
        tool_name: str,
        field: str,
        summary: str,
        limitations: str,
    ) -> Dict[str, Any]:
        return {
            "driver_type": driver_type,
            "driver": driver,
            "mechanistic_role": mechanistic_role,
            "direction": direction,
            "confidence": round(self._clamp(confidence), 3),
            "evidence_refs": [{"tool_name": tool_name, "field": field, "summary": summary}],
            "limitations": limitations,
        }

    def _driver_sentence(self, soc: str, drivers: List[Dict[str, Any]]) -> str:
        leading = [driver.get("driver", "retrieved evidence") for driver in drivers[:3]]
        if not leading:
            return f"{soc} has no molecular attribution drivers in the current Stage 1 evidence package."
        text = "; ".join(str(item) for item in leading)
        return (
            f"{soc} attribution is driven mainly by {text}. "
            "The explanation is constrained to retrieved tool_results and evidence_items."
        )

    def _method_limitations(self, context: Dict[str, Any]) -> List[str]:
        availability = context.get("method_availability", {})
        limitations = []
        if not availability.get("smarts"):
            limitations.append("No explicit SMARTS-level toxicophore match was available.")
        if not availability.get("gnn_attention"):
            limitations.append("No GNN attention attribution was available in Stage 1 tool_results.")
        if not availability.get("shap"):
            limitations.append("No SHAP atom/feature attribution was available in Stage 1 tool_results.")
        return limitations

    def _normalize_direction(self, value: Any) -> str:
        text = (self._string_value(value) or "unknown").lower()
        if text in {"increase", "decrease", "contextual", "unknown"}:
            return text
        if text in {"up", "higher", "raises"}:
            return "increase"
        if text in {"down", "lower", "reduces"}:
            return "decrease"
        return "unknown"

    def _unique_strings(self, values: List[str]) -> List[str]:
        unique: List[str] = []
        for value in values:
            text = self._string_value(value)
            if text and text not in unique:
                unique.append(text)
        return unique

    def _compact_for_llm(self, value: Any, *, max_items: int = 8, max_string: int = 320) -> Any:
        plain = to_plain_dict(value)
        if isinstance(plain, str):
            return plain if len(plain) <= max_string else plain[: max_string - 3] + "..."
        if isinstance(plain, list):
            return [self._compact_for_llm(item, max_items=max_items, max_string=max_string) for item in plain[:max_items]]
        if isinstance(plain, dict):
            compact: Dict[str, Any] = {}
            for idx, (key, item) in enumerate(plain.items()):
                if idx >= max_items:
                    compact["_truncated"] = True
                    break
                compact[str(key)] = self._compact_for_llm(item, max_items=max_items, max_string=max_string)
            return compact
        return plain

    def build_personalized_report(
        self,
        patient_info: PatientInfo,
        drug_info: DrugInfo,
        universal_report: UniversalToxicityReport,
        evidence_package: EvidencePackage,
        patient_features: PatientFeatures,
    ) -> PersonalizedToxicityReport:
        factors = self._collect_patient_attributions(
            patient_info, drug_info, evidence_package, patient_features
        )
        personalized_items: List[PersonalizedToxicityItem] = []

        for baseline_item in universal_report.general_toxicity:
            organ = ORGAN_BY_SOC.get(baseline_item.soc, baseline_item.soc)
            organ_factors = factors.get(organ, [])
            if baseline_item.probability is None:
                personalized_items.append(
                    PersonalizedToxicityItem(
                        soc=baseline_item.soc,
                        baseline=BaselineRisk(
                            risk_level=baseline_item.risk_level,
                            probability=baseline_item.probability,
                        ),
                        personalized_risk_level=None,
                        personalized_probability=None,
                        risk_shift=None,
                        ctcae_grade_predicted=None,
                        patient_attribution=[],
                        mechanism_chain_modifiers=[],
                        clinical_recommendation=None,
                    )
                )
                continue

            modifier_product = self._attribution_product(organ_factors)
            personalized_probability = self._clamp(baseline_item.probability * modifier_product)
            risk_shift = round(personalized_probability - baseline_item.probability, 3)
            ctcae_grade = self._risk_to_ctcae_grade(personalized_probability)
            mechanism_chain_modifiers = self._mechanism_modifiers_for_factors(
                organ_factors=organ_factors,
                baseline_item=baseline_item,
            )

            personalized_items.append(
                PersonalizedToxicityItem(
                    soc=baseline_item.soc,
                    baseline=BaselineRisk(
                        risk_level=baseline_item.risk_level,
                        probability=baseline_item.probability,
                    ),
                    personalized_risk_level=self._risk_level(personalized_probability),
                    personalized_probability=personalized_probability,
                    risk_shift=risk_shift,
                    ctcae_grade_predicted=ctcae_grade,
                    patient_attribution=organ_factors,
                    mechanism_chain_modifiers=mechanism_chain_modifiers,
                    clinical_recommendation=self._clinical_recommendation(
                        organ=organ,
                        probability=personalized_probability,
                        ctcae_grade=ctcae_grade,
                        factors=organ_factors,
                        drug_info=drug_info,
                        patient_info=patient_info,
                    ),
                )
            )

        return PersonalizedToxicityReport(
            drug=universal_report.drug,
            patient_id=patient_info.patient_id,
            personalized_toxicity=personalized_items,
        )

    def synthesize_draft_report(
        self,
        patient_info: PatientInfo,
        drug_info: DrugInfo,
        universal_report: UniversalToxicityReport,
        personalized_report: PersonalizedToxicityReport,
        evidence_package: EvidencePackage,
        patient_features: PatientFeatures,
    ) -> Dict[str, Any]:
        drug_entity = (
            evidence_package.tool_results.get("drug_card_lookup", {}).get("drug_entity")
            or {
                "primary_name": None,
                "inchi_key": None,
                "smiles": None,
                "drugbank_id": None,
                "chembl_id": None,
                "pubchem_id": None,
                "atc": None,
                "drug_type": None,
            }
        )
        structure_profile = (
            evidence_package.tool_results.get("admetsar_predict", {}).get("structure_profile")
            or {
                "descriptors": {"MW": None, "TPSA": None, "SlogP": None, "QED": None},
                "drug_likeness": {"lipinski": None, "pfizer": None, "gsk": None},
                "structural_alerts": [],
            }
        )
        admet_profile = evidence_package.tool_results.get("admetsar_predict", {}).get("admet_profile") or []
        known_ade_profile = (
            evidence_package.tool_results.get("persade_drug_profile", {}).get("known_ade_profile")
            or []
        )
        mechanism_chains = (
            evidence_package.tool_results.get("mechanism_chains_lookup", {}).get("mechanism_chains")
            or []
        )
        persade_contextual_evidence = (
            evidence_package.tool_results.get("persade_subgroup_scores", {}).get("persade_contextual_evidence")
            or []
        )
        baseline_organ_risk = self._build_baseline_organ_risk(evidence_package.tool_results)
        attribution_explanations = self._stage1_attribution_explanations(universal_report)
        return {
            "metadata": {
                "system": "PersAgent",
                "report_type": "personalized_drug_toxicity",
                "disclaimer": (
                    "Clinical decision support demo. Use with clinician review; "
                    "not a standalone diagnosis or prescribing order."
                ),
            },
            "patient_info": patient_info,
            "patient_features": patient_features,
            "drug_info": drug_info,
            "drug_entity": drug_entity,
            "structure_profile": structure_profile,
            "admet_profile": admet_profile,
            "known_ade_profile": known_ade_profile,
            "mechanism_chains": mechanism_chains,
            "persade_contextual_evidence": persade_contextual_evidence,
            "baseline_organ_risk": baseline_organ_risk,
            "attribution_explanations": attribution_explanations,
            "evidence_package": evidence_package,
            "universal_report": universal_report,
            "personalized_report": personalized_report,
            "verification_status": "PENDING",
            "conflict_flags": evidence_package.conflicts,
            "reasoning_summary": self._reasoning_summary(
                universal_report=universal_report,
                personalized_report=personalized_report,
                conflicts=evidence_package.conflicts,
            ),
            "clinical_alignment": {
                "ctcae_version": "v5.0",
                "risk_formula": (
                    "Stage 1 builds drug -> metabolism -> active_or_toxic_species -> "
                    "target_binding -> pathway_perturbation -> organ_toxicity_phenotype chains; "
                    "Stage 2 personalized_probability = baseline.probability x product(node modifier magnitudes)"
                ),
                "soc_systems": [item.soc for item in universal_report.general_toxicity],
            },
        }

    def _stage1_attribution_explanations(self, universal_report: UniversalToxicityReport) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in universal_report.general_toxicity:
            attribution = item.attribution
            rows.append(
                {
                    "soc": item.soc,
                    "baseline_risk_level": item.baseline_risk_level,
                    "baseline_probability": item.baseline_probability,
                    "attribution_explanation": attribution.attribution_explanation,
                    "attribution_narrative": attribution.attribution_narrative,
                    "attribution_generation_method": attribution.attribution_generation_method,
                    "molecular_attribution": attribution.molecular_attribution,
                    "attribution_limitations": attribution.attribution_limitations,
                }
            )
        return rows

    def revise_with_verification(
        self,
        draft_report: Dict[str, Any],
        verification_report: VerificationReport,
    ) -> Dict[str, Any]:
        revised = dict(draft_report)
        revised["verification_status"] = verification_report.status
        revised["verification_report"] = verification_report
        if verification_report.status == "BLOCKED":
            revised["final_decision"] = {
                "status": "BLOCKED",
                "message": "Report output blocked by deterministic safety redline.",
            }
        elif verification_report.status == "FLAGGED":
            revised["final_decision"] = {
                "status": "FLAGGED",
                "message": "Report may be displayed with highlighted review items.",
            }
        else:
            revised["final_decision"] = {
                "status": "PASS",
                "message": "Report passed format, content, safety, and calibration checks.",
            }
        return revised

    def _collect_patient_attributions(
        self,
        patient_info: PatientInfo,
        drug_info: DrugInfo,
        evidence_package: EvidencePackage,
        patient_features: PatientFeatures,
    ) -> Dict[str, List[PatientAttribution]]:
        factors: Dict[str, List[PatientAttribution]] = {organ: [] for organ in ORGAN_SYSTEMS}
        tool_results = evidence_package.tool_results
        drug_lower = drug_info.name.lower()
        organ_classes = patient_features.organ_function_classes

        # Demographics / organ-function stratification now come from the
        # standardized PatientFeatures (Stage 2 Step 1) — the single source of
        # truth — instead of re-deriving thresholds inline here.
        if patient_features.elderly:
            self._add_factor(
                factors,
                "hematologic",
                "comorbidity",
                "Age >=65 years",
                "up",
                1.15,
                "COMORB-AGE-65",
                "Clinical rule",
                4,
                "Older age increases bleeding complication vulnerability.",
            )
            self._add_factor(
                factors,
                "kidney",
                "comorbidity",
                "Age >=65 years",
                "up",
                1.08,
                "COMORB-AGE-65",
                "Clinical rule",
                4,
                "Older adults have narrower physiologic reserve during toxic exposures.",
            )

        hepatic_class = organ_classes.get("hepatic")
        if hepatic_class is not None and hepatic_class.klass in {"moderate", "severe"}:
            severe = hepatic_class.klass == "severe"
            hepatic_magnitude = 1.80 if severe else 1.45
            heme_magnitude = 1.45 if severe else 1.25
            self._add_factor(
                factors,
                "liver",
                "organ_function",
                f"Hepatic impairment ({hepatic_class.basis})",
                "up",
                hepatic_magnitude,
                f"ORG-HEPATIC-{hepatic_class.klass.upper()}",
                "Clinical rule",
                4,
                "Reduced hepatic reserve and metabolism increase toxicity susceptibility.",
            )
            self._add_factor(
                factors,
                "hematologic",
                "comorbidity",
                f"Hepatic impairment ({hepatic_class.basis})",
                "up",
                heme_magnitude,
                f"COMORB-HEPATIC-{hepatic_class.klass.upper()}",
                "Clinical rule",
                4,
                "Cirrhosis can destabilize coagulation and anticoagulant response.",
            )

        renal_class = organ_classes.get("renal")
        if renal_class is not None and renal_class.klass in {"moderate", "severe", "kidney_failure"}:
            magnitude = 1.30 if renal_class.klass == "moderate" else 1.60
            self._add_factor(
                factors,
                "kidney",
                "organ_function",
                f"Renal impairment ({renal_class.basis})",
                "up",
                magnitude,
                f"ORG-RENAL-{renal_class.klass.upper()}",
                "Clinical rule",
                4,
                "Renal impairment increases exposure fragility and adverse outcome risk.",
            )
            if drug_lower in {"warfarin"}:
                self._add_factor(
                    factors,
                    "hematologic",
                    "organ_function",
                    "Renal impairment during anticoagulation",
                    "up",
                    1.10,
                    "ORG-RENAL-WARFARIN-BLEEDING",
                    "Clinical rule",
                    4,
                    "Renal impairment is associated with anticoagulation instability.",
                )

        for rec in tool_results.get("cpic_lookup", {}).get("recommendations", []):
            for impact in rec.get("organ_impacts", []):
                self._add_factor(
                    factors,
                    impact["organ_system"],
                    "PGx",
                    f"{rec['gene']} {rec['genotype']} ({rec['phenotype']})",
                    "up" if impact.get("direction") == "increase" else "down",
                    float(impact.get("magnitude", 1.0)),
                    f"PGX-{rec['gene']}-{rec['genotype']}",
                    "CPIC",
                    1,
                    rec.get("classification", "A"),
                )

        for interaction in tool_results.get("ddi_query", {}).get("interactions", []):
            for impact in interaction.get("organ_impacts", []):
                self._add_factor(
                    factors,
                    impact["organ_system"],
                    "comedication",
                    f"{drug_info.name} + {interaction['co_medication']}",
                    "up" if impact.get("direction") == "increase" else "down",
                    float(impact.get("magnitude", 1.0)),
                    f"DDI-{drug_info.name.upper()}-{interaction['co_medication'].upper()}",
                    "Local DDI rules",
                    2,
                    interaction.get("severity", "major"),
                )

        for score in tool_results.get("hla_peptide_score", {}).get("scores", []):
            if score.get("risk") == "very_high":
                for impact in score.get("organ_impacts", []):
                    self._add_factor(
                        factors,
                        impact["organ_system"],
                        "PGx",
                        f"HLA risk allele {score['hla']}",
                        "up",
                        float(impact.get("magnitude", 1.0)),
                        f"PGX-{score['hla']}",
                        "HLA peptide scorer",
                        1,
                        "A",
                    )

        for snippet in tool_results.get("persade_contextual_retrieval", {}).get("snippets", []):
            organ = snippet.get("organ_system")
            if organ in factors:
                self._add_factor(
                    factors,
                    organ,
                    "comorbidity",
                    f"Context: {snippet['context']}",
                    "up",
                    1.12,
                    f"CTX-{snippet['context'].upper().replace(' ', '-')}",
                    "PersADE contextual retrieval",
                    EVIDENCE_TIER_BY_LEVEL.get(snippet.get("evidence_level"), 3),
                    snippet["finding"],
                )

        return factors

    def _add_factor(
        self,
        factors: Dict[str, List[PatientAttribution]],
        organ: str,
        factor_type: str,
        factor: str,
        direction: str,
        magnitude: float,
        rule_id: str,
        evidence_source: str,
        evidence_tier: int,
        evidence_grade: str,
        affected_node: Optional[str] = None,
        effect: Optional[str] = None,
    ) -> None:
        if organ not in factors:
            return
        node = affected_node or self._infer_affected_node(factor_type, factor, rule_id)
        factors[organ].append(
            PatientAttribution(
                factor_type=factor_type,  # type: ignore[arg-type]
                factor=factor,
                direction=direction,  # type: ignore[arg-type]
                magnitude=round(magnitude, 3),
                rule_id=rule_id,
                evidence=PatientFactorEvidence(
                    source=evidence_source,
                    tier=evidence_tier,
                    grade=evidence_grade,
                ),
                affected_node=node,  # type: ignore[arg-type]
                effect=effect or self._infer_modifier_effect(factor, node, direction),
            )
        )

    def _clinical_recommendation(
        self,
        organ: str,
        probability: float,
        ctcae_grade: int,
        factors: List[PatientAttribution],
        drug_info: DrugInfo,
        patient_info: PatientInfo,
    ) -> ClinicalRecommendationOutput:
        drug_lower = drug_info.name.lower()
        factor_text = "; ".join(f.factor for f in factors)

        if drug_lower == "warfarin" and organ == "hematologic" and probability >= 0.50:
            return ClinicalRecommendationOutput(
                action="dose_adjust",
                text=(
                    "Use genotype- and interaction-guided lower warfarin starting dose; "
                    "avoid routine 5 mg/day initiation without INR-guided adjustment. "
                    "Check INR within 2-3 days and at least weekly until stable. "
                    f"Detected modifiers: {factor_text}."
                ),
                ctcae_aligned=ctcae_grade >= 3,
            )

        if any(f.rule_id.startswith("DDI-") and f.evidence.grade == "contraindicated" for f in factors):
            return ClinicalRecommendationOutput(
                action="avoid",
                text="Avoid the contraindicated combination and select a safer alternative.",
                ctcae_aligned=True,
            )

        if probability >= 0.65:
            return ClinicalRecommendationOutput(
                action="monitor",
                text=(
                    f"High {SOC_BY_ORGAN.get(organ, organ)} risk; arrange close monitoring and "
                    "specialist review before continuation."
                ),
                ctcae_aligned=ctcae_grade >= 3,
            )

        if organ == "liver" and patient_info.child_pugh in {"B", "C"}:
            return ClinicalRecommendationOutput(
                action="monitor",
                text="Monitor ALT/AST, bilirubin, albumin, INR trend, and clinical hepatic decompensation signs.",
                ctcae_aligned=True,
            )

        if organ == "kidney" and patient_info.egfr_ml_min is not None and patient_info.egfr_ml_min < 60:
            return ClinicalRecommendationOutput(
                action="monitor",
                text="Trend renal function and reassess drug toxicity risk after acute illness or dehydration.",
                ctcae_aligned=True,
            )

        if factors:
            return ClinicalRecommendationOutput(
                action="monitor",
                text=f"Monitor because patient-specific modifiers were detected: {factor_text}.",
                ctcae_aligned=True,
            )

        return ClinicalRecommendationOutput(
            action="monitor",
            text="Use standard monitoring; reassess if new organ dysfunction, genotype, or comedication data emerge.",
            ctcae_aligned=True,
        )

    def _build_baseline_organ_risk(self, tool_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        buckets = {
            organ: {
                "soc": SOC_BY_ORGAN[organ],
                "supports": {
                    "gold_standard": 0.0,
                    "admet": 0.0,
                    "ade_signal": 0.0,
                    "mechanism": 0.0,
                    "weak_signal": 0.0,
                },
                "main_ade_terms": [],
                "main_drivers": [],
                "evidence_summary": [],
            }
            for organ in ORGAN_SYSTEMS
        }

        self._add_gold_standard_baseline_evidence(buckets, tool_results.get("gold_standard_checks", []))
        self._add_admet_baseline_evidence(buckets, tool_results.get("admetsar_predict", {}).get("admet_profile", []))
        self._add_known_ade_baseline_evidence(
            buckets,
            tool_results.get("persade_drug_profile", {}).get("known_ade_profile", []),
        )
        self._add_mechanism_baseline_evidence(
            buckets,
            tool_results.get("mechanism_chains_lookup", {}).get("mechanism_chains", []),
        )

        baseline: List[Dict[str, Any]] = []
        for organ in ORGAN_SYSTEMS:
            bucket = buckets[organ]
            if organ not in MODELED_ORGANS:
                baseline.append(
                    {
                        "soc": bucket["soc"],
                        "risk_level": None,
                        "probability": None,
                        "uncertainty": None,
                        "main_ade_terms": [],
                        "main_drivers": [],
                        "evidence_summary": [],
                    }
                )
                continue

            supports = bucket["supports"]
            probability = self._clamp(
                BASELINE_ORGAN_PRIOR
                + supports["gold_standard"]
                + supports["admet"]
                + supports["ade_signal"]
                + supports["mechanism"]
                + supports["weak_signal"]
            )
            uncertainty = self._baseline_uncertainty(supports)
            baseline.append(
                {
                    "soc": bucket["soc"],
                    "risk_level": self._risk_level(probability),
                    "probability": round(probability, 3),
                    "uncertainty": uncertainty,
                    "main_ade_terms": bucket["main_ade_terms"][:5],
                    "main_drivers": bucket["main_drivers"][:6],
                    "evidence_summary": bucket["evidence_summary"][:10],
                }
            )
        return baseline

    def _add_gold_standard_baseline_evidence(
        self,
        buckets: Dict[str, Dict[str, Any]],
        checks: List[Dict[str, Any]],
    ) -> None:
        for check in checks or []:
            organ = self._organ_from_soc_or_term(check.get("soc"), check.get("endpoint"))
            if organ not in buckets:
                continue
            probability = self._safe_float(check.get("probability"), 0.0)
            support = min(0.45, max(0.0, probability - BASELINE_ORGAN_PRIOR))
            buckets[organ]["supports"]["gold_standard"] = max(
                buckets[organ]["supports"]["gold_standard"],
                support,
            )
            self._append_unique(buckets[organ]["main_drivers"], check.get("rule") or "Gold standard rubric")
            buckets[organ]["evidence_summary"].append(
                {
                    "source": check.get("source", "gold_standard_rubric"),
                    "evidence_type": "gold_standard",
                    "detail": check.get("summary", check.get("rule", "")),
                    "support": round(support, 3),
                }
            )

    def _add_admet_baseline_evidence(
        self,
        buckets: Dict[str, Dict[str, Any]],
        admet_profile: List[Dict[str, Any]],
    ) -> None:
        for item in admet_profile or []:
            if item.get("value") == "unknown":
                continue
            organ = self._organ_from_soc_or_term(item.get("soc"), item.get("endpoint"))
            if organ not in buckets or not self._admet_positive_signal(item):
                continue
            support = 0.10 if item.get("endpoint_type") == "classification" else 0.06
            endpoint = item.get("endpoint", "ADMET endpoint")
            bucket = buckets[organ]
            bucket["supports"]["admet"] = min(0.22, bucket["supports"]["admet"] + support)
            self._append_unique(bucket["main_drivers"], f"{endpoint} positive")
            bucket["evidence_summary"].append(
                {
                    "source": "admetSAR3",
                    "evidence_type": "model_feature",
                    "detail": f"{endpoint}={item.get('value')}",
                    "support": round(support, 3),
                }
            )

    def _add_known_ade_baseline_evidence(
        self,
        buckets: Dict[str, Dict[str, Any]],
        known_ade_profile: List[Dict[str, Any]],
    ) -> None:
        sorted_ades = sorted(
            known_ade_profile or [],
            key=lambda item: (
                self._priority_rank(item.get("priority")),
                self._severity_rank(item.get("severity_grade")),
                self._safe_float(item.get("ror_lower_ci"), 0.0),
                self._safe_float(item.get("case_number"), 0.0),
            ),
            reverse=True,
        )
        for item in sorted_ades:
            organ = self._organ_from_soc_or_term(item.get("soc"), item.get("ade_name"))
            if organ not in buckets:
                continue
            support = self._ade_signal_support(item)
            bucket = buckets[organ]
            current = bucket["supports"]["ade_signal"]
            bucket["supports"]["ade_signal"] = min(
                0.26,
                max(current, support) + (0.01 if current else 0.0),
            )
            ade_name = item.get("ade_name") or item.get("ade_id") or "ADE signal"
            self._append_unique(bucket["main_ade_terms"], ade_name)
            self._append_unique(bucket["main_drivers"], "PersADE high ROR signal")
            bucket["evidence_summary"].append(
                {
                    "source": item.get("source", "PersADE/FAERS"),
                    "evidence_type": item.get("evidence_level", "signal"),
                    "detail": (
                        f"{ade_name}: ROR={item.get('ror')}, "
                        f"lower_CI={item.get('ror_lower_ci')}, cases={item.get('case_number')}"
                    ),
                    "support": round(support, 3),
                }
            )

    def _add_mechanism_baseline_evidence(
        self,
        buckets: Dict[str, Dict[str, Any]],
        mechanism_chains: List[Dict[str, Any]],
    ) -> None:
        support_by_type = {
            "direct_DTA": 0.12,
            "indirect_DTI_AT": 0.08,
            "pathway_inferred": 0.04,
        }
        for chain in mechanism_chains or []:
            organ = self._organ_from_soc_or_term(chain.get("soc"), self._chain_ade_name(chain))
            if organ not in buckets:
                continue
            evidence_type = chain.get("evidence_type", "pathway_inferred")
            support = support_by_type.get(evidence_type, 0.03)
            target = self._chain_node(chain, "Target")
            pathway = self._chain_node(chain, "Pathway")
            gene = target.get("gene")
            if self._is_key_mechanism(gene, target.get("protein"), pathway.get("name")):
                support += 0.03
            bucket = buckets[organ]
            bucket["supports"]["mechanism"] = min(0.18, bucket["supports"]["mechanism"] + support)
            driver = self._mechanism_driver(evidence_type, gene, pathway.get("name"))
            self._append_unique(bucket["main_drivers"], driver)
            bucket["evidence_summary"].append(
                {
                    "source": "PersADE DTA/DTI/AT/Pathway",
                    "evidence_type": evidence_type,
                    "detail": driver,
                    "pubmed": chain.get("pubmed", []),
                    "support": round(support, 3),
                }
            )

    def _baseline_uncertainty(self, supports: Dict[str, float]) -> float:
        uncertainty = 0.78
        if supports["gold_standard"] > 0:
            uncertainty -= 0.32
        if supports["admet"] > 0:
            uncertainty -= min(0.18, supports["admet"] * 0.75)
        if supports["ade_signal"] > 0:
            uncertainty -= min(0.22, supports["ade_signal"] * 0.85)
        if supports["mechanism"] > 0:
            uncertainty -= min(0.14, supports["mechanism"] * 0.75)
        if supports["weak_signal"] > 0:
            uncertainty -= min(0.06, supports["weak_signal"] * 0.50)
        return round(self._clamp(uncertainty), 3)

    def _organ_from_soc_or_term(self, soc: Optional[str], term: Optional[str] = None) -> Optional[str]:
        if soc in ORGAN_BY_SOC:
            return ORGAN_BY_SOC[soc]
        text = f"{soc or ''} {term or ''}".lower()
        keyword_map = (
            ("liver", ("hepatic", "hepat", "liver", "bilirubin", "cholest")),
            ("heart", ("cardiac", "cardio", "myocard", "arrhythm", "qt ", "heart", "torsade")),
            ("kidney", ("renal", "kidney", "neph", "urinary", "bladder")),
            ("hematologic", ("haemorr", "hemorr", "bleed", "anaemia", "anemia", "thromb", "coagul", "inr")),
            ("immune", ("allerg", "immune", "anaphyl", "hypersens")),
            ("skin", ("rash", "skin", "dermat", "prurit", "urticaria")),
            ("neurologic", ("seizure", "neuro", "brain", "dizziness", "headache", "stroke")),
            ("gastrointestinal", ("nausea", "vomit", "diarr", "abdom", "gastr", "intestinal")),
        )
        for organ, keywords in keyword_map:
            if any(keyword in text for keyword in keywords):
                return organ
        return None

    def _admet_positive_signal(self, item: Dict[str, Any]) -> bool:
        value = item.get("value")
        numeric = self._safe_float(value, None)
        if numeric is not None:
            return numeric >= 0.5
        text = str(value).strip().lower()
        return text in {"positive", "active", "yes", "true", "toxic", "inhibitor", "substrate"}

    def _ade_signal_support(self, item: Dict[str, Any]) -> float:
        support = 0.04
        if item.get("priority") == "High":
            support += 0.05
        elif item.get("priority") == "Medium":
            support += 0.03
        severity = item.get("severity_grade")
        if severity == "Critical":
            support += 0.04
        elif severity == "Severe":
            support += 0.03
        ror_lower = self._safe_float(item.get("ror_lower_ci"), 0.0)
        if ror_lower >= 5:
            support += 0.04
        elif ror_lower >= 2:
            support += 0.02
        cases = self._safe_float(item.get("case_number"), 0.0)
        if cases >= 50:
            support += 0.02
        elif cases >= 10:
            support += 0.01
        return min(0.22, support)

    def _priority_rank(self, value: Optional[str]) -> int:
        return {"High": 3, "Medium": 2, "Low": 1, "Unknown": 0}.get(str(value or "Unknown"), 0)

    def _severity_rank(self, value: Optional[str]) -> int:
        return {"Critical": 5, "Severe": 4, "Moderate": 3, "Mild": 2, "Minimal": 1}.get(str(value or ""), 0)

    def _safe_float(self, value: Any, default: Optional[float] = 0.0) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _append_unique(self, values: List[str], value: Any) -> None:
        text = str(value).strip() if value is not None else ""
        if text and text not in values:
            values.append(text)

    def _chain_node(self, chain: Dict[str, Any], node_type: str) -> Dict[str, Any]:
        for node in chain.get("chain", []):
            if node.get("node_type") == node_type:
                return node
        return {}

    def _chain_ade_name(self, chain: Dict[str, Any]) -> Optional[str]:
        return self._chain_node(chain, "ADE").get("name")

    def _is_key_mechanism(self, gene: Any, protein: Any, pathway: Any) -> bool:
        text = f"{gene or ''} {protein or ''} {pathway or ''}".upper()
        return any(
            token in text
            for token in ("KCNH2", "HERG", "ABCB11", "BSEP", "MITOCHONDR", "CYP", "HLA")
        )

    def _mechanism_driver(self, evidence_type: str, gene: Any, pathway: Any) -> str:
        gene_text = str(gene or "target")
        pathway_text = str(pathway or "pathway")
        if evidence_type == "direct_DTA":
            return f"Direct DTA support via {gene_text}"
        if evidence_type == "indirect_DTI_AT":
            return f"Drug-target and ADE-target overlap via {gene_text}"
        return f"Target-pathway inference via {gene_text} / {pathway_text}"

    def _drug_output(self, drug_info: DrugInfo, drug_card: Dict[str, Any]) -> DrugOutput:
        return DrugOutput(
            name=drug_card.get("canonical_name") or drug_info.name,
            smiles=drug_info.smiles or drug_card.get("smiles"),
            drugbank_id=drug_card.get("drug_id") or drug_info.drugbank_id,
        )

    def _impact_map(self, impacts: List[Dict[str, Any]]) -> Dict[str, float]:
        mapped: Dict[str, float] = {}
        for impact in impacts:
            organ = impact.get("organ_system")
            if organ:
                mapped[organ] = mapped.get(organ, 1.0) * float(impact.get("magnitude", 1.0))
        return mapped

    def _population_signal_map(self, persade_result: Dict[str, Any]) -> Dict[str, float]:
        mapped: Dict[str, float] = {}
        for signal in persade_result.get("signals", []):
            organ = signal.get("organ_system")
            if not organ:
                continue
            ror_lower = float(signal.get("ror_lower_ci") or 0.0)
            case_number = int(signal.get("case_number") or 0)
            support = 0.04
            if signal.get("priority") == "High":
                support += 0.04
            elif signal.get("priority") == "Medium":
                support += 0.02
            if signal.get("severity_grade") == "Critical":
                support += 0.03
            elif signal.get("severity_grade") == "Severe":
                support += 0.02
            if ror_lower >= 5:
                support += 0.03
            elif ror_lower >= 2:
                support += 0.02
            if case_number >= 50:
                support += 0.02
            elif case_number >= 10:
                support += 0.01
            mapped[organ] = max(mapped.get(organ, 0.0), min(0.14, support))
        return mapped

    def _structural_alerts_for_organ(self, tool_results: Dict[str, Any], organ: str) -> List[StructuralAlert]:
        alerts = []
        alerts.extend(tool_results.get("drug_card_lookup", {}).get("structural_alerts", []))
        alerts.extend(
            tool_results.get("admetsar_predict", {})
            .get("structure_profile", {})
            .get("structural_alerts", [])
        )
        selected: List[StructuralAlert] = []
        seen_alerts = set()
        for alert in alerts:
            alert_organs = self._alert_organs(alert)
            if organ not in alert_organs and "all" not in alert_organs:
                continue
            key = (alert.get("alert"), alert.get("smarts"))
            if key in seen_alerts:
                continue
            seen_alerts.add(key)
            selected.append(
                StructuralAlert(
                    alert=alert.get("alert", "unspecified structural alert"),
                    smarts=alert.get("smarts", ""),
                    atoms=alert.get("atoms", alert.get("matched_atoms", [])),
                    contribution=float(alert.get("contribution", 0.15)),
                )
            )
        return selected

    def _alert_organs(self, alert: Dict[str, Any]) -> set[str]:
        if alert.get("organ_system"):
            return {alert["organ_system"]}
        if alert.get("organ_systems"):
            return set(alert["organ_systems"])
        relevance = str(alert.get("toxicity_relevance", "")).lower()
        if "hepato" in relevance:
            return {"liver"}
        if "cardio" in relevance:
            return {"heart"}
        if "mutagen" in relevance:
            return {"liver", "hematologic"}
        if "hemato" in relevance:
            return {"hematologic"}
        if "immun" in relevance:
            return {"immune", "skin"}
        if "reactiv" in relevance:
            return {"liver", "skin", "immune"}
        return {"all"}

    def _property_attributions_for_organ(
        self,
        admet_result: Dict[str, Any],
        organ: str,
    ) -> List[PropertyAttribution]:
        endpoints = admet_result.get("property_endpoints", [])
        selected: List[PropertyAttribution] = []
        for endpoint in endpoints:
            if endpoint.get("organ_system") not in {organ, "all"}:
                continue
            selected.append(
                PropertyAttribution(
                    feature=endpoint.get("feature", "unknown_admet_endpoint"),
                    value=float(endpoint.get("value", 0.0)),
                    contribution=float(endpoint.get("contribution", 0.0)),
                )
            )
        return selected

    def _admet_endpoints_for_organ(
        self,
        admet_profile: List[Dict[str, Any]],
        organ: str,
    ) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        for item in admet_profile or []:
            value = item.get("value")
            if value in {None, "", "unknown"}:
                continue
            mapped_organ = self._organ_from_soc_or_term(item.get("soc"), item.get("endpoint"))
            if mapped_organ != organ:
                continue
            selected.append(
                {
                    "endpoint": item.get("endpoint"),
                    "value": value,
                    "endpoint_type": item.get("endpoint_type"),
                    "mechanism_group": item.get("mechanism_group"),
                    "soc": item.get("soc"),
                    "evidence_role": item.get("evidence_role", "model_feature"),
                }
            )
            if len(selected) >= 12:
                break
        return selected

    def _target_pathway_for_organ(
        self,
        mechanism_chains: List[MechanismChain],
        raw_mechanism_chains: Optional[List[Dict[str, Any]]] = None,
        organ: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        target_pathway: List[Dict[str, Any]] = []
        if organ:
            for raw_chain in self._raw_mechanism_chains_for_organ(raw_mechanism_chains or [], organ)[:5]:
                target = self._chain_node(raw_chain, "Target")
                pathway = self._chain_node(raw_chain, "Pathway")
                ade = self._chain_node(raw_chain, "ADE")
                target_pathway.append(
                    {
                        "source": "PersADE DTA/DTI/AT/Pathway",
                        "evidence_type": raw_chain.get("evidence_type"),
                        "target": {
                            "id": target.get("id"),
                            "gene": target.get("gene"),
                            "protein": target.get("protein"),
                        },
                        "pathway": {
                            "id": pathway.get("id"),
                            "name": pathway.get("name"),
                        },
                        "ade": {
                            "id": ade.get("id") or raw_chain.get("ade_id"),
                            "name": ade.get("name"),
                        },
                        "pubmed": raw_chain.get("pubmed", []),
                    }
                )
            if target_pathway:
                return target_pathway

        if mechanism_chains:
            chain = mechanism_chains[0]
            target_pathway.append(
                {
                    "source": "ToxicityChainBuilder fallback",
                    "evidence_type": "fallback_unknown",
                    "target": {"id": None, "gene": None, "protein": None},
                    "pathway": {"id": None, "name": None},
                    "ade": {"id": None, "name": chain.soc},
                    "pubmed": [],
                    "summary": "No local PersADE target/pathway chain mapped to this SOC; fallback chain is retained internally for Stage 2 modifier mapping.",
                }
            )
        return target_pathway

    def _raw_mechanism_chains_for_organ(
        self,
        raw_mechanism_chains: List[Dict[str, Any]],
        organ: str,
    ) -> List[Dict[str, Any]]:
        evidence_rank = {"direct_DTA": 3, "indirect_DTI_AT": 2, "pathway_inferred": 1}
        selected: List[Dict[str, Any]] = []
        seen = set()
        for chain in raw_mechanism_chains or []:
            ade = self._chain_node(chain, "ADE")
            mapped_organ = self._organ_from_soc_or_term(chain.get("soc"), ade.get("name"))
            if mapped_organ != organ:
                continue
            target = self._chain_node(chain, "Target")
            pathway = self._chain_node(chain, "Pathway")
            key = (
                chain.get("evidence_type"),
                ade.get("id") or chain.get("ade_id"),
                target.get("id"),
                pathway.get("id"),
            )
            if key in seen:
                continue
            selected.append(chain)
            seen.add(key)
        return sorted(
            selected,
            key=lambda item: (
                evidence_rank.get(item.get("evidence_type"), 0),
                len(item.get("pubmed", [])),
            ),
            reverse=True,
        )

    def _local_mechanism_summary(
        self,
        raw_mechanism_chains: List[Dict[str, Any]],
        organ: str,
    ) -> str:
        chains = self._raw_mechanism_chains_for_organ(raw_mechanism_chains, organ)
        if not chains:
            return ""
        best = chains[0]
        target = self._chain_node(best, "Target")
        pathway = self._chain_node(best, "Pathway")
        ade = self._chain_node(best, "ADE")
        target_name = target.get("gene") or target.get("id") or "target"
        pathway_name = pathway.get("name") or pathway.get("id") or "pathway"
        ade_name = ade.get("name") or best.get("ade_id") or "ADE"
        return (
            "Local PersADE mechanism support: "
            f"{target_name} -> {pathway_name} -> {ade_name} "
            f"({best.get('evidence_type', 'mechanism_evidence')})."
        )

    def _mechanism_modifiers_for_factors(
        self,
        organ_factors: List[PatientAttribution],
        baseline_item: GeneralToxicityItem,
    ) -> List[MechanismChainModifier]:
        chains = baseline_item.attribution.mechanism_chains
        if not chains:
            return []
        best_chain = max(chains, key=lambda chain: chain.chain_score)
        modifiers: List[MechanismChainModifier] = []
        for factor in organ_factors:
            affected_node = factor.affected_node or "organ_toxicity_phenotype"
            direction = "increase" if factor.direction == "up" else "decrease"
            modifiers.append(
                MechanismChainModifier(
                    chain_id=best_chain.chain_id,
                    affected_node=affected_node,  # type: ignore[arg-type]
                    factor=factor.factor,
                    direction=direction,
                    magnitude=factor.magnitude,
                    effect=factor.effect or self._infer_modifier_effect(
                        factor.factor,
                        affected_node,
                        factor.direction,
                    ),
                    rule_id=factor.rule_id,
                    evidence=MechanismEvidence(
                        source=factor.evidence.source,
                        tier=factor.evidence.tier,
                        ref=factor.evidence.grade or factor.rule_id,
                    ),
                )
            )
        return modifiers

    def _infer_affected_node(self, factor_type: str, factor: str, rule_id: str) -> str:
        text = f"{factor_type} {factor} {rule_id}".upper()
        if "HLA" in text or "VKORC1" in text:
            return "target_binding"
        if "NSAID" in text or "ANTIPLATELET" in text:
            return "pathway_perturbation"
        if "CYP" in text or "UGT" in text or "DDI" in text or "HEPATIC" in text:
            return "metabolism"
        if "EGFR" in text or "RENAL" in text:
            return "active_or_toxic_species"
        return "organ_toxicity_phenotype"

    def _infer_modifier_effect(self, factor: str, affected_node: str, direction: str) -> str:
        direction_text = "increases" if direction == "up" else "decreases"
        if affected_node == "metabolism":
            return f"{factor} {direction_text} exposure by modifying metabolic clearance or activation."
        if affected_node == "active_or_toxic_species":
            return f"{factor} {direction_text} active/toxic species exposure or clearance vulnerability."
        if affected_node == "target_binding":
            return f"{factor} {direction_text} pharmacodynamic or immune target sensitivity."
        if affected_node == "pathway_perturbation":
            return f"{factor} {direction_text} downstream pathway susceptibility."
        return f"{factor} {direction_text} organ-level vulnerability to the terminal toxicity phenotype."

    def _evidence_for_organ(
        self,
        evidence_package: EvidencePackage,
        organ: str,
    ) -> List[GeneralToxicityEvidence]:
        selected: List[GeneralToxicityEvidence] = []
        seen = set()

        for item in evidence_package.evidence_items:
            if not self._evidence_item_matches_organ(item.payload, item.finding, item.tool_name, organ):
                continue
            citations = item.citations or [
                EvidenceCitation(
                    source=item.tool_name,
                    version="simulated",
                    year=2026,
                    evidence_level=item.evidence_level,
                    summary=item.finding,
                )
            ]
            for citation in citations:
                source = citation.source or item.tool_name
                tier = EVIDENCE_TIER_BY_LEVEL.get(item.evidence_level, 5)
                ref = self._evidence_ref(citation, item.finding)
                key = (source, tier, ref)
                if key in seen:
                    continue
                selected.append(GeneralToxicityEvidence(source=source, tier=tier, ref=ref))
                seen.add(key)

        if not selected:
            selected.append(
                GeneralToxicityEvidence(
                    source="PersAgent baseline fallback",
                    tier=5,
                    ref=f"No organ-specific evidence item matched {organ}; retained baseline estimate.",
                )
            )
        return selected

    def _evidence_item_matches_organ(
        self,
        payload: Dict[str, Any],
        finding: str,
        tool_name: str,
        organ: str,
    ) -> bool:
        if payload.get("organ_system") == organ:
            return True
        for impact in payload.get("organ_impacts", []):
            if impact.get("organ_system") == organ:
                return True
        if organ in finding.lower():
            return True
        return tool_name in {"drug_card_lookup", "admetsar_predict"}

    def _evidence_ref(self, citation: EvidenceCitation, finding: str) -> str:
        parts = []
        if citation.version:
            parts.append(str(citation.version))
        if citation.year:
            parts.append(str(citation.year))
        if citation.pmid:
            parts.append(f"PMID:{citation.pmid}")
        if citation.url:
            parts.append(str(citation.url))
        if citation.summary:
            parts.append(citation.summary)
        elif finding:
            parts.append(finding)
        return "; ".join(parts)

    def _attribution_product(self, factors: List[PatientAttribution]) -> float:
        product = 1.0
        for factor in factors:
            if factor.direction == "down":
                product *= max(0.1, 1.0 / factor.magnitude) if factor.magnitude >= 1 else factor.magnitude
            else:
                product *= factor.magnitude
        return round(product, 3)

    def _risk_level(self, probability: float) -> str:
        if probability >= 0.65:
            return "high"
        if probability >= 0.35:
            return "moderate"
        return "low"

    def _risk_to_ctcae_grade(self, probability: float) -> int:
        if probability < 0.20:
            return 1
        if probability < 0.40:
            return 2
        if probability < 0.65:
            return 3
        return 4

    def _reasoning_summary(
        self,
        universal_report: UniversalToxicityReport,
        personalized_report: PersonalizedToxicityReport,
        conflicts: List[str],
    ) -> List[str]:
        modeled_items = [
            item
            for item in personalized_report.personalized_toxicity
            if item.personalized_probability is not None and item.baseline.probability is not None
        ]
        dominant = sorted(
            modeled_items,
            key=lambda item: item.personalized_probability or 0.0,
            reverse=True,
        )[:3]
        summaries = [
            "Universal toxicity is represented as SOC-level baseline probability with structural, ADMET property, and mechanism attribution.",
            "Personalized toxicity is calculated as baseline probability multiplied by patient attribution magnitudes.",
            "Current modeled organ scope is liver and heart; other SOC rows are retained as null placeholders.",
        ]
        for item in dominant:
            factors = ", ".join(f.factor for f in item.patient_attribution) or "no patient-specific modifiers"
            summaries.append(
                f"{item.soc}: baseline {item.baseline.probability:.2f} -> "
                f"personalized {item.personalized_probability:.2f}; "
                f"CTCAE grade {item.ctcae_grade_predicted}; modifiers: {factors}."
            )
        if conflicts:
            summaries.append(f"Conflicts flagged: {' | '.join(conflicts)}")
        return summaries

    def _default_organ_attribution(
        self,
        organ: str,
        drug_info: DrugInfo,
        drug_card: Dict[str, Any],
    ) -> str:
        if drug_info.target_description and not (drug_info.smiles or drug_card.get("smiles")):
            return f"Biologic/target-mechanism attribution from {drug_info.target_description}."
        return f"No dominant {organ} mechanism signal from current local evidence; risk remains monitored."

    def _clamp(self, value: float, lower: float = 0.0, upper: float = 1.0) -> float:
        return round(max(lower, min(upper, value)), 3)

