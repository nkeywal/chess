# kp_vs_kp.py
# All code/comments in English as requested.

from __future__ import annotations

import hashlib
from typing import Any, Mapping

import chess


def gen_hints_kp_vs_kp() -> Mapping[str, Any]:
    """
    Hints for generation.
    For KP vs KP, we don't need restrictive mask hints.
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
    # White pawn must be somewhat advanced (rank 3+, index 2)
    if wp_r < 2:
        return False
    
    # 2. King Proximity Heuristic
    d_wk_wp = chess.square_distance(wk, wp)
    d_wk_bp = chess.square_distance(wk, bp)
    d_bk_bp = chess.square_distance(bk, bp)
    d_bk_wp = chess.square_distance(bk, wp)

    if d_wk_wp > 4 and d_wk_bp > 4:
        return False
    
    if d_bk_bp > 4 and d_bk_wp > 4:
        return False

    # 3. Blocked Pawns
    if wp_f == bp_f and wp_r < bp_r:
        if d_wk_bp > 3 and d_bk_wp > 3:
            return False

    return True


def _get_stable_random(board: chess.Board) -> float:
    """Returns a stable float in [0, 1) based on the position."""
    h = hashlib.md5(board.fen().encode()).hexdigest()
    return int(h[:8], 16) / 0xffffffff


def filter_tb_kp_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KP TB-specific filter.
    
    Targeting: 40% Win, 40% Draw, 20% Loss.
    """
    wdl = tb["wdl"]
    dtm = tb["dtm"]

    # Skip immediate results or trivial captures/promotions.
    # For losses, we also want some "resistance".
    if dtm is not None and abs(dtm) <= 4:
        return False
    
    # Avoid extremely long maneuvering.
    if dtm is not None and abs(dtm) > 80:
        return False

    legal_moves = list(board.legal_moves)
    if len(legal_moves) < 2:
        return False

    # 1. Unique Best Move Analysis
    best_wdl = wdl
    best_dtm = -9999 if wdl == 1 else (9999 if wdl == -1 else 0)
    
    move_results = []
    for move in legal_moves:
        res = tb["probe_move"](move)
        m_wdl = res["wdl"]
        m_dtm = res["dtm"] or 0
        move_results.append((m_wdl, m_dtm))

    # Find the "optimal" result among moves.
    # For Win: max wdl (1), then min DTM (positive).
    # For Draw: max wdl (0).
    # For Loss: max wdl (-1), then max DTM (negative, e.g. -20 is better than -10).
    
    optimal_moves_count = 0
    
    if wdl == 1:
        # Win: best moves are those that win (wdl=1).
        # To be "unique", only one move should win.
        for m_wdl, m_dtm in move_results:
            if m_wdl == 1:
                optimal_moves_count += 1
    elif wdl == 0:
        # Draw: best moves are those that draw (wdl=0).
        for m_wdl, m_dtm in move_results:
            if m_wdl == 0:
                optimal_moves_count += 1
    else: # wdl == -1
        # Loss: all moves lose (wdl=-1).
        # A move is "optimal" if it maximizes DTM (delays mate).
        max_dtm = -9999
        for m_wdl, m_dtm in move_results:
            if m_dtm > max_dtm:
                max_dtm = m_dtm
        
        # Count how many moves achieve this max DTM.
        for m_wdl, m_dtm in move_results:
            if m_dtm == max_dtm:
                optimal_moves_count += 1
        
        # For losses, we also want the best move to be significantly better than the second best.
        # This makes the "choice" meaningful.
        dtms = sorted([r[1] for r in move_results], reverse=True)
        if len(dtms) >= 2:
            if dtms[0] - dtms[1] < 2: # Difference of at least 2 plies
                return False

    if optimal_moves_count != 1:
        return False

    # 2. Distribution Balancing (Probabilistic Downsampling)
    # Based on previous run: Wins were ~2x Draws. Losses were 0.
    # We want Win:Draw:Loss = 2:2:1.
    
    rnd = _get_stable_random(board)
    if wdl == 1:
        # Keep ~50% of wins to match draws.
        if rnd > 0.5:
            return False
    elif wdl == 0:
        # Keep all draws.
        pass
    elif wdl == -1:
        # Keep all "interesting" losses.
        # We'll see if we get enough.
        pass

    return True