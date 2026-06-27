"""LLM-backed semantic extraction for clinical input normalization."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Callable, Dict, Optional


LLMJsonExtractor = Callable[[str, Any, Dict[str, Any]], Optional[Dict[str, Any]]]


class SemanticInputExtractor:
    """Extract structured input JSON with an optional live LLM or injected callback."""

    def __init__(
        self,
        *,
        config: Any,
        llm_json_extractor: Optional[LLMJsonExtractor] = None,
    ) -> None:
        self.config = config
        self._llm_json_extractor = llm_json_extractor

    def extract(self, *, kind: str, raw: Any, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._llm_json_extractor is not None:
            return self._coerce_payload(self._llm_json_extractor(kind, raw, schema))
        if not self.config.use_live_llm or not self.config.api_key:
            return None
        try:
            return self._call_live_llm_json(kind=kind, raw=raw, schema=schema)
        except Exception:
            return None

    def _call_live_llm_json(self, *, kind: str, raw: Any, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            from openai import OpenAI
        except ImportError:
            return None

        client_kwargs: Dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url:
            client_kwargs["base_url"] = self.config.base_url
        client = OpenAI(**client_kwargs)

        payload = {
            "task": f"Extract {kind} input for PersAgent.",
            "schema": schema,
            "input": raw,
        }
        completion = client.chat.completions.create(
            model=self.config.brain_model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are PersAgent's input-structuring agent. Convert the user's clinical "
                        "description into one JSON object matching the requested schema. Extract only "
                        "facts stated by the user; do not invent labs, genotypes, identifiers, doses, "
                        "or diagnoses. Use null, [], {}, unknown, or unspecified for missing fields. "
                        "Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
        )
        content = completion.choices[0].message.content or "{}"
        return self._coerce_payload(json_object_from_text(content))

    @staticmethod
    def _coerce_payload(payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, Mapping):
            return None
        return dict(payload)


def json_object_from_text(text: str) -> Optional[Dict[str, Any]]:
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

