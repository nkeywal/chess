# extract_puzzles_5men.py
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import csv
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, TextIO, Tuple

import chess


PIECE_ORDER = "KQRBNP"
MIN_RATING = 1000
MAX_PLIES = 6  # 3 full moves = 6 plies


@dataclass(frozen=True)
class Material:
    white: str  # canonical, e.g. "KRP"
    black: str  # canonical, e.g. "KR"

    @property
    def filename(self) -> str:
        return f"puzzle_{self.white}_{self.black}.txt"

    @property
    def key(self) -> str:
        return f"{self.white}_{self.black}"


@dataclass
class Stats:
    scanned: int = 0
    kept: int = 0
    inverted: int = 0

    excluded_rating: int = 0
    excluded_missing_moves: int = 0

    excluded_piece_count_fast: int = 0
    excluded_piece_count_post: int = 0
    excluded_castling_rights: int = 0

    excluded_illegal_move0: int = 0
    excluded_illegal_move1: int = 0
    excluded_second_ep: int = 0

    excluded_illegal_solution: int = 0
    excluded_trivial_fast: int = 0  # mate<=3 or black-only-king<=3 (when >=6 plies available)

    excluded_invalid_fen: int = 0
    missing_fields: int = 0


@dataclass
class OnlineAgg:
    count: int = 0
    sum_rating: float = 0.0
    sum_pop: float = 0.0
    min_rating: int = 10**9
    max_rating: int = -10**9
    min_pop: int = 10**9
    max_pop: int = -10**9

    rating_hist: Optional[Dict[int, int]] = None
    pop_hist: Optional[Dict[int, int]] = None

    def __post_init__(self) -> None:
        self.rating_hist = self.rating_hist or {}
        self.pop_hist = self.pop_hist or {}

    def add(self, rating: int, pop: int) -> None:
        self.count += 1
        self.sum_rating += rating
        self.sum_pop += pop
        self.min_rating = min(self.min_rating, rating)
        self.max_rating = max(self.max_rating, rating)
        self.min_pop = min(self.min_pop, pop)
        self.max_pop = max(self.max_pop, pop)

        self.rating_hist[rating // 50] = self.rating_hist.get(rating // 50, 0) + 1
        self.pop_hist[pop // 10] = self.pop_hist.get(pop // 10, 0) + 1

    def avg_rating(self) -> float:
        return self.sum_rating / self.count if self.count else 0.0

    def avg_pop(self) -> float:
        return self.sum_pop / self.count if self.count else 0.0

    def _hist_percentile(self, hist: Dict[int, int], p: float) -> int:
        if not hist or self.count == 0:
            return 0
        target = max(1, int(math.ceil(p * self.count)))
        cum = 0
        for b in sorted(hist.keys()):
            cum += hist[b]
            if cum >= target:
                return b
        return max(hist.keys())

    def rating_p50(self) -> int:
        return self._hist_percentile(self.rating_hist, 0.50) * 50

    def rating_p90(self) -> int:
        return self._hist_percentile(self.rating_hist, 0.90) * 50

    def pop_p50(self) -> int:
        return self._hist_percentile(self.pop_hist, 0.50) * 10

    def pop_p90(self) -> int:
        return self._hist_percentile(self.pop_hist, 0.90) * 10


def fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.2f}h"


def canonicalize_material(s: str) -> str:
    s = s.strip().upper()
    for c in s:
        if c not in PIECE_ORDER:
            raise ValueError(f"Invalid piece letter: {c!r}. Allowed: KQRBNP.")
    counts = {p: s.count(p) for p in PIECE_ORDER}
    return "".join(p * counts[p] for p in PIECE_ORDER)


def material_from_board(board: chess.Board) -> Material:
    w_counts = {p: 0 for p in PIECE_ORDER}
    b_counts = {p: 0 for p in PIECE_ORDER}

    for pt, letter in [
        (chess.KING, "K"),
        (chess.QUEEN, "Q"),
        (chess.ROOK, "R"),
        (chess.BISHOP, "B"),
        (chess.KNIGHT, "N"),
        (chess.PAWN, "P"),
    ]:
        w_counts[letter] = len(board.pieces(pt, chess.WHITE))
        b_counts[letter] = len(board.pieces(pt, chess.BLACK))

    w = "".join(p * w_counts[p] for p in PIECE_ORDER)
    b = "".join(p * b_counts[p] for p in PIECE_ORDER)
    return Material(white=canonicalize_material(w), black=canonicalize_material(b))


def piece_count_from_fen_placement(fen: str) -> int:
    placement = fen.split(" ", 1)[0]
    return sum(1 for ch in placement if ch.isalpha())


def square_rot180(sq: int) -> int:
    return 63 - sq


def rotate_uci_180(uci: str) -> str:
    """
    Rotate an UCI move 180 degrees (a1<->h8) by mapping from/to squares.
    Promotion letter (if any) is preserved.
    """
    if len(uci) < 4:
        return uci
    a = chess.parse_square(uci[0:2])
    b = chess.parse_square(uci[2:4])
    a2 = square_rot180(a)
    b2 = square_rot180(b)
    promo = uci[4:] if len(uci) > 4 else ""
    return chess.square_name(a2) + chess.square_name(b2) + promo


def invert_position_if_black_to_move(board: chess.Board) -> chess.Board:
    """
    If it's Black to move, convert the position so that the solver becomes White:
      - rotate 180 degrees
      - swap colors
      - keep EP square mapped (for correct move legality during simulation)
    """
    if board.turn == chess.WHITE:
        return board

    b2 = chess.Board()
    b2.clear_board()

    for sq, piece in board.piece_map().items():
        b2.set_piece_at(square_rot180(sq), chess.Piece(piece.piece_type, not piece.color))

    b2.turn = chess.WHITE
    b2.castling_rights = 0

    if board.ep_square is None:
        b2.ep_square = None
    else:
        b2.ep_square = square_rot180(board.ep_square)

    b2.halfmove_clock = board.halfmove_clock
    b2.fullmove_number = 1
    return b2


def black_has_only_king(board: chess.Board) -> bool:
    """
    True iff Black has no pieces other than the King.
    """
    for pt in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN):
        if board.pieces(pt, chess.BLACK):
            return False
    return True


def stable_fen_for_output(board: chess.Board) -> str:
    """
    Output format used by your pipeline: always white to move, no castling, no EP.
    """
    return f"{board.board_fen()} w - - 0 1"


def zstd_csv_rows(zst_path: Path) -> Iterable[Dict[str, str]]:
    """
    Stream rows from a .csv.zst file using `zstd -dc`.
    Requires `zstd` installed.
    """
    import subprocess

    proc = subprocess.Popen(
        ["zstd", "-dc", str(zst_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    if proc.stdout is None:
        raise RuntimeError("Unable to open zstd stream. Is zstd installed?")

    reader = csv.DictReader(proc.stdout)
    if not reader.fieldnames:
        proc.wait()
        raise RuntimeError("CSV stream has no header.")

    try:
        for row in reader:
            yield row
    finally:
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"zstd exited with code {proc.returncode}")


def get_handle(handles: Dict[Path, TextIO], path: Path) -> TextIO:
    h = handles.get(path)
    if h is not None:
        return h
    path.parent.mkdir(parents=True, exist_ok=True)
    h = path.open("a", encoding="utf-8")
    handles[path] = h
    return h


def close_handles(handles: Dict[Path, TextIO]) -> None:
    for h in handles.values():
        try:
            h.close()
        except Exception:
            pass


def write_stats_by_material(path: Path, by_mat: Dict[str, OnlineAgg]) -> None:
    lines = []
    for key in sorted(by_mat.keys()):
        a = by_mat[key]
        lines.append(
            f"{key}\tcount={a.count}"
            f"\trating=min:{a.min_rating} max:{a.max_rating} avg:{a.avg_rating():.1f} p50~{a.rating_p50()} p90~{a.rating_p90()}"
            f"\tpop=min:{a.min_pop} max:{a.max_pop} avg:{a.avg_pop():.1f} p50~{a.pop_p50()} p90~{a.pop_p90()}"
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Extract <=5-piece Lichess puzzles (post-blunder), with filters:\n"
            "- rating >= 1000 (first)\n"
            "- exclude if solver first move (move1) is en-passant\n"
            "- exclude if (within first 6 plies of the provided line) there is checkmate OR black is reduced to lone king\n"
            "  but only when at least 6 plies are available; otherwise keep.\n"
            "- invert only if the real puzzle position is Black-to-move."
        )
    )
    p.add_argument("--in", dest="inp", required=True, type=Path, help="Input lichess_db_puzzle.csv.zst")
    p.add_argument("--out", dest="out_dir", required=True, type=Path, help="Output directory")
    p.add_argument("--max-pieces", type=int, default=5, help="Max pieces after blunder (including kings)")
    p.add_argument("--stats", dest="stats_path", type=Path, default=None, help="Write stats to this file")
    p.add_argument("--min-rating", type=int, default=MIN_RATING, help="Minimum puzzle rating (default 1000)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    zst_path: Path = args.inp
    out_dir: Path = args.out_dir
    max_pieces: int = args.max_pieces
    min_rating: int = args.min_rating

    required = {"PuzzleId", "FEN", "Moves", "Rating", "Popularity"}

    t0 = time.time()
    stats = Stats()
    handles: Dict[Path, TextIO] = {}

    by_mat: Dict[str, OnlineAgg] = {}
    global_agg = OnlineAgg()

    try:
        for row in zstd_csv_rows(zst_path):
            stats.scanned += 1

            if not required.issubset(row.keys()):
                stats.missing_fields += 1
                continue

            # Rating filter FIRST.
            rating_s = row.get("Rating", "")
            try:
                rating_i = int(float(rating_s))
            except ValueError:
                stats.missing_fields += 1
                continue
            if rating_i < min_rating:
                stats.excluded_rating += 1
                continue

            pop_s = row.get("Popularity", "")
            try:
                pop_i = int(float(pop_s))
            except ValueError:
                stats.missing_fields += 1
                continue

            moves_field = (row.get("Moves", "") or "").strip()
            moves = moves_field.split()
            if len(moves) < 2:
                stats.excluded_missing_moves += 1
                continue

            fen0 = row["FEN"]

            # Fast piece-count prefilter on the *pre-blunder* position.
            # After one move, piece count can only drop by at most 1 (capture).
            n0 = piece_count_from_fen_placement(fen0)
            if n0 >= max_pieces + 2:
                stats.excluded_piece_count_fast += 1
                continue

            # Fast castling rights prefilter (string-level).
            parts0 = fen0.split()
            if len(parts0) < 4:
                stats.excluded_invalid_fen += 1
                continue
            if parts0[2] != "-":
                stats.excluded_castling_rights += 1
                continue

            # Parse the position and apply the blunder move0.
            try:
                b = chess.Board(fen0)
            except Exception:
                stats.excluded_invalid_fen += 1
                continue

            try:
                mv0 = chess.Move.from_uci(moves[0])
            except Exception:
                stats.excluded_illegal_move0 += 1
                continue
            if not b.is_legal(mv0):
                stats.excluded_illegal_move0 += 1
                continue
            b.push(mv0)  # real puzzle position (solver to move)

            # Reject any castling rights at puzzle start (should be irrelevant for <=5, but keep strict).
            if b.castling_rights:
                stats.excluded_castling_rights += 1
                continue

            # Post-blunder piece-count filter (exact).
            if len(b.piece_map()) > max_pieces:
                stats.excluded_piece_count_post += 1
                continue

            # Exclude if solver first move (move1) is en-passant.
            try:
                mv1 = chess.Move.from_uci(moves[1])
            except Exception:
                stats.excluded_illegal_move1 += 1
                continue
            if not b.is_legal(mv1):
                stats.excluded_illegal_move1 += 1
                continue
            if b.is_en_passant(mv1):
                stats.excluded_second_ep += 1
                continue

            # Invert only if puzzle start is Black-to-move, and rotate the line accordingly.
            inverted = False
            if b.turn == chess.BLACK:
                b2 = invert_position_if_black_to_move(b)
                inverted = True
                stats.inverted += 1
            else:
                b2 = b.copy(stack=False)

            # Apply the "trivial-fast" filter only if we have >= 6 plies available after move0.
            # That means we need at least moves[1]..moves[6].
            if len(moves) >= 1 + MAX_PLIES + 1:
                # Prepare the first 6 plies from the file after the blunder (moves[1:7]).
                seq = moves[1 : 1 + MAX_PLIES]
                if inverted:
                    seq = [rotate_uci_180(m) for m in seq]

                tmp = b2.copy(stack=False)

                # Check condition at start (0 plies), within <=3 moves.
                mate_or_lone = tmp.is_checkmate() or black_has_only_king(tmp)

                if not mate_or_lone:
                    for u in seq:
                        try:
                            mv = chess.Move.from_uci(u)
                        except Exception:
                            stats.excluded_illegal_solution += 1
                            mate_or_lone = False
                            break

                        if not tmp.is_legal(mv):
                            stats.excluded_illegal_solution += 1
                            mate_or_lone = False
                            break

                        tmp.push(mv)

                        # If mate occurs within <= 6 plies -> exclude.
                        if tmp.is_checkmate():
                            mate_or_lone = True
                            break

                        # If Black is reduced to lone king within <= 6 plies -> exclude.
                        if black_has_only_king(tmp):
                            mate_or_lone = True
                            break

                # If we encountered illegal solution moves, we already counted it and keep conservative behavior:
                # We do NOT exclude for "trivial-fast" in that case (since we could not evaluate reliably).
                if stats.excluded_illegal_solution > 0 and mate_or_lone is False:
                    pass
                else:
                    if mate_or_lone:
                        stats.excluded_trivial_fast += 1
                        continue
            # else: fewer than 6 plies available => keep (as requested)

            # Finalize output position:
            # - Always white to move by construction.
            # - Remove castling and EP (TE-friendly).
            out_board = b2.copy(stack=False)
            out_board.turn = chess.WHITE
            out_board.castling_rights = 0
            out_board.ep_square = None
            out_board.halfmove_clock = 0
            out_board.fullmove_number = 1

            mat = material_from_board(out_board)
            fen_out = stable_fen_for_output(out_board)

            out_path = out_dir / mat.filename
            h = get_handle(handles, out_path)
            # Added outcome 'W' to the record (puzzles are wins)
            h.write(f"{row['PuzzleId']}\t{rating_s}\t{pop_s}\t{fen_out}\tW\n")

            agg = by_mat.get(mat.key)
            if agg is None:
                agg = OnlineAgg()
                by_mat[mat.key] = agg
            agg.add(rating_i, pop_i)
            global_agg.add(rating_i, pop_i)

            stats.kept += 1

    finally:
        close_handles(handles)

    elapsed = time.time() - t0
    out_dir.mkdir(parents=True, exist_ok=True)

    stats_path = args.stats_path or (out_dir / "stats_by_material.txt")
    write_stats_by_material(stats_path, by_mat)

    global_path = out_dir / "stats_global.txt"
    global_path.write_text(
        "global\n"
        f"count={global_agg.count}\n"
        f"rating_min={global_agg.min_rating} rating_max={global_agg.max_rating} rating_avg={global_agg.avg_rating():.1f} "
        f"rating_p50~{global_agg.rating_p50()} rating_p90~{global_agg.rating_p90()}\n"
        f"pop_min={global_agg.min_pop} pop_max={global_agg.max_pop} pop_avg={global_agg.avg_pop():.1f} "
        f"pop_p50~{global_agg.pop_p50()} pop_p90~{global_agg.pop_p90()}\n",
        encoding="utf-8",
    )

    print(
        "done:\n"
        f"  elapsed: {fmt_elapsed(elapsed)}\n"
        f"  scanned: {stats.scanned}\n"
        f"  kept: {stats.kept}\n"
        f"  inverted: {stats.inverted}\n"
        f"  excluded_rating: {stats.excluded_rating}\n"
        f"  excluded_missing_moves: {stats.excluded_missing_moves}\n"
        f"  excluded_piece_count_fast: {stats.excluded_piece_count_fast}\n"
        f"  excluded_piece_count_post: {stats.excluded_piece_count_post}\n"
        f"  excluded_castling_rights: {stats.excluded_castling_rights}\n"
        f"  excluded_illegal_move0: {stats.excluded_illegal_move0}\n"
        f"  excluded_illegal_move1: {stats.excluded_illegal_move1}\n"
        f"  excluded_second_ep: {stats.excluded_second_ep}\n"
        f"  excluded_illegal_solution: {stats.excluded_illegal_solution}\n"
        f"  excluded_trivial_fast: {stats.excluded_trivial_fast}\n"
        f"  excluded_invalid_fen: {stats.excluded_invalid_fen}\n"
        f"  missing_fields: {stats.missing_fields}\n"
        f"  stats_by_material: {stats_path}\n"
        f"  stats_global: {global_path}\n"
        f"  out_dir: {out_dir}"
    )


if __name__ == "__main__":
    main()
