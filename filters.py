# filters.py
# All code/comments in English as requested.

from __future__ import annotations

import chess
from typing import Any, Mapping


def filter_notb_generic(board: chess.Board) -> bool:
    """
    Cheap generic filter that runs before any tablebase probing.

    Return True to keep the position for the next stage (TB stage),
    or False to reject early.
    """
    
    if board.legal_moves.count() < 2:
    	return False
    
    return True 


def filter_tb_generic(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    Generic filter that runs after tablebase probing.

    `tb` contains:
      - "wdl": int in {-1, 0, +1} from White's perspective
      - "dtm": Optional[int] in plies, from White's perspective (None if draw)
      - "moves": list of dicts with:
          - "uci": str
          - "wdl": int in {-1, 0, +1} from White's perspective
          - "dtm": Optional[int] from White's perspective (None if draw)
    """
    return True


# Optional material-specific functions can be added as needed, e.g.:
# def filter_notb_kp_vs_kr(board: chess.Board) -> bool:
#     return True
#
# def filter_tb_kp_vs_kr(board: chess.Board, tb: Mapping[str, Any]) -> bool:
#     return True




# --- filters.py additions for: White KP vs Black K ---

def filter_notb_kp_vs_k(board: chess.Board) -> bool:
    """
    KP vs K no-TB specific filter (White to move):

    - The White pawn must be on human rank 3/4/5 (0-based rank 2/3/4).
    - Reject if the Black king is behind the pawn (bkr < pr) AND the White king does not block the pawn
      (i.e., White king is not on the square immediately in front of the pawn).
    """
    p = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    pr = chess.square_rank(p)
    if pr not in (2, 3, 4):
        return False

    bkr = chess.square_rank(bk)

    # "Block the pawn" = occupy the square directly in front of it (same file, rank+1).
    # For a white pawn on rank 7 this would be offboard, but rank 7 is excluded by your global pawn rule anyway.
    pawn_front = p + 8

    if bkr < pr and wk != pawn_front:
        return False

    return True



def filter_tb_kp_vs_k(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs K TB-specific filter (White POV outcomes):

    - If White wins (tb["wdl"] == +1): exactly one first move must also be a win.
    - If draw (tb["wdl"] == 0): require White king to be close to the pawn (Chebyshev distance <= 2).
    """
    if tb["wdl"] == 1:
        winning = 0
        for m in tb["moves"]:
            if m["wdl"] == 1:
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



#######################################################################################""

# --- filters.py additions for: White KR vs Black KP ---

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

    # Pawn on human rank 2/3/4 <=> 0-based rank in {1,2,3}
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
    wdl = tb["wdl"]
    if wdl < 0:
        return False

    moves = tb["moves"]

    if wdl > 0:
        winning = 0
        for m in moves:
            if m["wdl"] == 1:
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

    return True


###############################################################################################"

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
    if tb["wdl"] != 0:
        return True

    drawing_moves = 0
    non_drawing_moves = 0
    for m in tb["moves"]:
        if m["wdl"] == 0:
            drawing_moves += 1
        else:
            non_drawing_moves += 1

    return drawing_moves == 1 or (drawing_moves == 2 and non_drawing_moves > 4)







