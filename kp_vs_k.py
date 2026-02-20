# kp_vs_k.py
# All code/comments in English as requested.

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Optional

import chess

from helpers import mask_files, mask_ranks


# =============================================================================
# KP vs K (White to move)
#
# Goal:
# - Avoid trivial "just push" wins and dead-easy draws.
# - Prefer positions that feel "in play": kings close, black king in the pawn square,
#   and (for wins) a unique winning move with at least one natural drawing blunder.
# - Reduce near-duplicates by canonicalizing pawn files via gen_hints + deterministic thinning.
#
# Target mix (approx, stateless):
#   - win ~70%
#   - draw ~30%
#
# We achieve this primarily by:
#   - Keeping 100% of qualified draws (and slightly widening draw acceptance).
#   - Deterministically thinning wins (keep ~26% on average) using stable hashing.
#
# Notes / constraints:
# - The TB helper `tb["probe_move"]` is safe ONLY when used on the root board state.
#   Do NOT push moves in this filter and then call probe_move() on the mutated board.
#   In generate_positions.py, probe_move() caches by move.uci() only.
# =============================================================================


# ----------------------------
# Small utilities
# ----------------------------

def _cheb_dist(a: int, b: int) -> int:
    af, ar = a & 7, a >> 3
    bf, br = b & 7, b >> 3
    df = af - bf
    dr = ar - br
    if df < 0:
        df = -df
    if dr < 0:
        dr = -dr
    return df if df > dr else dr


def _stable_u32(board: chess.Board) -> int:
    """
    Deterministic per-position 32-bit hash for sampling/thinning.

    Prefer a proper transposition key if available; fallback to hashing the FEN.
    """
    key: Optional[int] = None

    # python-chess 1.x sometimes has zobrist_hash(); older versions have transposition_key()
    if hasattr(board, "zobrist_hash"):
        try:
            v = board.zobrist_hash()
            if isinstance(v, int):
                key = v
        except Exception:
            key = None

    if key is None:
        for attr in ("transposition_key", "_transposition_key"):
            if hasattr(board, attr):
                try:
                    v = getattr(board, attr)
                    v = v() if callable(v) else v
                    if isinstance(v, int):
                        key = v
                        break
                except Exception:
                    pass

    if key is None:
        h = hashlib.md5(board.fen().encode("utf-8")).hexdigest()
        key = int(h[:8], 16)

    return key & 0xFFFFFFFF


def _mix32(x: int) -> int:
    # Small 32-bit mix (Avalanche-ish)
    x &= 0xFFFFFFFF
    x ^= (x >> 16) & 0xFFFFFFFF
    x = (x * 0x7feb352d) & 0xFFFFFFFF
    x ^= (x >> 15) & 0xFFFFFFFF
    x = (x * 0x846ca68b) & 0xFFFFFFFF
    x ^= (x >> 16) & 0xFFFFFFFF
    return x & 0xFFFFFFFF


def _stable_u32_salt(board: chess.Board, salt: int) -> int:
    return _mix32(_stable_u32(board) ^ (salt & 0xFFFFFFFF))


def _keep_with_prob(board: chess.Board, p: float, salt: int) -> bool:
    """
    Deterministic keep-with-probability gate.
    """
    if p >= 1.0:
        return True
    if p <= 0.0:
        return False
    return _stable_u32_salt(board, salt) < int(p * 0x100000000)


def _pawn_front_sq(p: int) -> int:
    # White pawn moves "up" (+8). Caller guarantees pawn not on rank 8.
    return p + 8


def _pawn_promo_sq(pf: int) -> int:
    return chess.square(pf, 7)


def _is_rook_pawn(pf: int) -> bool:
    return pf == 0 or pf == 7


def _is_knight_pawn(pf: int) -> bool:
    return pf == 1 or pf == 6


# =============================================================================
# Generation hints (important for variety + speed)
# =============================================================================

def gen_hints_kp_vs_k() -> Mapping[str, Any]:
    """
    Hints to reduce trivial/duplicate candidates before TB probing.

    - Canonicalize pawn to files a-d (mirror symmetry) => halves near-duplicates.
    - Focus on advanced pawns (ranks 4-7 in human terms => 0-based ranks 3-6).
    - Keep kings in the "combat zone" around the pawn.
    """
    return {
        "piece_masks": {
            # Pawn on files a-d only, and ranks 4..7 (0-based 3..6).
            (True, chess.PAWN): mask_files(0, 3) & mask_ranks([3, 4, 5, 6]),
        },
        # Encourage interaction, but not always "touching".
        "wk_to_pawn_cheb": (1, 3),
        "bk_to_pawn_cheb": (0, 4),
    }


# =============================================================================
# Stage A: no-tablebase filter
# =============================================================================

def filter_notb_kp_vs_k(board: chess.Board) -> bool:
    """
    KP vs K no-TB filter (White to move).

    We want positions that are plausibly non-trivial, so we enforce:
    - Pawn somewhat advanced (rank 3..6 is already hinted, but keep a hard guard).
    - Kings not too far from the pawn (interaction).
    - Black king inside (or very close to) the pawn square to avoid "free queening".
    - Avoid the ultra-trivial "pawn on 7th and not blocked" (usually one-move promotion).
    """
    try:
        p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    except StopIteration:
        return False

    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    if wk is None or bk is None:
        return False

    pf, pr = chess.square_file(p), chess.square_rank(p)

    # Hard guard: keep pawn advanced only.
    if pr < 3 or pr > 6:
        return False

    # Avoid the "one-move queen" farm.
    if pr == 6:
        if bk != _pawn_front_sq(p):
            return False

    # Interaction: both kings should be relevant.
    d_wk_p = _cheb_dist(wk, p)
    d_bk_p = _cheb_dist(bk, p)
    if d_wk_p > 3:
        return False
    if d_bk_p > 4:
        return False

    # Avoid positions where Black attacks the pawn and White cannot protect it or move it to safety.
    if d_bk_p == 1:
        can_save = False
        for move in board.legal_moves:
            if move.from_square == p:
                if _cheb_dist(bk, move.to_square) > 1 or _cheb_dist(wk, move.to_square) == 1:
                    can_save = True
                    break
            elif move.from_square == wk:
                if _cheb_dist(move.to_square, p) == 1:
                    can_save = True
                    break
        if not can_save:
            return False

    # Pawn square heuristic.
    moves_to_promote = 7 - pr
    promo_sq = _pawn_promo_sq(pf)
    if _cheb_dist(bk, promo_sq) > (moves_to_promote + 1):
        return False

    # Also keep WK not totally off the pawn file when pawn is still far.
    if pr <= 4 and abs((wk & 7) - pf) >= 3:
        return False

    return True


# =============================================================================
# Stage B: tablebase filter
# =============================================================================

# Win thinning: tune these if your observed baseline ratio is different.
# With your observed ~90% win / 10% draw, keeping ~0.26 of wins yields ~70/30 overall.
_WIN_KEEP_P_ROOK = 0.34    # keep more rook-pawn wins (rare/thematic)
_WIN_KEEP_P_KNIGHT = 0.30  # keep more knight-pawn wins
_WIN_KEEP_P_OTHER = 0.21   # main thinning knob (c/d pawns in our canonical a-d set)


def filter_tb_kp_vs_k(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs K TB filter.

    Outcomes are from White POV:
      - wdl == +1 : White wins (DTM is positive int)
      - wdl ==  0 : Draw (DTM is None)

    We target:
      - Interesting wins: unique winning move AND at least one drawing blunder.
      - Interesting draws: advanced pawn + close kings + "block/opposition" structure (feels winnable).
      - Deterministic thinning to reduce near-duplicates and reach ~70/30 win/draw.
    """
    wdl = tb["wdl"]
    dtm = tb["dtm"]

    try:
        p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    except StopIteration:
        return False
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    if wk is None or bk is None:
        return False

    pf, pr = chess.square_file(p), chess.square_rank(p)

    if wdl not in (0, 1):
        return False

    legal_moves = list(board.legal_moves)
    if len(legal_moves) < 2:
        return False

    # -------------------------------------------------------------------------
    # WIN case
    # -------------------------------------------------------------------------
    if wdl == 1:
        # DTM sanity window: remove "instant wins" and very long shuffles.
        if dtm is None:
            return False
        if dtm < 16:
            return False
        if dtm > 180:
            return False

        winning_moves = []
        drawing_moves = []

        for mv in legal_moves:
            res = tb["probe_move"](mv)
            if res["wdl"] == 1:
                winning_moves.append((mv, res["dtm"]))
            else:
                drawing_moves.append(mv)

        # Must have exactly one winning move.
        if len(winning_moves) != 1:
            return False

        # Must have at least one drawing blunder (otherwise "any move wins").
        if len(drawing_moves) == 0:
            return False

        best_move, best_dtm = winning_moves[0]
        if best_dtm is None:
            return False

        # Prefer wins where the winning move isn't a trivial pawn push from 6th/7th.
        if board.piece_at(best_move.from_square).piece_type == chess.PAWN:
            if best_dtm < 20:
                return False

        # Encourage "looks drawable": at least one drawing move should be a king move.
        has_king_blunder = False
        for mv in drawing_moves:
            pt = board.piece_at(mv.from_square).piece_type
            if pt == chess.KING:
                has_king_blunder = True
                break
        if not has_king_blunder:
            return False

        # Deterministically thin wins to reach ~70/30 overall.
        if _is_rook_pawn(pf):
            keep_p = _WIN_KEEP_P_ROOK
        elif _is_knight_pawn(pf):
            keep_p = _WIN_KEEP_P_KNIGHT
        else:
            keep_p = _WIN_KEEP_P_OTHER

        # Slightly favor advanced pawn wins (they are rarer and more thematic).
        if pr >= 5:
            keep_p = min(1.0, keep_p + 0.05)

        return _keep_with_prob(board, keep_p, salt=0xB16B00B5)

    # -------------------------------------------------------------------------
    # DRAW case
    # -------------------------------------------------------------------------
    if wdl == 0:
        # "Hard draw" heuristics:
        # We slightly widen acceptance to increase draw yield:
        # - allow pr == 3 (4th rank) only in very tight "block" configurations.
        if pr < 3:
            return False

        d_wk_p = _cheb_dist(wk, p)
        d_bk_p = _cheb_dist(bk, p)

        if d_wk_p > 2:
            return False
        if d_bk_p > 2:
            return False

        pawn_front = _pawn_front_sq(p)
        promo_sq = _pawn_promo_sq(pf)

        in_front_same_file = (chess.square_file(bk) == pf and chess.square_rank(bk) > pr)

        # Primary block zone: BK blocks or is clearly in front.
        if bk != pawn_front and bk != promo_sq and not in_front_same_file:
            # Secondary: BK adjacent to the front square (common "shouldering" draws).
            if chess.square_distance(bk, pawn_front) > 1:
                return False

        # Make it feel "almost winning": WK should be at/above pawn rank.
        if chess.square_rank(wk) < pr:
            return False

        # pr==3 draws are only accepted if BK is directly blocking and kings are very tight.
        if pr == 3:
            if bk != pawn_front:
                return False
            if d_wk_p > 1 or d_bk_p > 1:
                return False

        # Rook pawn special-case: encourage corner motif.
        if _is_rook_pawn(pf):
            corner = chess.A8 if pf == 0 else chess.H8
            if _cheb_dist(bk, corner) > 2:
                return False

        # Prefer positions where White has limited king moves (zugzwang-ish), but allow a bit more.
        if len(legal_moves) > 10:
            return False

        # Keep 100% of qualified draws (we want ~30% overall).
        return True

    return False
