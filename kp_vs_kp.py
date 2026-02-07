# kp_vs_kp.py
# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess


def gen_hints_kp_vs_kp() -> Mapping[str, Any]:
    """
    Hints for generation.
    For KP vs KP, we don't need restrictive mask hints as the search space 64^4 is manageable.
    """
    return {}


def filter_notb_kp_vs_kp(board: chess.Board) -> bool:
    """
    KP vs KP no-TB specific filter (White to move).
    """
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wp_r = chess.square_rank(wp)
    bp_r = chess.square_rank(bp)
    wp_f = chess.square_file(wp)
    bp_f = chess.square_file(bp)

    # 1. Pawn Advancement Heuristic
    # White pawn must be somewhat advanced (rank 3+, index 2) to trigger immediate tension.
    if wp_r < 2:
        return False
    
    # 2. King Proximity Heuristic
    # Kings should not be completely disconnected from the pawns.
    # At least one king should be within reasonable distance of a pawn.
    # Distance 4 covers half the board (Chebyshev).
    d_wk_wp = chess.square_distance(wk, wp)
    d_wk_bp = chess.square_distance(wk, bp)
    d_bk_bp = chess.square_distance(bk, bp)
    d_bk_wp = chess.square_distance(bk, wp)

    # If White King is far from both pawns, unlikely to be interesting (unless simple race).
    if d_wk_wp > 4 and d_wk_bp > 4:
        return False
    
    # If Black King is far from both pawns, unlikely to be interesting.
    if d_bk_bp > 4 and d_bk_wp > 4:
        return False

    # 3. Blocked Pawns
    # If pawns are on the same file and blocked (WP < BP), Kings must be closer to intervene.
    if wp_f == bp_f and wp_r < bp_r:
        # If blocked, at least one king should be very close (dist <= 3) to the blockade.
        # "Blockade" is roughly the squares between the pawns.
        # We simplify by checking distance to enemy pawn.
        if d_wk_bp > 3 and d_bk_wp > 3:
            return False

    return True


def filter_tb_kp_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KP TB-specific filter.
    
    Interestingness Criteria:
    - Result is Win or Draw (White to move).
    - Unique Best Move (Only 1 move preserves the optimal result).
    - DTM is not trivial (too short) or extremely long (shuffling).
    """
    wdl = tb["wdl"]
    dtm = tb["dtm"]

    # We skip "White loses" (-1).
    if wdl == -1:
        return False

    # Skip immediate checkmates or trivial captures/promotions.
    if dtm is not None and abs(dtm) <= 2:
        return False
    
    # Filter very long DTMs (e.g. > 100 plies) which are often just shuffling/maneuvering
    # that is hard to explain as a "puzzle".
    if dtm is not None and abs(dtm) > 80:
        return False

    legal_moves = list(board.legal_moves)
    if len(legal_moves) < 2:
        # Forced move is not a puzzle.
        return False

    # Check for Unique Best Move
    count_optimal = 0
    
    for move in legal_moves:
        res = tb["probe_move"](move)
        m_wdl = res["wdl"]
        
        if wdl == 1:
            # White wins: Move must also win.
            if m_wdl == 1:
                count_optimal += 1
        else:
            # White draws: Move must draw. (Other moves lose).
            if m_wdl == 0:
                count_optimal += 1
        
        # Optimization: Fail early
        if count_optimal > 1:
            return False

    return count_optimal == 1
