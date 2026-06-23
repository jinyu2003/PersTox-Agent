from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_USER_AGENT = "PersTox-Agent-KBBuilder/0.1 (+research; contact: local)"


class DownloadError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def format_bytes(size: int | None) -> str:
    if size is None:
        return "未知大小"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _parse_total_from_content_range(value: str | None) -> int | None:
    # Content-Range: bytes 200-1023/1024
    if not value:
        return None
    try:
        total = value.split("/", 1)[1].strip()
        return int(total) if total.isdigit() else None
    except (IndexError, ValueError):
        return None


def download_file(
    url: str,
    destination: Path,
    *,
    retries: int = 3,
    timeout: int = 120,
    user_agent: str = DEFAULT_USER_AGENT,
    label: str | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    title = label or destination.name
    if destination.exists() and destination.stat().st_size > 0:
        print(f"已存在，跳过下载: {title} ({format_bytes(destination.stat().st_size)})", file=sys.stderr, flush=True)
        return destination
    tmp_path = destination.with_suffix(destination.suffix + ".part")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        # 若已有 .part，则尝试用 HTTP Range 断点续传。
        existing = tmp_path.stat().st_size if tmp_path.exists() else 0
        # 部分 CDN/WAF（如 FDA Akamai）对缺少 Accept 头的请求返回 404，
        # 这里默认带上 Accept，行为对齐 curl。
        headers = {"User-Agent": user_agent, "Accept": "*/*"}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", response.getcode())
                length_header = response.headers.get("Content-Length")
                remaining = int(length_header) if length_header and length_header.isdigit() else None

                if existing > 0 and status == 206:
                    # 服务器接受续传：追加写入。
                    mode = "ab"
                    downloaded = existing
                    total = _parse_total_from_content_range(response.headers.get("Content-Range"))
                    if total is None and remaining is not None:
                        total = existing + remaining
                    print(
                        f"断点续传: {title} 从 {format_bytes(existing)} 继续 -> {destination}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    # 不支持 Range（返回 200）或无已有数据：从头写入。
                    if existing > 0:
                        print(
                            f"服务器不支持断点续传，重新从头下载: {title}",
                            file=sys.stderr,
                            flush=True,
                        )
                    mode = "wb"
                    downloaded = 0
                    total = remaining
                    print(f"开始下载: {title} -> {destination}", file=sys.stderr, flush=True)

                last_report = 0.0
                with tmp_path.open(mode) as f:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 2:
                            if total:
                                pct = downloaded / total * 100
                                print(
                                    f"  进度: {format_bytes(downloaded)} / {format_bytes(total)} ({pct:.1f}%)",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            else:
                                print(f"  进度: {format_bytes(downloaded)} / 未知大小", file=sys.stderr, flush=True)
                            last_report = now
            tmp_path.replace(destination)
            print(f"完成下载: {title} ({format_bytes(destination.stat().st_size)})", file=sys.stderr, flush=True)
            return destination
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            # 保留 .part 以便下次续传，不再删除。
            if attempt < retries:
                wait = min(2**attempt, 10)
                print(
                    f"下载中断({type(exc).__name__})，{wait}s 后重试 (attempt {attempt}/{retries}): {title}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait)

    raise DownloadError(f"Failed to download {url}: {last_error}") from last_error


def write_source_metadata(
    *,
    source: dict,
    output_dir: Path,
    downloaded_files: list[Path],
    dry_run: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "source_id": source["id"],
        "name": source["name"],
        "tier": source.get("tier"),
        "module": source.get("module"),
        "agent_strategy": source.get("agent_strategy"),
        "access": source.get("access"),
        "retrieved_at": None if dry_run else utc_now(),
        "dry_run": dry_run,
        "license_note": source.get("license_note"),
        "landing_page": source.get("landing_page"),
        "files": [
            {
                "path": str(path),
                "sha256": None if dry_run or not path.exists() else sha256_file(path),
            }
            for path in downloaded_files
        ],
    }
    metadata_path = output_dir / "_source_metadata.json"
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return metadata_path


def planned_downloads(source: dict) -> list[dict]:
    return [
        item
        for item in source.get("downloads", [])
        if item.get("enabled", True) and item.get("url") and item.get("filename")
    ]


def download_source(source: dict, data_dir: Path, *, dry_run: bool = False) -> dict:
    raw_root = data_dir / "raw"
    source_dir = raw_root / source["id"]
    downloads = planned_downloads(source)
    downloaded_files: list[Path] = []

    if not downloads:
        write_source_metadata(
            source=source,
            output_dir=source_dir,
            downloaded_files=[],
            dry_run=dry_run,
        )
        return {
            "source_id": source["id"],
            "status": "skipped_no_direct_download",
            "message": "No enabled direct-download files in manifest.",
        }

    for item in downloads:
        destination = source_dir / item["filename"]
        downloaded_files.append(destination)
        if not dry_run:
            download_file(item["url"], destination, label=item.get("label"))

    metadata_path = write_source_metadata(
        source=source,
        output_dir=source_dir,
        downloaded_files=downloaded_files,
        dry_run=dry_run,
    )
    return {
        "source_id": source["id"],
        "status": "planned" if dry_run else "downloaded",
        "files": [str(path) for path in downloaded_files],
        "metadata": str(metadata_path),
    }
