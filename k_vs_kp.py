# k_vs_kp.py
# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple, List
import hashlib

import chess
from helpers import mask_files, mask_ranks


# =============================================================================
# Goals
# =============================================================================
# - Positions are always White to move (generator invariant).
# - We want ONLY {Draw, Loss} from White POV with a ~50/50 mix.
# - Losses should "look savable": White king is close, and natural-looking moves
#   still lose, but with meaningful DTM differences (trap-like).
# - Draws should be non-trivial: ideally an "only move" draw.
#
# IMPORTANT correctness note:
# - tb["probe_move"](move) in generate_positions.py is cached by move.uci() only.
#   That function is only correct for probing *root* legal moves.
#   Do NOT call tb["probe_move"] after pushing moves on the board.
#   (We only probe root moves in this file.)
# =============================================================================


# ----------------------------
# Tuning knobs (safe defaults)
# ----------------------------

# Pawn ranks (0-based) to consider (human ranks 3/4/5).
_ALLOWED_PAWN_RANKS = (2, 3, 4)

# Canonicalize left-right symmetry: only keep pawn files a..d (0..3).
_CANON_PAWN_FILE_MAX = 3

# Root "anti-triviality": exclude very short mate distances.
_MIN_ABS_DTM_ROOT = 16

# Loss difficulty window (plies to mate, from White POV; dtm is negative on loss).
_MIN_ABS_DTM_LOSS = 28
_MAX_ABS_DTM_LOSS = 180

# Require a meaningful gap between best defense and typical defenses.
# For losses: median_dtm - best_dtm must be at least this many plies.
_LOSS_MEDIAN_GAP_MIN = 6

# Require at least one "plausible" wrong move that still heads toward the pawn
# but loses significantly faster than best defense.
_LOSS_PLAUSIBLE_BLUNDER_GAP_MIN = 6

# Downsampling to hit ~50/50 and reduce near-duplicates.
# These are applied AFTER all structural checks.
_KEEP_PROB_DRAW = 0.11
_KEEP_PROB_LOSS = 1.00

# Additional per-bucket thinning to reduce clusters of near-identical positions.
# Keep 1 out of BUCKET_DENOM per bucket deterministically (via stable hash).
_BUCKET_DENOM_DRAW = 1
_BUCKET_DENOM_LOSS = 1


# =============================================================================
# Small utilities
# =============================================================================

def _cheb(a: int, b: int) -> int:
    """Chebyshev distance between squares a and b."""
    af, ar = chess.square_file(a), chess.square_rank(a)
    bf, br = chess.square_file(b), chess.square_rank(b)
    return max(abs(af - bf), abs(ar - br))


def _board_u64_key(board: chess.Board) -> int:
    """
    Stable-ish key across runs. Prefer zobrist/transposition when available,
    else fall back to hashing the FEN (slower but stable).
    """
    key: object
    if hasattr(board, "zobrist_hash"):
        key = board.zobrist_hash()
    elif hasattr(board, "transposition_key"):
        key = board.transposition_key()
    elif hasattr(board, "_transposition_key"):
        key = board._transposition_key
    else:
        key = board.fen()

    if callable(key):
        key = key()
    if isinstance(key, int):
        return key & ((1 << 64) - 1)

    # Fallback: hash bytes deterministically (md5).
    h = hashlib.md5(str(key).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "little", signed=False)


def _stable_u32(board: chess.Board, salt: int = 0) -> int:
    """Deterministic 32-bit value based on position + salt."""
    x = _board_u64_key(board) ^ (salt & ((1 << 64) - 1))
    # mix down to 32 bits (xorshift-ish)
    x ^= (x >> 33) & ((1 << 64) - 1)
    x *= 0xff51afd7ed558ccd
    x &= ((1 << 64) - 1)
    x ^= (x >> 33) & ((1 << 64) - 1)
    x *= 0xc4ceb9fe1a85ec53
    x &= ((1 << 64) - 1)
    x ^= (x >> 33) & ((1 << 64) - 1)
    return (x ^ (x >> 32)) & 0xFFFFFFFF


def _keep_with_prob(board: chess.Board, p: float, salt: int) -> bool:
    """Keep with probability p in a deterministic way."""
    if p >= 1.0:
        return True
    if p <= 0.0:
        return False
    threshold = int(p * 0x100000000)  # 2^32
    return _stable_u32(board, salt) < threshold


def _bucket_id(board: chess.Board) -> int:
    """
    Coarse bucket used to thin near-duplicate positions without state.
    """
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))

    pf, pr = chess.square_file(p), chess.square_rank(p)
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)

    d_wk = max(abs(wkf - pf), abs(wkr - pr))
    d_bk = max(abs(bkf - pf), abs(bkr - pr))

    wk_rel = (wkr - pr) + 7  # shift to 0..14
    bk_rel = (bkr - pr) + 7

    # "Opposition-ish" feature: same file and within 2 ranks.
    opp = 1 if (wkf == pf and abs(wkr - pr) <= 2) else 0

    rook_pawn = 1 if pf in (0, 7) else 0
    edge_pawn = 1 if pf in (0, 1, 6, 7) else 0

    # pack into an int (small ranges)
    out = 0
    out |= (pf & 7)
    out |= (pr & 7) << 3
    out |= (d_wk & 7) << 6
    out |= (d_bk & 7) << 9
    out |= (wk_rel & 15) << 12
    out |= (bk_rel & 15) << 16
    out |= (opp & 1) << 20
    out |= (rook_pawn & 1) << 21
    out |= (edge_pawn & 1) << 22
    return out


def _thin_by_bucket(board: chess.Board, denom: int, salt: int) -> bool:
    """Keep 1/denom of positions for a given coarse bucket."""
    if denom <= 1:
        return True
    b = _bucket_id(board)
    x = _stable_u32(board, salt ^ (b * 0x9E3779B1))
    return (x % denom) == 0


def _move_toward_pawn(move: chess.Move, pawn_sq: int, d_before: int) -> bool:
    """Return True if the king move does not increase Chebyshev distance to the pawn."""
    d_after = _cheb(move.to_square, pawn_sq)
    return d_after <= d_before


# =============================================================================
# Generation hints (optional but strongly recommended)
# =============================================================================

def gen_hints_k_vs_kp() -> Mapping[str, Any]:
    """
    3 pieces: White K vs Black K+P.

    Hints are necessary-conditions only:
      - pawn on ranks 3/4/5 (0-based 2/3/4)
      - canonical pawn files a..d to reduce mirror duplicates
    """
    return {
        "piece_masks": {
            (False, chess.PAWN): mask_files(0, _CANON_PAWN_FILE_MAX) & mask_ranks(list(_ALLOWED_PAWN_RANKS)),
        },
    }


# =============================================================================
# Filters
# =============================================================================

def filter_notb_k_vs_kp(board: chess.Board) -> bool:
    """
    K vs KP no-TB specific filter (White to move).

    Purpose:
      - Enforce "interaction zone" positions: kings are close enough that it isn't
        a pure counting race, and White can't trivially capture immediately.
      - Reduce near-duplicates via canonical pawn file and distance constraints.

    Keeps positions only if:
      - Black pawn is on ranks 2/3/4 (0-based) and files a..d (canonical symmetry).
      - White king cannot capture the pawn immediately (distance >= 2).
      - White king is close: Chebyshev distance in {2, 3}.
      - Black king is also relevant: Chebyshev distance <= 3.
      - Kings are not extremely far from each other (keeps interaction).
    """
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    try:
        p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    except StopIteration:
        return False

    pf, pr = chess.square_file(p), chess.square_rank(p)
    if pr not in _ALLOWED_PAWN_RANKS:
        return False
    if pf > _CANON_PAWN_FILE_MAX:
        return False

    # Interaction zone distances.
    d_wk_p = _cheb(wk, p)
    d_bk_p = _cheb(bk, p)

    if d_wk_p < 2:
        return False
    if d_wk_p > 3:
        return False

    if d_bk_p > 3:
        return False

    # Avoid "kings too far": these become timing-only races.
    if _cheb(wk, bk) > 5:
        return False

    # Basic stability.
    if board.is_check():
        return False

    # Must have some choice (generic filter already ensures >=2 legal moves,
    # but here we enforce >=3 to avoid degenerate zugzwang-only corners).
    if sum(1 for _ in board.legal_moves) < 3:
        return False

    return True


def filter_tb_k_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    K vs KP TB-specific filter (White POV outcomes).

    Target final mix: ~50% draw / ~50% loss, no wins.

    DRAW selection:
      - Exactly one drawing move at the root (only-move draw).
      - At least one losing move exists (obvious).
      - The drawing move must be "plausible": not increasing distance to pawn.
      - There must exist at least one other plausible-looking king move (also not
        increasing distance) that nevertheless loses (to make it trap-like).

    LOSS selection:
      - Root is loss (wdl == -1) with a non-trivial DTM window.
      - All root moves must lose (sanity).
      - Best defense is meaningfully better than typical defenses
        (median gap >= _LOSS_MEDIAN_GAP_MIN).
      - There exists a plausible blunder (still not increasing distance) that loses
        significantly faster than best defense.

    Downsampling:
      - Deterministic thinning via stable hash to:
          - reduce near-duplicates (bucket thinning)
          - approximate the requested 50/50 draw/loss balance
    """
    wdl = int(tb["wdl"])
    dtm: Optional[int] = tb.get("dtm", None)

    # Reject "wins" (shouldn't happen in K vs KP under our no-TB constraints).
    if wdl > 0:
        return False

    # Global anti-triviality: short mates are usually too easy.
    if dtm is not None and abs(dtm) < _MIN_ABS_DTM_ROOT:
        return False

    wk = board.king(chess.WHITE)
    p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    d_before = _cheb(wk, p)

    legal_moves = list(board.legal_moves)
    if len(legal_moves) < 2:
        return False

    # Probe ONLY root legal moves.
    draws: List[chess.Move] = []
    losses: List[Tuple[chess.Move, int]] = []  # dtm is negative

    for mv in legal_moves:
        res = tb["probe_move"](mv)
        m_wdl = int(res["wdl"])
        m_dtm = res.get("dtm", None)

        if m_wdl == 0:
            draws.append(mv)
        elif m_wdl < 0:
            if m_dtm is None:
                # Defensive loss must have DTM.
                return False
            losses.append((mv, int(m_dtm)))
        else:
            # If any move wins, root can't be draw/loss in standard WDL logic.
            return False

    # ----------------------------
    # DRAW branch
    # ----------------------------
    if wdl == 0:
        # Only-move draw
        if len(draws) != 1:
            return False
        if len(losses) < 1:
            return False

        draw_mv = draws[0]

        # Plausibility: drawing move doesn't "run away" from the pawn.
        if not _move_toward_pawn(draw_mv, p, d_before):
            return False

        # Trap-like: at least one other plausible-looking move loses.
        plausible_losing = 0
        quick_losing = 0
        for mv, m_dtm in losses:
            if _move_toward_pawn(mv, p, d_before):
                plausible_losing += 1
            # "Danger": at least one move loses within 60 plies (feels close).
            if abs(m_dtm) <= 60:
                quick_losing += 1

        if plausible_losing == 0:
            return False
        if quick_losing == 0:
            return False

        # Downsample draws to approach 50/50 overall and reduce close positions.
        if not _keep_with_prob(board, _KEEP_PROB_DRAW, salt=0xD00D):
            return False
        if not _thin_by_bucket(board, _BUCKET_DENOM_DRAW, salt=0xA11CE):
            return False

        return True

    # ----------------------------
    # LOSS branch
    # ----------------------------
    if wdl < 0:
        if dtm is None:
            return False
        if abs(dtm) < _MIN_ABS_DTM_LOSS or abs(dtm) > _MAX_ABS_DTM_LOSS:
            return False

        # Must be a pure loss: no drawing root move (otherwise root would be draw).
        if len(draws) != 0:
            return False
        if len(losses) < 2:
            # Needs at least some choice to be interesting.
            return False

        # Sort by dtm ascending: most negative (longest survival) first.
        losses_sorted = sorted(losses, key=lambda t: t[1])

        best_mv, best_dtm = losses_sorted[0]
        second_dtm = losses_sorted[1][1]
        dtms = [d for _m, d in losses_sorted]

        # Require "unique-ish" best defense: at least 3 plies better than 2nd best.
        if second_dtm - best_dtm < 3:
            return False

        # Median gap vs best: best should be significantly better than typical moves.
        median_dtm = dtms[len(dtms) // 2]
        if (median_dtm - best_dtm) < _LOSS_MEDIAN_GAP_MIN:
            return False

        # "Looks savable": there exists a plausible blunder (still moving toward pawn)
        # that loses much faster than best defense.
        plausible_blunder = False
        for mv, m_dtm in losses_sorted[1:]:
            if not _move_toward_pawn(mv, p, d_before):
                continue
            if (m_dtm - best_dtm) >= _LOSS_PLAUSIBLE_BLUNDER_GAP_MIN:
                plausible_blunder = True
                break
        if not plausible_blunder:
            return False

        # Downsample losses only slightly (mostly we downsample draws).
        if not _keep_with_prob(board, _KEEP_PROB_LOSS, salt=0x1055):
            return False
        if not _thin_by_bucket(board, _BUCKET_DENOM_LOSS, salt=0xB105):
            return False

        return True

    # Should not reach.
    return False
