from __future__ import annotations
from typing import Any, Mapping
import chess

def filter_notb_kp_vs_kr(board: chess.Board) -> bool:
    """
    KP (White) vs KR (Black) - NO-TB Filter.
    """
    p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    r = next(iter(board.pieces(chess.ROOK, chess.BLACK)))

    pr, pf = chess.square_rank(p), chess.square_file(p)

    # 1. Pawn Rank: Human Ranks 5, 6, 7 (Indices 4, 5, 6).
    # We still allow Rank 7 into the pipeline for the "Loss" scenario.
    if pr < 4: 
        return False 

    # 2. White King: Must be close (Distance <= 1).
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    if max(abs(wkf - pf), abs(wkr - pr)) > 1:
        return False

    # 3. Black King: Reject if ON THE TRAJECTORY.
    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)
    if bkf == pf and bkr > pr:
        return False

    # 4. Safety & Tactics
    if board.is_check(): 
        return False

    for move in board.legal_moves:
        if board.is_capture(move):
            return False

    return True


def filter_tb_kp_vs_kr(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KR - Tablebase Filter.
    """
    wdl = tb["wdl"]
    dtm = tb["dtm"]
    
    # 1. GLOBAL FILTER: ANTI-TACTICS
    if dtm is not None and abs(dtm) < 11:
        return False

    # Get pawn rank once.
    p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    pr = chess.square_rank(p)

    # --- SCENARIO A: WHITE LOSES (Black Wins) ---
    if wdl < 0:
        # LOSSES are interesting even on Rank 7 (The "Almost Queen" Trap).
        # We accept Rank 6 (Index 5) and Rank 7 (Index 6).
        if pr < 5: 
            return False
        return True

    # --- SCENARIO B: DRAW ---
    if wdl == 0:
        # STRICT FILTER: Only Rank 6 (Index 5).
        # Rank 7 draws are usually trivial (Rook sacrifice).
        # Rank 5 draws are boring technical defense.
        if pr != 5:
            return False

        drawing_moves = 0
        losing_moves = 0
        
        for move in board.legal_moves:
            res = tb["probe_move"](move)
            if res["wdl"] == 0:
                drawing_moves += 1
                if drawing_moves >= 2:
                    return False
            elif res["wdl"] < 0:
                losing_moves += 1
        
        # Danger required.
        if losing_moves == 0: return False
            
        return True

    # --- SCENARIO C: WHITE WINS ---
    if wdl > 0:
        # STRICT FILTER: Only Rank 6 (Index 5).
        # Rank 7 wins are usually just "Push to Queen".
        if pr != 5:
            return False

        winning_moves = 0
        for move in board.legal_moves:
            res = tb["probe_move"](move)
            if res["wdl"] > 0:
                winning_moves += 1
        
        # Puzzle Logic: Only 1 winning move allowed.
        if winning_moves == 1:
            return True

        return False

    return False
