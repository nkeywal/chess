# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess

from helpers import mask_files, mask_ranks


def filter_notb_krp_vs_kr(board: chess.Board) -> bool:
    """
    KRP (White) vs KR (Black).
    CORRECTED VERSION.
    """
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    # Safe extraction
    try:
        wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
        wr = next(iter(board.pieces(chess.ROOK, chess.WHITE)))
        br = next(iter(board.pieces(chess.ROOK, chess.BLACK)))
    except StopIteration:
        return False

    pf, pr = chess.square_file(wp), chess.square_rank(wp)

    # 1. Pawn: files b-g, ranks index 4/5 (human 5/6).
    # This is the decision zone (Lucena vs Philidor).
    if pf < 1 or pf > 6:
        return False
    if pr not in (4, 5):
        return False

    # 2. Safety: no check to the white king (essential for evaluation).
    if board.is_check():
        return False

    # 3. White king: must support the pawn (distance <= 2).
    # If it is farther, it is not useful.
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    if max(abs(wkf - pf), abs(wkr - pr)) > 2:
        return False

    # (NOTE: The black king distance constraint was removed
    # to allow "cut off" kings far away).

    # 4. Activity: no immediate capture (tactical cleanup).
    for mv in board.legal_moves:
        if board.is_capture(mv):
            return False

    # 5. Black rook: major correction here.
    # It must not attack the king (check) or the rook (exchange),
    # BUT it must be able to attack the pawn (foundation of defense).
    br_attacks = board.attacks(br)
    for sq in (wk, wr):  # <-- wp was removed from this list!
        if (br_attacks >> sq) & 1:
            return False

    # 6. Pawn protection.
    # If the pawn is attacked (by king or rook), it must be defended.
    attackers = board.attackers(chess.BLACK, wp)
    if attackers:
        defenders = board.attackers(chess.WHITE, wp)
        if not defenders:
            return False

    return True


def filter_tb_krp_vs_kr(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KRP vs KR TB filter.
    Selects 'Precision Wins' or 'False Wins'.
    """
    dtm = tb["dtm"]
    if dtm is not None and abs(dtm) < 11:
        return False

    wdl = tb["wdl"]

    # Reject losses: too rare or caused by blunders.
    if wdl < 0:
        return False

    # --- Case 1: Win (seek precision / Lucena) ---
    if wdl > 0:
        winning = 0
        for move in board.legal_moves:
            if tb["probe_move"](move)["wdl"] == 1:
                winning += 1
                if winning > 1:
                    return False  # Too easy if multiple winning lines exist.
        return winning == 1  # Exactly one winning move (bridge-building, etc.).

    # --- Case 2: Draw (seek the illusion of a win) ---
    if wdl == 0:
        wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
        wk = board.king(chess.WHITE)
        bk = board.king(chess.BLACK)

        pr = chess.square_rank(wp)
        pf = chess.square_file(wp)
        wkr = chess.square_rank(wk)
        bkf = chess.square_file(bk)

        # 1. Activity illusion: the white king is in front of or next to the pawn.
        # If it is behind (wkr < pr), it is passive and the draw is obvious.
        if wkr < pr:
            return False

        # 2. Passage illusion: the black king is NOT in front of the pawn.
        # If it is on the same file (bkf == pf), it visibly blocks.
        # We want it on the side (cut off or flank) so the player thinks "It's free!".
        if bkf == pf:
            return False

        return True

    return False


def gen_hints_krp_vs_kr() -> Mapping[str, Any]:
    """
    5 pieces: White KRP vs Black KR.

    Derived from filter_notb_krp_vs_kr (necessary conditions only).
    """
    return {
        "piece_masks": {
            (True, chess.PAWN): mask_files(1, 6) & mask_ranks([4, 5]),
        },
        "wk_to_pawn_cheb": (0, 2),
    }
