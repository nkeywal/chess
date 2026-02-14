#!/usr/bin/env python3
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import chess
import chess.gaviota


PIECE_ORDER = "KQRBNP"

LETTER_TO_PIECE_TYPE = {
    "K": chess.KING,
    "Q": chess.QUEEN,
    "R": chess.ROOK,
    "B": chess.BISHOP,
    "N": chess.KNIGHT,
    "P": chess.PAWN,
}

# Must match the generator's alphabet. (64 chars for squares 0..63)
ALPHABET_64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-"
CHAR_TO_SQ = {c: i for i, c in enumerate(ALPHABET_64)}

OUTCOME_CHARS = set("WDL")


@dataclass(frozen=True)
class Material:
    white: str
    black: str

    @property
    def total_pieces(self) -> int:
        return len(self.white) + len(self.black)

    @property
    def label(self) -> str:
        return f"{self.white} vs {self.black}"


def find_gaviota_dirs(root: Path) -> List[Path]:
    """Find all directories under `root` that contain Gaviota table files (*.gtb.cp4)."""
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Gaviota root directory not found: {root}")

    dirs = set()
    for p in root.rglob("*.gtb.cp4"):
        dirs.add(p.parent)

    if not dirs:
        raise FileNotFoundError(f"No *.gtb.cp4 files found under: {root}")

    def sort_key(d: Path):
        # Prefer numeric directories like ./gaviota/3, ./gaviota/4, ...
        try:
            n = int(d.name)
        except ValueError:
            n = 999
        return (n, str(d))

    return sorted(dirs, key=sort_key)


def open_tablebase_native_fixed(dirs: List[Path]) -> Any:
    """
    Open NativeTablebase but fix the path list passed to libgtb:
    - ensure NULL-terminated char** (argv-style), which some libgtb builds expect.
    - define argtypes to avoid ABI ambiguity.
    """
    libname = ctypes.util.find_library("gtb") or "libgtb.so.1"
    lib = ctypes.cdll.LoadLibrary(libname)

    tb = chess.gaviota.NativeTablebase(lib)

    # tb_restart(verbosity:int, compression_scheme:int, paths:char**)
    tb.libgtb.tb_restart.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
    tb.libgtb.tb_restart.restype = ctypes.c_char_p

    def _tb_restart_null_terminated() -> None:
        n = len(tb.paths)
        c_paths = (ctypes.c_char_p * (n + 1))()
        c_paths[:n] = [p.encode("utf-8") for p in tb.paths]
        c_paths[n] = None  # NULL terminator
        tb.libgtb.tb_restart(ctypes.c_int(1), ctypes.c_int(4), c_paths)
        tb.c_paths = c_paths  # keep alive

    tb._tb_restart = _tb_restart_null_terminated  # type: ignore[attr-defined]

    tb.add_directory(str(dirs[0]))
    for d in dirs[1:]:
        tb.add_directory(str(d))

    return tb


def probe_wdl_white_from_dtm(tablebase: Any, board: chess.Board) -> int:
    """
    Probe using ONLY probe_dtm() and return WDL in {-1,0,+1} from White POV.
    """
    if len(board.piece_map()) == 2:
        return 0

    if board.is_checkmate():
        # side to move is checkmated
        wdl_stm = -1
        return wdl_stm if board.turn == chess.WHITE else -wdl_stm

    if board.is_stalemate():
        return 0

    dtm_stm = int(tablebase.probe_dtm(board))
    if dtm_stm == 0:
        return 0

    # dtm_stm > 0 => side to move mates; dtm_stm < 0 => side to move gets mated
    wdl_stm = 1 if dtm_stm > 0 else -1
    return wdl_stm if board.turn == chess.WHITE else -wdl_stm


def parse_material_from_filename(path: Path) -> Optional[Material]:
    """
    Accept names like:
      - KR_KP.txt
      - KR_KP.full.txt
    """
    name = path.name
    if not name.endswith(".txt"):
        return None

    stem = name[:-4]  # drop .txt
    if stem.endswith(".full"):
        stem = stem[:-5]

    parts = stem.split("_")
    if len(parts) != 2:
        return None

    w, b = parts[0].strip().upper(), parts[1].strip().upper()
    if not w or not b:
        return None

    for s in (w, b):
        for c in s:
            if c not in LETTER_TO_PIECE_TYPE:
                return None

    return Material(white=w, black=b)


def build_board_from_record(material: Material, rec: str) -> chess.Board:
    """
    Decode the position from a record that contains exactly material.total_pieces chars,
    using the same KQRBNP-per-side order as the generator.
    """
    if len(rec) != material.total_pieces:
        raise ValueError(f"Bad record length: got {len(rec)}, expected {material.total_pieces}")

    b = chess.Board(None)
    b.turn = chess.WHITE
    b.castling_rights = 0
    b.ep_square = None
    b.halfmove_clock = 0
    b.fullmove_number = 1

    idx = 0

    def place_side(color: bool, mat: str) -> None:
        nonlocal idx
        for letter in PIECE_ORDER:
            cnt = mat.count(letter)
            if cnt == 0:
                continue
            pt = LETTER_TO_PIECE_TYPE[letter]
            for _ in range(cnt):
                ch = rec[idx]
                idx += 1
                sq = CHAR_TO_SQ.get(ch)
                if sq is None:
                    raise ValueError(f"Invalid square char: {ch!r}")
                b.set_piece_at(sq, chess.Piece(pt, color))

    place_side(chess.WHITE, material.white)
    place_side(chess.BLACK, material.black)

    return b


def iter_records(path: Path, rec_len: int) -> Iterator[str]:
    """
    Yield decoded records (length rec_len), stripping any trailing outcome char if present.
    Supports:
      - one-record-per-line
      - packed stream with or without outcome char
    """
    data = path.read_text(encoding="ascii", errors="strict")

    if "\n" in data or "\r" in data:
        for line in data.splitlines():
            s = line.strip()
            if not s:
                continue
            if len(s) == rec_len + 1 and s[-1] in OUTCOME_CHARS:
                yield s[:-1]
            elif len(s) >= rec_len:
                yield s[:rec_len]
        return

    s = data.strip()
    if not s:
        return

    step = rec_len
    if len(s) >= rec_len + 1 and s[rec_len] in OUTCOME_CHARS:
        step = rec_len + 1

    if (len(s) % step) != 0:
        step = rec_len

    for i in range(0, len(s) - rec_len + 1, step):
        chunk = s[i : i + step]
        if len(chunk) < rec_len:
            break
        if step == rec_len + 1 and len(chunk) == rec_len + 1 and chunk[-1] in OUTCOME_CHARS:
            yield chunk[:-1]
        else:
            yield chunk[:rec_len]


def reservoir_sample(records: Iterable[str], k: int, rng: random.Random) -> List[str]:
    """
    Reservoir sampling: returns a uniform sample of up to k items from a stream.
    If stream has <= k items, returns them all.
    """
    sample: List[str] = []
    for i, item in enumerate(records):
        if i < k:
            sample.append(item)
        else:
            j = rng.randrange(i + 1)
            if j < k:
                sample[j] = item
    return sample


def compute_wdl_percentages(wins: int, draws: int, losses: int) -> Tuple[int, int, int]:
    n = wins + draws + losses
    if n <= 0:
        return (0, 0, 0)

    # Integers only; keep sum==100 by assigning remainder to loss.
    win_pct = int(round(100.0 * wins / n))
    draw_pct = int(round(100.0 * draws / n))
    loss_pct = 100 - win_pct - draw_pct
    return (win_pct, draw_pct, loss_pct)


def choose_data_files(data_dir: Path) -> List[Path]:
    """
    Choose one file per type:
    - If both TYPE.txt and TYPE.full.txt exist, prefer TYPE.txt.
    """
    txts = sorted([p for p in data_dir.glob("*.txt") if p.is_file()])

    by_key: Dict[str, Path] = {}
    for p in txts:
        stem = p.name[:-4]  # drop .txt
        key = stem[:-5] if stem.endswith(".full") else stem
        if key not in by_key:
            by_key[key] = p
        else:
            cur = by_key[key]
            if cur.name.endswith(".full.txt") and not p.name.endswith(".full.txt"):
                by_key[key] = p

    return sorted(by_key.values())


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compute win/draw/loss rates for all position types in ./data using Gaviota."
    )
    ap.add_argument("--data-dir", default="./data", help="Directory containing *.txt position files.")
    ap.add_argument("--gaviota-root", default="./gaviota", help="Root directory containing Gaviota *.gtb.cp4 files.")
    ap.add_argument("--sample", type=int, default=5000, help="Sample size per file (if fewer records, use all).")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    gaviota_root = Path(args.gaviota_root)

    if not data_dir.exists() or not data_dir.is_dir():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    gaviota_dirs = find_gaviota_dirs(gaviota_root)
    tb = open_tablebase_native_fixed(gaviota_dirs)

    rng = random.SystemRandom()

    rows: List[Tuple[str, int, int, int]] = []
    for path in choose_data_files(data_dir):
        mat = parse_material_from_filename(path)
        if mat is None:
            continue

        rec_len = mat.total_pieces
        sample_recs = reservoir_sample(iter_records(path, rec_len), args.sample, rng)

        wins = draws = losses = 0
        for rec in sample_recs:
            b = build_board_from_record(mat, rec)
            wdl = probe_wdl_white_from_dtm(tb, b)
            if wdl > 0:
                wins += 1
            elif wdl < 0:
                losses += 1
            else:
                draws += 1

        wp, dp, lp = compute_wdl_percentages(wins, draws, losses)
        rows.append((mat.label, wp, dp, lp))

    for label, wp, dp, lp in sorted(rows, key=lambda t: t[0]):
        print(f"{label}: win {wp}%  draw {dp}%  loss {lp}%")


if __name__ == "__main__":
    main()
