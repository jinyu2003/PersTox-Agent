"""Configuration helpers for PerTox-agent.

Non-sensitive LLM defaults are read from ``configs/llm.json``. Secrets and
machine-local overrides are read from ``.env`` or the process environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LLM_CONFIG_PATH = PROJECT_ROOT / "configs" / "llm.json"


def _load_dotenv(path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs without adding a runtime dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    brain_model: str
    knowledge_model: str
    verifier_model: str
    api_key: str | None = field(repr=False)
    base_url: str | None
    max_tokens: int
    temperature: float
    attribution_parallelism: int
    use_live_llm: bool


PROVIDER_DEFAULTS = {
    "deepseek": {
        "brain_model": "deepseek-v4-flash",
        "knowledge_model": "deepseek-v4-flash",
        "verifier_model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "brain_model": "gpt-4o",
        "knowledge_model": "gpt-4o-mini",
        "verifier_model": "gpt-4o-mini",
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
    },
}


def _load_llm_config(path: Path = LLM_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid LLM config JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid LLM config JSON at {path}: expected an object")
    return payload


def _section(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _config_flag(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_VALUES


def _config_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _config_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _provider_api_key(provider: str, llm_config: dict[str, Any]) -> str | None:
    if os.getenv("LLM_API_KEY"):
        return os.getenv("LLM_API_KEY")
    api_key_envs = _section(llm_config, "api_key_envs")
    default_env = (
        os.getenv("LLM_API_KEY_ENV")
        or _string_or_none(api_key_envs.get(provider))
        or PROVIDER_DEFAULTS.get(provider, {}).get("api_key_env")
    )
    if default_env and os.getenv(default_env):
        return os.getenv(default_env)
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_API_KEY")
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY")
    return os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")


def _provider_base_url(provider: str, llm_config: dict[str, Any]) -> str | None:
    if os.getenv("LLM_BASE_URL"):
        return os.getenv("LLM_BASE_URL")
    if provider == "deepseek" and os.getenv("DEEPSEEK_BASE_URL"):
        return os.getenv("DEEPSEEK_BASE_URL")
    if provider == "openai" and os.getenv("OPENAI_BASE_URL"):
        return os.getenv("OPENAI_BASE_URL")
    base_urls = _section(llm_config, "base_urls")
    if provider in base_urls:
        return _string_or_none(base_urls.get(provider))
    return PROVIDER_DEFAULTS.get(provider, {}).get("base_url")


def get_model_config() -> ModelConfig:
    _load_dotenv()
    llm_config = _load_llm_config()
    models = _section(llm_config, "models")
    generation = _section(llm_config, "generation")

    provider = (
        os.getenv("LLM_PROVIDER")
        or _string_or_none(llm_config.get("provider"))
        or "deepseek"
    ).strip().lower()
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["deepseek"])
    env_model = os.getenv("LLM_MODEL")
    config_default_model = _string_or_none(models.get("default"))
    max_tokens_default = _config_int(generation.get("max_tokens"), 2048)
    parallelism_default = _config_int(generation.get("attribution_parallelism"), 2)
    temperature_default = _config_float(generation.get("temperature"), 0.0)

    return ModelConfig(
        provider=provider,
        brain_model=(
            os.getenv("BRAIN_MODEL")
            or env_model
            or _string_or_none(models.get("brain_model"))
            or config_default_model
            or defaults["brain_model"]
        ),
        knowledge_model=(
            os.getenv("KNOWLEDGE_MODEL")
            or env_model
            or _string_or_none(models.get("knowledge_model"))
            or config_default_model
            or defaults["knowledge_model"]
        ),
        verifier_model=(
            os.getenv("VERIFIER_MODEL")
            or env_model
            or _string_or_none(models.get("verifier_model"))
            or config_default_model
            or defaults["verifier_model"]
        ),
        api_key=_provider_api_key(provider, llm_config),
        base_url=_provider_base_url(provider, llm_config),
        max_tokens=_env_int("LLM_MAX_TOKENS", max_tokens_default),
        temperature=_env_float("LLM_TEMPERATURE", temperature_default),
        attribution_parallelism=max(1, _env_int("LLM_ATTRIBUTION_PARALLELISM", parallelism_default)),
        use_live_llm=_env_flag("PERSAGENT_USE_LIVE_LLM", _config_flag(llm_config.get("use_live_llm"))),
    )




