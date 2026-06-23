#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from kb_builder.downloader import download_source
from kb_builder.manifest import DEFAULT_MANIFEST, SourceFilter, iter_sources, load_sources, source_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download or list PersTox-Agent external knowledge-base sources."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--list", action="store_true", help="List selected sources only.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned downloads without fetching files.")
    parser.add_argument("--mvp", action="store_true", help="Select only MVP liver/cardiac sources.")
    parser.add_argument("--strict-t1", action="store_true", help="只选择 tier 字段严格等于 T1 的数据源；T1-T2/T2/T3 都会排除。")
    parser.add_argument("--source", action="append", default=[], help="Source id to select. Can be repeated.")
    parser.add_argument("--exclude-source", action="append", default=[], help="Source id to exclude. Can be repeated.")
    parser.add_argument(
        "--strategy",
        action="append",
        choices=["local_full", "hybrid", "api_cache"],
        default=[],
        help="Agent retrieval strategy to select. Can be repeated.",
    )
    parser.add_argument("--module", action="append", default=[], help="Module name to select. Can be repeated.")
    parser.add_argument("--include-manual", action="store_true", help="Include manual/license-gated sources in listing.")
    parser.add_argument("--include-large", action="store_true", help="Include large sources.")
    parser.add_argument("--include-api-examples", action="store_true", help="Include API-only sources in selection.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable results.")
    return parser.parse_args()


def build_filter(args: argparse.Namespace) -> SourceFilter:
    return SourceFilter(
        source_ids=set(args.source) if args.source else None,
        strategies=set(args.strategy) if args.strategy else None,
        modules=set(args.module) if args.module else None,
        mvp_only=args.mvp,
        include_manual=args.include_manual,
        include_large=args.include_large,
        include_api_examples=args.include_api_examples,
    )


def main() -> int:
    args = parse_args()
    sources = list(iter_sources(load_sources(args.manifest), build_filter(args)))
    if args.strict_t1:
        sources = [source for source in sources if source.get("tier") == "T1"]
    if args.exclude_source:
        excluded = set(args.exclude_source)
        sources = [source for source in sources if source["id"] not in excluded]

    if args.list:
        if args.json:
            print(json.dumps(sources, ensure_ascii=False, indent=2))
            return 0
        print("source_id                    tier     strategy                 access           module")
        print("-" * 100)
        for source in sources:
            print(source_summary(source))
        return 0

    results = []
    for source in sources:
        access = source.get("access")
        if access in {"manual_license", "api", "landing_page"}:
            results.append(
                {
                    "source_id": source["id"],
                    "status": f"skipped_{access}",
                    "message": source.get("license_note") or "No direct download configured.",
                    "landing_page": source.get("landing_page"),
                }
            )
            continue
        try:
            results.append(download_source(source, args.data_dir, dry_run=args.dry_run))
        except Exception as exc:
            results.append({"source_id": source["id"], "status": "error", "message": str(exc)})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"{result['source_id']}: {result['status']}")
            if result.get("message"):
                print(f"  {result['message']}")
            if result.get("landing_page"):
                print(f"  {result['landing_page']}")
            for path in result.get("files", []):
                print(f"  {path}")
    return 1 if any(item["status"] == "error" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
