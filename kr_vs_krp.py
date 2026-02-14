# kr_vs_krp.py
# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping, Optional
import hashlib

import chess

from helpers import mask_files, mask_ranks


# =============================================================================
# KR (White) vs KRP (Black), White to move
#
# Target distribution in the FINAL accepted set:
#   - win:  0%
#   - draw: 50%
#   - loss: 50%
#
# Complexity (root-only, safe with current probe_move cache):
#   - Draws: very few drawing moves (<=2), and at least one sharp losing blunder.
#   - Losses: non-trivial DTM, unique best defense, strong spread, and large blunders.
#
# NOTE: tb["probe_move"] in generate_positions.py caches by move.uci() only.
# It is safe only when used on the root board state (which we do here).
# =============================================================================


# ----------------------------
# Tuning knobs
# ----------------------------

_ALLOWED_PAWN_RANKS = (2, 3, 4)  # 0-based ranks (human 3/4/5)
_CANON_PAWN_FILE_MIN = 1         # b
_CANON_PAWN_FILE_MAX = 3         # d  (mirror-symmetry in b-g)

# Draw "shape"
_MAX_DRAWING_MOVES = 2
_DRAW_QUICK_LOSS_MAX_ABS_DTM = 60  # losing blunder within 60 plies => feels sharp

# Loss window (plies)
_MIN_ABS_DTM_LOSS = 24
_MAX_ABS_DTM_LOSS = 200

# Loss spread requirements (dtm is negative in White POV)
_LOSS_UNIQUE_GAP_MIN = 4
_LOSS_MEDIAN_GAP_MIN = 16
_LOSS_BIG_BLUNDER_GAP_MIN = 14

# Deterministic sampling (to approach 50/50 and reduce near-duplicates)
_KEEP_PROB_DRAW = 0.90
_KEEP_PROB_LOSS = 0.55
_BUCKET_DENOM_DRAW = 6
_BUCKET_DENOM_LOSS = 6


# =============================================================================
# Stable hashing / thinning (cheap)
# =============================================================================

def _board_u64_key(board: chess.Board) -> int:
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

    h = hashlib.md5(str(key).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "little", signed=False)


def _stable_u32(board: chess.Board, salt: int = 0) -> int:
    x = _board_u64_key(board) ^ (salt & ((1 << 64) - 1))
    x ^= (x >> 33) & ((1 << 64) - 1)
    x = (x * 0xff51afd7ed558ccd) & ((1 << 64) - 1)
    x ^= (x >> 33) & ((1 << 64) - 1)
    x = (x * 0xc4ceb9fe1a85ec53) & ((1 << 64) - 1)
    x ^= (x >> 33) & ((1 << 64) - 1)
    return (x ^ (x >> 32)) & 0xFFFFFFFF


def _keep_with_prob(board: chess.Board, p: float, salt: int) -> bool:
    if p >= 1.0:
        return True
    if p <= 0.0:
        return False
    return _stable_u32(board, salt) < int(p * 0x100000000)


def _bucket_id(board: chess.Board) -> int:
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wr = next(iter(board.pieces(chess.ROOK, chess.WHITE)))
    br = next(iter(board.pieces(chess.ROOK, chess.BLACK)))

    pf, pr = chess.square_file(bp), chess.square_rank(bp)
    d_wk = chess.square_distance(wk, bp)
    d_bk = chess.square_distance(bk, bp)

    wrf, wrr = chess.square_file(wr), chess.square_rank(wr)
    brf, brr = chess.square_file(br), chess.square_rank(br)

    out = 0
    out |= (pf & 7)
    out |= (pr & 7) << 3
    out |= (d_wk & 7) << 6
    out |= (d_bk & 7) << 9
    out |= ((wrf & 7) // 2) << 12
    out |= ((brf & 7) // 2) << 15
    out |= ((wrr & 7) // 2) << 18
    out |= ((brr & 7) // 2) << 21
    return out


def _thin_by_bucket(board: chess.Board, denom: int, salt: int) -> bool:
    if denom <= 1:
        return True
    b = _bucket_id(board)
    x = _stable_u32(board, salt ^ (b * 0x9E3779B1))
    return (x % denom) == 0


# =============================================================================
# NO-TB filter
# =============================================================================

def filter_notb_kr_vs_krp(board: chess.Board) -> bool:
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    try:
        bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
        wr = next(iter(board.pieces(chess.ROOK, chess.WHITE)))
        br = next(iter(board.pieces(chess.ROOK, chess.BLACK)))
    except StopIteration:
        return False

    pf, pr = chess.square_file(bp), chess.square_rank(bp)

    # Canonical pawn (mirror duplicates) + focus ranks.
    if pf < _CANON_PAWN_FILE_MIN or pf > _CANON_PAWN_FILE_MAX:
        return False
    if pr not in _ALLOWED_PAWN_RANKS:
        return False

    # Combat zone.
    if chess.square_distance(wk, bp) > 4:
        return False
    if chess.square_distance(bk, bp) > 4:
        return False

    if board.is_check():
        return False

    # Remove immediate tactical simplifications: any capture from the root => reject.
    for mv in board.legal_moves:
        if board.is_capture(mv):
            return False

    # Require both king and rook options (otherwise too forced / dull).
    rook_moves = 0
    king_moves = 0
    for mv in board.legal_moves:
        pt = board.piece_at(mv.from_square).piece_type
        if pt == chess.ROOK:
            rook_moves += 1
        elif pt == chess.KING:
            king_moves += 1
        if rook_moves >= 3 and king_moves >= 2:
            break
    if rook_moves < 3 or king_moves < 2:
        return False

    # Avoid immediate adjacency tactics.
    if chess.square_distance(br, wk) <= 1:
        return False
    if chess.square_distance(wr, bk) <= 1:
        return False

    return True


# =============================================================================
# TB filter (streaming, early exits for speed)
# =============================================================================

def filter_tb_kr_vs_krp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    wdl = int(tb["wdl"])
    dtm: Optional[int] = tb.get("dtm", None)

    # Exclude wins entirely.
    if wdl > 0:
        return False

    legal_moves = list(board.legal_moves)
    if len(legal_moves) < 4:
        return False

    # ----------------------------
    # DRAW branch
    # ----------------------------
    if wdl == 0:
        draw_moves = 0
        loss_exists = False
        quick_loss = False
        loss_rook = 0
        loss_king = 0

        # We must ensure draw_moves <= _MAX_DRAWING_MOVES,
        # so we need to examine all moves, but we can early-reject if exceeded.
        for mv in legal_moves:
            res = tb["probe_move"](mv)
            m_wdl = int(res["wdl"])
            if m_wdl > 0:
                return False  # shouldn't happen
            if m_wdl == 0:
                draw_moves += 1
                if draw_moves > _MAX_DRAWING_MOVES:
                    return False
            else:
                loss_exists = True
                m_dtm = res.get("dtm", None)
                if m_dtm is None:
                    return False
                if abs(int(m_dtm)) <= _DRAW_QUICK_LOSS_MAX_ABS_DTM:
                    quick_loss = True
                pt = board.piece_at(mv.from_square).piece_type
                if pt == chess.ROOK:
                    loss_rook += 1
                elif pt == chess.KING:
                    loss_king += 1

        if not loss_exists:
            return False
        if not quick_loss:
            return False
        if loss_rook == 0 or loss_king == 0:
            return False

        # Deterministic thinning.
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
        a = abs(int(dtm))
        if a < _MIN_ABS_DTM_LOSS or a > _MAX_ABS_DTM_LOSS:
            return False

        dtms: list[int] = []
        best_dtm = None
        best_count = 0
        best_mv: Optional[chess.Move] = None
        second_best_dtm = None

        # track large blunders by piece type (rook/king)
        big_blunder_rook = False
        big_blunder_king = False

        for mv in legal_moves:
            res = tb["probe_move"](mv)
            m_wdl = int(res["wdl"])
            if m_wdl >= 0:
                return False  # if any draw exists, root would be draw
            m_dtm = res.get("dtm", None)
            if m_dtm is None:
                return False
            d = int(m_dtm)
            dtms.append(d)

            if best_dtm is None or d < best_dtm:
                second_best_dtm = best_dtm
                best_dtm = d
                best_mv = mv
                best_count = 1
            elif d == best_dtm:
                best_count += 1
            else:
                if second_best_dtm is None or d < second_best_dtm:
                    second_best_dtm = d

        if len(dtms) < 4:
            return False
        if best_dtm is None or best_mv is None or second_best_dtm is None:
            return False

        # Unique best defense.
        if best_count != 1:
            return False
        if (second_best_dtm - best_dtm) < _LOSS_UNIQUE_GAP_MIN:
            return False

        # Median gap.
        dtms_sorted = sorted(dtms)
        median_dtm = dtms_sorted[len(dtms_sorted) // 2]
        if (median_dtm - best_dtm) < _LOSS_MEDIAN_GAP_MIN:
            return False

        # Detect existence of large blunders (faster loss).
        # (We need best_dtm known; do a second pass without more TB calls.)
        for mv, d in zip(legal_moves, dtms):
            if (d - best_dtm) < _LOSS_BIG_BLUNDER_GAP_MIN:
                continue
            pt = board.piece_at(mv.from_square).piece_type
            if pt == chess.ROOK:
                big_blunder_rook = True
            elif pt == chess.KING:
                big_blunder_king = True
            if big_blunder_rook and big_blunder_king:
                break

        if not (big_blunder_rook or big_blunder_king):
            return False

        # Avoid ultra-forced patterns: if best defense is rook move and king has almost no options.
        if board.piece_at(best_mv.from_square).piece_type == chess.ROOK:
            king_moves = 0
            for mv in legal_moves:
                if board.piece_at(mv.from_square).piece_type == chess.KING:
                    king_moves += 1
            if king_moves <= 1:
                return False

        # Deterministic thinning.
        if not _keep_with_prob(board, _KEEP_PROB_LOSS, salt=0x1055):
            return False
        if not _thin_by_bucket(board, _BUCKET_DENOM_LOSS, salt=0xB105):
            return False
        return True

    return False


# =============================================================================
# Generation hints (fast pruning)
# =============================================================================

def gen_hints_kr_vs_krp() -> Mapping[str, Any]:
    return {
        "piece_masks": {
            (False, chess.PAWN): mask_files(_CANON_PAWN_FILE_MIN, _CANON_PAWN_FILE_MAX)
                               & mask_ranks(list(_ALLOWED_PAWN_RANKS)),
        },
        "wk_to_pawn_cheb": (0, 4),
        "bk_to_pawn_cheb": (0, 4),
    }
