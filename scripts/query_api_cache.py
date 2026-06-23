#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kb_builder.api_cache import api_examples, cached_get


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a small cached API query for KB connector validation.")
    parser.add_argument("example", choices=sorted(api_examples().keys()))
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "api_responses")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    example = api_examples()[args.example]
    response_path = cached_get(
        url=example["url"],
        params=example.get("params") or {},
        cache_dir=args.cache_dir / args.example,
        force=args.force,
    )
    print(response_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
