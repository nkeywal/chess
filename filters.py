# filters.py
# All code/comments in English as requested.

from __future__ import annotations

from typing import Any, Mapping

import chess

from helpers import mask_files, mask_ranks
from k_vs_kp import filter_notb_k_vs_kp, filter_tb_k_vs_kp, gen_hints_k_vs_kp
from kbp_vs_kb import (
    filter_notb_kbp_vs_kb,
    filter_tb_kbp_vs_kb,
    gen_hints_kbp_vs_kb,
)
from kp_vs_k import filter_notb_kp_vs_k, filter_tb_kp_vs_k, gen_hints_kp_vs_k
from kp_vs_kp import (
    filter_notb_kp_vs_kp,
    filter_tb_kp_vs_kp,
    gen_hints_kp_vs_kp,
)
from kp_vs_kr import filter_notb_kp_vs_kr, filter_tb_kp_vs_kr
from kr_vs_kp import filter_notb_kr_vs_kp, filter_tb_kr_vs_kp
from kr_vs_krp import (
    filter_notb_kr_vs_krp,
    filter_tb_kr_vs_krp,
    gen_hints_kr_vs_krp,
)
from krp_vs_kr import (
    filter_notb_krp_vs_kr,
    filter_tb_krp_vs_kr,
    gen_hints_krp_vs_kr,
)


# =============================================================================
# Generic filters
# =============================================================================

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


# =============================================================================
# Anomalies detected (not fixed here unless obvious bug/cosmetic)
# =============================================================================
#
# - filter_tb_kr_vs_krp(): the draw branch computes counters (drawing_rook_moves, etc.) and then returns False
#   unconditionally. This makes the whole draw branch reject everything (likely unintended).
#
# - filter_notb_kr_vs_kp(): historically, comments/docstrings often referenced different pawn ranks
#   (e.g., "human rank 5/6/7"), while the effective code constrains pr <= 3. This file aligns the docstring
#   to the code, but the underlying intent may differ from earlier commentary.
#
# - Several TB filters iterate over all legal moves and call probe_move() many times; for some materials this can
#   dominate runtime and might be a performance hotspot (not a correctness issue).
#
# - Some filters are highly heuristic and may bias the sampled positions distribution in ways that are not obvious
#   from the docstring alone (e.g., multiple simultaneous distance constraints).
