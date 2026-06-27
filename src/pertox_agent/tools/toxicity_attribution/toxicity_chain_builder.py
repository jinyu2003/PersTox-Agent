"""Build structured toxicity attribution chains from retrieved evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from pertox_agent.schemas import (
    DrugInfo,
    MechanismChain,
    MechanismChainNode,
    MechanismEvidence,
    MechanismNodeType,
)


REQUIRED_NODE_TYPES: List[MechanismNodeType] = [
    "drug_original",
    "metabolism",
    "active_or_toxic_species",
    "target_binding",
    "pathway_perturbation",
    "organ_toxicity_phenotype",
]


class ToxicityChainBuilder:
    """Converts retrieved evidence into the supported causal attribution template."""

    def build_for_organ(
        self,
        *,
        drug_info: DrugInfo,
        tool_results: Dict[str, Any],
        organ: str,
        soc: str,
    ) -> List[MechanismChain]:
        chains: List[MechanismChain] = []
        seen = set()

        for source_name in ("mechanism_query", "drug_card_lookup"):
            for raw_chain in tool_results.get(source_name, {}).get("mechanism_chains", []):
                organs = set(raw_chain.get("organ_systems") or [])
                raw_organ = raw_chain.get("organ_system")
                if raw_organ:
                    organs.add(raw_organ)
                if organ not in organs and "all" not in organs:
                    continue
                key = raw_chain.get("chain_id") or raw_chain.get("summary")
                if key in seen:
                    continue
                seen.add(key)
                chains.append(self._coerce_chain(raw_chain, organ, soc, drug_info))

        if chains:
            return chains

        return [self._fallback_chain(drug_info=drug_info, tool_results=tool_results, organ=organ, soc=soc)]

    def summarize(self, chains: Sequence[MechanismChain]) -> str:
        if not chains:
            return "No structured mechanism chain was built."
        best = max(chains, key=lambda chain: chain.chain_score)
        status = "complete" if best.chain_complete else f"incomplete; missing {', '.join(best.missing_nodes)}"
        return f"{best.summary} Structured chain status: {status}."

    def _coerce_chain(
        self,
        raw_chain: Dict[str, Any],
        organ: str,
        soc: str,
        drug_info: DrugInfo,
    ) -> MechanismChain:
        nodes: List[MechanismChainNode] = []
        for idx, raw_node in enumerate(raw_chain.get("nodes", []), start=1):
            evidence = [
                self._mechanism_evidence(item)
                for item in raw_node.get("evidence", [])
            ]
            nodes.append(
                MechanismChainNode(
                    order=int(raw_node.get("order", idx)),
                    node_type=raw_node["node_type"],
                    label=raw_node.get("label", raw_node["node_type"]),
                    role=raw_node.get("role"),
                    description=raw_node.get("description", ""),
                    confidence=float(raw_node.get("confidence", raw_chain.get("chain_confidence", 0.50))),
                    phase=raw_node.get("phase", []),
                    enzymes=raw_node.get("enzymes", []),
                    species_type=raw_node.get("species_type"),
                    binding_role=raw_node.get("binding_role"),
                    target_type=raw_node.get("target_type"),
                    organ_system=raw_node.get("organ_system"),
                    soc=raw_node.get("soc"),
                    evidence=evidence,
                )
            )

        nodes = self._ensure_required_nodes(nodes, drug_info, organ, soc)
        missing_nodes = self._missing_nodes(nodes)
        chain_score = float(raw_chain.get("chain_score", self._score_nodes(nodes, missing_nodes)))
        chain_confidence = float(raw_chain.get("chain_confidence", self._confidence(nodes, missing_nodes)))
        evidence = [
            self._mechanism_evidence(item)
            for item in raw_chain.get("evidence", [])
        ] or [
            MechanismEvidence(
                source="Local mechanism chain fallback",
                tier=5,
                ref=raw_chain.get("summary", "Structured mechanism chain without explicit citation."),
            )
        ]

        return MechanismChain(
            chain_id=raw_chain.get("chain_id") or self._chain_id(drug_info.name, organ),
            organ_system=organ,
            soc=soc,
            summary=raw_chain.get("summary") or self._summary_from_nodes(nodes),
            chain_complete=not missing_nodes,
            missing_nodes=missing_nodes,
            chain_score=round(max(0.0, min(1.0, chain_score)), 3),
            chain_confidence=round(max(0.0, min(1.0, chain_confidence)), 3),
            nodes=nodes,
            evidence=evidence,
        )

    def _fallback_chain(
        self,
        *,
        drug_info: DrugInfo,
        tool_results: Dict[str, Any],
        organ: str,
        soc: str,
    ) -> MechanismChain:
        metabolism = tool_results.get("drugbank_metabolism_query", {}).get("metabolism", {})
        primary = metabolism.get("primary_enzymes") or []
        secondary = metabolism.get("secondary_enzymes") or []
        targets = tool_results.get("dti_query", {}).get("targets", [])
        pathways = tool_results.get("pathway_enrich", {}).get("pathways", [])
        attributions = tool_results.get("mechanism_query", {}).get("organ_attributions", {})
        phenotype = attributions.get(organ, f"{soc} phenotype not mechanistically specified.")

        nodes = [
            MechanismChainNode(
                order=1,
                node_type="drug_original",
                label=drug_info.name,
                role="parent_drug",
                description="Input drug standardized by the drug card lookup.",
                confidence=0.75,
                evidence=[MechanismEvidence(source="Drug input", tier=5, ref=drug_info.drugbank_id or drug_info.name)],
            ),
            MechanismChainNode(
                order=2,
                node_type="metabolism",
                label=self._metabolism_label(primary, secondary),
                role="phase_I_or_phase_II",
                description=metabolism.get("elimination", "Metabolism evidence is limited."),
                confidence=0.65 if primary or secondary else 0.25,
                phase=self._phase_from_enzymes(primary + secondary),
                enzymes=primary + secondary,
                evidence=[
                    MechanismEvidence(
                        source="Local DrugBank metabolism",
                        tier=2,
                        ref=", ".join(primary + secondary) or "No metabolism enzyme evidence returned.",
                    )
                ],
            ),
            MechanismChainNode(
                order=3,
                node_type="active_or_toxic_species",
                label="unknown active or toxic species",
                role="unknown",
                description="No structured parent exposure or metabolite species was available for this organ.",
                confidence=0.20,
                species_type="unknown_active_or_toxic_species",
                evidence=[],
            ),
            MechanismChainNode(
                order=4,
                node_type="target_binding",
                label=self._target_label(targets),
                role="unknown",
                description="Target binding is inferred from the drug-level target profile.",
                confidence=0.55 if targets else 0.20,
                binding_role=self._binding_role(targets),
                target_type="protein" if targets else None,
                evidence=[
                    MechanismEvidence(
                        source="Local DTI Knowledge Base",
                        tier=2,
                        ref=self._target_label(targets),
                    )
                ] if targets else [],
            ),
            MechanismChainNode(
                order=5,
                node_type="pathway_perturbation",
                label=self._pathway_label(pathways),
                role="downstream_pathway",
                description="Pathway evidence is mapped from known targets and mechanism text.",
                confidence=0.55 if pathways else 0.20,
                evidence=[
                    MechanismEvidence(
                        source="Local Pathway Enrichment",
                        tier=2,
                        ref=self._pathway_label(pathways),
                    )
                ] if pathways else [],
            ),
            MechanismChainNode(
                order=6,
                node_type="organ_toxicity_phenotype",
                label=phenotype,
                role="SOC phenotype",
                description=f"Final toxicity phenotype mapped to {soc}.",
                confidence=0.50 if organ in attributions else 0.25,
                organ_system=organ,
                soc=soc,
                evidence=[],
            ),
        ]
        missing_nodes = self._missing_nodes(nodes)
        summary = self._summary_from_nodes(nodes)
        return MechanismChain(
            chain_id=self._chain_id(drug_info.name, organ),
            organ_system=organ,
            soc=soc,
            summary=summary,
            chain_complete=not missing_nodes,
            missing_nodes=missing_nodes,
            chain_score=self._score_nodes(nodes, missing_nodes),
            chain_confidence=self._confidence(nodes, missing_nodes),
            nodes=nodes,
            evidence=[
                MechanismEvidence(
                    source="PersAgent toxicity attribution chain builder",
                    tier=5,
                    ref="Fallback chain assembled from available metabolism, target, pathway, and SOC evidence.",
                )
            ],
        )

    def _ensure_required_nodes(
        self,
        nodes: List[MechanismChainNode],
        drug_info: DrugInfo,
        organ: str,
        soc: str,
    ) -> List[MechanismChainNode]:
        observed = {node.node_type for node in nodes}
        order = max([node.order for node in nodes], default=0)
        for node_type in REQUIRED_NODE_TYPES:
            if node_type in observed:
                continue
            order += 1
            nodes.append(
                MechanismChainNode(
                    order=order,
                    node_type=node_type,
                    label=f"unknown {node_type}",
                    role="missing",
                    description=f"No structured evidence was available for {node_type}.",
                    confidence=0.0,
                    species_type=(
                        "unknown_active_or_toxic_species"
                        if node_type == "active_or_toxic_species"
                        else None
                    ),
                    organ_system=organ if node_type == "organ_toxicity_phenotype" else None,
                    soc=soc if node_type == "organ_toxicity_phenotype" else None,
                )
            )
        for node in nodes:
            if node.node_type == "drug_original" and node.label.startswith("unknown"):
                node.label = drug_info.name
        return sorted(nodes, key=lambda node: node.order)

    def _missing_nodes(self, nodes: Sequence[MechanismChainNode]) -> List[MechanismNodeType]:
        missing: List[MechanismNodeType] = []
        by_type = {node.node_type: node for node in nodes}
        for node_type in REQUIRED_NODE_TYPES:
            node = by_type.get(node_type)
            if node is None or node.confidence <= 0.25 or node.role == "missing":
                missing.append(node_type)
        return missing

    def _score_nodes(self, nodes: Sequence[MechanismChainNode], missing_nodes: Sequence[str]) -> float:
        if not nodes:
            return 0.0
        mean_confidence = sum(node.confidence for node in nodes) / len(nodes)
        completeness = 1.0 - (len(missing_nodes) / len(REQUIRED_NODE_TYPES))
        return round(max(0.0, min(1.0, (0.65 * mean_confidence) + (0.35 * completeness))), 3)

    def _confidence(self, nodes: Sequence[MechanismChainNode], missing_nodes: Sequence[str]) -> float:
        if not nodes:
            return 0.0
        evidence_nodes = sum(1 for node in nodes if node.evidence)
        evidence_ratio = evidence_nodes / len(nodes)
        missing_penalty = 0.08 * len(missing_nodes)
        mean_confidence = sum(node.confidence for node in nodes) / len(nodes)
        return round(max(0.0, min(1.0, (0.60 * mean_confidence) + (0.40 * evidence_ratio) - missing_penalty)), 3)

    def _mechanism_evidence(self, raw: Dict[str, Any]) -> MechanismEvidence:
        return MechanismEvidence(
            source=raw.get("source", "Local mechanism evidence"),
            tier=int(raw.get("tier", 5)),
            ref=raw.get("ref", raw.get("summary", "")),
        )

    def _summary_from_nodes(self, nodes: Sequence[MechanismChainNode]) -> str:
        labels = [node.label for node in sorted(nodes, key=lambda item: item.order)]
        return " -> ".join(labels)

    def _metabolism_label(self, primary: Sequence[str], secondary: Sequence[str]) -> str:
        enzymes = list(primary) + list(secondary)
        return " / ".join(enzymes) if enzymes else "unknown I/II phase metabolism"

    def _phase_from_enzymes(self, enzymes: Sequence[str]) -> List[str]:
        phases = []
        if any(enzyme.upper().startswith("CYP") or enzyme.upper() in {"CES"} for enzyme in enzymes):
            phases.append("I")
        if any("UGT" in enzyme.upper() or enzyme.upper() in {"SULT", "GST", "NAT"} for enzyme in enzymes):
            phases.append("II")
        return phases or ["other"]

    def _target_label(self, targets: Sequence[Dict[str, Any]]) -> str:
        if not targets:
            return "unknown target binding"
        return " / ".join(str(target.get("target", "unknown target")) for target in targets[:3])

    def _binding_role(self, targets: Sequence[Dict[str, Any]]) -> str:
        if not targets:
            return "unknown"
        text = " ".join(str(target.get("affinity", "")) for target in targets).lower()
        if "off" in text:
            return "off_target"
        if "inhibition" in text or "pharmacologic" in text or "active" in text:
            return "on_target"
        return "unknown"

    def _pathway_label(self, pathways: Sequence[str]) -> str:
        if not pathways:
            return "unknown downstream pathway"
        return " / ".join(pathways[:3])

    def _chain_id(self, drug_name: str, organ: str) -> str:
        normalized = drug_name.lower().replace(" ", "_")
        return f"{normalized}_{organ}_mechanism_chain"

