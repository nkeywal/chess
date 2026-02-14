# All code/comments in English as requested.
from __future__ import annotations

from typing import Any, Mapping, Tuple, List
import chess

from helpers import mask_files, mask_ranks


# -----------------------
# Tuning knobs
# -----------------------

# Avoid trivial "already decided" stuff.
_MIN_LOSS_ABS_DTM = 12     # losing for White but not immediate
_RESILIENT_AFTER_DTM = 12  # after a White move, Black still wins but mate is far (good resistance)
_QUICK_AFTER_DTM = 6       # after a White move, Black can mate quickly (clear blunder)

# Draw hardness: only a few drawing moves; many losing moves.
_MAX_DRAWING_MOVES = 3
_MIN_LOSING_ALTERNATIVES = 3
_MIN_DTM_SPREAD_DRAW = 8

# Loss hardness: looks drawable.
_MIN_RESILIENT_MOVES = 3
_MIN_QUICK_BLUNDERS = 1
_MIN_DTM_SPREAD_LOSS = 8


def _cheb(a: int, b: int) -> int:
    af, ar = chess.square_file(a), chess.square_rank(a)
    bf, br = chess.square_file(b), chess.square_rank(b)
    return max(abs(af - bf), abs(ar - br))


def _piece_counts_ok(board: chess.Board) -> bool:
    """Ensure we are really in KR vs KRP (exactly 5 pieces)."""
    if board.occupied.bit_count() != 5:
        return False
    if len(board.pieces(chess.KING, chess.WHITE)) != 1:
        return False
    if len(board.pieces(chess.ROOK, chess.WHITE)) != 1:
        return False
    if len(board.pieces(chess.KING, chess.BLACK)) != 1:
        return False
    if len(board.pieces(chess.ROOK, chess.BLACK)) != 1:
        return False
    if len(board.pieces(chess.PAWN, chess.BLACK)) != 1:
        return False
    if board.pieces(chess.PAWN, chess.WHITE):
        return False
    # No other pieces.
    for pt in (chess.QUEEN, chess.BISHOP, chess.KNIGHT):
        if board.pieces(pt, chess.WHITE) or board.pieces(pt, chess.BLACK):
            return False
    return True


def filter_notb_kr_vs_krp(board: chess.Board) -> bool:
    """
    No-TB prefilter: keep positions that are "endgame-like" (no immediate tactics/captures),
    so TB probing later finds instructional decision points.
    """
    if board.turn != chess.WHITE:
        return False
    if not _piece_counts_ok(board):
        return False

    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wr = next(iter(board.pieces(chess.ROOK, chess.WHITE)))
    br = next(iter(board.pieces(chess.ROOK, chess.BLACK)))

    pf, pr = chess.square_file(bp), chess.square_rank(bp)

    # Pawn zone (kept from your original intent): b-g, middle ranks.
    if pf < 1 or pf > 6:
        return False
    if pr not in (2, 3, 4):
        return False

    # Both kings not too far from pawn (keeps "interaction").
    if _cheb(wk, bp) > 3:
        return False
    if _cheb(bk, bp) > 3:
        return False

    # No check, no immediate captures (avoid trivial tactics).
    if board.is_check():
        return False
    for mv in board.legal_moves:
        if board.is_capture(mv):
            return False

    # Avoid trivial rook tactics: black rook directly attacks white king or rook.
    br_att = board.attacks(br)
    if br_att & chess.BB_SQUARES[wk]:
        return False
    if br_att & chess.BB_SQUARES[wr]:
        return False

    return True


def _probe_after_white_move(board: chess.Board, tb: Mapping[str, Any], move: chess.Move) -> Tuple[int, int]:
    """
    Returns (wdl_after, dtm_after) for the position AFTER White plays `move`
    (so side to move becomes Black). Convention: WDL is from side-to-move POV.
    """
    res = tb["probe_move"](move)
    wdl_after = int(res["wdl"])
    dtm_after = int(res.get("dtm", 0) or 0)
    return wdl_after, dtm_after


def _looks_like_false_fortress_loss(board: chess.Board) -> bool:
    """
    Losses that *look drawable*: White king blocks in front/near the pawn,
    giving a fortress vibe, yet TB says it's losing.
    """
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    pf, pr = chess.square_file(bp), chess.square_rank(bp)
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)

    # For a black pawn heading towards rank 0, "in front" means white king on same or lower rank.
    if wkr > pr:
        return False

    # Close blockade impression.
    if max(abs(wkf - pf), abs(wkr - pr)) > 2:
        return False

    return True


def _looks_like_losing_draw(board: chess.Board) -> bool:
    """
    Draws that *look losing*: white king not ideally placed to stop the pawn,
    pawn is advanced-ish and protected-ish.
    """
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    pf, pr = chess.square_file(bp), chess.square_rank(bp)

    # Encourage more "advanced" pawn to create pressure illusion.
    if pr < 3:
        return False

    # White king not sitting comfortably in front/adjacent of pawn.
    if _cheb(wk, bp) <= 2:
        return False

    # Black king reasonably close to support.
    if _cheb(bk, bp) > 3:
        return False

    # Pawn is defended by something (king/rook), makes it feel more dangerous.
    if not board.is_attacked_by(chess.BLACK, bp):
        return False

    return True


def _hard_loss(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    White to move is losing (wdl < 0).
    Keep only losses that look drawable and contain meaningful choices:
    - not immediate,
    - multiple resisting moves (mate still far),
    - at least one blunder that loses fast,
    - and fortress-like illusion.
    """
    dtm = int(tb.get("dtm", 0) or 0)
    if abs(dtm) < _MIN_LOSS_ABS_DTM:
        return False

    resilient = 0
    quick = 0
    dtms: List[int] = []

    for mv in board.legal_moves:
        wdl_after, dtm_after = _probe_after_white_move(board, tb, mv)

        # After White's move, Black to move should still be winning in a true loss.
        if wdl_after <= 0:
            return False

        dtms.append(dtm_after)
        if dtm_after >= _RESILIENT_AFTER_DTM:
            resilient += 1
        if dtm_after <= _QUICK_AFTER_DTM:
            quick += 1

    if resilient < _MIN_RESILIENT_MOVES:
        return False
    if quick < _MIN_QUICK_BLUNDERS:
        return False
    if (max(dtms) - min(dtms)) < _MIN_DTM_SPREAD_LOSS:
        return False

    return _looks_like_false_fortress_loss(board)


def _hard_draw(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    White to move is drawing (wdl == 0).
    Keep draws that are hard:
    - only a few drawing moves,
    - many losing alternatives (easy to mess up),
    - and some alternatives lose quickly (tactical/tempo trap),
    - plus overall position looks losing.
    """
    drawing_moves = 0
    losing_alts = 0
    dtms_losing: List[int] = []
    any_quick_loss = False
    any_long_loss = False

    for mv in board.legal_moves:
        wdl_after, dtm_after = _probe_after_white_move(board, tb, mv)

        # After White's move, it's Black to move.
        if wdl_after == 0:
            drawing_moves += 1
            if drawing_moves > _MAX_DRAWING_MOVES:
                return False
        elif wdl_after > 0:
            # White blundered into a Black win.
            losing_alts += 1
            dtms_losing.append(dtm_after)
            if dtm_after <= _QUICK_AFTER_DTM:
                any_quick_loss = True
            if dtm_after >= _RESILIENT_AFTER_DTM:
                any_long_loss = True
        else:
            # In a true draw, allowing a "win for White" move often makes the position too non-representative.
            # (Also tends to be tactical; we prefer draw-vs-loss tension, not hidden wins.)
            return False

    if drawing_moves == 0:
        return False
    if losing_alts < _MIN_LOSING_ALTERNATIVES:
        return False
    if not any_quick_loss:
        return False
    if not any_long_loss:
        return False

    if dtms_losing and (max(dtms_losing) - min(dtms_losing)) < _MIN_DTM_SPREAD_DRAW:
        return False

    return _looks_like_losing_draw(board)


def filter_tb_kr_vs_krp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    TB filter for KR (White) vs KRP (Black).

    Target set: only DRAW and LOSS for White.
    - wdl == 0: keep hard draws (look losing; easy to blunder)
    - wdl < 0 : keep hard losses (look drawable; many resisting moves)
    - wdl > 0 : always reject (no winning positions for White)
    """
    if board.turn != chess.WHITE:
        return False
    if not _piece_counts_ok(board):
        return False

    wdl = int(tb["wdl"])

    if wdl > 0:
        return False  # explicitly exclude White wins

    if wdl == 0:
        return _hard_draw(board, tb)

    # wdl < 0
    return _hard_loss(board, tb)


def gen_hints_kr_vs_krp() -> Mapping[str, Any]:
    """
    5 pieces: White KR vs Black KRP.
    Derived from filter_notb_kr_vs_krp (necessary conditions only).
    """
    return {
        "piece_masks": {
            # Black pawn on b-g, ranks 3-5 from White POV (0-based 2/3/4).
            (False, chess.PAWN): mask_files(1, 6) & mask_ranks([2, 3, 4]),
        },
        "wk_to_pawn_cheb": (0, 3),
        "bk_to_pawn_cheb": (0, 3),
    }
