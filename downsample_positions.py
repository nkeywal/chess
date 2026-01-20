#!/usr/bin/env python3
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import os
import random
import re
from pathlib import Path
from typing import Optional


PIECE_RE = re.compile(r"[KQRBNP]+")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Downsample position files in ./data. "
            "Files over --max-bytes are renamed to *.full.txt and "
            "a smaller file is created with ~--target-bytes random records."
        )
    )
    p.add_argument("--out-dir", default="data", help="Directory containing position files.")
    p.add_argument("--max-bytes", type=int, default=500_000, help="Threshold to downsample.")
    p.add_argument("--target-bytes", type=int, default=400_000, help="Approx target size for new file.")
    p.add_argument("--seed", type=int, default=None, help="Optional RNG seed for reproducibility.")
    return p.parse_args()


def record_len_from_name(path: Path) -> Optional[int]:
    stem = path.stem
    if stem.endswith(".full"):
        stem = stem[:-5]

    parts = stem.split("_")
    if len(parts) >= 2:
        w = "".join(ch for ch in parts[0] if ch in "KQRBNP")
        b = "".join(ch for ch in parts[1] if ch in "KQRBNP")
        if w or b:
            return len(w) + len(b)

    groups = PIECE_RE.findall(stem)
    if len(groups) >= 2:
        return len(groups[0]) + len(groups[1])
    if len(groups) == 1:
        return len(groups[0])
    return None


def downsample_file(path: Path, max_bytes: int, target_bytes: int, rng: random.Random) -> None:
    size = path.stat().st_size
    if size <= max_bytes:
        return
    if path.name.endswith(".full.txt"):
        return

    record_len = record_len_from_name(path)
    if not record_len or record_len <= 0:
        print(f"skip: {path} (cannot infer record length)")
        return
    if size % record_len != 0:
        print(f"skip: {path} (size not divisible by record length {record_len})")
        return

    total_records = size // record_len
    target_records = max(1, min(total_records, target_bytes // record_len))
    if target_records >= total_records:
        return

    full_path = path.with_name(f"{path.stem}.full.txt")
    os.replace(path, full_path)

    # Sample indices, then read in sorted order for fewer seeks.
    sample_indices = rng.sample(range(total_records), target_records)
    sample_indices.sort()

    with full_path.open("rb") as f_in, path.open("wb") as f_out:
        for idx in sample_indices:
            f_in.seek(idx * record_len)
            chunk = f_in.read(record_len)
            if len(chunk) != record_len:
                raise RuntimeError(f"Short read at index {idx} in {full_path}")
            f_out.write(chunk)

    print(
        f"downsampled: {path.name} -> {path.name} "
        f"({target_records} records, ~{target_records * record_len} bytes)"
    )


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    if not out_dir.exists():
        raise SystemExit(f"out dir not found: {out_dir}")

    rng = random.Random(args.seed)

    for path in sorted(out_dir.glob("*.txt")):
        downsample_file(path, args.max_bytes, args.target_bytes, rng)


if __name__ == "__main__":
    main()
