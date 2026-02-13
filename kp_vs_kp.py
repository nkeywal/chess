# kp_vs_kp.py
# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping, Tuple, List

import chess

from helpers import mask_files

# =============================================================================
# Generation hints
# =============================================================================

def gen_hints_kp_vs_kp() -> Mapping[str, Any]:
    """
    KP vs KP generation hints.

    Remove left-right symmetric duplicates by restricting the WHITE pawn
    to files a-d.
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
    if hasattr(board, "transposition_key") and callable(getattr(board, "transposition_key")):
        try:
            return int(board.transposition_key()) & _U64_MASK
        except Exception:
            pass
    return hash(board.fen()) & _U64_MASK

def _splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & _U64_MASK
    z = x
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9 & _U64_MASK
    z = (z ^ (z >> 27)) * 0x94D049BB133111EB & _U64_MASK
    return (z ^ (z >> 31)) & _U64_MASK

def _stable_random01(board: chess.Board, salt: int = 0) -> float:
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
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    t = (wp, bp, wk, bk)
    tm = (_mirror_sq_lr(wp), _mirror_sq_lr(bp), _mirror_sq_lr(wk), _mirror_sq_lr(bk))
    return t <= tm

# =============================================================================
# Extra geometry helpers
# =============================================================================

def _cheb(a: int, b: int) -> int:
    return chess.square_distance(a, b)

def _opposition_like(wk: int, bk: int) -> bool:
    """
    Opposition-like alignment: same file or same rank, separated by an even distance >= 2.
    This is a good proxy for distant opposition / parity motifs.
    """
    wf, wr = chess.square_file(wk), chess.square_rank(wk)
    bf, br = chess.square_file(bk), chess.square_rank(bk)
    if wf == bf:
        d = abs(wr - br)
        return d >= 2 and (d % 2 == 0)
    if wr == br:
        d = abs(wf - bf)
        return d >= 2 and (d % 2 == 0)
    return False

def _king_moves(board: chess.Board) -> List[chess.Move]:
    out: List[chess.Move] = []
    for mv in board.legal_moves:
        if board.piece_type_at(mv.from_square) == chess.KING:
            out.append(mv)
    return out

def _pawn_moves(board: chess.Board) -> List[chess.Move]:
    out: List[chess.Move] = []
    for mv in board.legal_moves:
        if board.piece_type_at(mv.from_square) == chess.PAWN:
            out.append(mv)
    return out

# =============================================================================
# Cheap (no-TB) filter
# =============================================================================

def filter_notb_kp_vs_kp(board: chess.Board) -> bool:
    """
    KP vs KP no-TB filter (White to move).

    Stricter than before:
    - enforce more branching (>= 6 legal moves, >= 3 king moves)
    - enforce stronger interaction: kings close to pawns AND at least one king close to the opponent pawn
    - avoid too-empty positions (at least one pawn in a "decision zone")
    """
    if board.turn != chess.WHITE:
        return False
    if not _is_lr_canonical(board):
        return False
    if board.is_check():
        return False

    legal = list(board.legal_moves)
    if len(legal) < 6:
        return False

    km = _king_moves(board)
    if len(km) < 3:
        return False

    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wpr = chess.square_rank(wp)
    bpr = chess.square_rank(bp)

    # Keep pawns in human ranks 3..6 (0-based 2..5)
    if not (2 <= wpr <= 5):
        return False
    if not (2 <= bpr <= 5):
        return False

    # At least one pawn in a "decision zone" (human rank 4 or 5 -> 0-based 3..4)
    if not (3 <= wpr <= 4 or 3 <= bpr <= 4):
        return False

    # Stronger king relevance
    d_wk_wp = _cheb(wk, wp)
    d_wk_bp = _cheb(wk, bp)
    d_bk_wp = _cheb(bk, wp)
    d_bk_bp = _cheb(bk, bp)

    if min(d_wk_wp, d_wk_bp) > 3:
        return False
    if min(d_bk_wp, d_bk_bp) > 3:
        return False

    # Ensure real interaction: at least one king can influence the opponent pawn soon.
    if min(d_wk_bp, d_bk_wp) > 3:
        return False

    # If pawns are far apart, require kings to be close enough so it isn't a pure race.
    wpf, bpf = chess.square_file(wp), chess.square_file(bp)
    file_diff = abs(wpf - bpf)
    locked_same_file = (wpf == bpf and abs(wpr - bpr) == 1)
    diagonal_contact = (file_diff == 1 and abs(wpr - bpr) == 1)

    if file_diff >= 2 and not (locked_same_file or diagonal_contact):
        if _cheb(wk, bk) > 4:
            return False

    return True

# =============================================================================
# TB filter (complexity + diversity + approximate outcome balance)
# =============================================================================

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

# DTM windows (plies, White POV)
_MIN_WIN_DTM = 24
_MIN_LOSS_DTM = 20

_MAX_DTM_BY_THEME = {
    0: 180,
    1: 150,
    2: 120,
    3: 80,
}

# Approximate outcome mixture (order-independent sampling).
# Aim: WIN 30-50, LOSS 20-40, DRAW 10-30 (cannot be guaranteed without post-sampling).
_OUTCOME_KEEP_P = {
    +1: 0.55,
     0: 0.30,
    -1: 0.70,
}

_THEME_KEEP_MOD = {
    0: 1.00,
    1: 1.00,
    2: 0.90,
    3: 0.75,
}

# Hardness knobs
_MIN_LOSING_ALTS_FOR_DRAW = 3
_MIN_NONWINNING_KING_ALTS_FOR_WIN = 2
_MIN_LOCAL_TRAPS_NEAR_BEST = 1

_MIN_LOSS_SPREAD_MEDIAN = 12
_MIN_LOSS_SPREAD_WORST = 24
_MIN_CLOSE_DEFENSES = 2
_CLOSE_DEF_PLIES = 4

def _bucket_salt(board: chess.Board, wdl: int) -> int:
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wpf, wpr = chess.square_file(wp), chess.square_rank(wp)
    bpf, bpr = chess.square_file(bp), chess.square_rank(bp)

    theme = _classify_theme(board)
    file_diff = abs(wpf - bpf)

    def dbin(d: int) -> int:
        if d <= 1: return 0
        if d <= 3: return 1
        if d <= 5: return 2
        return 3

    wdl_i = {-1: 0, 0: 1, 1: 2}[int(wdl)]
    salt = (
        wdl_i
        | (theme << 2)
        | (file_diff << 5)
        | (wpf << 8)
        | (dbin(_cheb(wk, wp)) << 12)
        | (dbin(_cheb(wk, bp)) << 14)
        | (dbin(_cheb(bk, wp)) << 16)
        | (dbin(_cheb(bk, bp)) << 18)
        | (dbin(_cheb(wk, bk)) << 20)
        | ((wpr & 7) << 22)
        | ((bpr & 7) << 25)
    )
    return salt & _U64_MASK

def filter_tb_kp_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KP TB filter (White POV), tuned for human complexity.

    Core ideas:
    - preserve "critical" (unique best move) positions
    - ensure mistakes are plausible: losing/drawing king moves near the best destination
    - avoid short/tactical conversions (DTM too small)
    - avoid pure races (theme-aware max DTM)
    """
    if board.turn != chess.WHITE:
        return False
    if not _is_lr_canonical(board):
        return False

    wdl = int(tb["wdl"])
    dtm = tb["dtm"]

    theme = _classify_theme(board)
    max_abs_dtm = _MAX_DTM_BY_THEME[theme]

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
    if len(legal_moves) < 6:
        return False

    king_moves = [mv for mv in legal_moves if board.piece_type_at(mv.from_square) == chess.KING]
    if len(king_moves) < 3:
        return False

    # Probe all moves once
    per_move = []
    for mv in legal_moves:
        pt = board.piece_type_at(mv.from_square) or 0
        is_cap = board.is_capture(mv)
        is_prom = mv.promotion is not None
        res = tb["probe_move"](mv)
        c_wdl = int(res["wdl"])
        c_dtm = res["dtm"]
        per_move.append((mv, pt, is_cap, is_prom, c_wdl, c_dtm))

    wins = [x for x in per_move if x[4] > 0]
    draws = [x for x in per_move if x[4] == 0]
    losses = [x for x in per_move if x[4] < 0]

    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))

    def local_traps_near(best_to: int, required_child_wdls: Tuple[int, ...]) -> int:
        """
        Count king moves whose destination is within Chebyshev distance 1 of best_to
        and whose child wdl is in required_child_wdls.
        """
        c = 0
        for (mv, pt, _cap, _prom, c_wdl, _dtm) in per_move:
            if pt != chess.KING:
                continue
            if _cheb(mv.to_square, best_to) <= 1 and c_wdl in required_child_wdls:
                c += 1
        return c

    def best_move_passes_geometry(best_mv: chess.Move) -> bool:
        """
        Prefer moves that are not just a "greedy approach" unless they create opposition-like parity.
        This biases towards opposition/triangulation motifs.
        """
        d_before = min(_cheb(wk, wp), _cheb(wk, bp))
        board.push(best_mv)
        wk2 = board.king(chess.WHITE)
        bk2 = board.king(chess.BLACK)
        d_after = min(_cheb(wk2, wp), _cheb(wk2, bp))
        ok = _opposition_like(wk2, bk2) or (d_after >= d_before)
        board.pop()
        return ok

    # Outcome-specific hardness checks
    if wdl > 0:
        # Unique winning move
        if len(wins) != 1:
            return False
        best_mv, best_pt, best_cap, best_prom, _c_wdl, _c_dtm = wins[0]
        if best_pt != chess.KING or best_cap or best_prom:
            return False

        # Must have both draw and loss alternatives (pressure)
        if len(draws) < 1 or len(losses) < 1:
            return False

        # Enough non-winning king alternatives
        nonwin_king = sum(1 for (mv, pt, _c, _p, c_wdl, _d) in per_move if pt == chess.KING and c_wdl != 1)
        if nonwin_king < _MIN_NONWINNING_KING_ALTS_FOR_WIN:
            return False

        # Local ambiguity: near-best king moves that fail
        traps = local_traps_near(best_mv.to_square, required_child_wdls=(0, -1)) - 0  # exclude best itself
        if traps < _MIN_LOCAL_TRAPS_NEAR_BEST:
            return False

        if not best_move_passes_geometry(best_mv):
            return False

    elif wdl == 0:
        # Unique drawing move
        if len(draws) != 1:
            return False
        best_mv, best_pt, best_cap, best_prom, _c_wdl, _c_dtm = draws[0]
        if best_pt != chess.KING or best_cap or best_prom:
            return False

        if len(losses) < _MIN_LOSING_ALTS_FOR_DRAW:
            return False

        # Need multiple losing KING moves (not only pawn blunders)
        losing_king = sum(1 for (_mv, pt, _c, _p, c_wdl, _d) in per_move if pt == chess.KING and c_wdl < 0)
        if losing_king < 2:
            return False

        # Local ambiguity: several near-best king moves lose
        traps = local_traps_near(best_mv.to_square, required_child_wdls=(-1,))
        if traps < 2:
            return False

        if not best_move_passes_geometry(best_mv):
            return False

    else:
        # Losing position: all moves must lose (otherwise WDL wouldn't be -1)
        if not (len(losses) == len(per_move) and len(wins) == 0 and len(draws) == 0):
            return False

        # Collect child DTMs (White POV, negative)
        dtms = []
        for (mv, pt, is_cap, is_prom, _c_wdl, c_dtm) in losses:
            if c_dtm is None:
                return False
            dtms.append((mv, pt, is_cap, is_prom, int(c_dtm)))

        # Best defense = most negative (longest survival)
        dtms.sort(key=lambda t: t[4])  # ascending: most negative first
        best_mv, best_pt, best_cap, best_prom, best_dtm = dtms[0]
        if best_cap or best_prom:
            return False
        if best_pt != chess.KING:
            return False
        if sum(1 for (_m, _pt, _c, _p, d) in dtms if d == best_dtm) > 1:
            return False

        med_dtm = dtms[len(dtms) // 2][4]
        worst_dtm = max(d for (_m, _pt, _c, _p, d) in dtms)

        if (med_dtm - best_dtm) < _MIN_LOSS_SPREAD_MEDIAN:
            return False
        if (worst_dtm - best_dtm) < _MIN_LOSS_SPREAD_WORST:
            return False

        close_defs = sum(1 for (_m, _pt, _c, _p, d) in dtms if d <= best_dtm + _CLOSE_DEF_PLIES)
        if close_defs < _MIN_CLOSE_DEFENSES:
            return False

        # Local ambiguity on defense as well: nearby king squares with still decent resistance
        near_good = 0
        for (mv, pt, _c, _p, d) in dtms[1:]:
            if pt != chess.KING:
                continue
            if _cheb(mv.to_square, best_mv.to_square) <= 1 and d <= best_dtm + 6:
                near_good += 1
        if near_good < 1:
            return False

    # Stable diversification / approximate outcome balancing
    p = _OUTCOME_KEEP_P[wdl] * _THEME_KEEP_MOD[theme]
    if p >= 1.0:
        return True

    salt = _bucket_salt(board, wdl)
    return _stable_random01(board, salt=salt) < p
