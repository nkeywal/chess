# endgame_stats.py
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, TextIO, Tuple

import chess
import chess.pgn

PIECE_ORDER = "KQRBNP"
PIECE_SYMBOL_TO_LETTER = {
    "k": "K",
    "q": "Q",
    "r": "R",
    "b": "B",
    "n": "N",
    "p": "P",
}


@dataclass(frozen=True)
class Target:
    key: str
    interest: str


def parse_elo(tag_value: Optional[str]) -> Optional[int]:
    if not tag_value:
        return None
    try:
        return int(tag_value)
    except ValueError:
        return None


def canonicalize_side(s: str) -> str:
    s = (s or "").strip().upper()
    counts = {p: 0 for p in PIECE_ORDER}
    for ch in s:
        if ch in counts:
            counts[ch] += 1
    return "".join(p * counts[p] for p in PIECE_ORDER)


def canonicalize_key(key: str) -> Optional[str]:
    key = (key or "").strip()
    if not key or "_" not in key:
        return None
    w, b = key.split("_", 1)
    w2 = canonicalize_side(w)
    b2 = canonicalize_side(b)
    if not w2 or not b2:
        return None
    return f"{w2}_{b2}"


def load_targets(path: str) -> Dict[str, Target]:
    targets: Dict[str, Target] = {}
    with open(path, "r", encoding="utf-8") as f:
        _header = f.readline()
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            raw_key = parts[0].strip()
            if not raw_key:
                continue
            key = canonicalize_key(raw_key)
            if key is None:
                continue
            interest = parts[1].strip() if len(parts) > 1 else ""
            targets[key] = Target(key=key, interest=interest)
    return targets


def iter_games(pgn_stream: TextIO) -> Iterable[chess.pgn.Game]:
    while True:
        game = chess.pgn.read_game(pgn_stream)
        if game is None:
            return
        yield game


def total_pieces(board: chess.Board) -> int:
    return board.occupied.bit_count()


def material_key_white_first(board: chess.Board) -> str:
    w_counts = {p: 0 for p in PIECE_ORDER}
    b_counts = {p: 0 for p in PIECE_ORDER}

    for piece in board.piece_map().values():
        sym = chess.piece_symbol(piece.piece_type)
        letter = PIECE_SYMBOL_TO_LETTER[sym]
        if piece.color == chess.WHITE:
            w_counts[letter] += 1
        else:
            b_counts[letter] += 1

    w = "".join(p * w_counts[p] for p in PIECE_ORDER)
    b = "".join(p * b_counts[p] for p in PIECE_ORDER)
    return f"{w}_{b}"


def map_to_target_key(wb_key: str, targets: Dict[str, Target]) -> Optional[str]:
    wb_key = canonicalize_key(wb_key) or wb_key
    if wb_key in targets:
        return wb_key
    if "_" not in wb_key:
        return None
    w, b = wb_key.split("_", 1)
    rev = f"{b}_{w}"
    if rev in targets:
        return rev
    return None


def is_rated_game(headers: chess.pgn.Headers) -> bool:
    event = (headers.get("Event") or "").lower()
    return "rated" in event


def is_standard_variant(headers: chess.pgn.Headers) -> bool:
    variant = (headers.get("Variant") or "").strip().lower()
    if not variant:
        return True
    return variant == "standard"


def is_bullet(headers: chess.pgn.Headers) -> bool:
    speed = (headers.get("Speed") or "").strip().lower()
    if speed:
        return speed in {"bullet", "ultrabullet", "hyperbullet"}
    event = (headers.get("Event") or "").lower()
    return "bullet" in event


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def emit_stats(
    targets: Dict[str, Target],
    counts: Dict[str, int],
    games_seen: int,
    games_used: int,
    endgame_positions: int,
    matched_positions: int,
    elapsed_s: float,
    first_endgame_key: Optional[str],
) -> None:
    denom = matched_positions if matched_positions > 0 else 1

    items = [(k, v) for k, v in counts.items() if v > 0]
    items.sort(key=lambda kv: kv[1], reverse=True)

    print(
        "stats:\n"
        f"  elapsed={elapsed_s/60:.1f}m games_seen={fmt_int(games_seen)} games_used={fmt_int(games_used)}\n"
        f"  endgame_positions(<=thr)={fmt_int(endgame_positions)} matched_positions={fmt_int(matched_positions)}\n",
        file=sys.stderr,
        flush=True,
    )

    if first_endgame_key is not None and matched_positions == 0:
        print(f"  first_endgame_seen={first_endgame_key}\n", file=sys.stderr, flush=True)

    if not items:
        print("  (no matches yet)\n", file=sys.stderr, flush=True)
        return

    for k, v in items:
        pct = (v / denom) * 100.0
        print(f"  {k}={fmt_int(v)} ({pct:.2f}%)", file=sys.stderr, flush=True)
    print("", file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Endgame material stats from Lichess PGN dumps (exact stats logged periodically).")
    ap.add_argument("--targets", required=True, help="TSV file listing material keys from your table")
    ap.add_argument("--piece-threshold", type=int, default=5, help="Count positions where total pieces <= this")
    ap.add_argument("--elo-min", type=int, default=1500, help="Keep only games where both players Elo >= this value")
    ap.add_argument("--rated-only", action="store_true", help="Keep only rated games")
    ap.add_argument("--exclude-bullet", action="store_true", help="Exclude bullet games")
    ap.add_argument("--exclude-chess960", action="store_true", help="Exclude chess960 / non-standard variants")
    ap.add_argument("--max-games", type=int, default=0, help="Stop after this many games (0 means no limit)")
    ap.add_argument("--sample-every", type=int, default=1, help="Keep 1 game out of N (default 1 keeps all)")
    ap.add_argument("--ignore-interest-zero", action="store_true", help="Ignore targets whose interest == '0'")
    ap.add_argument("--log-every", type=int, default=60, help="Log exact stats every N seconds (default 60)")
    ap.add_argument("--pgn", default="-", help="PGN file path, or '-' for stdin (default '-')")
    args = ap.parse_args()

    targets_all = load_targets(args.targets)
    if args.ignore_interest_zero:
        targets = {k: v for k, v in targets_all.items() if v.interest != "0"}
    else:
        targets = targets_all

    pos_counts: Dict[str, int] = {k: 0 for k in targets.keys()}
    matched_positions = 0
    endgame_positions = 0
    first_endgame_key: Optional[str] = None

    games_seen = 0
    games_used = 0
    plies_seen = 0

    if args.pgn == "-":
        pgn_stream = sys.stdin
    else:
        pgn_stream = open(args.pgn, "r", encoding="utf-8")

    t0 = time.time()
    last_emit = t0

    # Avoid calling time.time() too frequently: check every 8192 plies.
    TIME_CHECK_MASK = 8192 - 1

    try:
        for game in iter_games(pgn_stream):
            games_seen += 1
            if args.sample_every > 1 and (games_seen - 1) % args.sample_every != 0:
                continue

            headers = game.headers

            if args.rated_only and not is_rated_game(headers):
                continue
            if args.exclude_chess960 and not is_standard_variant(headers):
                continue
            if args.exclude_bullet and is_bullet(headers):
                continue

            we = parse_elo(headers.get("WhiteElo"))
            be = parse_elo(headers.get("BlackElo"))
            if we is None or be is None:
                continue
            if we < args.elo_min or be < args.elo_min:
                continue

            games_used += 1
            if args.max_games > 0 and games_used > args.max_games:
                break

            board = game.board()

            for move in game.mainline_moves():
                board.push(move)
                plies_seen += 1

                # Periodic emit even if matched_positions == 0.
                if args.log_every > 0 and (plies_seen & TIME_CHECK_MASK) == 0:
                    now = time.time()
                    if (now - last_emit) >= args.log_every:
                        emit_stats(
                            targets, pos_counts,
                            games_seen, games_used,
                            endgame_positions, matched_positions,
                            now - t0, first_endgame_key
                        )
                        last_emit = now

                if total_pieces(board) > args.piece_threshold:
                    continue

                endgame_positions += 1

                wb_key = material_key_white_first(board)
                if first_endgame_key is None:
                    first_endgame_key = wb_key

                k = map_to_target_key(wb_key, targets)
                if k is None:
                    continue

                pos_counts[k] += 1
                matched_positions += 1

        emit_stats(
            targets, pos_counts,
            games_seen, games_used,
            endgame_positions, matched_positions,
            time.time() - t0, first_endgame_key
        )

    finally:
        if args.pgn != "-":
            pgn_stream.close()

    # Final TSV on stdout
    denom = matched_positions if matched_positions > 0 else 1
    print("material\tinterest\tpositions\tpos_pct")
    for key in sorted(targets.keys()):
        c = pos_counts.get(key, 0)
        if c == 0:
            continue
        pct = (c / denom) * 100.0
        print(f"{key}\t{targets[key].interest}\t{c}\t{pct:.6f}")


if __name__ == "__main__":
    main()
