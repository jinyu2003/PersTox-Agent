"""PersAgent input parsing backed by the canonical ``tool`` package."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Dict, Optional

from pertox_agent.tools.shared.common import resolve_drug


NORMALIZED_DRUG_KEYS = ("drug_name", "smiles", "drugbank_id", "inchi_key")

_PLACEHOLDER_VALUES = {
    "",
    "...",
    "null",
    "none",
    "n/a",
    "na",
    "unknown",
    "unspecified",
    "not provided",
}

_FIELD_ALIASES = {
    "drug_name": (
        "drug_name",
        "drug name",
        "drug",
        "target drug",
        "name",
        "generic_name",
        "generic name",
        "canonical_name",
        "canonical name",
        "medication",
        "medication_name",
        "medicine",
        "compound",
        "药物",
        "药名",
        "药品",
        "用药",
        "通用名",
    ),
    "smiles": (
        "smiles",
        "canonical_smiles",
        "canonical smiles",
        "isomeric_smiles",
        "isomeric smiles",
    ),
    "drugbank_id": (
        "drugbank_id",
        "drugbank id",
        "drugbank",
        "drug_bank_id",
        "drug bank id",
        "drugbankid",
    ),
    "inchi_key": (
        "inchi_key",
        "inchi key",
        "inchikey",
        "inchi-key",
    ),
}

_DRUG_CONTAINER_KEYS = ("drug", "drug_info", "raw_drug_info", "medication", "medication_info")

_DRUG_NAME_ALIASES = {
    "华法林": "warfarin",
    "华法令": "warfarin",
    "阿司匹林": "aspirin",
    "乙酰水杨酸": "aspirin",
    "布洛芬": "ibuprofen",
    "对乙酰氨基酚": "acetaminophen",
    "扑热息痛": "acetaminophen",
    "二甲双胍": "metformin",
    "胺碘酮": "amiodarone",
    "氯吡格雷": "clopidogrel",
    "辛伐他汀": "simvastatin",
    "阿托伐他汀": "atorvastatin",
}

_NATURAL_LANGUAGE_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "daily",
    "day",
    "dose",
    "drug",
    "for",
    "is",
    "medication",
    "medicine",
    "mg",
    "oral",
    "patient",
    "po",
    "route",
    "taking",
    "target",
    "the",
    "therapy",
    "using",
    "with",
}

_DOSE_UNITS = (
    "mg",
    "mcg",
    "ug",
    "μg",
    "g",
    "iu",
    "u",
    "unit",
    "units",
    "ml",
    "mL",
    "片",
    "粒",
    "丸",
    "支",
    "袋",
)


def normalize_drug_input(raw_input: Any) -> Dict[str, Optional[str]]:
    """Return canonical drug identifiers resolved through PersAgent ``tool``."""
    payload = load_input_payload(raw_input)
    normalized = _normalize_payload(payload)
    resolved = _resolve_with_current_tools(normalized, payload)
    if resolved.get("drug_name") and any(normalized.get(key) for key in ("smiles", "drugbank_id", "inchi_key")):
        normalized["drug_name"] = resolved["drug_name"]
    _merge_normalized(normalized, resolved)
    return {key: _clean_value(normalized.get(key), key) for key in NORMALIZED_DRUG_KEYS}


def load_input_payload(raw_input: Any) -> Any:
    """Load a JSON file/string when possible; otherwise keep the input value."""
    if isinstance(raw_input, Mapping):
        return raw_input

    if isinstance(raw_input, Path):
        return _load_path(raw_input)

    if isinstance(raw_input, str):
        possible_path = _coerce_possible_path(raw_input)
        if possible_path is not None and possible_path.is_file():
            return _load_path(possible_path)
        parsed = _parse_json_text(raw_input)
        return parsed if parsed is not None else raw_input

    return raw_input


def drug_info_kwargs_from_input(raw_input: Any) -> Dict[str, Any]:
    """Build ``DrugInfo`` keyword arguments from user input."""
    payload = load_input_payload(raw_input)
    source = _drug_source_mapping(payload)
    normalized = normalize_drug_input(payload)
    fallback_name = _clean_value(_mapping_lookup(source, ("name", "drug_name", "drug", "medication")), "drug_name")
    payload_text = _payload_to_text(payload)

    return {
        "name": normalized["drug_name"] or fallback_name or "unknown",
        "smiles": normalized["smiles"],
        "drugbank_id": normalized["drugbank_id"],
        "inchi_key": normalized["inchi_key"],
        "dose": _clean_value(_mapping_lookup(source, ("dose", "dosage", "剂量", "用量")), "dose")
        or _extract_dose(payload_text)
        or "unspecified",
        "route": _clean_value(_mapping_lookup(source, ("route", "administration_route", "给药途径", "途径")), "route")
        or _extract_route(payload_text)
        or "unspecified",
        "frequency": _clean_value(_mapping_lookup(source, ("frequency", "freq", "频率", "用药频率")), "frequency")
        or _extract_frequency(payload_text),
        "form": _clean_value(_mapping_lookup(source, ("form", "dosage_form", "剂型")), "form")
        or _extract_form(payload_text),
        "target_description": _clean_value(
            _mapping_lookup(source, ("target_description", "target", "mechanism")),
            "target_description",
        ),
        "known_toxicities": _known_toxicities(source),
    }


def _normalize_payload(payload: Any) -> Dict[str, Optional[str]]:
    normalized = _empty_normalized()
    if isinstance(payload, Mapping):
        _merge_normalized(normalized, _normalize_from_mapping(payload))
        _merge_normalized(normalized, _normalize_from_text(json.dumps(payload, ensure_ascii=False)))
    elif isinstance(payload, str):
        parsed = _parse_json_text(payload)
        if isinstance(parsed, Mapping):
            _merge_normalized(normalized, _normalize_from_mapping(parsed))
        _merge_normalized(normalized, _normalize_from_text(payload))
    return normalized


def _resolve_with_current_tools(
    normalized: Dict[str, Optional[str]],
    payload: Any,
) -> Dict[str, Optional[str]]:
    query = _resolver_query(normalized, payload)
    if not query:
        return _empty_normalized()
    try:
        entity = resolve_drug(query)
    except Exception:
        return _empty_normalized()

    return {
        "drug_name": _clean_value(entity.get("name"), "drug_name"),
        "smiles": _clean_value(entity.get("smiles"), "smiles"),
        "drugbank_id": _clean_value(entity.get("drugbank_id"), "drugbank_id"),
        "inchi_key": _clean_value(entity.get("inchi_key"), "inchi_key"),
    }


def _resolver_query(normalized: Dict[str, Optional[str]], payload: Any) -> Dict[str, str]:
    identifier_query: Dict[str, str] = {}
    for source_key, target_key in (
        ("smiles", "smiles"),
        ("drugbank_id", "drugbank_id"),
        ("inchi_key", "inchi_key"),
    ):
        value = _clean_value(normalized.get(source_key), source_key)
        if value:
            identifier_query[target_key] = value

    if identifier_query:
        return identifier_query

    drug_name = _clean_value(normalized.get("drug_name"), "drug_name")
    if drug_name:
        return {"name": drug_name}

    if isinstance(payload, str):
        value = _extract_resolvable_drug_name(payload) or _clean_value(payload, "drug_name")
        if value and "\n" not in value and len(value) <= 120:
            return {"drug": value}

    return {}


def _empty_normalized() -> Dict[str, Optional[str]]:
    return {key: None for key in NORMALIZED_DRUG_KEYS}


def _merge_normalized(target: Dict[str, Optional[str]], source: Dict[str, Optional[str]]) -> None:
    for key in NORMALIZED_DRUG_KEYS:
        if target.get(key):
            continue
        value = _clean_value(source.get(key), key)
        if value:
            target[key] = value


def _load_path(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    parsed = _parse_json_text(text)
    return parsed if parsed is not None else text


def _coerce_possible_path(value: str) -> Optional[Path]:
    text = value.strip().strip('"').strip("'")
    if not text or "\n" in text or "\r" in text or len(text) > 260:
        return None
    try:
        return Path(text)
    except (OSError, ValueError):
        return None


def _parse_json_text(text: str) -> Any:
    stripped = _strip_code_fence(text.strip())
    for candidate in _json_candidates(stripped):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _strip_code_fence(text: str) -> str:
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    return fence_match.group(1).strip() if fence_match else text


def _json_candidates(text: str) -> Iterable[str]:
    if not text:
        return []

    candidates = [text]
    if text.startswith('"') and ":" in text:
        candidates.append("{" + text + "}")

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        candidates.append(text[idx : idx + end])
        break
    return candidates


def _normalize_from_mapping(mapping: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    normalized = _empty_normalized()
    for target_key, aliases in _FIELD_ALIASES.items():
        for source in _candidate_source_maps(mapping):
            value = _clean_value(_mapping_lookup(source, aliases), target_key)
            if value:
                normalized[target_key] = value
                break
    return normalized


def _candidate_source_maps(mapping: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    for key in _DRUG_CONTAINER_KEYS:
        nested = _case_insensitive_get(mapping, key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    candidates.append(mapping)
    return candidates


def _drug_source_mapping(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    candidates = _candidate_source_maps(payload)
    return candidates[0] if candidates else payload


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


def _normalize_from_text(text: str) -> Dict[str, Optional[str]]:
    normalized = _empty_normalized()
    normalized_text = _normalize_text_for_matching(text)

    for target_key, aliases in _FIELD_ALIASES.items():
        value = _extract_labeled_value(normalized_text, aliases)
        if value:
            normalized[target_key] = _clean_value(value, target_key)

    if not normalized["drugbank_id"]:
        match = re.search(r"\bDB\d{5,}\b", normalized_text, flags=re.IGNORECASE)
        if match:
            normalized["drugbank_id"] = match.group(0).upper()

    if not normalized["inchi_key"]:
        match = re.search(r"\b[A-Z]{14}-[A-Z]{10}-[A-Z0-9]\b", normalized_text, flags=re.IGNORECASE)
        if match:
            normalized["inchi_key"] = match.group(0).upper()

    if not normalized["drug_name"]:
        normalized["drug_name"] = _extract_context_drug_name(normalized_text)

    if not normalized["drug_name"]:
        normalized["drug_name"] = _extract_resolvable_drug_name(normalized_text)

    return normalized


def _extract_labeled_value(text: str, aliases: Iterable[str]) -> Optional[str]:
    for alias in aliases:
        alias_pattern = _alias_regex(alias)
        bounded_alias = _bounded_alias_pattern(alias_pattern, alias)
        quoted = re.search(
            rf"['\"]?{bounded_alias}['\"]?\s*(?:[:=：]|is|为|是)\s*['\"](?P<value>[^'\"]+)['\"]",
            text,
            flags=re.IGNORECASE,
        )
        if quoted:
            return quoted.group("value")

        bare = re.search(
            rf"{bounded_alias}\s*(?:[:=：]|is|为|是)\s*(?P<value>[^\n,;，；。]+)",
            text,
            flags=re.IGNORECASE,
        )
        if bare:
            return bare.group("value")
    return None


def _extract_context_drug_name(text: str) -> Optional[str]:
    for alias, canonical in _DRUG_NAME_ALIASES.items():
        if alias in text:
            return canonical

    patterns = (
        r"\b(?:taking|takes|using|receiving|prescribed|administered|given|started on|on)\s+(?P<value>[A-Za-z][A-Za-z0-9 +/().'\-]{1,100})",
        r"\b(?:target drug|drug|medication|medicine|compound)\s*(?:is|was|:|=)?\s*(?P<value>[A-Za-z][A-Za-z0-9 +/().'\-]{1,100})",
        r"(?:正在)?(?:服用|使用|给予|用药|应用)\s*(?P<value>[^,;，；。\n]{1,80})",
        r"(?:药物|药品|药名|通用名)\s*(?:为|是|:|：)?\s*(?P<value>[^,;，；。\n]{1,80})",
        r"^\s*(?P<value>[A-Za-z][A-Za-z0-9 +/().'\-]{1,80})\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = _clean_value(match.group("value"), "drug_name")
            if candidate:
                return candidate
    return None


def _alias_regex(alias: str) -> str:
    tokens = [re.escape(token) for token in re.split(r"[\s_\-]+", alias.strip()) if token]
    return r"[\s_\-]*".join(tokens)


def _bounded_alias_pattern(alias_pattern: str, alias: str) -> str:
    if re.search(r"[A-Za-z0-9]", alias):
        return rf"(?<![A-Za-z0-9]){alias_pattern}(?![A-Za-z0-9])"
    return alias_pattern


def _clean_value(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        value = next((item for item in value if item not in (None, "")), None)
    if isinstance(value, Mapping):
        return None

    text = str(value).strip().strip('"').strip("'")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(".,;:")
    if text.lower() in _PLACEHOLDER_VALUES:
        return None

    if field_name == "drug_name":
        text = _trim_drug_name_candidate(text)
        text = _canonical_drug_alias(text)
        return text or None

    if field_name == "drugbank_id":
        match = re.search(r"\bDB\d{5,}\b", text, flags=re.IGNORECASE)
        return match.group(0).upper() if match else text.upper()
    if field_name == "inchi_key":
        match = re.search(r"\b[A-Z]{14}-[A-Z]{10}-[A-Z0-9]\b", text, flags=re.IGNORECASE)
        return match.group(0).upper() if match else text.upper()
    return text


def _normalize_text_for_matching(text: str) -> str:
    replacements = {
        "，": ",",
        "；": ";",
        "。": ".",
        "：": ":",
        "（": "(",
        "）": ")",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def _payload_to_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        return json.dumps(payload, ensure_ascii=False)
    if isinstance(payload, str):
        return payload
    return ""


def _canonical_drug_alias(value: str) -> str:
    text = value.strip()
    if text in _DRUG_NAME_ALIASES:
        return _DRUG_NAME_ALIASES[text]
    lowered = text.lower()
    return _DRUG_NAME_ALIASES.get(lowered, text)


def _trim_drug_name_candidate(value: str) -> str:
    text = value.strip().strip('"').strip("'")
    text = re.split(r"[,;，；。\n]", text, maxsplit=1)[0].strip()
    text = re.split(
        r"\b(?:dose|dosage|route|frequency|freq|oral|po|intravenous|iv|subcutaneous|sc|once|twice|daily|bid|tid|qid|qd|q\d+h)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    text = re.split(
        rf"\b\d+(?:\.\d+)?\s*(?:{'|'.join(re.escape(unit) for unit in _DOSE_UNITS)})\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    text = re.split(r"(?:每日|每天|每晚|每周|口服|静脉|皮下|肌内|剂量|用量|频率|给药)", text, maxsplit=1)[0].strip()
    return text.strip(" -:/")


def _extract_resolvable_drug_name(text: str) -> Optional[str]:
    for alias, canonical in _DRUG_NAME_ALIASES.items():
        if alias in text:
            return canonical

    for candidate in _candidate_drug_names(text):
        try:
            entity = resolve_drug({"drug": candidate})
        except Exception:
            continue
        if entity.get("drugbank_id") or entity.get("inchi_key"):
            return entity.get("name") or candidate
    return None


def _candidate_drug_names(text: str) -> Iterable[str]:
    cleaned = _normalize_text_for_matching(text)
    cleaned = re.sub(r"\bDB\d{5,}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*[A-Za-zμ]+(?:\s*/\s*[A-Za-z]+)?\b", " ", cleaned)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'\-]*", cleaned)
    candidates: list[str] = []
    max_window = min(4, len(tokens))
    for window in range(max_window, 0, -1):
        for idx in range(0, len(tokens) - window + 1):
            phrase_tokens = tokens[idx : idx + window]
            if all(token.lower() in _NATURAL_LANGUAGE_STOPWORDS for token in phrase_tokens):
                continue
            phrase = " ".join(phrase_tokens)
            if phrase.lower() in _NATURAL_LANGUAGE_STOPWORDS:
                continue
            if phrase not in candidates:
                candidates.append(phrase)
            if len(candidates) >= 24:
                return candidates
    return candidates


def _extract_dose(text: str) -> Optional[str]:
    if not text:
        return None
    normalized = _normalize_text_for_matching(text)
    labeled = _extract_labeled_value(normalized, ("dose", "dosage", "剂量", "用量"))
    if labeled:
        labeled_dose = _dose_from_text(labeled, surrounding_text=normalized)
        if labeled_dose:
            return labeled_dose
    return _dose_from_text(normalized, surrounding_text=normalized)


def _dose_from_text(text: str, *, surrounding_text: str) -> Optional[str]:
    unit_pattern = "|".join(re.escape(unit) for unit in _DOSE_UNITS)
    match = re.search(
        rf"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{unit_pattern})(?:\s*/\s*(?P<per>day|d|日|天))?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    amount = match.group("amount")
    unit = match.group("unit")
    per = match.group("per")
    start = max(0, match.start() - 10)
    end = min(len(surrounding_text), match.end() + 10)
    context = surrounding_text[start:end].lower()
    if not per and any(token in context for token in ("daily", "per day", "once daily", "每日", "每天")):
        per = "day"
    suffix = "/day" if per and per.lower() in {"day", "d", "日", "天"} else ""
    return f"{amount} {unit}{suffix}"


def _extract_route(text: str) -> Optional[str]:
    if not text:
        return None
    normalized = _normalize_text_for_matching(text)
    labeled = _extract_labeled_value(normalized, ("route", "administration route", "给药途径", "途径"))
    route_source = labeled or normalized
    route_patterns = (
        (r"\b(?:po|p\.o\.)\b|口服|经口", "oral"),
        (r"\b(?:oral|orally|by mouth)\b", "oral"),
        (r"\b(?:iv|i\.v\.|intravenous|intravenously)\b|静脉", "intravenous"),
        (r"\b(?:sc|s\.c\.|subcutaneous|subcutaneously)\b|皮下", "subcutaneous"),
        (r"\b(?:im|i\.m\.|intramuscular|intramuscularly)\b|肌内|肌肉", "intramuscular"),
        (r"\b(?:topical|transdermal)\b|外用|经皮", "topical"),
        (r"\b(?:inhaled|inhalation)\b|吸入", "inhaled"),
    )
    for pattern, route in route_patterns:
        if re.search(pattern, route_source, flags=re.IGNORECASE):
            return route
    return None


def _extract_frequency(text: str) -> Optional[str]:
    if not text:
        return None
    normalized = _normalize_text_for_matching(text)
    labeled = _extract_labeled_value(normalized, ("frequency", "freq", "用药频率", "频率"))
    frequency_source = labeled or normalized
    frequency_patterns = (
        (r"\b(?:twice daily|two times daily|bid|b\.i\.d\.)\b|每日两次|每天两次|一日两次", "twice daily"),
        (r"\b(?:three times daily|tid|t\.i\.d\.)\b|每日三次|每天三次|一日三次", "three times daily"),
        (r"\b(?:four times daily|qid|q\.i\.d\.)\b|每日四次|每天四次|一日四次", "four times daily"),
        (r"\b(?:once daily|daily|qd|q\.d\.)\b|每日一次|每天一次|一日一次|每日|每天", "daily"),
        (r"\b(?:weekly|once weekly)\b|每周一次|每周", "weekly"),
        (r"\bq(?P<hours>\d+)h\b", None),
    )
    for pattern, frequency in frequency_patterns:
        match = re.search(pattern, frequency_source, flags=re.IGNORECASE)
        if not match:
            continue
        if frequency is None:
            return f"every {match.group('hours')} hours"
        return frequency
    return None


def _extract_form(text: str) -> Optional[str]:
    if not text:
        return None
    normalized = _normalize_text_for_matching(text)
    labeled = _extract_labeled_value(normalized, ("form", "dosage form", "剂型"))
    if labeled:
        return _clean_value(labeled, "form")
    form_patterns = (
        (r"\btablet(?:s)?\b|片剂|药片", "tablet"),
        (r"\bcapsule(?:s)?\b|胶囊", "capsule"),
        (r"\binjection\b|注射剂|针剂", "injection"),
        (r"\bsolution\b|溶液", "solution"),
        (r"\bcream\b|乳膏", "cream"),
    )
    for pattern, form in form_patterns:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return form
    return None


def _known_toxicities(source: Mapping[str, Any]) -> list[str]:
    value = _mapping_lookup(source, ("known_toxicities", "known toxicity", "toxicities"))
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;|]", value) if item.strip()]
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return [str(item).strip() for item in value if str(item).strip()]
    return []

