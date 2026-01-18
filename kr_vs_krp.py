# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess

from helpers import mask_files, mask_ranks


def filter_notb_kr_vs_krp(board: chess.Board) -> bool:
    """
    KR (White) vs KRP (Black) no-TB filter.
    """
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    # Safe extraction.
    try:
        bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
        wr = next(iter(board.pieces(chess.ROOK, chess.WHITE)))
        br = next(iter(board.pieces(chess.ROOK, chess.BLACK)))
    except StopIteration:
        return False

    pf, pr = chess.square_file(bp), chess.square_rank(bp)

    # Black pawn: files b-g, ranks 2/3/4 (0-based).
    if pf < 1 or pf > 6:
        return False
    if pr not in (2, 3, 4):
        return False

    # Combat zone: both kings within distance <= 3 of the pawn.
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)
    if max(abs(wkf - pf), abs(wkr - pr)) > 3:
        return False
    if max(abs(bkf - pf), abs(bkr - pr)) > 3:
        return False

    # Safety: white king not in check; no immediate capture for White.
    if board.is_check():
        return False
    for mv in board.legal_moves:
        if board.is_capture(mv):
            return False

    # Black rook activity: no check on white king, no attack on white rook.
    br_attacks = board.attacks(br)
    for sq in (wk, wr):
        if (br_attacks >> sq) & 1:
            return False

    return True


def filter_tb_kr_vs_krp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KR (White) vs KRP (Black) TB filter.
    """
    wdl = tb["wdl"]
    dtm = tb["dtm"]

    # A. White wins -> exclude.
    if wdl > 0:
        return False

    # B. White loses -> false fortress.
    if wdl < 0:
        if abs(dtm) < 11:
            return False
        p = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
        pf, pr = chess.square_file(p), chess.square_rank(p)
        wk = board.king(chess.WHITE)
        wkf, wkr = chess.square_file(wk), chess.square_rank(wk)

        if wkr > pr:
            return False

        if max(abs(wkf - pf), abs(wkr - pr)) > 2:
            return False

        return True

    # C. Draw -> precision save.
    drawing_rook_moves = 0
    drawing_king_moves = 0
    losing_exists = False

    for move in board.legal_moves:
        outcome = tb["probe_move"](move)["wdl"]
        if outcome < 0:
            losing_exists = True
        else:  # drawing move
            piece = board.piece_at(move.from_square)
            if piece.piece_type == chess.ROOK:
                drawing_rook_moves += 1
                if drawing_rook_moves >= 3 or drawing_king_moves >= 1:
                    return False
            else:
                drawing_king_moves += 1
                if drawing_king_moves >= 2 or drawing_rook_moves >= 1:
                    return False

    if not losing_exists:
        return False

    return False


def gen_hints_kr_vs_krp() -> Mapping[str, Any]:
    """
    5 pieces: White KR vs Black KRP.

    Derived from filter_notb_kr_vs_krp (necessary conditions only).
    """
    return {
        "piece_masks": {
            (False, chess.PAWN): mask_files(1, 6) & mask_ranks([2, 3, 4]),
        },
        "wk_to_pawn_cheb": (0, 3),
        "bk_to_pawn_cheb": (0, 3),
    }
