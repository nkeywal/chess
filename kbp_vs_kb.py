# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess

from helpers import mask_files, mask_ranks


def filter_notb_kbp_vs_kb(board: chess.Board) -> bool:
    """
    KBP (White) vs KB (Black) no-TB filter.
    """
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    try:
        wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
        wb = next(iter(board.pieces(chess.BISHOP, chess.WHITE)))
        bb = next(iter(board.pieces(chess.BISHOP, chess.BLACK)))
    except StopIteration:
        return False

    # Bishops must be on the same color squares.
    if (chess.square_file(wb) + chess.square_rank(wb)) % 2 != (
        chess.square_file(bb) + chess.square_rank(bb)
    ) % 2:
        return False

    pf, pr = chess.square_file(wp), chess.square_rank(wp)

    # White pawn: files b-g, ranks 5/6 (0-based 4/5).
    if pf < 1 or pf > 6:
        return False
    if pr not in (4, 5):
        return False

    # Combat zone distances.
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)
    if max(abs(wkf - pf), abs(wkr - pr)) > 2:
        return False
    if max(abs(bkf - pf), abs(bkr - pr)) > 4:
        return False

    # Stability: no check on the white king, no immediate capture.
    if board.is_check():
        return False
    for mv in board.legal_moves:
        if board.is_capture(mv):
            return False

    # White bishop must not be en prise.
    if board.attackers(chess.BLACK, wb):
        return False

    # Exclude if White bishop can capture the Black bishop while the Black king
    # neither protects the bishop nor attacks the White pawn.
    if (board.attacks(wb) >> bb) & 1:
        bk_attacks = board.attacks(bk)
        if not ((bk_attacks >> bb) & 1) and not ((bk_attacks >> wp) & 1):
            return False

    return True


def filter_tb_kbp_vs_kb(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KBP (White) vs KB (Black) TB filter.
    """
    dtm = tb["dtm"]
    if dtm is not None and abs(dtm) < 11:
        return False

    wdl = tb["wdl"]

    # A. Win: exactly one winning move.
    if wdl > 0:
        winning = 0
        for move in board.legal_moves:
            if tb["probe_move"](move)["wdl"] == 1:
                winning += 1
                if winning > 1:
                    return False
        return winning == 1

    # B. Draw: white king at/above pawn rank; black king not in front of pawn.
    if wdl == 0:
        wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
        wk = board.king(chess.WHITE)
        bk = board.king(chess.BLACK)

        if chess.square_rank(wk) < chess.square_rank(wp):
            return False

        pawn_front = wp + 8
        if bk == pawn_front:
            return False

        return True

    return False


def gen_hints_kbp_vs_kb() -> Mapping[str, Any]:
    """
    5 pieces: White KBP vs Black KB.

    Derived from filter_notb_kbp_vs_kb (necessary conditions only).
    """
    return {
        "piece_masks": {
            (True, chess.PAWN): mask_files(1, 6) & mask_ranks([3, 4, 5]),
        },
        "wk_to_pawn_cheb": (0, 2),
        "bk_to_pawn_cheb": (0, 4),
        "bishops_same_color": True,
    }
