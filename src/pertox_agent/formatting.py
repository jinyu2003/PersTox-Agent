"""Runtime formatting helpers for PersAgent reports."""


from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict


def to_plain_dict(value: Any) -> Any:
    """Convert Pydantic models and datetimes into JSON-ready Python values."""
    if hasattr(value, "model_dump"):
        return to_plain_dict(value.model_dump(mode="json"))
    if hasattr(value, "dict"):
        return to_plain_dict(value.dict())
    if isinstance(value, dict):
        return {key: to_plain_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain_dict(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def format_to_json(report: Dict[str, Any]) -> Dict[str, Any]:
    """Format the agent draft into the public JSON payload shape."""
    report_payload = to_plain_dict(report)
    payload = {
        "drug_entity": report_payload.get("drug_entity"),
        "patient_features": report_payload.get("patient_features"),
        "structure_profile": report_payload.get("structure_profile"),
        "admet_profile": report_payload.get("admet_profile"),
        "known_ade_profile": report_payload.get("known_ade_profile"),
        "mechanism_chains": report_payload.get("mechanism_chains"),
        "persade_contextual_evidence": report_payload.get("persade_contextual_evidence"),
        "baseline_organ_risk": report_payload.get("baseline_organ_risk"),
        "attribution_explanations": report_payload.get("attribution_explanations"),
        "universal_toxicity_report": report_payload.get("universal_report"),
        "personalized_toxicity_report": report_payload.get("personalized_report"),
        "verification_status": report_payload.get("verification_status"),
        "verification_report": report_payload.get("verification_report"),
        "final_decision": report_payload.get("final_decision"),
    }
    return {
        "content_type": "application/json",
        "payload": payload,
        "json": json.dumps(payload, ensure_ascii=False, indent=2),
    }

