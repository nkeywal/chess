# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess


def filter_notb_kp_vs_kr(board: chess.Board) -> bool:
    """
    KP vs KR filters
    """
    p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    r = next(iter(board.pieces(chess.ROOK, chess.BLACK)))

    pr = chess.square_rank(p)
    pf = chess.square_file(p)

    # Pawn on human rank >= 5  <=>  0-based rank >= 4
    if pr < 4:
        return False

    # Pawn not on 7th rank (human) <=> 0-based rank != 6
    if pr == 6:
        return False

    # Pawn must not be able to capture the rook immediately (White to move).
    # White pawn captures to p+7 (down-left) or p+9 (down-right) in 0..63 indexing.
    if pf > 0 and r == p + 7:
        return False
    if pf < 7 and r == p + 9:
        return False

    bkr = chess.square_rank(bk)
    bkf = chess.square_file(bk)

    # Black king in front of the pawn: same file and strictly higher rank
    if bkf == pf and bkr > pr:
        return False

    # Black king not more than 2 ranks behind the pawn: pr - bkr <= 2
    if pr - bkr > 2:
        return False

    # Black king within 3 files of the pawn
    if abs(bkf - pf) > 3:
        return False

    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)

    # Chebyshev distance between White king and pawn
    if max(abs(wkf - pf), abs(wkr - pr)) > 1:
        return False

    rf, rr = chess.square_file(r), chess.square_rank(r)

    # Chebyshev distance between White king and Black rook must be > 1
    if max(abs(wkf - rf), abs(wkr - rr)) <= 1:
        return False

    # white king not on the same rank/file as the rook
    if wkf == rf or wkr == rr:
        return False

    return True


def filter_tb_kp_vs_kr(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KR TB-specific filter:
    """
    dtm = tb["dtm"]
    if dtm is not None and abs(dtm) < 11:
        return False

    if tb["wdl"] != 0:
        return True

    drawing_moves = 0
    non_drawing_moves = 0
    for move in board.legal_moves:
        if tb["probe_move"](move)["wdl"] == 0:
            drawing_moves += 1
        else:
            non_drawing_moves += 1
        if drawing_moves > 2:
            return False

    return drawing_moves == 1 or (drawing_moves == 2 and non_drawing_moves > 4)
