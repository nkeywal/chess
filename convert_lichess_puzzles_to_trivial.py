# convert_lichess_puzzles_to_trivial.py
# All code/comments in English as requested.

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import chess


PIECE_ORDER = "KQRBNP"
ALPHABET_64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-"


LETTER_TO_PIECE_TYPE = {
    "K": chess.KING,
    "Q": chess.QUEEN,
    "R": chess.ROOK,
    "B": chess.BISHOP,
    "N": chess.KNIGHT,
    "P": chess.PAWN,
}


@dataclass(frozen=True)
class Material:
    white: str  # canonical e.g. "KRP"
    black: str  # canonical e.g. "KR"

    @property
    def key(self) -> str:
        return f"{self.white}_{self.black}"

    @property
    def src_filename(self) -> str:
        return f"puzzle_{self.white}_{self.black}.txt"

    @property
    def dst_filename(self) -> str:
        # Trivial-endgames format files in your repo are typically named like "KR_KP.txt"
        return f"{self.white}_{self.black}.txt"

    @property
    def total_pieces(self) -> int:
        return len(self.white) + len(self.black)


def canonicalize_material(s: str) -> str:
    s = s.strip().upper()
    for c in s:
        if c not in LETTER_TO_PIECE_TYPE:
            raise ValueError(f"Invalid piece letter: {c!r}. Allowed: KQRBNP.")
    counts = {p: s.count(p) for p in PIECE_ORDER}
    return "".join(p * counts[p] for p in PIECE_ORDER)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Convert Lichess puzzle_* files (PuzzleId/Rating/Popularity/FEN) into "
            "Trivial Endgames compact format (1 byte per piece, no separators), "
            "for materials with count > threshold in stats_by_material.txt."
        )
    )
    p.add_argument("--src", required=True, type=Path, help="Source directory containing puzzle_*.txt and stats_by_material.txt")
    p.add_argument("--dst", required=True, type=Path, help="Destination directory for Trivial Endgames files")
    p.add_argument("--stats", default=None, type=Path, help="Path to stats_by_material.txt (default: <src>/stats_by_material.txt)")
    p.add_argument("--min-count", type=int, default=51, help="Minimum Lichess puzzle count to include (default: 51 means > 50)")
    return p.parse_args()


def parse_stats_by_material(path: Path) -> Dict[str, int]:
    """
    Parse lines like:
      KP_KP  count=11917 rating=min:...  pop=min:...
    Returns: { "KP_KP": 11917, ... }
    """
    out: Dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # First token is the material key.
        parts = line.split("\t")
        key = parts[0].strip()
        count = None
        for tok in parts[1:]:
            tok = tok.strip()
            if tok.startswith("count="):
                try:
                    count = int(tok.split("=", 1)[1])
                except ValueError:
                    count = None
                break
        if count is not None:
            out[key] = count
    return out


def material_from_key(key: str) -> Material:
    if "_" not in key:
        raise ValueError(f"Invalid material key: {key!r}")
    w, b = key.split("_", 1)
    return Material(white=canonicalize_material(w), black=canonicalize_material(b))


def squares_for_piece_set(board: chess.Board, is_white: bool, piece_letter: str) -> List[int]:
    """
    Return sorted list of squares for the given side and piece letter.
    """
    pt = LETTER_TO_PIECE_TYPE[piece_letter]
    color = chess.WHITE if is_white else chess.BLACK
    squares = list(board.pieces(pt, color))
    squares.sort()
    return squares


def encode_position(board: chess.Board, material: Material) -> bytes:
    """
    Encode a position to the Trivial Endgames compact format:
      - 1 byte per piece (square index 0..63 mapped through ALPHABET_64)
      - Order is canonical piece order KQRBNP for White material string, then for Black.
      - Within identical pieces, squares are sorted ascending.

    Output is raw bytes with no newline.
    """
    out_chars: List[str] = []

    # White pieces in material.white order (already canonical KQRBNP with repeats).
    for ch in material.white:
        # For repeated letters, we will gather all squares once per letter type,
        # so we need a per-type cursor.
        pass

    # Implement with per-type cursors to avoid O(n^2) rescans.
    def build_side(is_white: bool, mat: str) -> List[str]:
        by_type: Dict[str, List[int]] = {}
        idx: Dict[str, int] = {}
        for letter in set(mat):
            by_type[letter] = squares_for_piece_set(board, is_white, letter)
            idx[letter] = 0

        side_out: List[str] = []
        for letter in mat:
            i = idx[letter]
            sqs = by_type[letter]
            if i >= len(sqs):
                raise ValueError("FEN does not match expected material (missing piece).")
            sq = sqs[i]
            idx[letter] = i + 1
            side_out.append(ALPHABET_64[sq])
        return side_out

    out_chars.extend(build_side(True, material.white))
    out_chars.extend(build_side(False, material.black))

    return "".join(out_chars).encode("ascii")


def iter_puzzle_data(path: Path) -> Iterable[Tuple[str, str]]:
    """
    puzzle_<W>_<B>.txt lines are:
      PuzzleId<TAB>Rating<TAB>Popularity<TAB>FEN<TAB>Outcome
    Yield (FEN, Outcome).
    """
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            fen = parts[3]
            outcome = parts[4] if len(parts) > 4 else "W"
            yield fen, outcome


def main() -> None:
    args = parse_args()
    src_dir: Path = args.src
    dst_dir: Path = args.dst
    stats_path: Path = args.stats or (src_dir / "stats_by_material.txt")
    min_count: int = args.min_count

    if not stats_path.exists():
        raise FileNotFoundError(f"stats_by_material.txt not found: {stats_path}")

    stats = parse_stats_by_material(stats_path)

    # Select materials with count >= min_count (default 51 => >50).
    selected_keys = [k for k, c in stats.items() if c >= min_count]
    selected_keys.sort(key=lambda k: stats[k], reverse=True)

    dst_dir.mkdir(parents=True, exist_ok=True)

    converted_files = 0
    converted_positions = 0

    for key in selected_keys:
        mat = material_from_key(key)
        src_path = src_dir / mat.src_filename
        if not src_path.exists():
            # The stats file may include materials you did not write out (should be rare).
            continue

        dst_path = dst_dir / mat.dst_filename

        n_pos = 0
        n_bad = 0

        # Write raw bytes, no separators (file size == positions * (total_pieces + 1)).
        with dst_path.open("wb") as out:
            for fen, outcome in iter_puzzle_data(src_path):
                try:
                    board = chess.Board(fen)
                    # Your extraction already normalized to white-to-move; still enforce.
                    if board.turn != chess.WHITE:
                        # If this happens, skip to avoid mixing conventions.
                        n_bad += 1
                        continue

                    # Encode and write.
                    enc = encode_position(board, mat)
                    out.write(enc + outcome.encode("ascii"))
                    n_pos += 1
                except Exception:
                    n_bad += 1
                    continue

        converted_files += 1
        converted_positions += n_pos

        # Basic sanity check on file size.
        expected = n_pos * (mat.total_pieces + 1)
        actual = dst_path.stat().st_size
        if actual != expected:
            raise RuntimeError(
                f"Size mismatch for {dst_path.name}: expected {expected} bytes "
                f"({n_pos} * {mat.total_pieces + 1}), got {actual}."
            )

        print(
            f"{key}: lichess_count={stats[key]} converted={n_pos} skipped={n_bad} "
            f"-> {dst_path} (bytes={actual})"
        )

    print(f"done: files={converted_files} positions={converted_positions} dst={dst_dir}")


if __name__ == "__main__":
    main()
