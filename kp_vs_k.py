# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess


def filter_notb_kp_vs_k(board: chess.Board) -> bool:
    """
    KP vs K no-TB specific filter (White to move).

    NOTE: This function previously had undefined variables (pf/bkf/wkf/wkr).
    Those are fixed here (obvious bug fix).
    """
    p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    pf, pr = chess.square_file(p), chess.square_rank(p)
    if pr not in (2, 3, 4):
        return False

    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)

    # "Block the pawn" = occupy the square directly in front of it (same file, rank+1).
    # For a white pawn on rank 7 this would be offboard, but rank 7 is excluded by your global pawn rule anyway.
    pawn_front = p + 8

    if bkr < pr and wk != pawn_front:
        return False

    # If Black king is outside the pawn's square and the White king is not on the pawn's path, reject.
    moves_to_promote = 7 - pr
    bk_in_square = max(abs(bkf - pf), abs(bkr - 7)) <= moves_to_promote
    wk_on_path = wkf == pf and wkr >= pr
    if not bk_in_square and not wk_on_path:
        return False

    return True


def filter_tb_kp_vs_k(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs K TB-specific filter (White POV outcomes):

    - If White wins (tb["wdl"] == +1): exactly one first move must also be a win.
    - If draw (tb["wdl"] == 0): require White king to be close to the pawn (Chebyshev distance <= 2).
    """
    dtm = tb["dtm"]
    if dtm is not None and abs(dtm) < 11:
        return False

    if tb["wdl"] == 1:
        winning = 0
        for move in board.legal_moves:
            if tb["probe_move"](move)["wdl"] == 1:
                winning += 1
                if winning > 1:
                    return False
        return winning == 1

    if tb["wdl"] == 0:
        p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
        wk = board.king(chess.WHITE)

        pf, pr = chess.square_file(p), chess.square_rank(p)
        wkf, wkr = chess.square_file(wk), chess.square_rank(wk)

        if max(abs(wkf - pf), abs(wkr - pr)) > 2:
            return False

        # White king more than 1 rank behind the pawn => reject.
        if pr - wkr > 1:
            return False

    return True
