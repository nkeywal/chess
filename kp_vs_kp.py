# kp_vs_kp.py
# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping, Optional, List, Tuple

import chess

from helpers import mask_files

# =============================================================================
# Generation hints
# =============================================================================

def gen_hints_kp_vs_kp() -> Mapping[str, Any]:
    """
    KP vs KP generation hints.

    Remove left-right symmetric duplicates by restricting the WHITE pawn to files a-d.
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
    Return a stable-ish 64-bit key for the position.
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
# Theme classifier + coarse diversity bucketing
# =============================================================================

# Theme id:
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


def _bucket_salt(board: chess.Board, wdl: int) -> int:
    """
    Coarse feature bucket used for stable sampling (anti-clustering).

    IMPORTANT: keep this bucket coarse so that near-duplicates collide and only
    a fraction is kept, improving variety.
    """
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wpf, wpr = chess.square_file(wp), chess.square_rank(wp)
    bpf, bpr = chess.square_file(bp), chess.square_rank(bp)

    theme = _classify_theme(board)
    file_diff = abs(wpf - bpf)

    file_diff_bin = 0 if file_diff == 0 else (1 if file_diff == 1 else 2)
    wpf_bin = 0 if wpf <= 1 else 1          # (a,b) vs (c,d) since WP is a-d only
    bpf_bin = bpf // 2                       # 0..3

    wpr_bin = 0 if wpr <= 3 else 1           # ranks 3-4 vs 5-6 (human)
    bpr_bin = 0 if bpr >= 4 else 1           # ranks 6-5 vs 4-3 (human, from Black side)

    def dbin(d: int) -> int:
        if d <= 2:
            return 0
        if d <= 4:
            return 1
        return 2

    dkw = dbin(chess.square_distance(wk, wp))
    dkb = dbin(chess.square_distance(wk, bp))
    dbw = dbin(chess.square_distance(bk, wp))
    dbb = dbin(chess.square_distance(bk, bp))
    dkk = dbin(chess.square_distance(wk, bk))

    wdl_i = {-1: 0, 0: 1, 1: 2}[int(wdl)]

    salt = (
        wdl_i
        | (theme << 2)
        | (file_diff_bin << 4)
        | (wpf_bin << 6)
        | (bpf_bin << 7)
        | (wpr_bin << 9)
        | (bpr_bin << 10)
        | (dkw << 11)
        | (dkb << 13)
        | (dbw << 15)
        | (dbb << 17)
        | (dkk << 19)
    )
    return salt & _U64_MASK


# =============================================================================
# Cheap (no-TB) filter + PRE-TB sampling (speed lever)
# =============================================================================

# Global pre-TB sampling (uniform-ish).
_PRE_TB_SAMPLE_P = 0.03

# Over-sample "likely losing" geometries (still no TB involved).
# This is the key to avoiding 0% losses when losses are rarer in raw generation.
_PRE_TB_SAMPLE_P_LOSSLIKE = 0.10


def filter_notb_kp_vs_kp(board: chess.Board) -> bool:
    """
    KP vs KP no-TB filter (White to move).

    Goals:
    - Reject obvious trivialities early.
    - Keep kings involved with at least one pawn.
    - Ensure enough king mobility (proxy for opposition/tempo).
    - Reduce redundancy via symmetry + stable pre-TB sampling.
    """
    if board.turn != chess.WHITE:
        return False
    if not _is_lr_canonical(board):
        return False
    if board.is_check():
        return False

    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wpr = chess.square_rank(wp)
    bpr = chess.square_rank(bp)
    wpf = chess.square_file(wp)
    bpf = chess.square_file(bp)

    # Keep pawns in human ranks 3..6 (0-based 2..5)
    if not (2 <= wpr <= 5):
        return False
    if not (2 <= bpr <= 5):
        return False

    file_diff = abs(wpf - bpf)
    locked_same_file = (wpf == bpf and abs(wpr - bpr) == 1)
    diagonal_contact = (file_diff == 1 and abs(wpr - bpr) == 1)

    # Kings must be relevant (avoid pure races).
    d_wk_wp = chess.square_distance(wk, wp)
    d_wk_bp = chess.square_distance(wk, bp)
    d_bk_wp = chess.square_distance(bk, wp)
    d_bk_bp = chess.square_distance(bk, bp)

    if min(d_wk_wp, d_wk_bp) > 4:
        return False
    if min(d_bk_wp, d_bk_bp) > 4:
        return False

    if file_diff >= 2 and not (locked_same_file or diagonal_contact):
        if chess.square_distance(wk, bk) > 5 and min(d_wk_bp, d_bk_wp) > 4:
            return False

    # Require real branching + king mobility (single pass).
    n = 0
    king_moves = 0
    pawn_moves = 0
    for mv in board.legal_moves:
        n += 1
        pt = board.piece_type_at(mv.from_square)
        if pt == chess.KING:
            king_moves += 1
        elif pt == chess.PAWN:
            pawn_moves += 1
        if n >= 6 and king_moves >= 3 and (pawn_moves >= 1 or locked_same_file):
            break

    if n < 6 or king_moves < 3:
        return False
    if not (pawn_moves >= 1 or locked_same_file):
        return False

    # "Likely losing" heuristic for White: black pawn is at least as advanced,
    # black king closer to black pawn, and white king not dominating.
    # This does not decide WDL, it only biases sampling so we don't starve losses.
    likely_losslike = (
        (bpr >= wpr) and
        (d_bk_bp <= d_wk_wp) and
        (d_wk_bp >= d_bk_bp)
    )

    p = _PRE_TB_SAMPLE_P_LOSSLIKE if likely_losslike else _PRE_TB_SAMPLE_P
    if p < 1.0:
        if _stable_random01(board, salt=_bucket_salt(board, 0)) >= p:
            return False

    return True


# =============================================================================
# TB filter: sharpness + local traps; includes pawn defenses for LOSS
# =============================================================================

# DTM windows (plies, White POV).
_MIN_WIN_DTM = 24
_MIN_LOSS_DTM = 20

_MAX_DTM_BY_THEME = {
    0: 220,
    1: 160,
    2: 130,
    3: 90,
}

# Outcome mixture (stable sampling).
# You target: win 30-50%, loss 20-40%, draw 10-30%.
# Start here, then retune after a run:
_OUTCOME_KEEP_P = {
    +1: 0.42,  # reduce wins a bit
     0: 0.18,  # reduce draws strongly
    -1: 0.95,  # keep almost all losses that pass hardness
}

_THEME_KEEP_MOD = {
    0: 1.00,
    1: 1.00,
    2: 0.95,
    3: 0.80,
}

# Ignore "instant blunders" when counting traps.
_MIN_CHILD_ABS_LOSS_DTM = 12


def filter_tb_kp_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KP TB filter (White POV), tuned for:
    - non-triviality (local traps / plausible king alternatives),
    - variety (stable coarse bucketing),
    - speed (probe king moves first),
    - and *non-starvation of losses* (pawn defenses are considered for LOSS).
    """
    if board.turn != chess.WHITE:
        return False
    if not _is_lr_canonical(board):
        return False

    wdl = int(tb["wdl"])
    dtm = tb["dtm"]

    theme = _classify_theme(board)
    max_abs_dtm = _MAX_DTM_BY_THEME[theme]

    # Global DTM sanity for wins/losses only.
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

    moves = list(board.legal_moves)
    if len(moves) < 6:
        return False

    king_moves: List[chess.Move] = []
    pawn_moves: List[chess.Move] = []
    for mv in moves:
        pt = board.piece_type_at(mv.from_square)
        if pt == chess.KING:
            king_moves.append(mv)
        elif pt == chess.PAWN:
            pawn_moves.append(mv)

    if len(king_moves) < 3:
        return False

    def cap_or_prom(mv: chess.Move) -> bool:
        return board.is_capture(mv) or (mv.promotion is not None)

    # Probe king moves once (high signal, small set).
    # k_res: (mv, child_wdl, child_dtm|None)
    k_res: List[Tuple[chess.Move, int, Optional[int]]] = []
    k_wins: List[Tuple[chess.Move, Optional[int]]] = []
    k_draws: List[chess.Move] = []
    k_losses: List[Tuple[chess.Move, Optional[int]]] = []

    for mv in king_moves:
        res = tb["probe_move"](mv)
        cw = int(res["wdl"])
        cd0 = res["dtm"]
        cd = None if cd0 is None else int(cd0)
        k_res.append((mv, cw, cd))
        if cw > 0:
            k_wins.append((mv, cd))
        elif cw == 0:
            k_draws.append(mv)
        else:
            k_losses.append((mv, cd))

    def count_local_traps(best_mv: chess.Move, best_to: int) -> Tuple[int, int, int]:
        """
        Returns (local_nonwin, local_draw, local_loss) among king moves near best_to (Chebyshev <= 1),
        excluding best_mv, ignoring "instant" losses.
        """
        nonwin = 0
        draws = 0
        losses = 0
        for mv, cw, cd in k_res:
            if mv == best_mv:
                continue
            if chess.square_distance(mv.to_square, best_to) > 1:
                continue
            if cw <= 0:
                if cw < 0:
                    if cd is None or abs(cd) < _MIN_CHILD_ABS_LOSS_DTM:
                        continue
                    losses += 1
                else:
                    draws += 1
                nonwin += 1
        return nonwin, draws, losses

    # ---------------- WIN ----------------
    if wdl > 0:
        best_mv: Optional[chess.Move] = None
        best_dtm: Optional[int] = None

        # Prefer a winning king move (opposition/tempo).
        if k_wins:
            def _key(t: Tuple[chess.Move, Optional[int]]) -> Tuple[int, str]:
                mv, cd = t
                return (cd if cd is not None else 10**9, mv.uci())
            best_mv, best_dtm = min(k_wins, key=_key)
        else:
            # Fallback: winning pawn move allowed if non-capture/non-promo, but still require king traps.
            for mv in pawn_moves:
                if cap_or_prom(mv):
                    continue
                res = tb["probe_move"](mv)
                if int(res["wdl"]) > 0:
                    cd0 = res["dtm"]
                    cd = None if cd0 is None else int(cd0)
                    if best_mv is None:
                        best_mv, best_dtm = mv, cd
                    else:
                        if best_dtm is None and cd is not None:
                            best_mv, best_dtm = mv, cd
                        elif best_dtm is not None and cd is not None and cd < best_dtm:
                            best_mv, best_dtm = mv, cd

        if best_mv is None:
            return False
        if cap_or_prom(best_mv):
            return False

        # Make wins sharper (and reduce win %):
        # - require at least one drawing king alternative
        # - AND at least one losing king alternative (otherwise "safe win" tends to be trivial)
        if len(k_draws) < 1:
            return False
        if len(k_losses) < 1:
            return False

        # Avoid "everything wins" king-wise.
        if len(k_wins) > 2:
            return False

        if board.piece_type_at(best_mv.from_square) == chess.KING:
            local_nonwin, local_draw, _local_loss = count_local_traps(best_mv, best_mv.to_square)
            if local_nonwin < 2 or local_draw < 1:
                return False
        else:
            nonwin_total = sum(
                1 for (_mv, cw, cd) in k_res
                if cw <= 0 and (cw == 0 or (cd is not None and abs(cd) >= _MIN_CHILD_ABS_LOSS_DTM))
            )
            if nonwin_total < 2:
                return False

    # ---------------- DRAW ----------------
    elif wdl == 0:
        # Keep only "sharp" draws: not too many drawing king moves.
        if not (1 <= len(k_draws) <= 2):
            return False

        # Need multiple serious losing king moves (avoid only-instant blunders).
        losing_long = 0
        for _mv, cw, cd in k_res:
            if cw < 0 and cd is not None and abs(cd) >= _MIN_CHILD_ABS_LOSS_DTM:
                losing_long += 1
        if losing_long < 2:
            return False

        # Local traps around a drawing king move square: at least 2 nearby losing king moves.
        best_draw = min(k_draws, key=lambda m: m.uci())  # stable
        local_nonwin, _local_draw, local_loss = count_local_traps(best_draw, best_draw.to_square)
        if local_loss < 2 or local_nonwin < 2:
            return False

    # ---------------- LOSS ----------------
    else:
        # For losses, consider BOTH king and pawn defenses (very important for KP vs KP).
        # We still keep "complex" losses only (spread + both near-good and bad alternatives).
        defenses_all: List[Tuple[chess.Move, int, int]] = []
        # (mv, child_dtm, piece_type)

        # King defenses from k_res
        for mv, cw, cd in k_res:
            if cw >= 0:
                continue
            if cd is None:
                continue
            if cap_or_prom(mv):
                continue
            defenses_all.append((mv, int(cd), chess.KING))

        # Pawn defenses (probe now; pawn moves are few)
        for mv in pawn_moves:
            if cap_or_prom(mv):
                continue
            res = tb["probe_move"](mv)
            cw = int(res["wdl"])
            cd0 = res["dtm"]
            if cw >= 0:
                # If any pawn move draws/wins, root wouldn't be losing; be conservative.
                return False
            if cd0 is None:
                return False
            defenses_all.append((mv, int(cd0), chess.PAWN))

        # Need enough defenses to be interesting.
        if len(defenses_all) < 4:
            return False

        # More negative => longer survival (White POV)
        defenses_all.sort(key=lambda t: t[1])
        best_mv, best_d, best_pt = defenses_all[0]

        if cap_or_prom(best_mv):
            return False

        # Avoid "too many equally best" defenses (often trivial/robustly lost).
        best_ties = sum(1 for (_m, d, _pt) in defenses_all if d == best_d)
        if best_ties > 2:
            return False

        # Require meaningful spread: median must be much worse than best.
        med_d = defenses_all[len(defenses_all) // 2][1]
        if (med_d - best_d) < 10:
            return False

        # Complexity for losses:
        # - at least 2 "near-best" defenses (within 4 plies of best)
        # - at least 2 "bad" defenses (>= 12 plies worse than best)
        near_good = sum(1 for (_m, d, _pt) in defenses_all if d <= best_d + 4)
        bad = sum(1 for (_m, d, _pt) in defenses_all if d >= best_d + 12)

        if near_good < 2:
            return False
        if bad < 2:
            return False

        # If best is a king move, enforce local tempo traps around the destination.
        if best_pt == chess.KING:
            local_bad = 0
            for mv, d, pt in defenses_all[1:]:
                if pt != chess.KING:
                    continue
                if chess.square_distance(mv.to_square, best_mv.to_square) > 1:
                    continue
                if (d - best_d) >= 12 and abs(d) >= _MIN_CHILD_ABS_LOSS_DTM:
                    local_bad += 1
            if local_bad < 1:
                return False
        else:
            # If best is a pawn move, still require king defenses to contain both near-good and bad options.
            king_only = [(mv, d) for (mv, d, pt) in defenses_all if pt == chess.KING]
            if len(king_only) < 2:
                return False
            k_near = sum(1 for (_m, d) in king_only if d <= best_d + 4)
            k_bad = sum(1 for (_m, d) in king_only if d >= best_d + 12)
            if k_near < 1 or k_bad < 1:
                return False

    # Stable diversification / approximate outcome balancing
    p = _OUTCOME_KEEP_P[wdl] * _THEME_KEEP_MOD[theme]
    if p >= 1.0:
        return True
    return _stable_random01(board, salt=_bucket_salt(board, wdl)) < p
