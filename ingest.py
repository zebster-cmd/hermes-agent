#!/usr/bin/env python3
"""
Hindsight Bulk Ingestion Tool
=============================

User-friendly CLI to ingest files and directories into Hindsight memory.

Supported formats:
  Documents:  .pdf .docx .doc .pptx .ppt .xlsx .xls
  Text:       .txt .md .csv .html .htm .json .jsonl
  Images:     .jpg .jpeg .png (OCR)
  Audio:      .mp3 .wav (transcription)

Usage:
  # Ingest a single file
  python ingest.py handbook.pdf

  # Ingest a whole directory (recursive)
  python ingest.py ./business_docs/

  # Ingest multiple files with tags
  python ingest.py sla.pdf deployment.md --tag policy --tag ops

  # Ingest with a context label
  python ingest.py team.csv --context "team roster"

  # Ingest to a specific bank
  python ingest.py docs/ --bank my-project

  # Dry run (show what would be ingested)
  python ingest.py docs/ --dry-run

  # Watch a directory for new files
  python ingest.py docs/ --watch

  # Check status of pending operations
  python ingest.py --status
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_HINDSIGHT_URL = os.environ.get("HINDSIGHT_URL", "http://localhost:8877")
DEFAULT_BANK_ID = os.environ.get("HINDSIGHT_BANK_ID", "hermes")
DEFAULT_API_KEY = os.environ.get("HINDSIGHT_API_KEY", "")

SUPPORTED_EXTENSIONS = {
    # Documents (handled by file upload endpoint + markitdown parser)
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    # Text
    ".txt", ".md", ".csv", ".html", ".htm",
    # Images (OCR)
    ".jpg", ".jpeg", ".png",
    # Audio (transcription)
    ".mp3", ".wav",
    # Data
    ".json", ".jsonl",
}

# Files to always skip
SKIP_NAMES = {
    ".git", ".venv", "__pycache__", "node_modules", ".DS_Store",
    "Thumbs.db", ".env", ".gitignore",
}

MAX_FILE_SIZE_MB = 100  # Hindsight's default limit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sizeof_fmt(num_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def discover_files(paths: list[str]) -> list[Path]:
    """Discover all supported files from a list of paths (files and/or directories)."""
    files = []
    for p in paths:
        path = Path(p)
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
            else:
                print(f"  Skipping unsupported file: {path} ({path.suffix})")
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                    if not any(skip in child.parts for skip in SKIP_NAMES):
                        files.append(child)
        else:
            print(f"  Warning: path not found: {p}")
    return files


def make_headers(api_key: str) -> dict:
    """Build request headers."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def check_health(base_url: str, headers: dict) -> bool:
    """Check if Hindsight API is reachable."""
    try:
        resp = requests.get(f"{base_url}/health", headers=headers, timeout=5)
        data = resp.json()
        return data.get("status") == "healthy"
    except Exception as e:
        print(f"  Cannot reach Hindsight API at {base_url}: {e}")
        return False


# ---------------------------------------------------------------------------
# Ingestion via file upload endpoint
# ---------------------------------------------------------------------------


def upload_file(
    base_url: str,
    bank_id: str,
    filepath: Path,
    tags: list[str],
    context: str,
    headers: dict,
) -> dict:
    """Upload a single file to Hindsight's file/retain endpoint."""
    request_body = {}
    if tags:
        request_body["tags"] = tags
    if context:
        request_body["context"] = context

    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{base_url}/v1/default/banks/{bank_id}/files/retain",
            files={"files": (filepath.name, f)},
            data={"request": json.dumps(request_body)},
            headers=headers,
            timeout=120,
        )

    resp.raise_for_status()
    return resp.json()


def upload_jsonl_as_memories(
    base_url: str,
    bank_id: str,
    filepath: Path,
    tags: list[str],
    context: str,
    headers: dict,
) -> dict:
    """
    Upload a .jsonl file as batch memories (one JSON object per line).

    Each line should have at minimum a "content" field.
    Optional fields: "context", "tags", "metadata", "timestamp".
    """
    items = []
    with open(filepath) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"    Warning: skipping malformed line {i}: {e}")
                continue

            if isinstance(obj, str):
                obj = {"content": obj}

            if "content" not in obj:
                print(f"    Warning: skipping line {i}: no 'content' field")
                continue

            # Merge global tags/context with per-item values
            item = {"content": obj["content"]}
            item_tags = list(set((obj.get("tags") or []) + tags))
            if item_tags:
                item["tags"] = item_tags
            item_context = obj.get("context") or context
            if item_context:
                item["context"] = item_context
            if obj.get("metadata"):
                item["metadata"] = obj["metadata"]
            if obj.get("timestamp"):
                item["timestamp"] = obj["timestamp"]
            elif obj.get("timestamp") is None:
                item["timestamp"] = "unset"  # Timeless by default for bulk loads

            items.append(item)

    if not items:
        return {"error": "No valid items found in JSONL file"}

    # Batch in chunks of 50
    total_ops = []
    for i in range(0, len(items), 50):
        batch = items[i : i + 50]
        post_headers = {**headers, "Content-Type": "application/json"}
        resp = requests.post(
            f"{base_url}/v1/default/banks/{bank_id}/memories",
            json={"items": batch},
            headers=post_headers,
            timeout=120,
        )
        resp.raise_for_status()
        total_ops.append({"batch": f"{i+1}-{i+len(batch)}", "items": len(batch)})

    return {"batches": total_ops, "total_items": len(items)}


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------


def show_status(base_url: str, bank_id: str, headers: dict):
    """Show status of recent operations."""
    resp = requests.get(
        f"{base_url}/v1/default/banks/{bank_id}/operations",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    ops = resp.json()

    if not ops:
        print("No operations found.")
        return

    # ops can be a list or dict with 'operations' key
    if isinstance(ops, dict):
        ops = ops.get("operations", [])

    # Normalize field names (API uses 'id'/'task_type' not 'operation_id'/'operation_type')
    def op_id(op):
        return op.get("id") or op.get("operation_id") or "unknown"

    def op_type(op):
        return op.get("task_type") or op.get("operation_type") or "unknown"

    pending = [o for o in ops if o.get("status") in ("pending", "processing")]
    failed = [o for o in ops if o.get("status") == "failed"]
    completed = [o for o in ops if o.get("status") == "completed"]

    print(f"\nOperations for bank '{bank_id}':")
    print(f"  Completed: {len(completed)}")
    print(f"  Pending:   {len(pending)}")
    print(f"  Failed:    {len(failed)}")

    if pending:
        print("\nPending operations:")
        for op in pending[:10]:
            filename = (op.get("result_metadata") or {}).get("original_filename", "")
            print(f"  {op_id(op)[:8]}.. {op_type(op)} {filename} [{op['status']}]")

    if failed:
        print("\nFailed operations:")
        for op in failed[:10]:
            filename = (op.get("result_metadata") or {}).get("original_filename", "")
            err = (op.get("error_message") or "")[:80]
            print(f"  {op_id(op)[:8]}.. {op_type(op)} {filename}")
            print(f"    Error: {err}")
        print(f"\n  Retry failed operations with:")
        for op in failed[:5]:
            print(f"    curl -X POST {base_url}/v1/default/banks/{bank_id}/operations/{op_id(op)}/retry")


def show_stats(base_url: str, bank_id: str, headers: dict):
    """Show bank memory statistics."""
    try:
        resp = requests.get(
            f"{base_url}/v1/default/banks/{bank_id}/stats",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        stats = resp.json()
        print(f"\nBank '{bank_id}' stats:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
    except Exception as e:
        print(f"  Could not fetch stats: {e}")


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------


def watch_directory(
    base_url: str,
    bank_id: str,
    watch_path: str,
    tags: list[str],
    context: str,
    headers: dict,
):
    """Watch a directory for new files and auto-ingest them."""
    seen = set()
    watch_dir = Path(watch_path)

    # Seed with existing files
    for f in discover_files([watch_path]):
        seen.add(str(f))

    print(f"\nWatching {watch_dir} for new files (Ctrl+C to stop)...")
    print(f"  Already tracking {len(seen)} existing files")

    try:
        while True:
            time.sleep(3)
            current = discover_files([watch_path])
            new_files = [f for f in current if str(f) not in seen]

            for filepath in new_files:
                seen.add(str(filepath))
                size = filepath.stat().st_size
                print(f"\n  New file: {filepath} ({sizeof_fmt(size)})")

                try:
                    result = upload_file(base_url, bank_id, filepath, tags, context, headers)
                    op_ids = result.get("operation_ids", [])
                    print(f"    Queued: {', '.join(op_ids[:3])}")
                except Exception as e:
                    print(f"    Error: {e}")

    except KeyboardInterrupt:
        print("\nStopped watching.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Ingest files into Hindsight memory for Hermes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s handbook.pdf                     Upload a single PDF
  %(prog)s ./docs/                          Upload all files in a directory
  %(prog)s sla.pdf deploy.md --tag policy   Upload with tags
  %(prog)s team.csv --context "team info"   Upload with context label
  %(prog)s --status                         Check operation status
  %(prog)s docs/ --watch                    Watch for new files
  %(prog)s docs/ --dry-run                  Preview without uploading

Supported formats:
  Documents: .pdf .docx .pptx .xlsx
  Text:      .txt .md .csv .html
  Data:      .json .jsonl
  Media:     .jpg .png (OCR) .mp3 .wav (transcription)

Environment variables:
  HINDSIGHT_URL       API URL (default: http://localhost:8877)
  HINDSIGHT_BANK_ID   Bank ID (default: hermes)
  HINDSIGHT_API_KEY   API key (optional for local)
        """,
    )

    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to ingest",
    )
    parser.add_argument(
        "--bank",
        default=DEFAULT_BANK_ID,
        help=f"Hindsight bank ID (default: {DEFAULT_BANK_ID})",
    )
    parser.add_argument(
        "--tag", "-t",
        action="append",
        default=[],
        dest="tags",
        help="Tag(s) to apply to all ingested files (repeatable)",
    )
    parser.add_argument(
        "--context", "-c",
        default="",
        help="Context label for all files (e.g. 'company policy', 'product spec')",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_HINDSIGHT_URL,
        help=f"Hindsight API URL (default: {DEFAULT_HINDSIGHT_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        help="API key (default: from HINDSIGHT_API_KEY env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ingested without uploading",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show status of recent operations",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show bank memory statistics",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch directory for new files and auto-ingest",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry all failed operations",
    )

    args = parser.parse_args()
    headers = make_headers(args.api_key)

    # ---- Status / stats commands (no paths required) ----

    if args.status:
        if not check_health(args.url, headers):
            sys.exit(1)
        show_status(args.url, args.bank, headers)
        sys.exit(0)

    if args.stats:
        if not check_health(args.url, headers):
            sys.exit(1)
        show_stats(args.url, args.bank, headers)
        sys.exit(0)

    if args.retry_failed:
        if not check_health(args.url, headers):
            sys.exit(1)
        resp = requests.get(
            f"{args.url}/v1/default/banks/{args.bank}/operations",
            headers=headers,
            timeout=10,
        )
        ops = resp.json()
        if isinstance(ops, dict):
            ops = ops.get("operations", [])
        failed = [o for o in ops if o.get("status") == "failed"]
        if not failed:
            print("No failed operations to retry.")
            sys.exit(0)
        print(f"Retrying {len(failed)} failed operations...")
        for op in failed:
            oid = op.get("id") or op.get("operation_id") or "unknown"
            otype = op.get("task_type") or op.get("operation_type") or "unknown"
            try:
                requests.post(
                    f"{args.url}/v1/default/banks/{args.bank}/operations/{oid}/retry",
                    headers=headers,
                    timeout=10,
                )
                print(f"  Retried: {oid[:8]}.. {otype}")
            except Exception as e:
                print(f"  Failed to retry {oid[:8]}..: {e}")
        sys.exit(0)

    # ---- File ingestion (paths required) ----

    if not args.paths:
        parser.print_help()
        sys.exit(1)

    # Discover files
    print(f"Discovering files...")
    files = discover_files(args.paths)

    if not files:
        print("No supported files found.")
        sys.exit(1)

    total_size = sum(f.stat().st_size for f in files)
    print(f"\nFound {len(files)} file(s) ({sizeof_fmt(total_size)}):")

    # Group by extension for summary
    by_ext: dict[str, list[Path]] = {}
    for f in files:
        ext = f.suffix.lower()
        by_ext.setdefault(ext, []).append(f)
    for ext, ext_files in sorted(by_ext.items()):
        ext_size = sum(f.stat().st_size for f in ext_files)
        print(f"  {ext:8s}  {len(ext_files):4d} file(s)  {sizeof_fmt(ext_size)}")

    if args.tags:
        print(f"\nTags: {', '.join(args.tags)}")
    if args.context:
        print(f"Context: {args.context}")
    print(f"Bank: {args.bank}")
    print(f"API: {args.url}")

    # Dry run
    if args.dry_run:
        print(f"\n--- DRY RUN (no files uploaded) ---")
        for f in files:
            print(f"  {f} ({sizeof_fmt(f.stat().st_size)})")
        sys.exit(0)

    # Health check
    if not check_health(args.url, headers):
        sys.exit(1)

    # Watch mode
    if args.watch and len(args.paths) == 1 and Path(args.paths[0]).is_dir():
        watch_directory(args.url, args.bank, args.paths[0], args.tags, args.context, headers)
        sys.exit(0)

    # ---- Upload files ----
    print(f"\nIngesting {len(files)} file(s)...\n")

    succeeded = 0
    failed = 0
    skipped = 0

    for i, filepath in enumerate(files, 1):
        size = filepath.stat().st_size
        size_mb = size / (1024 * 1024)

        if size_mb > MAX_FILE_SIZE_MB:
            print(f"  [{i}/{len(files)}] SKIP {filepath} ({sizeof_fmt(size)} > {MAX_FILE_SIZE_MB}MB limit)")
            skipped += 1
            continue

        if size == 0:
            print(f"  [{i}/{len(files)}] SKIP {filepath} (empty file)")
            skipped += 1
            continue

        print(f"  [{i}/{len(files)}] {filepath} ({sizeof_fmt(size)}) ...", end=" ", flush=True)

        try:
            # .jsonl files get special handling (batch memory API)
            if filepath.suffix.lower() == ".jsonl":
                result = upload_jsonl_as_memories(
                    args.url, args.bank, filepath, args.tags, args.context, headers
                )
                total = result.get("total_items", 0)
                print(f"OK ({total} items)")
            else:
                result = upload_file(
                    args.url, args.bank, filepath, args.tags, args.context, headers
                )
                op_ids = result.get("operation_ids", [])
                print(f"OK (op: {op_ids[0][:8]}..)" if op_ids else "OK")

            succeeded += 1

        except requests.exceptions.HTTPError as e:
            print(f"FAILED ({e.response.status_code}: {e.response.text[:100]})")
            failed += 1
        except Exception as e:
            print(f"FAILED ({e})")
            failed += 1

    # Summary
    print(f"\n{'='*50}")
    print(f"Ingestion complete:")
    print(f"  Succeeded: {succeeded}")
    print(f"  Failed:    {failed}")
    print(f"  Skipped:   {skipped}")

    if succeeded > 0:
        print(f"\nFiles are being processed asynchronously.")
        print(f"Check status with: python {sys.argv[0]} --status")

    if failed > 0:
        print(f"\nRetry failed operations with: python {sys.argv[0]} --retry-failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
