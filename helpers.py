# All code/comments in English as requested.

from __future__ import annotations


def mask_files(f_min: int, f_max: int) -> int:
    """
    Bitmask of squares with file in [f_min, f_max], where file a=0..h=7.
    """
    m = 0
    for sq in range(64):
        f = sq & 7
        if f_min <= f <= f_max:
            m |= (1 << sq)
    return m


def mask_ranks(ranks: list[int]) -> int:
    """
    Bitmask of squares with rank in ranks, where rank 0..7 (0 is 1st rank).
    """
    rs = set(ranks)
    m = 0
    for sq in range(64):
        r = sq >> 3
        if r in rs:
            m |= (1 << sq)
    return m
