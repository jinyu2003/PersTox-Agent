from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_USER_AGENT = "PersTox-Agent-KBBuilder/0.1 (+research; contact: local)"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def cache_key(url: str, params: dict[str, Any] | None = None) -> str:
    payload = {"url": url, "params": params or {}}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_url(url: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return url
    query = urllib.parse.urlencode(params, doseq=True)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{query}"


def _fetch(full_url: str, *, timeout: int, user_agent: str, retries: int) -> tuple[bytes, dict[str, str]]:
    # 对临时故障（5xx、SSL EOF、网络抖动）重试；4xx 直接抛出不重试。
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    request = urllib.request.Request(full_url, headers=headers)
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 客户端错误（4xx）不重试，重试无意义。
            if exc.code < 500 or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                raise
        time.sleep(min(2**attempt, 10))
    raise last_error  # pragma: no cover


def cached_get(
    *,
    url: str,
    cache_dir: Path,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
    user_agent: str = DEFAULT_USER_AGENT,
    force: bool = False,
    retries: int = 3,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(url, params)
    response_path = cache_dir / f"{key}.response"
    metadata_path = cache_dir / f"{key}.json"
    if response_path.exists() and metadata_path.exists() and not force:
        return response_path

    full_url = build_url(url, params)
    body, headers = _fetch(full_url, timeout=timeout, user_agent=user_agent, retries=retries)

    response_path.write_bytes(body)
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "url": url,
                "full_url": full_url,
                "params": params or {},
                "retrieved_at": utc_now(),
                "headers": headers,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")
    return response_path


def _fetch_post(full_url: str, *, body: bytes, headers: dict[str, str],
                timeout: int, retries: int) -> tuple[bytes, dict[str, str]]:
    """POST with the same retry policy as _fetch (5xx/transient retried, 4xx raised)."""
    request = urllib.request.Request(full_url, data=body, headers=headers, method="POST")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                raise
        time.sleep(min(2**attempt, 10))
    raise last_error  # pragma: no cover


def cached_post(
    *,
    url: str,
    cache_dir: Path,
    json_body: dict[str, Any],
    timeout: int = 60,
    user_agent: str = DEFAULT_USER_AGENT,
    force: bool = False,
    retries: int = 3,
) -> Path:
    """Cache-first POST. Cache key = url + JSON body, so identical payloads are
    served from disk. Mirrors cached_get's provenance sidecar."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(url, json_body)
    response_path = cache_dir / f"{key}.response"
    metadata_path = cache_dir / f"{key}.json"
    if response_path.exists() and metadata_path.exists() and not force:
        return response_path

    body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    headers = {"User-Agent": user_agent, "Accept": "*/*", "Content-Type": "application/json"}
    resp_body, resp_headers = _fetch_post(url, body=body, headers=headers,
                                          timeout=timeout, retries=retries)
    response_path.write_bytes(resp_body)
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump({"url": url, "method": "POST", "request_body": json_body,
                   "retrieved_at": utc_now(), "headers": resp_headers},
                  f, ensure_ascii=False, indent=2)
        f.write("\n")
    return response_path


def api_examples() -> dict[str, dict[str, Any]]:
    return {
        "rxnorm_by_name": {
            "url": "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            "params": {"name": "warfarin"},
        },
        "atc_by_drug": {
            "url": "https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json",
            "params": {"drugName": "warfarin", "relaSource": "ATC"},
        },
        "atc_tree": {
            "url": "https://rxnav.nlm.nih.gov/REST/rxclass/allClasses.json",
            "params": {"classTypes": "ATC1-4"},
        },
        "pubchem_by_name": {
            "url": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/warfarin/property/CanonicalSMILES,IsomericSMILES,InChIKey/JSON",
            "params": {},
        },
        "chembl_molecule_by_name": {
            "url": "https://www.ebi.ac.uk/chembl/api/data/molecule.json",
            "params": {"pref_name__iexact": "warfarin"},
        },
        "reactome_pathways_for_uniprot": {
            "url": "https://reactome.org/ContentService/data/pathways/low/entity/P03372",
            "params": {},
        },
        "openfda_label_by_substance": {
            "url": "https://api.fda.gov/drug/label.json",
            "params": {"search": 'openfda.substance_name:"warfarin"', "limit": 1},
        },
        "openfda_faers_by_drug": {
            "url": "https://api.fda.gov/drug/event.json",
            "params": {"search": "patient.drug.medicinalproduct:warfarin", "limit": 1},
        },
        "pubmed_esearch": {
            "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            "params": {"db": "pubmed", "term": "warfarin hepatotoxicity",
                       "retmax": 5, "retmode": "json"},
        },
    }
