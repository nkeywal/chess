# generate_positions.py
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import chess
import chess.gaviota

import filters


PIECE_ORDER = "KQRBNP"
ALPHABET_64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-"

# Map piece letter to python-chess piece type.
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
    white: str  # canonical, e.g. "KPP"
    black: str  # canonical, e.g. "KR"

    @property
    def key(self) -> str:
        # Example: "kp_vs_kr"
        return f"{self.white.lower()}_vs_{self.black.lower()}"

    @property
    def filename(self) -> str:
        return f"{self.white}_{self.black}.txt"

    @property
    def total_pieces(self) -> int:
        return len(self.white) + len(self.black)


def canonicalize_material(s: str) -> str:
    s = s.strip().upper()
    for c in s:
        if c not in LETTER_TO_PIECE_TYPE:
            raise ValueError(f"Invalid piece letter: {c!r}. Allowed: KQRBNP.")
    if s.count("K") != 1:
        raise ValueError("Each side must contain exactly one King (K).")

    counts = {p: s.count(p) for p in PIECE_ORDER}
    return "".join(p * counts[p] for p in PIECE_ORDER)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate legal positions (White to move) and filter using optional TB probes."
    )
    p.add_argument("--w", required=True, help="White material, e.g. KPP, KR, KQ")
    p.add_argument("--b", required=True, help="Black material, e.g. K, KP, KR")
    return p.parse_args()


def find_gaviota_dirs(root: Path) -> List[Path]:
    """
    Find all directories under `root` that contain Gaviota table files (*.gtb.cp4).
    Return them sorted from smallest-piece folder to largest if possible, otherwise by path.
    Fail-fast if none found.
    """
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Gaviota root directory not found: {root}")

    dirs = set()
    for p in root.rglob("*.gtb.cp4"):
        dirs.add(p.parent)

    if not dirs:
        raise FileNotFoundError(f"No *.gtb.cp4 files found under: {root}")

    def sort_key(d: Path):
        try:
            n = int(d.name)
        except ValueError:
            n = 999
        return (n, str(d))

    return sorted(dirs, key=sort_key)


def square_is_pawn_legal(square: int) -> bool:
    # No pawn on rank 1 or rank 8. python-chess ranks are 0..7.
    r = chess.square_rank(square)
    return r != 0 and r != 7


def kings_not_adjacent(wk: int, bk: int) -> bool:
    wf = chess.square_file(wk)
    wr = chess.square_rank(wk)
    bf = chess.square_file(bk)
    br = chess.square_rank(bk)
    return max(abs(wf - bf), abs(wr - br)) > 1


def build_board(placements: Dict[Tuple[bool, int], Sequence[int]]) -> chess.Board:
    """
    Create a python-chess Board from placements.

    placements key: (color_is_white, piece_type) -> sorted squares
    """
    board = chess.Board(None)  # empty board
    board.clear_stack()
    board.turn = chess.WHITE
    board.castling_rights = 0
    board.ep_square = None
    board.halfmove_clock = 0
    board.fullmove_number = 1

    for (is_white, piece_type), squares in placements.items():
        color = chess.WHITE if is_white else chess.BLACK
        for sq in squares:
            board.set_piece_at(sq, chess.Piece(piece_type, color))

    return board


def is_position_valid(board: chess.Board) -> bool:
    """
    Position validity as per spec:
      - White to move
      - No pawn on rank 1 or rank 8
      - Kings exist, not adjacent
      - Black king NOT in check (since White to move, the side not to move cannot be in check)
      - White king may be in check
    """
    if board.turn != chess.WHITE:
        return False

    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    if wk is None or bk is None:
        return False

    if not kings_not_adjacent(wk, bk):
        return False

    for sq, piece in board.piece_map().items():
        if piece.piece_type == chess.PAWN and not square_is_pawn_legal(sq):
            return False

    if board.is_attacked_by(chess.WHITE, bk):
        return False

    return True


def groups_for_generation(material: Material) -> List[Tuple[bool, int, int]]:
    """
    Returns piece-groups in canonical KQRBNP order, for White then Black.

    Each group is: (is_white, piece_type, count)
    """
    def counts(s: str) -> Dict[str, int]:
        return {p: s.count(p) for p in PIECE_ORDER}

    w_counts = counts(material.white)
    b_counts = counts(material.black)

    groups: List[Tuple[bool, int, int]] = []
    for p in PIECE_ORDER:
        c = w_counts[p]
        if c:
            groups.append((True, LETTER_TO_PIECE_TYPE[p], c))
    for p in PIECE_ORDER:
        c = b_counts[p]
        if c:
            groups.append((False, LETTER_TO_PIECE_TYPE[p], c))
    return groups


def iter_group_squares(available: Sequence[int], piece_type: int, count: int) -> Iterable[Tuple[int, ...]]:
    """
    Yield combinations of squares for a group, respecting pawn constraints.
    """
    import itertools

    if piece_type == chess.PAWN:
        candidates = [sq for sq in available if square_is_pawn_legal(sq)]
    else:
        candidates = list(available)

    return itertools.combinations(candidates, count)


def generate_placements(groups: List[Tuple[bool, int, int]]) -> Iterable[Dict[Tuple[bool, int], Sequence[int]]]:
    """
    Exhaustively generate all placements (unique w.r.t. identical pieces).

    We place groups sequentially; each group chooses a combination of squares
    from remaining squares. This avoids duplicates for identical pieces.

    Output is a mapping:
      (is_white, piece_type) -> sorted tuple of squares
    """
    all_squares = tuple(range(64))

    def rec(i: int, remaining: Tuple[int, ...], acc: Dict[Tuple[bool, int], Sequence[int]]):
        if i == len(groups):
            yield acc
            return

        is_white, piece_type, count = groups[i]

        for combo in iter_group_squares(remaining, piece_type, count):
            chosen = set(combo)
            new_remaining = tuple(sq for sq in remaining if sq not in chosen)

            new_acc = dict(acc)
            new_acc[(is_white, piece_type)] = tuple(sorted(combo))

            yield from rec(i + 1, new_remaining, new_acc)

    yield from rec(0, all_squares, {})


def dtm_stm_to_white(dtm_stm: int, stm_color: bool) -> int:
    """
    Convert DTM from 'side to move' perspective to 'White perspective'.

    Gaviota DTM is signed for the side to move.
    """
    if stm_color == chess.WHITE:
        return dtm_stm
    return -dtm_stm


def probe_dtm_only_white_pov(tablebase: Any, board: chess.Board) -> Tuple[int, Optional[int]]:
    """
    Probe using ONLY probe_dtm() and normalize to White's perspective.

    Returns:
      - wdl_white: int in {-1, 0, +1} from White's perspective
      - dtm_white: Optional[int] in plies from White's perspective
          - None if draw

    Special-case: K vs K is always draw; no TB needed.
    """
    if len(board.piece_map()) == 2:
        return 0, None

    if board.is_checkmate():
        wdl_stm = -1  # side to move is checkmated
        dtm_stm = 0
        wdl_white = wdl_stm if board.turn == chess.WHITE else -wdl_stm
        dtm_white = dtm_stm_to_white(dtm_stm, board.turn)
        return wdl_white, dtm_white

    if board.is_stalemate():
        return 0, None

    dtm_stm = tablebase.probe_dtm(board)
    if dtm_stm == 0:
        return 0, None

    dtm_white = dtm_stm_to_white(dtm_stm, board.turn)

    wdl_stm = 1 if dtm_stm > 0 else -1
    wdl_white = wdl_stm if board.turn == chess.WHITE else -wdl_stm
    return wdl_white, dtm_white


def build_tb_info_with_probe(
    tablebase: Any,
    board: chess.Board,
    wdl_white: int,
    dtm_white: Optional[int],
) -> Dict[str, Any]:
    """
    Build TB info dict for filters, with an on-demand per-move probe:
      - wdl: int {-1,0,+1}, White POV
      - dtm: Optional[int], White POV (None if draw)
      - probe_move: callable(move) -> {uci, wdl, dtm}, White POV for the child
    """
    cache: Dict[str, Dict[str, Any]] = {}

    def probe_move(move: chess.Move) -> Dict[str, Any]:
        key = move.uci()
        cached = cache.get(key)
        if cached is not None:
            return cached

        board.push(move)
        w2, d2 = probe_dtm_only_white_pov(tablebase, board)
        board.pop()

        out = {
            "uci": key,
            "wdl": w2,
            "dtm": d2,
        }
        cache[key] = out
        return out

    return {
        "wdl": wdl_white,
        "dtm": dtm_white,
        "probe_move": probe_move,
    }


def encode_record(material: Material, board: chess.Board) -> str:
    """
    Encode the position as a fixed-length record:
      - White pieces (KQRBNP order), then Black pieces (KQRBNP)
      - For identical pieces, squares sorted by index
      - Each square encoded by one char via ALPHABET_64[0..63]
    """
    per: Dict[Tuple[bool, int], List[int]] = {}
    for sq, piece in board.piece_map().items():
        is_white = piece.color == chess.WHITE
        key = (is_white, piece.piece_type)
        per.setdefault(key, []).append(sq)

    for k in per:
        per[k].sort()

    out_chars: List[str] = []

    def emit_side(is_white: bool, mat: str):
        for letter in PIECE_ORDER:
            pt = LETTER_TO_PIECE_TYPE[letter]
            count = mat.count(letter)
            if count == 0:
                continue
            squares = per.get((is_white, pt), [])
            if len(squares) != count:
                raise RuntimeError("Internal error: piece counts mismatch while encoding.")
            for sq in squares:
                out_chars.append(ALPHABET_64[sq])

    emit_side(True, material.white)
    emit_side(False, material.black)

    return "".join(out_chars)


def get_filter_fn(name: str):
    fn = getattr(filters, name, None)
    if fn is None:
        return None
    if not callable(fn):
        raise TypeError(f"{name} exists but is not callable.")
    return fn


def fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.2f}h"


def main() -> None:
    args = parse_args()

    wcanon = canonicalize_material(args.w)
    bcanon = canonicalize_material(args.b)
    material = Material(white=wcanon, black=bcanon)

    if material.total_pieces > 5:
        raise ValueError(
            f"Total pieces must be <= 5, got {material.total_pieces} ({material.white} vs {material.black})."
        )

    if not callable(getattr(filters, "filter_notb_generic", None)):
        raise RuntimeError("filters.filter_notb_generic(board) must exist and be callable.")
    if not callable(getattr(filters, "filter_tb_generic", None)):
        raise RuntimeError("filters.filter_tb_generic(board, tb) must exist and be callable.")

    filter_notb_specific = get_filter_fn(f"filter_notb_{material.key}")
    filter_tb_specific = get_filter_fn(f"filter_tb_{material.key}")

    gaviota_root = Path("./gaviota")
    gaviota_dirs = find_gaviota_dirs(gaviota_root)

    out_dir = Path("./out")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / material.filename

    # Counters.
    candidates_total = 0
    valid_positions = 0

    rejected_notb_generic = 0
    rejected_notb_specific = 0
    passed_notb = 0

    rejected_tb_generic = 0
    rejected_tb_specific = 0
    accepted = 0

    # Accepted-position stats (root outcome, White POV).
    accepted_win = 0
    accepted_draw = 0
    accepted_loss = 0

    # DTM stats split by winner (positive values only).
    dtm_white_count = 0
    dtm_white_sum = 0
    dtm_white_min: Optional[int] = None
    dtm_white_max: Optional[int] = None

    dtm_black_count = 0
    dtm_black_sum = 0
    dtm_black_min: Optional[int] = None
    dtm_black_max: Optional[int] = None

    tb_opened = False

    t0 = time.perf_counter()

    def log_progress() -> None:
        elapsed = time.perf_counter() - t0
        rate_valid = (valid_positions / elapsed) if elapsed > 0 else 0.0
        rate_accepted = (accepted / elapsed) if elapsed > 0 else 0.0

        print(
            "progress:"
            f" elapsed={fmt_elapsed(elapsed)}"
            f" candidates={candidates_total}"
            f" valid={valid_positions}"
            f" passed_notb={passed_notb}"
            f" accepted={accepted}"
            f" rate_valid={rate_valid:,.0f}/s"
            f" rate_accepted={rate_accepted:,.0f}/s"
            f" | notb_rej_generic={rejected_notb_generic}"
            f" notb_rej_specific={rejected_notb_specific}"
            f" tb_rej_generic={rejected_tb_generic}"
            f" tb_rej_specific={rejected_tb_specific}"
            f" | tb_opened={tb_opened}"
        )

    with out_path.open("w", encoding="ascii", newline="") as f_out:
        tablebase = None  # lazy init

        groups = groups_for_generation(material)

        for placement in generate_placements(groups):
            candidates_total += 1

            board = build_board(placement)

            if not is_position_valid(board):
                continue

            valid_positions += 1

            # Stage A: no-tablebase filters.
            if not filters.filter_notb_generic(board):
                rejected_notb_generic += 1
                continue

            if filter_notb_specific is not None and not filter_notb_specific(board):
                rejected_notb_specific += 1
                continue

            passed_notb += 1

            # Stage B: tablebase stage (lazy open).
            if tablebase is None:
                tablebase = chess.gaviota.open_tablebase(str(gaviota_dirs[0]))
                for d in gaviota_dirs[1:]:
                    tablebase.add_directory(str(d))
                tb_opened = True

            # Probe only DTM for the root position first.
            wdl_white, dtm_white = probe_dtm_only_white_pov(tablebase, board)

            # Build TB info with on-demand per-move probe (expensive only if needed).
            tb_info = build_tb_info_with_probe(tablebase, board, wdl_white, dtm_white)

            if not filters.filter_tb_generic(board, tb_info):
                rejected_tb_generic += 1
                continue

            if filter_tb_specific is not None and not filter_tb_specific(board, tb_info):
                rejected_tb_specific += 1
                continue

            # Accepted -> write record (no separators, no newline).
            f_out.write(encode_record(material, board))
            accepted += 1

            # Update accepted-position stats (root outcome).
            if wdl_white > 0:
                accepted_win += 1
            elif wdl_white < 0:
                accepted_loss += 1
            else:
                accepted_draw += 1

            # DTM stats split by winner.
            if dtm_white is not None:
                if wdl_white > 0:
                    v = dtm_white
                    dtm_white_count += 1
                    dtm_white_sum += v
                    if dtm_white_min is None or v < dtm_white_min:
                        dtm_white_min = v
                    if dtm_white_max is None or v > dtm_white_max:
                        dtm_white_max = v
                elif wdl_white < 0:
                    v = abs(dtm_white)
                    dtm_black_count += 1
                    dtm_black_sum += v
                    if dtm_black_min is None or v < dtm_black_min:
                        dtm_black_min = v
                    if dtm_black_max is None or v > dtm_black_max:
                        dtm_black_max = v

            if accepted % 100000 == 0:
                log_progress()

        if tablebase is not None:
            tablebase.close()

    elapsed = time.perf_counter() - t0

    print("done:")
    print(f"  output: {out_path}")
    print(f"  elapsed: {fmt_elapsed(elapsed)}")
    print(f"  candidates_total: {candidates_total}")
    print(f"  valid_positions: {valid_positions}")
    print(f"  passed_notb: {passed_notb}")
    print(f"  accepted: {accepted}")
    print(f"  rejected_notb_generic: {rejected_notb_generic}")
    print(f"  rejected_notb_specific: {rejected_notb_specific}")
    print(f"  rejected_tb_generic: {rejected_tb_generic}")
    print(f"  rejected_tb_specific: {rejected_tb_specific}")

    print(f"  accepted_win: {accepted_win}")
    print(f"  accepted_draw: {accepted_draw}")
    print(f"  accepted_loss: {accepted_loss}")

    if dtm_white_count > 0:
        dtm_white_avg = dtm_white_sum / dtm_white_count
        print(f"  dtmWhiteMin: {dtm_white_min}")
        print(f"  dtmWhiteMax: {dtm_white_max}")
        print(f"  dtmWhiteAvg: {dtm_white_avg:.2f}")
    else:
        print("  dtmWhiteMin: n/a")
        print("  dtmWhiteMax: n/a")
        print("  dtmWhiteAvg: n/a")

    if dtm_black_count > 0:
        dtm_black_avg = dtm_black_sum / dtm_black_count
        print(f"  dtmBlackMin: {dtm_black_min}")
        print(f"  dtmBlackMax: {dtm_black_max}")
        print(f"  dtmBlackAvg: {dtm_black_avg:.2f}")
    else:
        print("  dtmBlackMin: n/a")
        print("  dtmBlackMax: n/a")
        print("  dtmBlackAvg: n/a")

    if elapsed > 0:
        print(f"  rate_valid_per_sec: {valid_positions/elapsed:,.0f}")
        print(f"  rate_accepted_per_sec: {accepted/elapsed:,.0f}")

    print(f"  tb_opened: {tb_opened}")
    if tb_opened:
        print(f"  gaviota_dirs: {[str(d) for d in gaviota_dirs]}")


if __name__ == "__main__":
    main()
