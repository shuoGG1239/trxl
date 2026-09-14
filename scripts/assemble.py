#!/usr/bin/env python3
"""
Clone/pull trxl repo and reassemble the original file with resume support.

Verifies each chunk by SHA-256 before assembly.
Re-run after network failure — git pull resumes, then missing chunks are re-fetched.

Usage:
  python assemble.py --repo https://github.com/shuoGG1239/trxl.git --output UnitySetup64-6000.3.10f1.exe
  python assemble.py --repo ./trxl --output UnitySetup64-6000.3.10f1.exe
  python assemble.py --repo https://github.com/shuoGG1239/trxl.git --output out.exe --clone-dir ./trxl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

PACK_MAGIC = b"webpack-cache-pack\x00"
PACK_VERSION = 1

MANIFEST_REL = Path(".next/cache/webpack/.meta/client-development.json")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, text=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def chunk_hash_on_disk(path: Path) -> str:
    with path.open("rb") as f:
        return sha256_bytes(f.read())


def load_manifest(repo_dir: Path) -> dict:
    manifest_path = repo_dir / MANIFEST_REL
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as f:
        return json.load(f)


def ensure_repo(repo_url: str, clone_dir: Path) -> None:
    if (clone_dir / ".git").is_dir():
        print(f"repo exists, pulling: {clone_dir}")
        try:
            run(["git", "pull", "--ff-only"], cwd=clone_dir)
        except subprocess.CalledProcessError:
            print("git pull failed — retry later or check network", file=sys.stderr)
            sys.exit(1)
    else:
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        print(f"cloning: {repo_url} -> {clone_dir}")
        try:
            run(["git", "clone", repo_url, str(clone_dir)])
        except subprocess.CalledProcessError:
            # Partial clone dir may exist after interrupted clone
            if clone_dir.exists():
                print(
                    f"clone interrupted. re-run to resume:\n"
                    f"  cd {clone_dir} && git fetch && git checkout main",
                    file=sys.stderr,
                )
            sys.exit(1)


def verify_chunks(repo_dir: Path, manifest: dict) -> tuple[list[dict], list[dict]]:
    ok: list[dict] = []
    bad: list[dict] = []

    for chunk in manifest["chunks"]:
        rel = f".next/cache/webpack/{chunk['file']}"
        path = repo_dir / rel
        if not path.is_file():
            bad.append({**chunk, "reason": "missing"})
            continue
        try:
            actual = chunk_hash_on_disk(path)
        except OSError as e:
            bad.append({**chunk, "reason": str(e)})
            continue
        if actual != chunk["hash"]:
            bad.append({**chunk, "reason": f"hash mismatch (got {actual[:12]}...)"})
            continue
        ok.append(chunk)

    return ok, bad


def checkout_missing(repo_dir: Path, bad: list[dict]) -> None:
    paths = [f".next/cache/webpack/{c['file']}" for c in bad]
    print(f"checking out {len(paths)} missing/corrupt chunks from git...")
    # Batch checkout to avoid command line length limits
    batch = 100
    for i in range(0, len(paths), batch):
        run(["git", "checkout", "HEAD", "--"] + paths[i : i + batch], cwd=repo_dir)


def assemble_output(repo_dir: Path, manifest: dict, output: Path, force: bool) -> None:
    source = manifest["source"]
    expected_hash = source["hash"]
    expected_size = source["size"]

    if output.is_file() and not force:
        h = hashlib.sha256()
        with output.open("rb") as f:
            for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() == expected_hash:
            print(f"output already complete: {output}")
            return
        print(f"output exists but hash mismatch, re-assembling (--force)")

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".part")

    chunks = sorted(manifest["chunks"], key=lambda c: c["id"])
    written = 0

    with tmp.open("wb") as out:
        for chunk in chunks:
            rel = f".next/cache/webpack/{chunk['file']}"
            path = repo_dir / rel
            payload = read_pack_payload(path)
            if len(payload) != chunk["payloadSize"]:
                print(f"error: chunk {chunk['id']} size mismatch", file=sys.stderr)
                sys.exit(1)
            out.write(payload)
            written += len(payload)
            if chunk["id"] % 50 == 0:
                print(f"  assembled {chunk['id'] + 1}/{len(chunks)} chunks ({written:,} bytes)")

    if written != expected_size:
        tmp.unlink(missing_ok=True)
        print(f"error: assembled size {written} != expected {expected_size}", file=sys.stderr)
        sys.exit(1)

    h = hashlib.sha256()
    with tmp.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    if h.hexdigest() != expected_hash:
        tmp.unlink(missing_ok=True)
        print("error: assembled file hash mismatch", file=sys.stderr)
        sys.exit(1)

    tmp.replace(output)
    print(f"\ndone: {output} ({written:,} bytes)")
    print(f"sha256: {expected_hash}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull repo and reassemble file with resume")
    parser.add_argument("--repo", required=True, help="git repo URL or local path")
    parser.add_argument("--output", required=True, type=Path, help="output file path")
    parser.add_argument("--clone-dir", type=Path, default=Path("./trxl"), help="local clone directory")
    parser.add_argument("--force", action="store_true", help="overwrite existing output")
    parser.add_argument("--skip-clone", action="store_true", help="use --clone-dir as-is without pull")
    args = parser.parse_args()

    repo_url = args.repo
    clone_dir = args.clone_dir.resolve()

    # Local repo path
    local = Path(repo_url)
    if local.is_dir() and (local / ".git").is_dir():
        clone_dir = local.resolve()
        args.skip_clone = True

    if not args.skip_clone:
        ensure_repo(repo_url, clone_dir)

    manifest = load_manifest(clone_dir)
    print(f"source: {manifest['source']['filename']} ({manifest['source']['size']:,} bytes)")

    ok, bad = verify_chunks(clone_dir, manifest)
    print(f"chunks verified: {len(ok)}/{len(manifest['chunks'])}")

    if bad:
        print(f"{len(bad)} chunks missing or corrupt — fetching from git...")
        checkout_missing(clone_dir, bad)
        ok, bad = verify_chunks(clone_dir, manifest)
        if bad:
            print("still missing chunks after checkout:", file=sys.stderr)
            for c in bad[:10]:
                print(f"  chunk {c['id']}: {c.get('reason')}", file=sys.stderr)
            if len(bad) > 10:
                print(f"  ... and {len(bad) - 10} more", file=sys.stderr)
            print("\nre-run this script after network recovers (git pull + retry)", file=sys.stderr)
            sys.exit(1)

    assemble_output(clone_dir, manifest, args.output.resolve(), args.force)


if __name__ == "__main__":
    main()
