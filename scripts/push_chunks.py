#!/usr/bin/env python3
"""
Batch-commit and push chunks to GitHub with resume support.

Tracks progress in scripts/.xfer_state.json (gitignored).
Re-run after network failure to continue from last successful batch.

Usage:
  python scripts/push_chunks.py
  python scripts/push_chunks.py --batch-size 80
  python scripts/push_chunks.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / ".next" / "cache" / "webpack" / ".meta" / "client-development.json"
STATE_PATH = REPO_ROOT / "scripts" / ".xfer_state.json"
DEFAULT_BATCH = 60


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=REPO_ROOT, check=check, text=True)


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        print(f"error: manifest not found, run split.py first:\n  {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)
    with MANIFEST_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    if STATE_PATH.is_file():
        with STATE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {"pushed_chunk_ids": [], "manifest_committed": False}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def git_files_tracked(paths: list[str]) -> set[str]:
    if not paths:
        return set()
    result = run(["git", "ls-files", "--"] + paths, check=False)
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def chunk_repo_path(chunk: dict) -> str:
    return f".next/cache/webpack/{chunk['file']}"


def push_batches(batch_size: int, dry_run: bool) -> None:
    manifest = load_manifest()
    chunks = manifest["chunks"]
    state = load_state()
    pushed_ids = set(state.get("pushed_chunk_ids", []))

    # Always ensure manifest is committed first
    manifest_rel = ".next/cache/webpack/.meta/client-development.json"
    if not state.get("manifest_committed"):
        if dry_run:
            print(f"[dry-run] would commit manifest: {manifest_rel}")
        else:
            run(["git", "add", manifest_rel])
            run(["git", "commit", "-m", "chore: update webpack client-development cache meta"])
            run(["git", "push", "origin", "HEAD"])
            state["manifest_committed"] = True
            save_state(state)
            print("manifest pushed")

    pending = [c for c in chunks if c["id"] not in pushed_ids]
    total = len(chunks)
    print(f"chunks: {total} total, {len(pushed_ids)} already pushed, {len(pending)} pending")

    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        paths = [chunk_repo_path(c) for c in batch]
        ids = [c["id"] for c in batch]

        if dry_run:
            print(f"[dry-run] batch {ids[0]}-{ids[-1]}: {len(paths)} files")
            continue

        # Skip files already in git index/history
        tracked = git_files_tracked(paths)
        to_add = [p for p in paths if p not in tracked]

        if to_add:
            run(["git", "add", "--"] + to_add)

        first_id, last_id = ids[0], ids[-1]
        run(["git", "commit", "-m", f"chore: refresh webpack cache packs {first_id}-{last_id}"])

        try:
            run(["git", "push", "origin", "HEAD"])
        except subprocess.CalledProcessError:
            print(
                "\npush failed (network?). progress saved.\n"
                "re-run: python scripts/push_chunks.py",
                file=sys.stderr,
            )
            sys.exit(1)

        pushed_ids.update(ids)
        state["pushed_chunk_ids"] = sorted(pushed_ids)
        save_state(state)
        print(f"pushed batch {first_id}-{last_id} ({len(pushed_ids)}/{total})")

    if len(pushed_ids) >= total:
        print("\nall chunks pushed successfully")
    else:
        print(f"\nprogress: {len(pushed_ids)}/{total} — re-run to continue")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Push chunks to GitHub in batches with resume")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help=f"files per commit (default {DEFAULT_BATCH})")
    parser.add_argument("--dry-run", action="store_true", help="show plan without git operations")
    args = parser.parse_args()

    if args.batch_size < 1:
        print("error: batch-size must be >= 1", file=sys.stderr)
        sys.exit(1)

    push_batches(args.batch_size, args.dry_run)
