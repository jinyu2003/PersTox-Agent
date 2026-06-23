#!/usr/bin/env python3
"""Normalize downloaded raw KB sources into data/normalized/<id>/*.jsonl."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kb_builder.normalize import REGISTRY, normalize_source


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--source", action="append", default=[],
        help=f"Source id to normalize (repeatable). Known: {', '.join(sorted(REGISTRY))}. Default: all.",
    )
    parser.add_argument("--list", action="store_true", help="List registered normalizers and exit.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        for sid, spec in sorted(REGISTRY.items()):
            print(f"{sid:<20} {spec['input']:<28} -> {spec['output']}")
        return 0

    targets = args.source or sorted(REGISTRY)
    results = []
    for sid in targets:
        try:
            results.append(normalize_source(sid, args.data_dir))
        except Exception as exc:  # noqa: BLE001
            results.append({"source_id": sid, "status": "error", "message": str(exc)})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r.get("status") == "error":
                print(f"{r['source_id']}: error - {r['message']}")
            else:
                print(f"{r['source_id']}: {r['rows']} rows -> {r['output']}")
    return 1 if any(r.get("status") == "error" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
