# kp_vs_kp.py
# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping, Tuple

import chess

from helpers import mask_files

# =============================================================================
# Generation hints
# =============================================================================

def gen_hints_kp_vs_kp() -> Mapping[str, Any]:
    """
    KP vs KP generation hints.

    We remove left-right symmetric duplicates by restricting the WHITE pawn
    to files a-d. Any position with a white pawn on e-h has a mirrored
    equivalent with identical tablebase value and pedagogical content.
    """
    return {
        "piece_masks": {
            (True, chess.PAWN): mask_files(0, 3),  # a-d only
        }
    }


# =============================================================================
# Stable hashing helpers (for order-independent sampling)
# =============================================================================

_U64_MASK = (1 << 64) - 1


def _board_u64_key(board: chess.Board) -> int:
    """
    Return a stable-ish 64-bit integer key for the position.
    Prefer python-chess transposition_key(); fallback to hash(fen).
    """
    if hasattr(board, "transposition_key") and callable(getattr(board, "transposition_key")):
        try:
            return int(board.transposition_key()) & _U64_MASK
        except Exception:
            pass
    return hash(board.fen()) & _U64_MASK


def _splitmix64(x: int) -> int:
    """SplitMix64 mixer."""
    x = (x + 0x9E3779B97F4A7C15) & _U64_MASK
    z = x
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9 & _U64_MASK
    z = (z ^ (z >> 27)) * 0x94D049BB133111EB & _U64_MASK
    return (z ^ (z >> 31)) & _U64_MASK


def _stable_random01(board: chess.Board, salt: int = 0) -> float:
    """Stable pseudo-random float in [0,1), derived from (position, salt)."""
    x = _board_u64_key(board) ^ (salt & _U64_MASK)
    x = _splitmix64(x)
    return ((x >> 11) & ((1 << 53) - 1)) / float(1 << 53)


# =============================================================================
# Symmetry canonicalization (LR mirror)
# =============================================================================

def _mirror_sq_lr(sq: int) -> int:
    f = sq & 7
    r = sq >> 3
    return (r << 3) | (7 - f)


def _is_lr_canonical(board: chess.Board) -> bool:
    """
    Keep only one representative under left-right symmetry.

    Compare tuples (wp, bp, wk, bk) with their mirrored version and keep the
    lexicographically smallest.
    """
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    t = (wp, bp, wk, bk)
    tm = (_mirror_sq_lr(wp), _mirror_sq_lr(bp), _mirror_sq_lr(wk), _mirror_sq_lr(bk))
    return t <= tm


# =============================================================================
# Cheap (no-TB) filter
# =============================================================================

def filter_notb_kp_vs_kp(board: chess.Board) -> bool:
    """
    KP vs KP no-TB filter (White to move).

    Goals:
    - Reject obviously trivial "counting races" early.
    - Keep the kings involved with at least one pawn.
    - Coarsely reduce redundancy (symmetry).
    """
    if board.turn != chess.WHITE:
        return False

    if not _is_lr_canonical(board):
        return False

    if board.is_check():
        return False

    # Require meaningful branching.
    moves_iter = iter(board.legal_moves)
    try:
        next(moves_iter); next(moves_iter); next(moves_iter); next(moves_iter)
    except StopIteration:
        return False

    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wpr = chess.square_rank(wp)
    bpr = chess.square_rank(bp)
    wpf = chess.square_file(wp)
    bpf = chess.square_file(bp)

    # Avoid "almost promotion" trivialities: keep pawns in human ranks 3..6.
    # (0-based ranks 2..5)
    if not (2 <= wpr <= 5):
        return False
    if not (2 <= bpr <= 5):
        return False

    file_diff = abs(wpf - bpf)
    locked_same_file = (wpf == bpf and abs(wpr - bpr) == 1)
    diagonal_contact = (file_diff == 1 and abs(wpr - bpr) == 1)

    # Kings must be relevant.
    d_wk_wp = chess.square_distance(wk, wp)
    d_wk_bp = chess.square_distance(wk, bp)
    d_bk_wp = chess.square_distance(bk, wp)
    d_bk_bp = chess.square_distance(bk, bp)

    if min(d_wk_wp, d_wk_bp) > 4:
        return False
    if min(d_bk_wp, d_bk_bp) > 4:
        return False

    # If this is a pure race (pawns far apart) we only keep cases where kings
    # are close enough to create tempo/opposition dynamics.
    if file_diff >= 2 and not (locked_same_file or diagonal_contact):
        if chess.square_distance(wk, bk) > 5 and min(d_wk_bp, d_bk_wp) > 4:
            return False

    return True


# =============================================================================
# TB filter (quality + diversity + outcome balance)
# =============================================================================

# Theme id (for diversification)
# 0: locked same-file pawns (adjacent)
# 1: diagonal pawn contact (capture motif)
# 2: adjacent files (no immediate contact)
# 3: separated files (>=2)
def _classify_theme(board: chess.Board) -> int:
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wpf, wpr = chess.square_file(wp), chess.square_rank(wp)
    bpf, bpr = chess.square_file(bp), chess.square_rank(bp)

    fd = abs(wpf - bpf)
    if wpf == bpf and abs(wpr - bpr) == 1:
        return 0
    if fd == 1 and abs(wpr - bpr) == 1:
        return 1
    if fd == 1:
        return 2
    return 3


# DTM windows (plies, White POV). Theme-dependent max avoids "very long shuffling" races.
_MIN_WIN_DTM = 16
_MIN_LOSS_DTM = 16

_MAX_DTM_BY_THEME = {
    0: 200,  # locked positions can be legitimately long but still critical
    1: 150,
    2: 120,
    3: 90,   # races: keep shorter horizons only
}

# Approximate outcome mixture (order-independent sampling).
# Tune after inspecting generate_positions.py counters (accepted_win/draw/loss).
_OUTCOME_KEEP_P = {
    +1: 0.80,  # wins
     0: 0.70,  # draws
    -1: 0.25,  # losses
}

# Slight theme rebalancing to avoid overfilling with "race-ish" positions.
_THEME_KEEP_MOD = {
    0: 1.00,
    1: 1.00,
    2: 0.90,
    3: 0.75,
}

# Draw / win hardness knobs
_MIN_LOSING_ALTS_FOR_DRAW = 2
_MIN_WORSENING_ALTS_FOR_WIN = 2  # at least 2 non-winning moves (including >=1 draw)

# Loss hardness knobs
_MIN_LOSS_SPREAD = 8  # plies difference between best defense and median defense
_REQUIRE_QUICK_BLUNDER = False
_QUICK_BLUNDER_THRESHOLD = -20  # mate <= 20 plies (White POV: dtm close to 0 is quick)


def _bucket_salt(board: chess.Board, wdl: int) -> int:
    """
    Coarse feature bucket used only for stable sampling (anti-clustering).
    """
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wpf, wpr = chess.square_file(wp), chess.square_rank(wp)
    bpf, bpr = chess.square_file(bp), chess.square_rank(bp)

    theme = _classify_theme(board)
    file_diff = abs(wpf - bpf)

    # Rank bins (3 buckets each)
    w_rank_bin = 0 if wpr <= 2 else (1 if wpr <= 4 else 2)
    b_rank_bin = 0 if bpr >= 5 else (1 if bpr >= 3 else 2)

    # Distance bins
    def dbin(d: int) -> int:
        if d <= 1:
            return 0
        if d <= 3:
            return 1
        if d <= 5:
            return 2
        return 3

    dkw = dbin(chess.square_distance(wk, wp))
    dkb = dbin(chess.square_distance(wk, bp))
    dbw = dbin(chess.square_distance(bk, wp))
    dbb = dbin(chess.square_distance(bk, bp))
    dkk = dbin(chess.square_distance(wk, bk))

    wdl_i = {-1: 0, 0: 1, 1: 2}[int(wdl)]

    salt = (
        wdl_i
        | (theme << 2)
        | (file_diff << 5)
        | (wpf << 8)
        | (w_rank_bin << 12)
        | (b_rank_bin << 14)
        | (dkw << 16)
        | (dkb << 18)
        | (dbw << 20)
        | (dbb << 22)
        | (dkk << 24)
    )
    return salt & _U64_MASK


def filter_tb_kp_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KP TB filter (White POV).

    We enforce "critical" positions:
    - WIN: exactly 1 winning move, and at least one tempting drawing move.
    - DRAW: exactly 1 drawing move, and multiple losing alternatives.
    - LOSS: exactly 1 best defensive move by DTM, and strong spread vs other defenses.

    Additionally:
    - Avoid best-move captures and promotions (to avoid "KP vs K" or trivial tactics,
      which are covered by the dedicated KP vs K material anyway).
    - Theme-aware DTM window to avoid long, low-signal races.
    - Order-independent, stable sampling to reduce near-duplicates.
    """
    if board.turn != chess.WHITE:
        return False
    if not _is_lr_canonical(board):
        return False

    wdl = int(tb["wdl"])
    dtm = tb["dtm"]

    theme = _classify_theme(board)
    max_abs_dtm = _MAX_DTM_BY_THEME[theme]

    # Global DTM sanity: exclude too short and too long horizons (wins/losses only).
    if wdl != 0:
        if dtm is None:
            return False
        a = abs(int(dtm))
        if wdl > 0:
            if a < _MIN_WIN_DTM or a > max_abs_dtm:
                return False
        else:
            if a < _MIN_LOSS_DTM or a > max_abs_dtm:
                return False

    legal_moves = list(board.legal_moves)
    if len(legal_moves) < 4:
        return False

    wins = []
    draws = []
    losses = []

    # Track move-types for "temptation" sanity.
    losing_king_moves = 0
    losing_pawn_moves = 0
    nonwinning_king_moves = 0
    nonwinning_pawn_moves = 0

    # For LOSS: collect child dtm values (White POV) for resistance scoring.
    loss_children: list[Tuple[chess.Move, int, bool, bool, int]] = []
    # (move, child_dtm_white, is_capture, is_promotion, piece_type)

    for mv in legal_moves:
        pt = board.piece_type_at(mv.from_square) or 0
        is_cap = board.is_capture(mv)
        is_prom = mv.promotion is not None

        res = tb["probe_move"](mv)
        c_wdl = int(res["wdl"])
        c_dtm = res["dtm"]

        if c_wdl > 0:
            wins.append((mv, pt, is_cap, is_prom))
        elif c_wdl == 0:
            draws.append((mv, pt, is_cap, is_prom))
        else:
            losses.append((mv, pt, is_cap, is_prom))
            if pt == chess.KING:
                losing_king_moves += 1
            elif pt == chess.PAWN:
                losing_pawn_moves += 1

        if c_wdl != 1:
            if pt == chess.KING:
                nonwinning_king_moves += 1
            elif pt == chess.PAWN:
                nonwinning_pawn_moves += 1

        if wdl < 0:
            if c_dtm is None:
                return False
            loss_children.append((mv, int(c_dtm), is_cap, is_prom, pt))

    # Must have at least one king move.
    if all(pt != chess.KING for (_mv, pt, _c, _p) in (wins + draws + losses)):
        return False

    # --- Outcome-specific hardness checks ---
    if wdl > 0:
        if len(wins) != 1:
            return False

        best_mv, best_pt, best_cap, best_prom = wins[0]
        if best_cap or best_prom:
            return False
        if best_pt != chess.KING:
            return False

        if len(draws) == 0:
            return False
        if (len(draws) + len(losses)) < _MIN_WORSENING_ALTS_FOR_WIN:
            return False

        if any(pt == chess.PAWN for (_mv, pt, _c, _p) in (wins + draws + losses)):
            if nonwinning_pawn_moves == 0:
                return False

    elif wdl == 0:
        if len(draws) != 1:
            return False

        best_mv, best_pt, best_cap, best_prom = draws[0]
        if best_cap or best_prom:
            return False
        if best_pt != chess.KING:
            return False

        if len(losses) < _MIN_LOSING_ALTS_FOR_DRAW:
            return False

        has_pawn_move = any(pt == chess.PAWN for (_mv, pt, _c, _p) in (wins + draws + losses))
        has_king_move = any(pt == chess.KING for (_mv, pt, _c, _p) in (wins + draws + losses))
        if has_pawn_move and has_king_move:
            if losing_king_moves == 0 or losing_pawn_moves == 0:
                return False

    else:
        if len(loss_children) != len(legal_moves):
            return False

        # For a losing position (White POV), child dtm values are negative.
        # More negative => longer survival (best resistance).
        loss_children.sort(key=lambda t: t[1])  # ascending: most negative first

        best_mv, best_dtm, best_cap, best_prom, best_pt = loss_children[0]
        if best_cap or best_prom:
            return False

        if sum(1 for (_m, d, _c, _p, _pt) in loss_children if d == best_dtm) > 1:
            return False

        med_dtm = loss_children[len(loss_children) // 2][1]
        if (med_dtm - best_dtm) < _MIN_LOSS_SPREAD:
            return False

        if _REQUIRE_QUICK_BLUNDER:
            worst_dtm = max(d for (_m, d, _c, _p, _pt) in loss_children)
            if worst_dtm < _QUICK_BLUNDER_THRESHOLD:
                return False

        if best_pt != chess.KING and theme in (0, 2):
            return False

    # --- Stable diversification / outcome balancing ---
    p = _OUTCOME_KEEP_P[wdl] * _THEME_KEEP_MOD[theme]
    if p >= 1.0:
        return True

    salt = _bucket_salt(board, wdl)
    return _stable_random01(board, salt=salt) < p
