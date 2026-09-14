#!/usr/bin/env python3
"""
Split a large file into webpack-cache-like chunks for git transport.

Usage:
  python scripts/split.py "I:\\UnitySetup64-6000.3.10f1.exe"
  python scripts/split.py path/to/file.exe --chunk-size 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

# Fake webpack pack header (makes chunks look like build cache artifacts)
PACK_MAGIC = b"webpack-cache-pack\x00"
PACK_VERSION = 1

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / ".next" / "cache" / "webpack" / "client-development"
META_DIR = REPO_ROOT / ".next" / "cache" / "webpack" / ".meta"
MANIFEST_PATH = META_DIR / "client-development.json"
DEFAULT_CHUNK_MB = 5


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_pack_chunk(dest: Path, payload: bytes) -> None:
    """Wrap raw bytes in a minimal fake webpack pack envelope."""
    header = PACK_MAGIC + struct.pack("<I", PACK_VERSION) + struct.pack("<Q", len(payload))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        f.write(header)
        f.write(payload)


def read_pack_payload(path: Path) -> bytes:
    with path.open("rb") as f:
        magic = f.read(len(PACK_MAGIC))
        if magic != PACK_MAGIC:
            raise ValueError(f"invalid pack magic in {path}")
        version = struct.unpack("<I", f.read(4))[0]
        if version != PACK_VERSION:
            raise ValueError(f"unsupported pack version {version} in {path}")
        payload_len = struct.unpack("<Q", f.read(8))[0]
        payload = f.read(payload_len)
        if len(payload) != payload_len:
            raise ValueError(f"truncated pack {path}")
        return payload


def split_file(source: Path, chunk_size: int, force: bool) -> None:
    if not source.is_file():
        print(f"error: source not found: {source}", file=sys.stderr)
        sys.exit(1)

    if MANIFEST_PATH.exists() and not force:
        print(
            f"manifest already exists: {MANIFEST_PATH}\n"
            "use --force to overwrite existing chunks",
            file=sys.stderr,
        )
        sys.exit(1)

    if CACHE_DIR.exists() and force:
        for p in CACHE_DIR.glob("*.pack"):
            p.unlink()

    source_size = source.stat().st_size
    source_hash = sha256_file(source)
    chunks: list[dict] = []
    index = 0

    print(f"source: {source} ({source_size:,} bytes)")
    print(f"chunk size: {chunk_size:,} bytes")
    print(f"sha256: {source_hash}")

    with source.open("rb") as src:
        while True:
            payload = src.read(chunk_size)
            if not payload:
                break

            rel_path = f"client-development/{index}.pack"
            dest = REPO_ROOT / ".next" / "cache" / "webpack" / rel_path
            write_pack_chunk(dest, payload)

            chunk_hash = sha256_bytes(PACK_MAGIC + struct.pack("<I", PACK_VERSION) + struct.pack("<Q", len(payload)) + payload)
            chunks.append(
                {
                    "id": index,
                    "file": rel_path,
                    "size": dest.stat().st_size,
                    "payloadSize": len(payload),
                    "hash": chunk_hash,
                }
            )
            index += 1
            if index % 50 == 0:
                print(f"  written {index} chunks...")

    manifest = {
        "version": "15.0.0",
        "cache": "filesystem",
        "name": "client-development",
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": {
            "filename": source.name,
            "size": source_size,
            "hash": source_hash,
        },
        "chunks": chunks,
    }

    META_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\ndone: {len(chunks)} chunks")
    print(f"manifest: {MANIFEST_PATH}")
    print(f"total cache size: {sum(c['size'] for c in chunks):,} bytes")
    print("\nnext step:")
    print("  python scripts/push_chunks.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split file into disguised webpack cache chunks")
    parser.add_argument("source", type=Path, help="path to source file")
    parser.add_argument(
        "--chunk-size",
        type=float,
        default=DEFAULT_CHUNK_MB,
        help=f"chunk payload size in MB (default {DEFAULT_CHUNK_MB})",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing chunks")
    args = parser.parse_args()

    chunk_bytes = int(args.chunk_size * 1024 * 1024)
    if chunk_bytes < 1024 * 1024:
        print("error: chunk size must be >= 1 MB", file=sys.stderr)
        sys.exit(1)

    split_file(args.source.resolve(), chunk_bytes, args.force)
