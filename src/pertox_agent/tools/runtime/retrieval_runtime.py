"""PersAgent runtime adapter over the canonical ``tool`` package.

Agent nodes call the functions in this module, and this module delegates every
knowledge operation to the existing PersAgent tools under ``tool/``. It keeps
report-oriented payload shaping close to the current tool implementations
without preserving the previous project's separate tool layer.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from html import unescape
from typing import Any, Dict, List, Optional

from pertox_agent.tools.real_world_evidence import persade_drug_ade_profile as _profile_tool
from pertox_agent.tools.real_world_evidence import persade_similar_case_retrieval as _context_tool
from pertox_agent.tools.real_world_evidence import persade_subgroup_risk as _subgroup_tool
from pertox_agent.tools.shared.common import NORMALIZED, resolve_drug
from pertox_agent.tools.molecular_evidence import admet_predictor as _admet_tool
from pertox_agent.tools.molecular_evidence import drug_drug_interaction as _ddi_tool
from pertox_agent.tools.molecular_evidence import drug_metabolism as _metabolism_tool
from pertox_agent.tools.molecular_evidence import drug_target_interaction as _dti_tool
from pertox_agent.tools.molecular_evidence import mechanism_evidence as _mechanism_tool
from pertox_agent.tools.molecular_evidence import pathway_enrichment as _pathway_tool


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _citation(source: str, level: str, summary: str) -> Dict[str, Any]:
    return {
        "source": source,
        "version": "PersAgent-local",
        "year": 2026,
        "evidence_level": level,
        "summary": summary,
    }


def _payload(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if drug_name:
        data["drug"] = drug_name
        data["name"] = drug_name
    if drugbank_id:
        data["drugbank_id"] = drugbank_id
    if smiles:
        data["smiles"] = smiles
    if inchi_key:
        data["inchi_key"] = inchi_key
    data.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
    data.setdefault("drug", drugbank_id or inchi_key or smiles or drug_name or "unknown")
    return data


def _resolve(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    return resolve_drug(_payload(drug_name, drugbank_id, smiles, inchi_key))


def _entity(ent: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "primary_name": ent.get("name"),
        "inchi_key": ent.get("inchi_key"),
        "smiles": ent.get("smiles"),
        "drugbank_id": ent.get("drugbank_id"),
        "chembl_id": ent.get("chembl_id"),
        "pubchem_id": ent.get("pubchem_id"),
        "atc": ent.get("atc"),
        "drug_type": ent.get("drug_type"),
    }


def _drug_ref(ent: Dict[str, Any]) -> str:
    return ent.get("drugbank_id") or ent.get("inchi_key") or ent.get("name") or ent.get("input") or "unknown"


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _tokens(value: Any) -> List[str]:
    raw = value if isinstance(value, list) else re.split(r"[|;,/\s]+", str(value or ""))
    out: List[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _plain_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _organ(*values: Any) -> Optional[str]:
    text = " ".join(str(value or "") for value in values).lower()
    mapping = (
        ("liver", ("hepatic", "hepat", "liver", "bilirubin", "cholest", "digestive/hepatobiliary")),
        ("heart", ("cardiac", "cardio", "myocard", "arrhythm", "qt", "herg", "torsade")),
        ("kidney", ("renal", "kidney", "neph", "urinary", "bladder", "urogenital")),
        ("hematologic", ("blood", "lymph", "haemorr", "hemorr", "bleed", "anemia", "anaemia", "thromb", "coagul", "inr")),
        ("immune", ("immune", "allerg", "anaphyl", "hypersens")),
        ("skin", ("skin", "rash", "dermat", "prurit", "urticaria")),
        ("neurologic", ("nervous", "neuro", "brain", "seizure", "dizziness", "headache", "stroke")),
        ("gastrointestinal", ("gastro", "nausea", "vomit", "diarr", "abdom", "intestinal")),
    )
    for organ, keywords in mapping:
        if any(keyword in text for keyword in keywords):
            return organ
    return None


def _soc(*values: Any) -> Optional[str]:
    organ = _organ(*values)
    return SOC_BY_ORGAN.get(organ) if organ else None


def _positive(value: Any) -> bool:
    numeric = _to_float(value)
    if numeric is not None:
        return numeric >= 0.5
    return str(value).strip().lower() in {
        "positive",
        "active",
        "yes",
        "true",
        "toxic",
        "inhibitor",
        "substrate",
        "accept",
    }


def _endpoint_type(endpoint: str, value: Any) -> str:
    if value in (None, "", "unknown"):
        return "unknown"
    if _to_float(value) is not None:
        return "numeric"
    if any(token in endpoint.lower() for token in ("_r_", "regress", "raw", "vdss", "logp", "pka", "half", "ppb")):
        return "numeric"
    return "classification"


def _structure_alerts(smiles: Optional[str]) -> List[Dict[str, Any]]:
    if not smiles:
        return []
    definitions = (
        ("coumarin-like lactone", "c1ccc2oc(=O)ccc2c1", ["hematologic", "liver"], (r"O=C\dO", r"OC\d=O", r"O=C.*O.*c")),
        ("Michael acceptor", "C=CC(=O)", ["liver", "skin", "immune"], (r"C=CC\(=O\)", r"C=C.*C\(=O\)")),
        ("nitro group", "[$([NX3](=O)=O),$([NX3+](=O)[O-])]", ["liver", "hematologic"], (r"\[N\+\]\(=O\)\[O-\]", r"N\(=O\)=O")),
    )
    alerts = []
    for alert, smarts, organs, patterns in definitions:
        if any(re.search(pattern, smiles) for pattern in patterns):
            alerts.append(
                {
                    "alert": alert,
                    "smarts": smarts,
                    "organ_systems": organs,
                    "toxicity_relevance": ",".join(organs),
                    "contribution": 0.12,
                }
            )
    return alerts


def _admet_profile(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    profile = []
    for item in raw.get("endpoints", []):
        endpoint = item.get("endpoint")
        value = item.get("value")
        profile.append(
            {
                "endpoint": endpoint,
                "value": value if value not in (None, "") else "unknown",
                "endpoint_type": _endpoint_type(str(endpoint or ""), value),
                "mechanism_group": item.get("mechanism_group") or "admet_other",
                "soc": _soc(item.get("organ"), item.get("mechanism_group"), endpoint),
                "evidence_role": "model_feature",
            }
        )
    return profile


def _property_endpoints(raw: Dict[str, Any], profile: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    endpoints = []
    for feature, value in (raw.get("descriptors") or {}).items():
        numeric = _to_float(value)
        if numeric is not None:
            endpoints.append(
                {
                    "feature": feature,
                    "value": numeric,
                    "organ_system": "all",
                    "contribution": 0.05 if feature in {"MW", "TPSA", "QED"} else 0.08,
                }
            )
    for item in profile:
        organ = _organ(item.get("soc"), item.get("endpoint"))
        numeric = _to_float(item.get("value"), 1.0)
        if organ and numeric is not None and item.get("value") != "unknown" and _positive(item.get("value")):
            endpoints.append(
                {
                    "feature": item.get("endpoint"),
                    "value": numeric,
                    "organ_system": organ,
                    "contribution": 0.12 if item.get("endpoint_type") == "classification" else 0.08,
                }
            )
    return endpoints


def _admet_impacts(profile: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    impacts: Dict[str, float] = {}
    for item in profile:
        organ = _organ(item.get("soc"), item.get("endpoint"))
        if organ and item.get("value") != "unknown" and _positive(item.get("value")):
            impacts[organ] = max(impacts.get(organ, 1.0), 1.08)
    return [{"organ_system": organ, "direction": "increase", "magnitude": magnitude} for organ, magnitude in impacts.items()]


def drug_card_lookup(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    record: Dict[str, Any] = {}
    if ent.get("drugbank_id"):
        try:
            from pertox_agent.tools.shared.common import _drugbank

            record = _drugbank().get_record(ent["drugbank_id"]) or {}
        except Exception:
            record = {}
    matched = bool(ent.get("inchi_key") or ent.get("drugbank_id") or ent.get("name"))
    return {
        "tool": "drug_card_lookup",
        "timestamp": _now_iso(),
        "evidence_level": "DrugCard",
        "source": "PersAgent DrugBank JSONL + PersADE drug_all",
        "matched": matched,
        "drug_entity": _entity(ent),
        "drug_id": ent.get("drugbank_id") or ent.get("inchi_key") or "UNKNOWN",
        "canonical_name": ent.get("name") or drug_name,
        "smiles": ent.get("smiles") or smiles,
        "inchi_key": ent.get("inchi_key") or inchi_key,
        "drugbank_id": ent.get("drugbank_id") or drugbank_id,
        "atc": ent.get("atc"),
        "description": record.get("description"),
        "indication": record.get("indication"),
        "targets": record.get("targets", []),
        "mechanism_chain": record.get("mechanism_of_action") or record.get("pharmacodynamics") or "Drug resolved by PersAgent local identity tools.",
        "mechanism_chains": [],
        "structural_alerts": _structure_alerts(ent.get("smiles") or smiles),
        "black_box_warning": record.get("toxicity"),
        "citation": _citation("PersAgent DrugBank/PersADE", "DrugCard", "Drug identity and pharmacology resolved locally."),
    }


def admetsar_predict(
    drug_name: Optional[str] = None,
    smiles: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    raw = _admet_tool.run(_payload(drug_name, drugbank_id, smiles, inchi_key))
    profile = _admet_profile(raw)
    structure = {
        "descriptors": raw.get("descriptors", {}),
        "drug_likeness": raw.get("drug_likeness", {}),
        "structural_alerts": _structure_alerts(ent.get("smiles") or smiles),
    }
    return {
        "tool": "admetsar_predict",
        "timestamp": _now_iso(),
        "evidence_level": "ADMET",
        "source": "PersAgent admetSAR3",
        "matched": bool(raw.get("applicability_domain")),
        "applicability_domain": raw.get("applicability_domain"),
        "drug_entity": _entity(ent),
        "drug_id": ent.get("drugbank_id") or ent.get("inchi_key"),
        "structure_profile": structure,
        "profile": {"descriptors": raw.get("descriptors", {}), "endpoints": raw.get("endpoints", [])},
        "descriptors": raw.get("descriptors", {}),
        "admet_endpoints": raw.get("endpoints", []),
        "admet_profile": profile,
        "property_endpoints": _property_endpoints(raw, profile),
        "structural_alerts": structure["structural_alerts"],
        "organ_impacts": _admet_impacts(profile),
        "citation": _citation("PersAgent admetSAR3", "ADMET", "ADMET endpoints retrieved through PersAgent."),
        "_persagent_raw": raw,
    }


def dti_query(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    raw = _dti_tool.run(_payload(drug_name, drugbank_id, smiles, inchi_key))
    targets = []
    for item in raw.get("targets", []):
        label = item.get("gene") or item.get("name") or item.get("uniprot_id")
        targets.append({**item, "target": label, "target_name": item.get("name"), "affinity": item.get("affinity") or item.get("affect")})
    return {
        **raw,
        "tool": "dti_query",
        "timestamp": _now_iso(),
        "evidence_level": "P2",
        "drug_entity": _entity(ent),
        "drug_id": ent.get("drugbank_id") or ent.get("inchi_key"),
        "targets": targets,
        "citation": _citation("PersAgent DrugBank/PersADE DTI", "P2", "Drug-target interactions retrieved locally."),
    }


def pathway_enrich(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    dti = dti_query(drug_name, drugbank_id, smiles, inchi_key)
    genes = sorted({item.get("gene") for item in dti.get("targets", []) if item.get("gene")})
    raw = _pathway_tool.run({"genes": genes, "max_results": 25}) if genes else {"pathways": [], "n_pathways_tested": 0}
    pathway_rows = raw.get("pathways", [])
    return {
        **raw,
        "tool": "pathway_enrich",
        "timestamp": _now_iso(),
        "evidence_level": "P2",
        "genes": genes,
        "pathways": [row.get("name") or row.get("pathway_id") for row in pathway_rows],
        "pathway_rows": pathway_rows,
        "citation": _citation("PersAgent pathway_enrich", "P2", "Pathway enrichment computed from retrieved target genes."),
    }


def _mechanism_chain(raw_chain: Dict[str, Any], drug_name: str) -> Optional[Dict[str, Any]]:
    ade = raw_chain.get("ade") or {}
    ade_name = ade.get("name") or ade.get("umls") or raw_chain.get("ade_id")
    organ = _organ(ade.get("organ_bucket"), ade_name)
    if not organ:
        return None
    soc = SOC_BY_ORGAN[organ]
    target = raw_chain.get("gene") or raw_chain.get("protein") or raw_chain.get("uniprot_id") or "target"
    pathway_id = (raw_chain.get("pathways_sample") or [None])[0]
    pathway = str(pathway_id).replace("_", " ") if pathway_id else "pathway not mapped"
    pubmed = _tokens(raw_chain.get("pubmed"))
    return {
        "chain_id": f"{drug_name.lower().replace(' ', '_')}_{organ}_{target}",
        "organ_system": organ,
        "organ_systems": [organ],
        "soc": soc,
        "ade_id": ade.get("umls") or raw_chain.get("ade_id"),
        "evidence_type": "direct_DTA",
        "pubmed": pubmed,
        "chain": [
            {"node_type": "Target", "id": raw_chain.get("uniprot_id"), "gene": raw_chain.get("gene"), "protein": raw_chain.get("protein")},
            {"node_type": "Pathway", "id": pathway_id, "name": pathway},
            {"node_type": "ADE", "id": ade.get("umls") or raw_chain.get("ade_id"), "name": ade_name},
        ],
        "nodes": [
            {"order": 1, "node_type": "drug_original", "label": drug_name, "role": "parent_drug", "confidence": 0.75, "description": "Drug resolved by PersAgent.", "evidence": [{"source": "PersAgent", "tier": 2, "ref": drug_name}]},
            {"order": 2, "node_type": "metabolism", "label": "DrugBank metabolism context", "role": "metabolism_context", "confidence": 0.35, "description": "Filled by metabolism tool when available.", "phase": ["other"], "enzymes": [], "evidence": []},
            {"order": 3, "node_type": "active_or_toxic_species", "label": "parent or unknown active/toxic species", "role": "exposure_context", "confidence": 0.30, "description": "No explicit metabolite in PersADE DTA row.", "species_type": "unknown_active_or_toxic_species", "evidence": []},
            {"order": 4, "node_type": "target_binding", "label": target, "role": "drug_target_ade_association", "confidence": 0.68, "description": "Target node from PersADE DTA.", "binding_role": "unknown", "target_type": "protein", "evidence": [{"source": "PersADE DTA", "tier": 2, "ref": ";".join(pubmed) or target}]},
            {"order": 5, "node_type": "pathway_perturbation", "label": pathway, "role": "target_pathway_context", "confidence": 0.55 if pathway_id else 0.20, "description": "Pathway mapped from PersADE uniprot_pathway.", "evidence": [{"source": "PersADE Pathway", "tier": 2, "ref": pathway_id or "not mapped"}] if pathway_id else []},
            {"order": 6, "node_type": "organ_toxicity_phenotype", "label": ade_name or soc, "role": "ADE phenotype", "confidence": 0.62, "description": f"ADE phenotype mapped to {soc}.", "organ_system": organ, "soc": soc, "evidence": [{"source": "PersADE ADE_Information", "tier": 2, "ref": ade.get("umls") or ade_name or soc}]},
        ],
        "summary": f"{drug_name} -> {target} -> {pathway} -> {ade_name or soc}",
        "chain_score": 0.68 if pathway_id else 0.56,
        "chain_confidence": 0.62 if pathway_id else 0.48,
        "evidence": [{"source": "PersADE DTA/Pathway", "tier": 2, "ref": ";".join(pubmed) or "local DTA row"}],
        "_persagent_raw": raw_chain,
    }


def mechanism_query(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    raw = _mechanism_tool.run({"drug": _drug_ref(ent)})
    chains = [chain for item in raw.get("mechanism_chains", []) if (chain := _mechanism_chain(item, ent.get("name") or drug_name or "drug"))]
    organ_counts: Dict[str, int] = {}
    for chain in chains:
        organ_counts[chain["organ_system"]] = organ_counts.get(chain["organ_system"], 0) + 1
    return {
        **raw,
        "tool": "mechanism_query",
        "timestamp": _now_iso(),
        "evidence_level": "P2",
        "source": "PersAgent mechanism_query",
        "drug_entity": _entity(ent),
        "drug_id": ent.get("drugbank_id") or ent.get("inchi_key"),
        "mechanism_chain": f"{len(chains)} PersADE DTA/target/pathway chains retrieved.",
        "mechanism_chains": chains,
        "organ_attributions": {organ: f"PersADE DTA returned {count} mechanism chains for {SOC_BY_ORGAN[organ]}." for organ, count in organ_counts.items()},
        "citation": _citation("PersAgent PersADE DTA/Pathway", "P2", "Mechanism chains normalized from PersAgent tools."),
        "_persagent_raw": raw,
    }


def mechanism_chains_lookup(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    result = mechanism_query(drug_name, drugbank_id, smiles, inchi_key)
    chains = result.get("mechanism_chains", [])
    return {
        "tool": "mechanism_chains_lookup",
        "timestamp": _now_iso(),
        "evidence_level": "DrugCard",
        "source": "PersAgent mechanism_query",
        "matched": bool(chains),
        "mechanism_chain_count": len(chains),
        "candidate_ade_count": len({chain.get("ade_id") for chain in chains if chain.get("ade_id")}),
        "evidence_counts": {"direct_DTA": len(chains), "indirect_DTI_AT": 0, "pathway_inferred": 0},
        "mechanism_chains": chains,
        "citation": _citation("PersAgent mechanism chains", "DrugCard", "Structured mechanism chains adapted from local evidence."),
    }


def persade_drug_profile(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    raw = _profile_tool.run({"drug": _drug_ref(ent), "top": 80})
    known = []
    for item in raw.get("top_ades", []):
        soc = _soc(item.get("organ"), item.get("ade_name"))
        organ = _organ(soc, item.get("ade_name"))
        known.append({**item, "soc": soc, "organ_system": organ, "source": "PersADE/FAERS", "evidence_level": "signal"})
    counts: Dict[str, int] = {}
    for item in known:
        if item.get("organ_system"):
            counts[item["organ_system"]] = counts.get(item["organ_system"], 0) + 1
    signals = []
    for item in known[:50]:
        signals.append(
            {
                "event": item.get("ade_name") or item.get("ade_id"),
                "organ_system": item.get("organ_system"),
                "soc": item.get("soc"),
                "ror": _to_float(item.get("ror"), 0.0),
                "ror_lower_ci": _to_float(item.get("ror_lower_ci"), 0.0),
                "case_number": _to_int(item.get("case_number")),
                "priority": item.get("priority"),
                "severity_grade": item.get("severity_grade"),
                "strength": "high" if item.get("priority") == "High" else "moderate",
                "source": "PersADE/FAERS",
            }
        )
    return {
        **raw,
        "tool": "persade_drug_profile",
        "timestamp": _now_iso(),
        "evidence_level": "P1",
        "source": "PersAgent PersADE drug profile",
        "drug_entity": _entity(ent),
        "drug_id": ent.get("drugbank_id") or ent.get("inchi_key"),
        "known_ade_profile": known,
        "signals": signals,
        "baseline_organ_scores": {organ: min(0.52, 0.12 + 0.04 * min(count, 6)) for organ, count in counts.items()},
        "organ_attributions": {organ: f"PersADE returned {count} high-priority ADE signals for {SOC_BY_ORGAN[organ]}." for organ, count in counts.items()},
        "citation": _citation("PersADE/FAERS signals", "P1", "Known ADE spectrum retrieved through PersAgent."),
        "_persagent_raw": raw,
    }


def drugbank_metabolism_query(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
) -> Dict[str, Any]:
    raw = _metabolism_tool.run(_payload(drug_name, drugbank_id, smiles, inchi_key))
    buckets = (raw.get("enzymes") or {}).get("buckets", {})
    primary = [gene for gene in buckets.get("substrate_of", []) if gene]
    secondary = [gene for key in ("inhibits", "induces", "other") for gene in buckets.get(key, []) if gene and gene not in primary]
    return {
        **raw,
        "tool": "drugbank_metabolism_query",
        "timestamp": _now_iso(),
        "evidence_level": "P2",
        "metabolism": {"primary_enzymes": primary, "secondary_enzymes": secondary, "elimination": raw.get("metabolism_text") or "; ".join(raw.get("elimination_routes", []))},
        "citation": _citation("PersAgent DrugBank metabolism", "P2", "Metabolism profile retrieved through PersAgent."),
        "_persagent_raw": raw,
    }


def _ddi_impacts(interaction: Dict[str, Any], target_name: Optional[str]) -> List[Dict[str, Any]]:
    text = " ".join(str(interaction.get(key) or "") for key in ("description", "advice", "mechanism_class")).lower()
    severity = str(interaction.get("severity") or "").lower()
    base = 1.35 if severity == "major" else 1.18 if severity == "moderate" else 1.08
    impacts = []
    if any(token in text for token in ("bleed", "hemorr", "haemorr", "anticoag", "inr")) or str(target_name or "").lower() == "warfarin":
        impacts.append({"organ_system": "hematologic", "direction": "increase", "magnitude": base})
    if any(token in text for token in ("qt", "arrhythm", "torsade")):
        impacts.append({"organ_system": "heart", "direction": "increase", "magnitude": base})
    if any(token in text for token in ("cyp", "metabolism", "serum concentration", "transporter")):
        impacts.append({"organ_system": "liver", "direction": "increase", "magnitude": max(1.10, base - 0.08)})
    if any(token in text for token in ("renal", "kidney", "nephro")):
        impacts.append({"organ_system": "kidney", "direction": "increase", "magnitude": base})
    return impacts or [{"organ_system": "liver", "direction": "increase", "magnitude": 1.08}]


def ddi_query(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    concomitant_medications: Optional[List[str]] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    raw = _ddi_tool.run({"drug": _drug_ref(ent), "co_medications": concomitant_medications or []})
    interactions = []
    for item in raw.get("interactions", []):
        severity = str(item.get("severity") or "Unknown").lower()
        interactions.append(
            {
                **item,
                "co_medication": item.get("co_drug"),
                "severity": severity,
                "mechanism": item.get("mechanism_class"),
                "clinical_effect": item.get("description") or item.get("advice") or "Interaction found in local DDI sources.",
                "management": item.get("advice"),
                "organ_impacts": _ddi_impacts({**item, "severity": severity}, ent.get("name")),
            }
        )
    return {
        **raw,
        "tool": "ddi_query",
        "timestamp": _now_iso(),
        "evidence_level": "P4",
        "drug_entity": _entity(ent),
        "drug_id": ent.get("drugbank_id") or ent.get("inchi_key"),
        "interactions": interactions,
        "citation": _citation("PersAgent DrugBank/DDInter2", "P4", "DDI evidence retrieved through PersAgent."),
        "_persagent_raw": raw,
    }


@lru_cache(maxsize=1)
def _dpwg_guidelines() -> List[Dict[str, Any]]:
    path = NORMALIZED / "dpwg" / "dpwg_guidelines.jsonl"
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _phenotype(gene: str, genotype: str) -> str:
    text = genotype.upper()
    if gene.upper().startswith("CYP2C9"):
        if "*3/*3" in text or "*2/*3" in text:
            return "poor_or_intermediate_metabolizer"
        if "*2" in text or "*3" in text:
            return "intermediate_metabolizer"
    if gene.upper() == "VKORC1" and any(token in text for token in ("AA", "TT", "-1639AA")):
        return "warfarin_sensitive"
    return "actionable_genotype"


def _pgx_magnitude(gene: str, genotype: str, drug_name: Optional[str]) -> float:
    text = f"{gene} {genotype} {drug_name or ''}".upper()
    if "WARFARIN" in text and ("*2/*3" in text or "*3/*3" in text):
        return 1.45
    if "WARFARIN" in text and ("CYP2C9" in text or "VKORC1" in text):
        return 1.25
    return 1.15


def _guideline_rank(source: Any) -> int:
    return {"CPIC": 4, "DPWG": 3, "CPNDS": 2, "RNPGX": 1}.get(str(source or "").upper(), 0)


def cpic_lookup(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    genotypes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    drug_lower = str(ent.get("name") or drug_name or "").lower()
    recommendations_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in _dpwg_guidelines():
        if drug_lower and drug_lower not in [str(item).lower() for item in row.get("drugs", [])]:
            continue
        row_genes = {str(item).upper() for item in row.get("genes", [])}
        for gene, genotype in (genotypes or {}).items():
            if gene.upper() in row_genes:
                key = (gene.upper(), str(genotype))
                candidate = {
                    "gene": gene.upper(),
                    "genotype": genotype,
                    "phenotype": _phenotype(gene, genotype),
                    "recommendation": _plain_text(row.get("summary")) or "Actionable pharmacogenomic guideline matched.",
                    "classification": row.get("source", "CPIC"),
                    "guideline_id": row.get("guideline_id"),
                    "organ_impacts": [
                        {
                            "organ_system": "hematologic" if drug_lower == "warfarin" else "liver",
                            "direction": "increase",
                            "magnitude": _pgx_magnitude(gene, genotype, ent.get("name")),
                        }
                    ],
                }
                current = recommendations_by_key.get(key)
                if current is None or _guideline_rank(candidate["classification"]) > _guideline_rank(current["classification"]):
                    recommendations_by_key[key] = candidate
    recommendations = list(recommendations_by_key.values())
    return {
        "tool": "cpic_lookup",
        "timestamp": _now_iso(),
        "evidence_level": "P3",
        "source": "PersAgent normalized CPIC/DPWG",
        "matched": bool(recommendations),
        "recommendations": recommendations,
        "citation": _citation("PersAgent CPIC/DPWG", "P3", "PGx recommendations matched against local guideline tables."),
    }


def hla_peptide_score(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    hla_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    drug_lower = str(ent.get("name") or drug_name or "").lower()
    scores = []
    for hla in hla_types or []:
        if drug_lower == "abacavir" and "HLA-B*57:01" in str(hla).upper():
            scores.append({"hla": hla, "risk": "very_high", "organ_impacts": [{"organ_system": "immune", "direction": "increase", "magnitude": 2.5}]})
    return {
        "tool": "hla_peptide_score",
        "timestamp": _now_iso(),
        "evidence_level": "P3",
        "source": "deterministic HLA safety rules",
        "matched": bool(scores),
        "scores": scores,
        "citation": _citation("HLA safety rules", "P3", "HLA risk scoring currently applies deterministic safety rules."),
    }


def persade_contextual_retrieval(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    patient_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    context = patient_context or {}
    exposure = context.get("exposure") or {}
    sex = str(context.get("sex") or "").lower()
    profile = {
        "age": context.get("age"),
        "sex": "F" if sex == "female" else "M" if sex == "male" else context.get("sex"),
        "route": exposure.get("route") or context.get("route"),
        "outcome": context.get("outcome"),
        "serious": context.get("serious"),
    }
    profile = {key: value for key, value in profile.items() if value not in (None, "", [], {})}
    raw = _context_tool.run({"drug": _drug_ref(ent), "patient": profile, "k": 100})
    snippets = []
    for item in raw.get("ade_distribution", [])[:8]:
        snippets.append(
            {
                "context": f"similar cohort ADE {item.get('ade_name')}",
                "finding": f"Among similar PersADE reports, {item.get('ade_name')} frequency={item.get('frequency')} ({item.get('count')} reports).",
                "organ_system": _organ(item.get("soc"), item.get("ade_name")),
                "evidence_level": "P2",
                "payload": item,
            }
        )
    return {
        **raw,
        "tool": "persade_contextual_retrieval",
        "timestamp": _now_iso(),
        "evidence_level": "P2",
        "source": "PersAgent PersADE contextual retrieval",
        "snippets": snippets,
        "citation": _citation("PersADE contextual retrieval", "P2", "Similar-patient ADE distribution retrieved through PersAgent."),
        "_persagent_raw": raw,
    }


def persade_subgroup_scores(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    candidate_ades: Optional[List[Any]] = None,
    subgroups: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Stage 2 Step 2: stratified population ADE risk vs the patient's subgroups."""
    ent = _resolve(drug_name, drugbank_id, smiles, inchi_key)
    raw = _subgroup_tool.run(
        {
            "inchi_key": ent.get("inchi_key") or inchi_key,
            "drug": _drug_ref(ent),
            "candidate_ades": candidate_ades or [],
            "subgroups": subgroups or {},
        }
    )
    evidence = raw.get("persade_contextual_evidence", [])
    return {
        **raw,
        "tool": "persade_subgroup_scores",
        "timestamp": _now_iso(),
        "evidence_level": "P2",
        "source": "PersAgent PersADE stratified score tables",
        "matched": bool(evidence),
        "drug_entity": _entity(ent),
        "drug_id": ent.get("drugbank_id") or ent.get("inchi_key"),
        "persade_contextual_evidence": evidence,
        "citation": _citation(
            "PersADE stratified score tables",
            "P2",
            "Subgroup (route/form/indication/age/sex) ADE risk vs overall, retrieved through PersAgent.",
        ),
    }


def similar_case_retrieval(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    patient_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "tool": "similar_case_retrieval",
        "timestamp": _now_iso(),
        "evidence_level": "P2",
        "source": "PersAgent case retrieval placeholder",
        "matched": False,
        "cases": [],
        "citation": _citation("PersAgent", "P2", "Dedicated case retrieval is not configured; contextual cohort retrieval is used instead."),
    }


def cohort_outcomes_query(
    drug_name: Optional[str] = None,
    drugbank_id: Optional[str] = None,
    smiles: Optional[str] = None,
    inchi_key: Optional[str] = None,
    patient_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    contextual = persade_contextual_retrieval(drug_name, drugbank_id, smiles, inchi_key, patient_context)
    findings = [
        {
            "finding": f"{item.get('ade_name')} appeared in {item.get('count')} similar-cohort reports.",
            "frequency": item.get("frequency"),
            "soc": item.get("soc"),
        }
        for item in contextual.get("ade_distribution", [])[:5]
    ]
    return {
        "tool": "cohort_outcomes_query",
        "timestamp": _now_iso(),
        "evidence_level": "P5",
        "source": "PersADE contextual cohort aggregate",
        "matched": bool(findings),
        "findings": findings,
        "citation": _citation("PersADE contextual cohort aggregate", "P5", "Cohort summaries aggregated from contextual retrieval."),
    }

