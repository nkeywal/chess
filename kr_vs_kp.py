# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess


def filter_notb_kr_vs_kp(board: chess.Board) -> bool:
    """
    KR vs KP no-TB specific filter (White to move):

    Keep only positions where:
    - Black pawn is on human rank 5/6/7 (0-based rank 4/5/6).
    - White rook is not on the same rank/file as the black pawn.
    - Black king protects its pawn: Chebyshev distance(bK, pawn) <= 1.
    - White king is "late": Chebyshev distance(wK, pawn) > 2 and < 6.
    - White rook is not attacked by the black king or the black pawn.
    """
    p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    r = next(iter(board.pieces(chess.ROOK, chess.WHITE)))

    pr = chess.square_rank(p)
    pf = chess.square_file(p)

    # We want pawns on human rank 2/3/4 <=> 0-based rank in {1,2,3}
    if pr > 3:
        return False

    rf = chess.square_file(r)
    rr = chess.square_rank(r)

    # Rook not on same file/rank as the pawn
    if rf == pf or rr == pr:
        return False

    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)

    # Black king protects the pawn (Chebyshev distance <= 1)
    if max(abs(bkf - pf), abs(bkr - pr)) > 1:
        return False

    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)

    # White king is "late": distance > 2 and < 6
    d_wk_p = max(abs(wkf - pf), abs(wkr - pr))
    if d_wk_p <= 2 or d_wk_p >= 6:
        return False

    # Rook is not attacked by black king
    if max(abs(bkf - rf), abs(bkr - rr)) <= 1:
        return False

    # Rook is not attacked by the black pawn.
    # Black pawn captures diagonally "down" (towards rank decreasing): p-9 and p-7.
    if pf > 0 and r == p - 9:
        return False
    if pf < 7 and r == p - 7:
        return False

    return True


def filter_tb_kr_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KR vs KP TB-specific filter (White POV outcomes):

    - Reject White losses (tb["wdl"] == -1).
    - If White wins (tb["wdl"] == +1): exactly one first move must also be a win.
    - If draw (tb["wdl"] == 0):
        - Reject if the black pawn is on the 2nd rank (human), i.e. 0-based rank == 1.
        - Enforce:
            (1) At least one legal move loses (wdl == -1).
            (2) All drawing moves are made by the same piece type (king OR rook).
            (3) If drawing moves are king moves: at most 2 drawing moves.
            (4) If drawing moves are rook moves: at most 4 drawing moves AND
                all rook drawing moves go in the same direction (N/S/E/W).
    """
    dtm = tb["dtm"]
    if dtm is not None and abs(dtm) < 11:
        return False

    wdl = tb["wdl"]
    if wdl < 0:
        return False

    if wdl > 0:
        winning = 0
        for move in board.legal_moves:
            if tb["probe_move"](move)["wdl"] == 1:
                winning += 1
                if winning > 1:
                    return False
        return winning == 1

    # Draw case.
    p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    r = next(iter(board.pieces(chess.ROOK, chess.WHITE)))

    pr = chess.square_rank(p)
    pf = chess.square_file(p)

    # Too easy draw
    if pr == 1:
        return False

    rf = chess.square_file(r)
    rr = chess.square_rank(r)

    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)

    # Black king protects the pawn but is not in front of it
    if bkr < pr:
        return False

    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)

    # White king is not that "late"
    d_wk_p = max(abs(wkf - pf), abs(wkr - pr))
    if d_wk_p >= 4:
        return False
    
    d_r_p = max(abs(rf - pf), abs(rr - pr))        
    if d_r_p > 4:
        return False       

    return True
