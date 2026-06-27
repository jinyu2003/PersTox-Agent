"""Shared input-field schemas and deterministic fusion helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional

from pertox_agent.tools.clinical_input.drug_parser import normalize_drug_input


EMPTY_INPUT_VALUES = {"", "unknown", "unspecified", "not provided", "n/a", "na", "none", "null"}

DRUG_INFO_FIELDS = {
    "name",
    "drugbank_id",
    "inchi_key",
    "smiles",
    "target_description",
    "dose",
    "route",
    "frequency",
    "form",
    "known_toxicities",
}

PATIENT_INFO_FIELDS = {
    "patient_id",
    "age",
    "sex",
    "weight_kg",
    "alt_u_l",
    "ast_u_l",
    "bilirubin_mg_dl",
    "child_pugh",
    "creatinine_mg_dl",
    "egfr_ml_min",
    "genotypes",
    "hla_types",
    "medical_history",
    "concomitant_medications",
    "organ_function",
    "exposure",
    "pregnancy_status",
    "missing_modalities",
}

DRUG_INPUT_SCHEMA: Dict[str, Any] = {
    "name": "canonical drug name when stated or clearly inferable from the user text",
    "drugbank_id": "DrugBank ID only if present in the user text",
    "inchi_key": "InChIKey only if present in the user text",
    "smiles": "SMILES only if present in the user text",
    "target_description": "target or mechanism phrase from the user text, or null",
    "dose": "dose string, e.g. 5 mg/day; use unspecified if absent",
    "route": "route string, e.g. oral, intravenous; use unspecified if absent",
    "frequency": "frequency string, e.g. daily, twice daily; null if absent",
    "form": "dosage form string, e.g. tablet; null if absent",
    "known_toxicities": "array of toxicity terms mentioned by the user",
}

PATIENT_INPUT_SCHEMA: Dict[str, Any] = {
    "patient_id": "string, default anonymous",
    "age": "integer years; use 0 only when absent and mark age as missing",
    "sex": "female | male | other | unknown",
    "weight_kg": "number or null",
    "alt_u_l": "number or null",
    "ast_u_l": "number or null",
    "bilirubin_mg_dl": "number or null",
    "child_pugh": "A | B | C | null",
    "creatinine_mg_dl": "number or null",
    "egfr_ml_min": "number or null",
    "genotypes": "object like {'CYP2C9': '*2/*3'}",
    "hla_types": "array of strings",
    "medical_history": "array of strings",
    "concomitant_medications": "array of drug names",
    "organ_function": "object for extra organ function facts",
    "exposure": "object for exposure facts not belonging to the drug schema",
    "pregnancy_status": "pregnant | not_pregnant | unknown | null",
    "missing_modalities": "array of clinically relevant missing inputs",
}

_ORGAN_FUNCTION_FIELD_ALIASES = {
    "egfr_ml_min": ("egfr", "egfr_ml_min"),
    "alt_u_l": ("alt", "alt_u_l"),
    "ast_u_l": ("ast", "ast_u_l"),
    "bilirubin_mg_dl": ("bilirubin", "total_bilirubin", "bilirubin_mg_dl"),
    "creatinine_mg_dl": ("creatinine", "scr", "creatinine_mg_dl"),
}


def has_input_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in EMPTY_INPUT_VALUES
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def string_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        value = next((item for item in value if item not in (None, "")), None)
        if value is None:
            return None
    if isinstance(value, Mapping):
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text or text.lower() in EMPTY_INPUT_VALUES:
        return None
    return re.sub(r"\s+", " ", text)


def int_value(value: Any, *, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def float_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;|]", value)
    elif isinstance(value, Mapping):
        return []
    else:
        parts = list(value) if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in parts if str(item).strip()]


def normalize_sex(value: Any) -> str:
    text = (str(value).strip().lower() if value is not None else "")
    if text in {"female", "f", "woman", "girl"}:
        return "female"
    if text in {"male", "m", "man", "boy"}:
        return "male"
    if text in {"other", "nonbinary", "non-binary"}:
        return "other"
    return "unknown"


def normalize_pregnancy_status(value: Any) -> Optional[str]:
    text = (str(value).strip().lower() if value is not None else "")
    if text in {"pregnant", "yes", "true"}:
        return "pregnant"
    if text in {"not_pregnant", "not pregnant", "no", "false"}:
        return "not_pregnant"
    if text == "unknown":
        return "unknown"
    return None


def merge_payloads(
    base_payload: Dict[str, Any],
    overlay_payload: Optional[Mapping[str, Any]],
    *,
    fields: Iterable[str],
    aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Overlay non-empty semantic fields onto deterministic fields."""
    merged = dict(base_payload)
    if not isinstance(overlay_payload, Mapping):
        return merged
    aliases = aliases or {}
    overlay = dict(overlay_payload)
    for source, target in aliases.items():
        if source in overlay and target not in overlay:
            overlay[target] = overlay[source]
    for key in fields:
        value = overlay.get(key)
        if has_input_value(value):
            merged[key] = value
    return merged


def finalize_patient_payload(raw_payload: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {key: raw_payload.get(key) for key in PATIENT_INFO_FIELDS}
    organ_function = payload.get("organ_function")
    if isinstance(organ_function, Mapping):
        for target, aliases in _ORGAN_FUNCTION_FIELD_ALIASES.items():
            if has_input_value(payload.get(target)):
                continue
            value = _mapping_lookup(organ_function, aliases)
            if has_input_value(value):
                payload[target] = value

    payload["patient_id"] = string_value(payload.get("patient_id")) or "anonymous"
    payload["age"] = int_value(payload.get("age"), default=0)
    payload["sex"] = normalize_sex(payload.get("sex"))

    for field in ("weight_kg", "alt_u_l", "ast_u_l", "bilirubin_mg_dl", "creatinine_mg_dl", "egfr_ml_min"):
        payload[field] = float_or_none(payload.get(field))

    child_pugh = string_value(payload.get("child_pugh"))
    payload["child_pugh"] = child_pugh.upper() if child_pugh and child_pugh.upper() in {"A", "B", "C"} else None
    payload["genotypes"] = dict(payload.get("genotypes") or {}) if isinstance(payload.get("genotypes"), Mapping) else {}
    payload["hla_types"] = string_list(payload.get("hla_types"))
    payload["medical_history"] = string_list(payload.get("medical_history"))
    payload["concomitant_medications"] = string_list(payload.get("concomitant_medications"))
    payload["organ_function"] = dict(payload.get("organ_function") or {}) if isinstance(payload.get("organ_function"), Mapping) else {}
    payload["exposure"] = dict(payload.get("exposure") or {}) if isinstance(payload.get("exposure"), Mapping) else {}
    payload["pregnancy_status"] = normalize_pregnancy_status(payload.get("pregnancy_status"))

    missing = set(string_list(payload.get("missing_modalities")))
    if payload["age"] == 0:
        missing.add("age")
    payload["missing_modalities"] = sorted(missing)
    return payload


def _mapping_lookup(mapping: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    for alias in aliases:
        normalized_alias = _normalize_key(alias)
        for raw_key, value in mapping.items():
            if _normalize_key(str(raw_key)) == normalized_alias:
                return value
    return None


def _normalize_key(key: str) -> str:
    return re.sub(r"[\s_\-]+", "", key).lower()


def finalize_drug_payload(raw_input: Any, raw_payload: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {key: raw_payload.get(key) for key in DRUG_INFO_FIELDS}
    resolver_payload = {
        "drugbank_id": payload.get("drugbank_id"),
        "inchi_key": payload.get("inchi_key"),
        "smiles": payload.get("smiles"),
    }
    if not any(resolver_payload.values()):
        resolver_payload["name"] = payload.get("name")
    normalized = normalize_drug_input(resolver_payload if any(resolver_payload.values()) else raw_input)
    payload["name"] = normalized["drug_name"] or string_value(payload.get("name")) or "unknown"
    payload["drugbank_id"] = normalized["drugbank_id"] or string_value(payload.get("drugbank_id"))
    payload["inchi_key"] = normalized["inchi_key"] or string_value(payload.get("inchi_key"))
    payload["smiles"] = normalized["smiles"] or string_value(payload.get("smiles"))
    payload["dose"] = string_value(payload.get("dose")) or "unspecified"
    payload["route"] = string_value(payload.get("route")) or "unspecified"
    payload["frequency"] = string_value(payload.get("frequency"))
    payload["form"] = string_value(payload.get("form"))
    payload["target_description"] = string_value(payload.get("target_description"))
    payload["known_toxicities"] = string_list(payload.get("known_toxicities"))
    return payload

