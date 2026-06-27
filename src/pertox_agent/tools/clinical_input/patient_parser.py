"""Deterministic parsing for raw patient inputs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Dict, Iterable, Optional

from pertox_agent.tools.clinical_input.drug_parser import load_input_payload
from pertox_agent.tools.clinical_input.field_fusion import (
    PATIENT_INFO_FIELDS,
    finalize_patient_payload,
    float_or_none,
    has_input_value,
    normalize_sex,
)


_PATIENT_ALIASES = {
    "patient_id": ("patient_id", "patient id", "id"),
    "age": ("age", "age_years", "years"),
    "sex": ("sex", "gender"),
    "weight_kg": ("weight_kg", "weight", "body_weight"),
    "alt_u_l": ("alt_u_l", "alt", "alanine_aminotransferase"),
    "ast_u_l": ("ast_u_l", "ast", "aspartate_aminotransferase"),
    "bilirubin_mg_dl": ("bilirubin_mg_dl", "bilirubin", "total_bilirubin"),
    "child_pugh": ("child_pugh", "child-pugh", "child pugh"),
    "creatinine_mg_dl": ("creatinine_mg_dl", "creatinine", "scr"),
    "egfr_ml_min": ("egfr_ml_min", "egfr", "eGFR"),
    "genotypes": ("genotypes", "pgx", "pharmacogenomics"),
    "hla_types": ("hla_types", "hla"),
    "medical_history": ("medical_history", "diagnoses", "diagnosis", "indications", "indication"),
    "concomitant_medications": (
        "concomitant_medications",
        "comedications",
        "co_medications",
        "current_medications",
        "other_medications",
    ),
    "organ_function": ("organ_function", "organ function", "labs"),
    "exposure": ("exposure",),
    "pregnancy_status": ("pregnancy_status", "pregnancy"),
    "missing_modalities": ("missing_modalities", "missing"),
}

_ORGAN_FUNCTION_ALIASES = {
    "egfr_ml_min": ("egfr", "egfr_ml_min", "eGFR"),
    "alt_u_l": ("alt", "alt_u_l"),
    "ast_u_l": ("ast", "ast_u_l"),
    "bilirubin_mg_dl": ("bilirubin", "total_bilirubin", "bilirubin_mg_dl"),
    "creatinine_mg_dl": ("creatinine", "scr", "creatinine_mg_dl"),
}


def patient_info_kwargs_from_input(raw_input: Any) -> Dict[str, Any]:
    """Build ``PatientInfo`` keyword arguments from user input."""
    payload = load_input_payload(raw_input)
    if isinstance(payload, Mapping):
        raw_payload = _parse_mapping(payload)
    elif isinstance(payload, str):
        raw_payload = _parse_text(payload)
    else:
        raw_payload = {}
    return finalize_patient_payload(raw_payload)


def _parse_mapping(mapping: Mapping[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {key: None for key in PATIENT_INFO_FIELDS}
    for target, aliases in _PATIENT_ALIASES.items():
        value = _mapping_lookup(mapping, aliases)
        if has_input_value(value):
            payload[target] = value

    organ_function = payload.get("organ_function")
    if isinstance(organ_function, Mapping):
        for target, aliases in _ORGAN_FUNCTION_ALIASES.items():
            if has_input_value(payload.get(target)):
                continue
            value = _mapping_lookup(organ_function, aliases)
            if has_input_value(value):
                payload[target] = value

    return payload


def _parse_text(text: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {key: None for key in PATIENT_INFO_FIELDS}
    normalized = re.sub(r"\s+", " ", text).strip()
    payload["age"] = _extract_age(normalized)
    payload["sex"] = _extract_sex(normalized)
    payload["egfr_ml_min"] = _extract_labeled_number(normalized, ("eGFR", "egfr"))
    payload["alt_u_l"] = _extract_labeled_number(normalized, ("ALT", "alt"))
    payload["ast_u_l"] = _extract_labeled_number(normalized, ("AST", "ast"))
    payload["bilirubin_mg_dl"] = _extract_labeled_number(normalized, ("bilirubin", "TBIL"))
    payload["creatinine_mg_dl"] = _extract_labeled_number(normalized, ("creatinine", "Scr"))
    payload["organ_function"] = {
        key: value
        for key, value in {
            "eGFR": payload["egfr_ml_min"],
            "ALT": payload["alt_u_l"],
            "AST": payload["ast_u_l"],
            "bilirubin": payload["bilirubin_mg_dl"],
            "creatinine": payload["creatinine_mg_dl"],
            "LVEF": _extract_labeled_number(normalized, ("LVEF",)),
        }.items()
        if value is not None
    }
    genotypes = _extract_genotypes(normalized)
    if genotypes:
        payload["genotypes"] = genotypes
    return payload


def _mapping_lookup(mapping: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    for alias in aliases:
        value = _case_insensitive_get(mapping, alias)
        if value is not None:
            return value
    return None


def _case_insensitive_get(mapping: Mapping[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    normalized_key = _normalize_key(key)
    for raw_key, value in mapping.items():
        if _normalize_key(str(raw_key)) == normalized_key:
            return value
    return None


def _normalize_key(key: str) -> str:
    return re.sub(r"[\s_\-]+", "", key).lower()


def _extract_age(text: str) -> Optional[int]:
    patterns = (
        r"\b(?P<age>\d{1,3})\s*(?:years?\s*old|yr|yrs|yo|y/o)\b",
        r"\bage\s*[:=]?\s*(?P<age>\d{1,3})\b",
        r"\b(?P<age>\d{1,3})-year-old\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group("age"))
    return None


def _extract_sex(text: str) -> Optional[str]:
    for token in ("female", "woman", "girl", "male", "man", "boy", "other", "nonbinary", "non-binary"):
        if re.search(rf"\b{re.escape(token)}\b", text, flags=re.IGNORECASE):
            return normalize_sex(token)
    return None


def _extract_labeled_number(text: str, labels: Iterable[str]) -> Optional[float]:
    for label in labels:
        match = re.search(
            rf"\b{re.escape(label)}\b\s*(?:[:=]|is|of)?\s*(?P<value>\d+(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return float_or_none(match.group("value"))
    return None


def _extract_genotypes(text: str) -> Dict[str, str]:
    genes: Dict[str, str] = {}
    gene_pattern = r"(CYP\d[A-Z0-9]+|VKORC1|TPMT|NUDT15|DPYD|UGT1A1|SLCO1B1|HLA[-A-Z0-9]*)"
    diplotype_pattern = r"([*A-Za-z0-9.+>\-]+(?:/|[|])[*A-Za-z0-9.+>\-]+)"
    for match in re.finditer(rf"\b{gene_pattern}\b\s*(?:[:=])?\s*{diplotype_pattern}", text, flags=re.IGNORECASE):
        genes[match.group(1).upper()] = match.group(2)
    return genes


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="patient input -> PatientInfo kwargs")
    ap.add_argument("payload", nargs="+")
    args = ap.parse_args(argv)
    print(json.dumps(patient_info_kwargs_from_input(" ".join(args.payload)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

