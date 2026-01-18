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

ALL_SQUARES_MASK = (1 << 64) - 1

# Pawns cannot be on rank 1 or rank 8.
# square index: a1=0 .. h8=63, rank = sq>>3 in [0..7]
PAWN_SQUARES_MASK = 0
for sq in range(64):
    r = sq >> 3
    if r != 0 and r != 7:
        PAWN_SQUARES_MASK |= (1 << sq)


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


# ----------------------------
# Fast generation (no Board)
# ----------------------------

def _iter_bits(mask: int) -> Iterable[int]:
    """Yield square indices for each 1 bit in mask (ascending)."""
    while mask:
        lsb = mask & -mask
        sq = lsb.bit_length() - 1
        yield sq
        mask ^= lsb


def _iter_k_combos(mask: int, k: int) -> Iterable[Tuple[int, ...]]:
    """
    Iterate combinations of k squares from a bitmask.
    Optimized for k in {1,2,3}. Falls back to a slower path for larger k (should not happen for total<=5).
    """
    if k == 1:
        for a in _iter_bits(mask):
            yield (a,)
        return

    if k == 2:
        m1 = mask
        while m1:
            l1 = m1 & -m1
            a = l1.bit_length() - 1
            m1 ^= l1

            m2 = mask & ~((1 << (a + 1)) - 1)  # keep bits > a
            while m2:
                l2 = m2 & -m2
                b = l2.bit_length() - 1
                m2 ^= l2
                yield (a, b)
        return

    if k == 3:
        m1 = mask
        while m1:
            l1 = m1 & -m1
            a = l1.bit_length() - 1
            m1 ^= l1

            m2 = mask & ~((1 << (a + 1)) - 1)  # bits > a
            while m2:
                l2 = m2 & -m2
                b = l2.bit_length() - 1
                m2 ^= l2

                m3 = m2 & ~((1 << (b + 1)) - 1)  # bits > b, and already > a
                while m3:
                    l3 = m3 & -m3
                    c = l3.bit_length() - 1
                    m3 ^= l3
                    yield (a, b, c)
        return

    # Fallback (should not be used for total pieces <= 5).
    import itertools
    squares = list(_iter_bits(mask))
    for combo in itertools.combinations(squares, k):
        yield combo


def _build_king_adjacency_masks() -> List[int]:
    """
    For each square s, return a bitmask of squares within Chebyshev distance <= 1 (including s).
    Used to forbid adjacent kings.
    """
    adj = [0] * 64
    for s in range(64):
        f = s & 7
        r = s >> 3
        m = 0
        for df in (-1, 0, 1):
            ff = f + df
            if ff < 0 or ff > 7:
                continue
            for dr in (-1, 0, 1):
                rr = r + dr
                if rr < 0 or rr > 7:
                    continue
                sq = (rr << 3) | ff
                m |= (1 << sq)
        adj[s] = m
    return adj


KING_ADJ_MASK = _build_king_adjacency_masks()


def _rook_or_bishop_line_attacks(from_sq: int, to_sq: int, occupied: int, step: int) -> bool:
    """
    Return True if a sliding piece on from_sq attacks to_sq along 'step' (±1, ±7, ±8, ±9),
    given the full occupied bitboard.
    Assumes from_sq and to_sq are aligned for that step direction.
    """
    sq = from_sq + step
    while sq != to_sq:
        if (occupied >> sq) & 1:
            return False
        sq += step
    return True


def _white_attacks_square(bk_sq: int, white_pieces: List[Tuple[int, int]], occupied: int) -> bool:
    """
    Determine if bk_sq is attacked by any white piece in white_pieces.
    white_pieces: list of (piece_type, square) excluding the white king.
    occupied: bitboard of all pieces (both colors), BK may be included (doesn't matter for this logic).
    """
    bk_f = bk_sq & 7
    bk_r = bk_sq >> 3

    for pt, sq in white_pieces:
        sf = sq & 7
        sr = sq >> 3
        df = bk_f - sf
        dr = bk_r - sr

        if pt == chess.PAWN:
            # White pawn attacks (sr+1, sf±1) -> +7/+9 in square indexing
            # Equivalent coordinate check:
            if dr == 1 and (df == -1 or df == 1):
                return True

        elif pt == chess.KNIGHT:
            adf = df if df >= 0 else -df
            adr = dr if dr >= 0 else -dr
            if (adf == 1 and adr == 2) or (adf == 2 and adr == 1):
                return True

        elif pt == chess.BISHOP:
            if df == dr and df != 0:
                step = 9 if df > 0 else -9
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True
            elif df == -dr and df != 0:
                step = 7 if df < 0 else -7  # careful with signs in square indexing
                # Let's derive properly:
                # If df = bk_f - sf, dr = bk_r - sr.
                # Moving NE is +9 (df>0, dr>0, df==dr)
                # Moving NW is +7 (df<0, dr>0, -df==dr)
                # Moving SE is -7 (df>0, dr<0, df==-dr)
                # Moving SW is -9 (df<0, dr<0, df==dr)
                # Here df == -dr.
                if df > 0 and dr < 0:
                    step = -7
                elif df < 0 and dr > 0:
                    step = 7
                else:
                    # Should not happen if df == -dr, but keep safe.
                    step = 7 if dr > 0 else -7
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True

        elif pt == chess.ROOK:
            if df == 0 and dr != 0:
                step = 8 if dr > 0 else -8
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True
            elif dr == 0 and df != 0:
                step = 1 if df > 0 else -1
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True

        elif pt == chess.QUEEN:
            # Rook-like
            if df == 0 and dr != 0:
                step = 8 if dr > 0 else -8
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True
            elif dr == 0 and df != 0:
                step = 1 if df > 0 else -1
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True
            # Bishop-like
            elif df == dr and df != 0:
                step = 9 if df > 0 else -9
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True
            elif df == -dr and df != 0:
                if df > 0 and dr < 0:
                    step = -7
                elif df < 0 and dr > 0:
                    step = 7
                else:
                    step = 7 if dr > 0 else -7
                if _rook_or_bishop_line_attacks(sq, bk_sq, occupied, step):
                    return True

        else:
            # White king is excluded from white_pieces (and kings adjacency is enforced elsewhere).
            pass

    return False


def generate_valid_square_placements(material: Material) -> Iterable[Tuple[int, int, List[Tuple[bool, int, Tuple[int, ...]]]]]:
    """
    Generate ONLY "valid positions" per spec, without building a Board:
      - White to move (handled later)
      - No pawn on rank 1/8 (enforced by pawn masks)
      - Kings not adjacent (enforced by BK choice using KING_ADJ_MASK)
      - Black king not in check by White (checked via _white_attacks_square)

    Yields:
      (wk_sq, bk_sq, pieces)
    where pieces is a list of (is_white, piece_type, squares_tuple) for all NON-KING groups
    in the same canonical ordering as groups_for_generation(), but excluding kings.
    """
    groups = groups_for_generation(material)

    # Split out non-king groups; keep canonical ordering.
    nonking_groups: List[Tuple[bool, int, int, int]] = []  # (is_white, pt, count, allowed_mask)
    for is_white, pt, count in groups:
        if pt == chess.KING:
            continue
        allowed = PAWN_SQUARES_MASK if pt == chess.PAWN else ALL_SQUARES_MASK
        nonking_groups.append((is_white, pt, count, allowed))

    # Pre-allocate chosen squares per non-king group (to avoid per-node dict allocations).
    chosen: List[Tuple[int, ...]] = [()] * len(nonking_groups)

    # Collect which chosen entries are white pieces for attack checking (built at leaf cheaply).
    # We will rebuild a compact list at leaf (<=3 pieces typically), so no need to maintain incrementally.

    def rec(i: int, used: int) -> Iterable[Tuple[int, int, List[Tuple[bool, int, Tuple[int, ...]]]]]:
        if i == len(nonking_groups):
            # BK is chosen last to reduce recursion overhead.
            used_no_bk = used
            # BK must not share squares and must not be adjacent to WK.
            bk_mask = ALL_SQUARES_MASK & ~used_no_bk & ~KING_ADJ_MASK[wk_sq]

            # Build compact list of white pieces (excluding WK) once for all BK tries at this leaf.
            white_pieces: List[Tuple[int, int]] = []
            # occupied without BK is used_no_bk; adding BK does not change blockers between attacker and BK squares.
            # However, we pass occupied including BK for simplicity.
            for (is_white2, pt2, _count2, _allowed2), sqs in zip(nonking_groups, chosen):
                if is_white2:
                    for s2 in sqs:
                        white_pieces.append((pt2, s2))

            for bk_sq in _iter_bits(bk_mask):
                occupied = used_no_bk | (1 << bk_sq)
                if _white_attacks_square(bk_sq, white_pieces, occupied):
                    continue

                pieces_out: List[Tuple[bool, int, Tuple[int, ...]]] = []
                for (is_white2, pt2, _count2, _allowed2), sqs in zip(nonking_groups, chosen):
                    pieces_out.append((is_white2, pt2, sqs))

                yield (wk_sq, bk_sq, pieces_out)

            return

        is_white, pt, count, allowed = nonking_groups[i]
        avail = allowed & ~used

        for combo in _iter_k_combos(avail, count):
            u2 = used
            for s in combo:
                u2 |= (1 << s)
            chosen[i] = combo
            yield from rec(i + 1, u2)

    # WK outer loop (simple range is fastest here).
    for wk_sq in range(64):
        used0 = 1 << wk_sq
        yield from rec(0, used0)


# ----------------------------
# Board construction (reused)
# ----------------------------

def _new_empty_board() -> chess.Board:
    b = chess.Board(None)  # empty board
    b.turn = chess.WHITE
    b.castling_rights = 0
    b.ep_square = None
    b.halfmove_clock = 0
    b.fullmove_number = 1
    return b


_HAS_CLEAR_BOARD = hasattr(_new_empty_board(), "clear_board")


def _fill_board_inplace(
    board: chess.Board,
    wk_sq: int,
    bk_sq: int,
    pieces: List[Tuple[bool, int, Tuple[int, ...]]],
    piece_cache: Dict[Tuple[bool, int], chess.Piece],
) -> chess.Board:
    """
    Fill an existing empty board with pieces. Requires python-chess Board.clear_board().
    Filters are assumed not to mutate the board (push/pop), otherwise reuse is unsafe.
    """
    board.clear_board()
    board.turn = chess.WHITE
    board.castling_rights = 0
    board.ep_square = None
    board.halfmove_clock = 0
    board.fullmove_number = 1

    board.set_piece_at(wk_sq, piece_cache[(True, chess.KING)])
    board.set_piece_at(bk_sq, piece_cache[(False, chess.KING)])

    for is_white, pt, sqs in pieces:
        piece = piece_cache[(is_white, pt)]
        for s in sqs:
            board.set_piece_at(s, piece)

    return board


# ----------------------------
# TB probing / encoding (unchanged)
# ----------------------------

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
    candidates_total = 0  # now equals valid_positions, since we generate only valid
    valid_positions = 0

    rejected_notb_generic = 0
    rejected_notb_specific = 0
    passed_notb = 0

    rejected_tb_generic = 0
    rejected_tb_specific = 0
    accepted = 0

    accepted_win = 0
    accepted_draw = 0
    accepted_loss = 0

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
    next_log_time = t0 + 60.0

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

    # Piece cache avoids re-allocating chess.Piece objects.
    piece_cache: Dict[Tuple[bool, int], chess.Piece] = {
        (True, chess.KING): chess.Piece(chess.KING, chess.WHITE),
        (False, chess.KING): chess.Piece(chess.KING, chess.BLACK),
        (True, chess.QUEEN): chess.Piece(chess.QUEEN, chess.WHITE),
        (False, chess.QUEEN): chess.Piece(chess.QUEEN, chess.BLACK),
        (True, chess.ROOK): chess.Piece(chess.ROOK, chess.WHITE),
        (False, chess.ROOK): chess.Piece(chess.ROOK, chess.BLACK),
        (True, chess.BISHOP): chess.Piece(chess.BISHOP, chess.WHITE),
        (False, chess.BISHOP): chess.Piece(chess.BISHOP, chess.BLACK),
        (True, chess.KNIGHT): chess.Piece(chess.KNIGHT, chess.WHITE),
        (False, chess.KNIGHT): chess.Piece(chess.KNIGHT, chess.BLACK),
        (True, chess.PAWN): chess.Piece(chess.PAWN, chess.WHITE),
        (False, chess.PAWN): chess.Piece(chess.PAWN, chess.BLACK),
    }

    # Board reuse if supported (significant speed win).
    board_reuse = _HAS_CLEAR_BOARD
    board = _new_empty_board() if board_reuse else None

    with out_path.open("w", encoding="ascii", newline="") as f_out:
        tablebase = None  # lazy init

        for wk_sq, bk_sq, pieces in generate_valid_square_placements(material):
            valid_positions += 1
            candidates_total += 1
            now = time.perf_counter()
            if now >= next_log_time:
                log_progress()
                next_log_time = now + 60.0

            # Build a Board only for valid positions.
            if board_reuse:
                assert board is not None
                b = _fill_board_inplace(board, wk_sq, bk_sq, pieces, piece_cache)
            else:
                b = _new_empty_board()
                b.set_piece_at(wk_sq, piece_cache[(True, chess.KING)])
                b.set_piece_at(bk_sq, piece_cache[(False, chess.KING)])
                for is_white, pt, sqs in pieces:
                    piece = piece_cache[(is_white, pt)]
                    for s in sqs:
                        b.set_piece_at(s, piece)

            # Stage A: no-tablebase filters (unchanged).
            if not filters.filter_notb_generic(b):
                rejected_notb_generic += 1
                continue

            if filter_notb_specific is not None and not filter_notb_specific(b):
                rejected_notb_specific += 1
                continue

            passed_notb += 1

            # Stage B: tablebase stage (lazy open).
            if tablebase is None:
                tablebase = chess.gaviota.open_tablebase(str(gaviota_dirs[0]))
                for d in gaviota_dirs[1:]:
                    tablebase.add_directory(str(d))
                tb_opened = True

            wdl_white, dtm_white = probe_dtm_only_white_pov(tablebase, b)
            tb_info = build_tb_info_with_probe(tablebase, b, wdl_white, dtm_white)

            if not filters.filter_tb_generic(b, tb_info):
                rejected_tb_generic += 1
                continue

            if filter_tb_specific is not None and not filter_tb_specific(b, tb_info):
                rejected_tb_specific += 1
                continue

            f_out.write(encode_record(material, b))
            accepted += 1

            if wdl_white > 0:
                accepted_win += 1
            elif wdl_white < 0:
                accepted_loss += 1
            else:
                accepted_draw += 1

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
