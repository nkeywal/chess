# kp_vs_kp.py
# All code/comments in English as requested.

from __future__ import annotations

import hashlib
from typing import Any, Mapping

import chess


def gen_hints_kp_vs_kp() -> Mapping[str, Any]:
    """
    Hints for generation.
    """
    return {}


def filter_notb_kp_vs_kp(board: chess.Board) -> bool:
    """
    KP vs KP no-TB specific filter (White to move).
    Strict filtering for "interesting" positions.
    """
    wp = next(iter(board.pieces(chess.PAWN, chess.WHITE)))
    bp = next(iter(board.pieces(chess.PAWN, chess.BLACK)))
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)

    wp_r = chess.square_rank(wp)
    # bp_r = chess.square_rank(bp) # unused logic
    # wp_f = chess.square_file(wp)
    # bp_f = chess.square_file(bp)

    # 1. Pawn Advancement Heuristic
    # White pawn must be somewhat advanced to be threatening.
    if wp_r < 2:
        return False
    
    # 2. King Proximity Heuristic (Tightened)
    # Kings must be relevant.
    d_wk_wp = chess.square_distance(wk, wp)
    d_wk_bp = chess.square_distance(wk, bp)
    d_bk_bp = chess.square_distance(bk, bp)
    d_bk_wp = chess.square_distance(bk, wp)

    # If Kings are too far, it's just a counting race.
    # We want interaction.
    if d_wk_wp > 3 and d_wk_bp > 3:
        return False
    if d_bk_bp > 3 and d_bk_wp > 3:
        return False

    return True


def _get_stable_random(board: chess.Board) -> float:
    """Returns a stable float in [0, 1) based on the position."""
    h = hashlib.md5(board.fen().encode()).hexdigest()
    return int(h[:8], 16) / 0xffffffff


def filter_tb_kp_vs_kp(board: chess.Board, tb: Mapping[str, Any]) -> bool:
    """
    KP vs KP TB-specific filter.
    High Difficulty Mode.
    
    Target Distribution: ~50% Win, ~30% Draw, ~20% Loss.
    """
    wdl = tb["wdl"]
    dtm = tb["dtm"]

    # 1. Basic Sanity & Difficulty Bounds
    
    # Skip extremely short games (Mate in 5 or less).
    # Hard puzzles usually require calculating deeper than 5 plies.
    if dtm is not None and abs(dtm) <= 10:
        return False
    
    # Skip extremely long shuffling.
    if dtm is not None and abs(dtm) > 60:
        return False

    legal_moves = list(board.legal_moves)
    if len(legal_moves) < 2:
        return False

    # 2. Analyze Moves
    
    best_move = None
    best_wdl = -2
    best_dtm_val = -99999
    
    # Bucket moves by WDL
    moves_win = []
    moves_draw = []
    moves_loss = []
    
    for move in legal_moves:
        res = tb["probe_move"](move)
        m_wdl = res["wdl"]
        m_dtm = res["dtm"] if res["dtm"] is not None else 0
        
        if m_wdl == 1:
            moves_win.append((move, m_wdl, m_dtm))
        elif m_wdl == 0:
            moves_draw.append((move, m_wdl, m_dtm))
        else:
            moves_loss.append((move, m_wdl, m_dtm))

    # 3. Identify Candidate "Best" and Check Uniqueness/Difficulty
    
    is_hard_win = False
    is_hard_draw = False
    is_hard_loss = False

    if wdl == 1:
        # WHITE WINS
        # Difficulty Criteria:
        # - Only 1 winning move.
        # - The winning move is NOT a capture (capturing the pawn is usually too obvious/trivial).
        
        if len(moves_win) != 1:
            return False
        
        best_move, _, _ = moves_win[0]
        
        # Difficulty: Reject if best move is a capture.
        if board.is_capture(best_move):
            return False
            
        is_hard_win = True

    elif wdl == 0:
        # DRAW
        # Difficulty Criteria:
        # - Only 1 drawing move. All others lose.
        # - The drawing move is NOT a capture (unless it's a specific stalemate trick, but no-capture is a good general hard filter).
        
        if len(moves_draw) != 1:
            return False
        
        # Ensure no winning moves exist (obviously, since wdl=0)
        
        best_move, _, _ = moves_draw[0]
        
        # Difficulty: Reject if best move is a capture.
        if board.is_capture(best_move):
            return False
            
        is_hard_draw = True

    elif wdl == -1:
        # LOSS
        # Difficulty Criteria:
        # - Only 1 "best" loss (maximizes DTM).
        # - Significant DTM difference (>= 4 plies) between best and 2nd best.
        # - Best move is NOT a capture (fighting spirit, not just trading into a lost K vs K).
        
        # Sort by DTM descending (best resistance first). 
        # Remember DTM for loss is negative (e.g. -20 is better than -5).
        # Gaviota returns signed DTM relative to side to move? 
        # The 'tb' dict passed here has 'dtm' normalized to White POV? 
        #   filters.py: "dtm": Optional[int] from White's perspective.
        #   So for a loss, DTM is negative. Maximize it (closer to 0 is bad? No, DTM -50 (mate in 50) is better than -10 (mate in 5)).
        #   Wait, DTM usually means "Distance to Mate". 
        #   -10 means "White gets mated in 10".
        #   -50 means "White gets mated in 50".
        #   We want to delay mate, so we want the smallest absolute value? No, we want to play for longer.
        #   We want -50 (more moves). 
        #   So we want to MINIMIZE the signed value (if -50 < -10)?
        #   Wait, let's look at `dtm_stm_to_white`.
        #   If White is to move and loses:
        #     STM (White) DTM is negative (e.g. -10).
        #     White POV DTM is -10.
        #   We want to drag it out. So we want DTM to be -50. 
        #   -50 < -10. So we want to MINIMIZE the signed integer.
        
        moves_loss.sort(key=lambda x: x[2]) # Ascending sort. -50 comes before -10.
        
        best_move, _, best_val = moves_loss[0] # Best resistance (most negative)
        
        if len(moves_loss) < 2:
            # If only 1 legal move, it's forced, not a puzzle.
            return False
            
        second_best_val = moves_loss[1][2]
        
        # We want Best (e.g. -50) to be significantly better than Second (-30).
        # -50 < -30. Difference is 20.
        # Condition: second_best - best >= 4.
        if (second_best_val - best_val) < 4:
            return False
        
        # Uniqueness check: ensure no other move has the same best_val
        if len([m for m in moves_loss if m[2] == best_val]) > 1:
            return False

        # Difficulty: Reject if best move is a capture.
        if board.is_capture(best_move):
            return False
            
        is_hard_loss = True

    # 4. Distribution Balancing
    # Goal: Win 50%, Draw 30%, Loss 20%
    # We use random sampling to adjust the natural yield of "Hard" positions.
    # Assumptions based on strict filters:
    # - Hard Wins will be fewer (due to No-Capture). Keep them all?
    # - Hard Draws might be rare. Keep them all?
    # - Hard Losses might be rare. Keep them all?
    # Let's start by keeping 100% of the "Hard" ones and check the output. 
    # But user insists on "Max 20% loss".
    # To be safe, we will aggressively cap Losses.
    
    rnd = _get_stable_random(board)
    
    if is_hard_win:
        # Keep 100% of hard wins.
        return True
    
    if is_hard_draw:
        # Keep 70% of hard draws (to bias towards Wins).
        return rnd < 0.70
        
    if is_hard_loss:
        # Keep only 30% of hard losses (to ensure they don't dominate).
        return rnd < 0.30

    return False
