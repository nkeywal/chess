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
    
    moves_iter = iter(board.legal_moves)
    try:
        next(moves_iter)
        next(moves_iter)
    except StopIteration:
        return False
    
    return True 


def filter_tb_generic(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    Generic filter that runs after tablebase probing.

    `tb` contains:
      - "wdl": int in {-1, 0, +1} from White's perspective
      - "dtm": Optional[int] in plies, from White's perspective (None if draw)
      - "probe_move": function(move) -> dict with:
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


def filter_notb_kr_vs_krp(board: chess.Board) -> bool:
    """
    KR (White) vs KRP (Black) no-TB filter.
    """
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    # Safe extraction.
    try:
        wp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
        wr = next(iter(board.pieces(chess.ROOK, chess.WHITE)))
        br = next(iter(board.pieces(chess.ROOK, chess.BLACK)))
    except StopIteration:
        return False

    pf, pr = chess.square_file(wp), chess.square_rank(wp)

    # 1. Black pawn: files b-g, ranks 2/3/4 (0-based, towards rank 0).
    if pf < 1 or pf > 6:
        return False
    if pr not in (2, 3, 4):
        return False

    # 2. Combat zone: both kings within distance <= 3 of the pawn.
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    bkf, bkr = chess.square_file(bk), chess.square_rank(bk)
    if max(abs(wkf - pf), abs(wkr - pr)) > 3:
        return False
    if max(abs(bkf - pf), abs(bkr - pr)) > 3:
        return False

    # 3. Safety: white king not in check; no immediate capture for White.
    if board.is_check():
        return False
    for mv in board.legal_moves:
        if board.is_capture(mv):
            return False

    # 4. Black rook activity: no check on white king, no attack on white rook.
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
        else: # drawing move
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

    # Bishops must not be en prise.
    if board.attackers(chess.BLACK, wb):
        return False
    if board.attackers(chess.WHITE, bb):
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
    if pf < 1 or pf > 6: return False
    if pr not in (4, 5): return False 

    # 2. Safety: no check to the white king (essential for evaluation).
    if board.is_check(): return False

    # 3. White king: must support the pawn (distance <= 2).
    # If it is farther, it is not useful.
    wkf, wkr = chess.square_file(wk), chess.square_rank(wk)
    if max(abs(wkf - pf), abs(wkr - pr)) > 2: return False

    # (NOTE: The black king distance constraint was removed
    # to allow "cut off" kings far away).

    # 4. Activity: no immediate capture (tactical cleanup).
    for mv in board.legal_moves:
        if board.is_capture(mv): return False

    # 5. Black rook: major correction here.
    # It must not attack the king (check) or the rook (exchange),
    # BUT it must be able to attack the pawn (foundation of defense).
    br_attacks = board.attacks(br)
    for sq in (wk, wr): # <-- wp was removed from this list!
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
    if wdl < 0: return False

    # --- Case 1: Win (seek precision / Lucena) ---
    if wdl > 0:
        winning = 0
        for move in board.legal_moves:
            if tb["probe_move"](move)["wdl"] == 1:
                winning += 1
                if winning > 1: 
                    return False # Too easy if multiple winning lines exist.
        return winning == 1 # Exactly one winning move (bridge-building, etc.).

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
        if wkr < pr: return False

        # 2. Passage illusion: the black king is NOT in front of the pawn.
        # If it is on the same file (bkf == pf), it visibly blocks.
        # We want it on the side (cut off or flank) so the player thinks "It's free!".
        if bkf == pf: return False
        
        return True

    return False




###################################"""
# --- filters.py additions for: White K vs Black KP ---


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



################################################""

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

    # Reject if the black king is outside the pawn's square and the white king is not on the pawn's path.
    moves_to_promote = 7 - pr
    promo_sq = chess.square(pf, 7)
    if max(abs(bkf - pf), abs(bkr - 7)) > moves_to_promote:
        if not (wkf == pf and wkr >= pr):
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
    dtm = tb["dtm"]
    if dtm is not None and abs(dtm) < 11:
        return False

    if tb["wdl"] != 0:
        return True

    drawing_moves = 0
    non_drawing_moves = 0
    for move in board.legal_moves:
        if tb["probe_move"](move)["wdl"] == 0:
            drawing_moves += 1
        else:
            non_drawing_moves += 1
        if drawing_moves > 2:
            return False

    return drawing_moves == 1 or (drawing_moves == 2 and non_drawing_moves > 4)
