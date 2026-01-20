# generate_positions.py
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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
for _sq in range(64):
    _r = _sq >> 3
    if _r != 0 and _r != 7:
        PAWN_SQUARES_MASK |= (1 << _sq)


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


def get_filter_fn(name: str):
    fn = getattr(filters, name, None)
    if fn is None:
        return None
    if not callable(fn):
        raise TypeError(f"{name} exists but is not callable.")
    return fn


def get_gen_hints(material: Material) -> Optional[Mapping[str, Any]]:
    """
    Optional generation hints defined in filters.py as:
      def gen_hints_<material_key>() -> Mapping[str, Any]
    """
    fn = getattr(filters, f"gen_hints_{material.key}", None)
    if fn is None:
        return None
    if not callable(fn):
        raise TypeError(f"gen_hints_{material.key} exists but is not callable.")
    out = fn()
    if out is None:
        return None
    if not isinstance(out, Mapping):
        raise TypeError(f"gen_hints_{material.key} must return a Mapping[str, Any].")
    return out


def fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.2f}h"


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



def open_tablebase_native_fixed(dirs: list[Path]) -> Any:
    """
    Open NativeTablebase but fix the path list passed to libgtb:
    - ensure NULL-terminated char** (argv-style), which some libgtb builds expect.
    - define argtypes to avoid any ABI ambiguity.
    """
    import ctypes
    import ctypes.util
    import chess.gaviota

    libname = ctypes.util.find_library("gtb") or "libgtb.so.1"
    lib = ctypes.cdll.LoadLibrary(libname)

    tb = chess.gaviota.NativeTablebase(lib)

    # Be explicit about the C signature expected by python-chess.
    # python-chess calls: tb_restart(verbosity:int, compression_scheme:int, paths:char**)
    tb.libgtb.tb_restart.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
    tb.libgtb.tb_restart.restype = ctypes.c_char_p

    # Monkeypatch _tb_restart to build a NULL-terminated char** list.
    def _tb_restart_null_terminated() -> None:
        n = len(tb.paths)
        c_paths = (ctypes.c_char_p * (n + 1))()
        c_paths[:n] = [p.encode("utf-8") for p in tb.paths]
        c_paths[n] = None  # NULL terminator (critical)

        verbosity = ctypes.c_int(1)
        compression_scheme = ctypes.c_int(4)

        ret = tb.libgtb.tb_restart(verbosity, compression_scheme, c_paths)
        if ret:
            # optional: log ret.decode("utf-8")
            pass

        tb.c_paths = c_paths  # keep alive

    tb._tb_restart = _tb_restart_null_terminated  # type: ignore[attr-defined]

    # Now add directories normally.
    tb.add_directory(str(dirs[0]))
    for d in dirs[1:]:
        tb.add_directory(str(d))

    return tb


# -----------------------------------
# Fast generation helpers (bitboards)
# -----------------------------------

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
    Optimized for k in {1,2,3}. Fallback for larger k.
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

                m3 = m2 & ~((1 << (b + 1)) - 1)  # bits > b (and already > a)
                while m3:
                    l3 = m3 & -m3
                    c = l3.bit_length() - 1
                    m3 ^= l3
                    yield (a, b, c)
        return

    # Fallback (should not happen for total pieces <= 5 unless you allow 3+ identical pieces).
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


def _build_square_color_masks() -> List[int]:
    """
    Return two masks: [color0_mask, color1_mask], where color is (file+rank)%2.
    """
    masks = [0, 0]
    for sq in range(64):
        f = sq & 7
        r = sq >> 3
        c = (f + r) & 1
        masks[c] |= (1 << sq)
    return masks


def _build_cheb_within_masks() -> List[List[int]]:
    """
    CHEB_WITHIN[sq][d] = mask of squares with Chebyshev distance <= d from sq.
    d in [0..7].
    """
    out: List[List[int]] = [[0] * 8 for _ in range(64)]
    for s in range(64):
        sf = s & 7
        sr = s >> 3
        for d in range(8):
            m = 0
            for sq in range(64):
                f = sq & 7
                r = sq >> 3
                if max(abs(f - sf), abs(r - sr)) <= d:
                    m |= (1 << sq)
            out[s][d] = m
    return out


KING_ADJ_MASK = _build_king_adjacency_masks()
SQUARE_COLOR_MASK = _build_square_color_masks()
CHEB_WITHIN = _build_cheb_within_masks()


def apply_cheb_range(candidates_mask: int, center_sq: int, dmin: int, dmax: int) -> int:
    """
    Restrict candidates_mask to squares with Chebyshev distance in [dmin, dmax] from center_sq.
    """
    if dmax < 0:
        return 0
    if dmin < 0:
        dmin = 0
    if dmax > 7:
        dmax = 7
    m = candidates_mask & CHEB_WITHIN[center_sq][dmax]
    if dmin <= 0:
        return m
    return m & ~CHEB_WITHIN[center_sq][dmin - 1]


def _sliding_attacks(from_sq: int, to_sq: int, occupied: int, step: int) -> bool:
    """
    Return True if a sliding piece on from_sq attacks to_sq along 'step' (±1, ±7, ±8, ±9),
    given the occupied bitboard.
    Assumes from_sq and to_sq are aligned on that ray.
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
    occupied: bitboard of all pieces (both colors); BK may be included.
    """
    bk_f = bk_sq & 7
    bk_r = bk_sq >> 3

    for pt, sq in white_pieces:
        sf = sq & 7
        sr = sq >> 3
        df = bk_f - sf
        dr = bk_r - sr

        if pt == chess.PAWN:
            # White pawn attacks one rank up (towards increasing rank): (df, dr) in {(-1, +1), (+1, +1)}
            if dr == 1 and (df == -1 or df == 1):
                return True

        elif pt == chess.KNIGHT:
            adf = df if df >= 0 else -df
            adr = dr if dr >= 0 else -dr
            if (adf == 1 and adr == 2) or (adf == 2 and adr == 1):
                return True

        elif pt == chess.BISHOP or pt == chess.QUEEN:
            if df != 0 and (df if df >= 0 else -df) == (dr if dr >= 0 else -dr):
                # Diagonal: abs(df) == abs(dr)
                step_f = 1 if df > 0 else -1
                step_r = 1 if dr > 0 else -1
                step = step_f + 8 * step_r  # NE=+9, NW=+7, SE=-7, SW=-9
                if _sliding_attacks(sq, bk_sq, occupied, step):
                    return True

        if pt == chess.ROOK or pt == chess.QUEEN:
            if df == 0 and dr != 0:
                step = 8 if dr > 0 else -8
                if _sliding_attacks(sq, bk_sq, occupied, step):
                    return True
            elif dr == 0 and df != 0:
                step = 1 if df > 0 else -1
                if _sliding_attacks(sq, bk_sq, occupied, step):
                    return True

    return False


def _estimate_branching(mask: int, count: int) -> int:
    """
    Cheap heuristic to order groups: smaller candidate space first.
    """
    n = mask.bit_count()
    # approximate combinations count for small counts
    if count <= 1:
        return n
    if count == 2:
        return (n * (n - 1)) // 2
    if count == 3:
        return (n * (n - 1) * (n - 2)) // 6
    return n ** count


def generate_valid_square_placements(material: Material, hints: Optional[Mapping[str, Any]]) -> Iterable[Tuple[int, int, List[Tuple[bool, int, Tuple[int, ...]]]]]:
    """
    Generate ONLY "valid positions" per spec, without building a Board:
      - White to move (handled later)
      - No pawn on rank 1/8 (enforced by pawn masks)
      - Kings not adjacent (enforced by BK choice)
      - Black king not in check by White (checked via _white_attacks_square)

    Hints (optional) can include:
      - "piece_masks": {(is_white: bool, piece_type: int): bitmask}
      - "wk_to_pawn_cheb": (dmin, dmax)   # used only if exactly one pawn exists (any color) with count==1
      - "bk_to_pawn_cheb": (dmin, dmax)   # same
      - "bishops_same_color": bool        # if True and there is exactly one bishop each side (count==1)
    """
    hints = hints or {}
    piece_masks: Mapping[Tuple[bool, int], int] = hints.get("piece_masks", {}) or {}
    wk_to_pawn = hints.get("wk_to_pawn_cheb", None)
    bk_to_pawn = hints.get("bk_to_pawn_cheb", None)
    bishops_same_color = bool(hints.get("bishops_same_color", False))

    # Detect single pawn anchor (any color, count==1).
    groups = groups_for_generation(material)
    pawn_groups = [(is_w, pt, cnt) for (is_w, pt, cnt) in groups if pt == chess.PAWN and cnt == 1]
    has_single_pawn_anchor = (len(pawn_groups) == 1)

    # Build non-king groups with allowed masks (including pawn legality + hint masks).
    ngroups: List[Tuple[bool, int, int, int]] = []
    for is_white, pt, count in groups:
        if pt == chess.KING:
            continue
        base = PAWN_SQUARES_MASK if pt == chess.PAWN else ALL_SQUARES_MASK
        m = base & piece_masks.get((is_white, pt), ALL_SQUARES_MASK)
        ngroups.append((is_white, pt, count, m))

    # Optional: reorder groups to reduce branching (without changing correctness).
    # Anchor-pawn mode already places the pawn first, so remove it from ordering below.
    pawn_anchor_index: Optional[int] = None
    pawn_anchor_color: Optional[bool] = None
    if has_single_pawn_anchor:
        pawn_is_white, _, _ = pawn_groups[0]
        pawn_anchor_color = pawn_is_white
        for i, (is_white, pt, count, _m) in enumerate(ngroups):
            if is_white == pawn_is_white and pt == chess.PAWN and count == 1:
                pawn_anchor_index = i
                break

    # Boardless bishop-color hint only makes sense when there is exactly one bishop for each side.
    # We enforce it by anchoring on the first bishop encountered in recursion.
    has_one_wb = any(is_w and pt == chess.BISHOP and cnt == 1 for (is_w, pt, cnt, _m) in ngroups)
    has_one_bb = any((not is_w) and pt == chess.BISHOP and cnt == 1 for (is_w, pt, cnt, _m) in ngroups)
    use_bishop_color_hint = bishops_same_color and has_one_wb and has_one_bb

    # Prepare list of indices for recursion, ordered by branching.
    rec_indices = list(range(len(ngroups)))
    if pawn_anchor_index is not None:
        rec_indices.remove(pawn_anchor_index)

    # Sort remaining groups by estimated branching (smaller first).
    # For count>1, combinations grow fast; this helps a lot when masks are narrow.
    def rec_sort_key(idx: int) -> int:
        _is_w, _pt, _cnt, _m = ngroups[idx]
        return _estimate_branching(_m, _cnt)

    rec_indices.sort(key=rec_sort_key)

    # Pre-allocate chosen squares per ngroup, to avoid per-node dict allocations.
    chosen: List[Tuple[int, ...]] = [()] * len(ngroups)

    # Candidate masks for kings (hints may include them; rare but supported).
    wk_mask_hint = piece_masks.get((True, chess.KING), ALL_SQUARES_MASK)
    bk_mask_hint = piece_masks.get((False, chess.KING), ALL_SQUARES_MASK)

    # Pawn anchor loop (if enabled) allows us to apply wk/bk-to-pawn distances early.
    pawn_sq_anchor: Optional[int] = None
    pawn_mask_anchor: int = 0
    if pawn_anchor_index is not None:
        _isw, _pt, _cnt, _m = ngroups[pawn_anchor_index]
        pawn_mask_anchor = _m  # already includes PAWN legality + hint masks

    def rec_build(i: int, used: int, bishop_color: Optional[int], wk_sq: int) -> Iterable[Tuple[int, int, List[Tuple[bool, int, Tuple[int, ...]]]]]:
        """
        Place all non-king groups (except BK), then choose BK last under:
          - not used
          - not adjacent to WK
          - optional bk-to-pawn distance (if single pawn anchor)
          - BK not attacked by white pieces
        """
        if i == len(rec_indices):
            used_no_bk = used

            # Build compact list of white pieces (excluding WK) for BK attack check.
            white_pieces: List[Tuple[int, int]] = []
            for (is_white, pt, _cnt, _m), sqs in zip(ngroups, chosen):
                if is_white:
                    for s in sqs:
                        white_pieces.append((pt, s))

            bk_candidates = (ALL_SQUARES_MASK & bk_mask_hint) & ~used_no_bk & ~KING_ADJ_MASK[wk_sq]

            if has_single_pawn_anchor and pawn_sq_anchor is not None and bk_to_pawn is not None:
                dmin, dmax = bk_to_pawn
                bk_candidates = apply_cheb_range(bk_candidates, pawn_sq_anchor, dmin, dmax)

            for bk_sq in _iter_bits(bk_candidates):
                occ = used_no_bk | (1 << bk_sq)
                if _white_attacks_square(bk_sq, white_pieces, occ):
                    continue

                pieces_out: List[Tuple[bool, int, Tuple[int, ...]]] = []
                for (is_white, pt, _cnt, _m), sqs in zip(ngroups, chosen):
                    pieces_out.append((is_white, pt, sqs))
                yield (wk_sq, bk_sq, pieces_out)
            return

        idx = rec_indices[i]
        is_white, pt, count, allowed = ngroups[idx]

        candidates = allowed & ~used

        # Bishop color parity constraint (same-color bishops) if requested.
        if use_bishop_color_hint and pt == chess.BISHOP and count == 1:
            if bishop_color is not None:
                candidates &= SQUARE_COLOR_MASK[bishop_color]

        # Additional king-distance constraint to pawn can be applied early to WK by selecting WK before recursion.
        # (handled outside)

        for combo in _iter_k_combos(candidates, count):
            used2 = used
            for s in combo:
                used2 |= (1 << s)

            bishop_color2 = bishop_color
            if use_bishop_color_hint and pt == chess.BISHOP and count == 1 and bishop_color2 is None:
                # Anchor bishop color based on the first bishop placed (any side).
                s0 = combo[0]
                bishop_color2 = ((s0 & 7) + (s0 >> 3)) & 1

            chosen[idx] = combo
            yield from rec_build(i + 1, used2, bishop_color2, wk_sq)

    # WK outer loop, with optional constraints relative to the single pawn anchor.
    # If single pawn anchor exists, we place the pawn first; otherwise pawn is placed in recursion.
    if pawn_anchor_index is not None:
        for pawn_sq in _iter_bits(pawn_mask_anchor):
            pawn_sq_anchor = pawn_sq
            chosen[pawn_anchor_index] = (pawn_sq,)

            # WK candidates: must respect wk_mask_hint and not overlap pawn.
            wk_candidates = (ALL_SQUARES_MASK & wk_mask_hint) & ~(1 << pawn_sq)

            if wk_to_pawn is not None:
                dmin, dmax = wk_to_pawn
                wk_candidates = apply_cheb_range(wk_candidates, pawn_sq, dmin, dmax)

            for wk_sq in _iter_bits(wk_candidates):
                used0 = (1 << pawn_sq) | (1 << wk_sq)

                # Place remaining pieces; bishop_color starts None.
                yield from rec_build(0, used0, None, wk_sq)

    else:
        # No pawn anchor: plain WK loop, then recurse placing all non-king pieces.
        for wk_sq in _iter_bits(ALL_SQUARES_MASK & wk_mask_hint):
            used0 = 1 << wk_sq
            yield from rec_build(0, used0, None, wk_sq)


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
    Fill an existing board with pieces. Requires python-chess Board.clear_board().
    NOTE: This is safe only if filters do not leave the board mutated (push/pop imbalance).
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
# TB probing / encoding
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
      - dtm_white: Optional[int] in plies from White's perspective (None if draw)
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

    hints = get_gen_hints(material)

    gaviota_root = Path("./gaviota")
    gaviota_dirs = find_gaviota_dirs(gaviota_root)

    out_dir = Path("./data")
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
    next_log_time = t0 + 60.0

    def log_progress() -> None:
        elapsed = time.perf_counter() - t0
        rate_valid = (valid_positions / elapsed) if elapsed > 0 else 0.0
        rate_accepted = (accepted / elapsed) if elapsed > 0 else 0.0

        print(
            "progress:"
            f" elapsed={fmt_elapsed(elapsed)}"
            f" candidates={candidates_total}"
            f" passed_notb={passed_notb}"
            f" accepted={accepted}"
            f" rate_valid={rate_valid:,.0f}/s"
            f" rate_accepted={rate_accepted:,.0f}/s"
            f" | notb_rej_generic={rejected_notb_generic}"
            f" notb_rej_specific={rejected_notb_specific}"
            f" tb_rej_generic={rejected_tb_generic}"
            f" tb_rej_specific={rejected_tb_specific}"
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

        for wk_sq, bk_sq, pieces in generate_valid_square_placements(material, hints):
            candidates_total += 1
            valid_positions += 1  # generator already enforces the "valid position" spec
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

            # Stage A: no-tablebase filters.
            if not filters.filter_notb_generic(b):
                rejected_notb_generic += 1
                continue

            if filter_notb_specific is not None and not filter_notb_specific(b):
                rejected_notb_specific += 1
                continue

            passed_notb += 1

            # Stage B: tablebase stage (lazy open).
            if tablebase is None:
                tablebase = open_tablebase_native_fixed(gaviota_dirs)
                tb_opened = True

            # Probe only DTM for the root position first.
            wdl_white, dtm_white = probe_dtm_only_white_pov(tablebase, b)

            # Build TB info with on-demand per-move probe.
            tb_info = build_tb_info_with_probe(tablebase, b, wdl_white, dtm_white)

            if not filters.filter_tb_generic(b, tb_info):
                rejected_tb_generic += 1
                continue

            if filter_tb_specific is not None and not filter_tb_specific(b, tb_info):
                rejected_tb_specific += 1
                continue

            # Accepted -> write record (no separators, no newline).
            f_out.write(encode_record(material, b))
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
    if hints:
        print(f"  gen_hints_used: True ({material.key})")
    else:
        print("  gen_hints_used: False")


if __name__ == "__main__":
    main()
