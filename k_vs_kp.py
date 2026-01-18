# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess


def filter_notb_k_vs_kp(board: chess.Board) -> bool:
    """
    K vs KP no-TB specific filter (White to move):

    - No capture on move 1: White king cannot capture the pawn immediately.
    - Advanced pawn: Black pawn must be on 0-based ranks 2/3/4 (human ranks 3/4/5).
    - Proximity: White king must be close to the pawn: Chebyshev distance <= 2.
    - Selection: Chebyshev distance(bK, pawn) must not be greater than Chebyshev distance(wK, pawn).
    - Extra exclusion: if pawn is on rank index 2, and White king is on rank index 0 or 1,
      and White king file is adjacent to the pawn file, reject.
    """
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))

    pr = chess.square_rank(p)
    if pr not in (2, 3, 4):
        return False

    pf = chess.square_file(p)
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)

    if pr == 2 and wkr in (0, 1) and abs(wkf - pf) == 1:
        return False

    d_wk_p = max(abs(wkf - pf), abs(wkr - pr))
    d_bk_p = max(abs(bkf - pf), abs(bkr - pr))

    if d_wk_p > 2:
        return False

    if d_wk_p <= 1:
        return False

    if d_bk_p > d_wk_p:
        return False

    return True


def filter_tb_k_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    K vs KP TB-specific filter (White POV outcomes):

    - If draw (tb["wdl"] == 0): keep only if exactly one first move draws.
    - If White loss (tb["wdl"] == -1): keep only if White king rank <= pawn rank
      (ranks decrease towards 0 for White here, so king is "lower" or same as the pawn towards promotion).
    - If White win (tb["wdl"] == +1): keep the position.
    """
    dtm = tb["dtm"]
    if dtm is not None and abs(dtm) < 11:
        return False

    wdl = tb["wdl"]

    if wdl == 0:
        drawing = 0
        for move in board.legal_moves:
            if tb["probe_move"](move)["wdl"] == 0:
                drawing += 1
                if drawing > 1:
                    return False
        return drawing == 1

    if wdl == -1:
        wk = board.king(chess.WHITE)
        p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
        return chess.square_rank(wk) <= chess.square_rank(p)

    return True
