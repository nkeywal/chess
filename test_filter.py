
import chess
from kp_vs_k import filter_notb_kp_vs_k

def test():
    # P=e4, BK=d4, WK=b4. White to move.
    # Black attacks e4. White cannot move to c3, c4, c5 because they are attacked by BK.
    # White cannot move pawn to e5 because it's attacked by BK.
    # This should be FALSE.
    board = chess.Board("8/8/8/8/1K1kP3/8/8/8 w - - 0 1")
    print(f"Position 1 (BK=d4, WK=b4, P=e4) - Expected False: {filter_notb_kp_vs_k(board)}")

    # P=e4, BK=d4, WK=b2. White to move.
    # WK is too far (dist 3).
    # This should be FALSE (already by previous distance check).
    board = chess.Board("8/8/8/8/3kP3/8/1K6/8 w - - 0 1")
    print(f"Position 2 (BK=d4, WK=b2, P=e4) - Expected False: {filter_notb_kp_vs_k(board)}")

    # P=e4, BK=d4, WK=e2. White to move.
    # WK at e2 can move to f3 to protect e4.
    # d4 attacks: c3, d3, e3, c4, e4, c5, d5, e5.
    # f3 is NOT attacked by d4.
    # So this should be TRUE.
    board = chess.Board("8/8/8/8/3kP3/8/4K3/8 w - - 0 1")
    print(f"Position 4 (BK=d4, WK=e2, P=e4) - Expected True: {filter_notb_kp_vs_k(board)}")

def filter_notb_k_p_vs_k_check(board):
    # We need to simulate the filter logic or just call it.
    # Note: filter_notb_kp_vs_k has some other checks (rank, etc.)
    # Let's see if our test positions pass those too.
    # Rank 4 (pr=3) is allowed.
    return filter_notb_kp_vs_k(board)

if __name__ == "__main__":
    test()
