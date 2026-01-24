# endgame_stats.py
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, TextIO, Set, List

import chess
import chess.pgn
import chess.gaviota

PIECE_ORDER = "KQRBNP"
PIECE_THRESHOLD = 5
MIN_PLYCOUNT = 35  # exclude games shorter than this many plies

# Gaviota location: a folder containing *.gtb.cp4 under subdirs.
# Example: ./gaviota/3, ./gaviota/4, ./gaviota/5, etc.
GAVIOTA_ROOT = Path("./gaviota")

PIECE_SYMBOL_TO_LETTER = {
    "k": "K",
    "q": "Q",
    "r": "R",
    "b": "B",
    "n": "N",
    "p": "P",
}

# Keys tracked and always written (including zeros).
TARGET_KEYS = [
    "KBP_KB",
    "KBP_KR",
    "K_KP",
    "KNP_KQ",
    "KNP_KR",
    "KP_K",
    "KP_KB",
    "KP_KBP",
    "KP_KN",
    "KP_KNP",
    "KP_KP",
    "KP_KPP",
    "KP_KQ",
    "KP_KQP",
    "KP_KR",
    "KP_KRP",
    "KPP_KB",
    "KPP_KN",
    "KPP_KQ",
    "KPP_KR",
    "KQ_KQ",
    "KQ_KQP",
    "KRP_KR",
    "KR_KP",
    "KR_KQ",
    "KR_KR",
    "KR_KRP",
]
TARGET_SET = set(TARGET_KEYS)


# ----------------------------
# PGN / header helpers
# ----------------------------

def parse_elo(tag_value: Optional[str]) -> Optional[int]:
    if not tag_value:
        return None
    try:
        return int(tag_value)
    except ValueError:
        return None


def parse_int(tag_value: Optional[str]) -> Optional[int]:
    if not tag_value:
        return None
    try:
        return int(tag_value)
    except ValueError:
        return None


def iter_games(pgn_stream: TextIO) -> Iterable[chess.pgn.Game]:
    while True:
        game = chess.pgn.read_game(pgn_stream)
        if game is None:
            return
        yield game


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


def result_to_white_outcome(result: str) -> Optional[int]:
    """
    Map PGN Result to White outcome: +1 win, 0 draw, -1 loss. None if unknown.
    """
    if result == "1-0":
        return 1
    if result == "0-1":
        return -1
    if result == "1/2-1/2":
        return 0
    return None


# ----------------------------
# Board / key helpers
# ----------------------------

def total_pieces(board: chess.Board) -> int:
    return board.occupied.bit_count()


def material_key_white_first(board: chess.Board) -> str:
    """
    Material key oriented White first: e.g. 'KP_KN'.
    Only called when pieces <= threshold, so piece_map is small.
    """
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


def map_to_target_key(wb_key: str) -> Optional[str]:
    """
    Match oriented key if present; otherwise match reversed key if present.
    """
    if wb_key in TARGET_SET:
        return wb_key
    if "_" not in wb_key:
        return None
    w, b = wb_key.split("_", 1)
    rev = f"{b}_{w}"
    if rev in TARGET_SET:
        return rev
    return None


# ----------------------------
# Gaviota helpers
# ----------------------------

def find_gaviota_dirs(root: Path) -> List[Path]:
    """
    Find all directories under `root` that contain Gaviota table files (*.gtb.cp4).
    Return them sorted from smallest-piece folder to largest if possible, otherwise by path.
    Fail-fast if none found.
    """
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Gaviota root directory not found: {root}")

    dirs = set()
    for p in root.rglob("*.gtb.cp4"):
        dirs.add(p.parent)

    if not dirs:
        raise FileNotFoundError(f"No *.gtb.cp4 files found under: {root}")

    def sort_key(d: Path):
        try:
            n = int(d.name)
        except ValueError:
            n = 999
        return (n, str(d))

    return sorted(dirs, key=sort_key)


def open_tablebase_native_fixed(dirs: List[Path]) -> Any:
    """
    Open NativeTablebase but fix the path list passed to libgtb:
    - ensure NULL-terminated char** (argv-style), which some libgtb builds expect.
    - define argtypes to avoid any ABI ambiguity.
    """
    import ctypes
    import ctypes.util

    libname = ctypes.util.find_library("gtb") or "libgtb.so.1"
    lib = ctypes.cdll.LoadLibrary(libname)

    tb = chess.gaviota.NativeTablebase(lib)

    tb.libgtb.tb_restart.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
    tb.libgtb.tb_restart.restype = ctypes.c_char_p

    def _tb_restart_null_terminated() -> None:
        n = len(tb.paths)
        c_paths = (ctypes.c_char_p * (n + 1))()
        c_paths[:n] = [p.encode("utf-8") for p in tb.paths]
        c_paths[n] = None

        verbosity = ctypes.c_int(1)
        compression_scheme = ctypes.c_int(4)

        ret = tb.libgtb.tb_restart(verbosity, compression_scheme, c_paths)
        if ret:
            pass
        tb.c_paths = c_paths

    tb._tb_restart = _tb_restart_null_terminated  # type: ignore[attr-defined]

    tb.add_directory(str(dirs[0]))
    for d in dirs[1:]:
        tb.add_directory(str(d))

    return tb


def wdl_white_from_dtm(dtm_stm: int, stm_is_white: bool) -> int:
    """
    dtm_stm is signed for side to move: >0 win for STM, <0 loss for STM, 0 draw.
    Convert to White POV WDL in {-1,0,+1}.
    """
    if dtm_stm == 0:
        return 0
    wdl_stm = 1 if dtm_stm > 0 else -1
    return wdl_stm if stm_is_white else -wdl_stm


def probe_wdl_white(tb: Any, board: chess.Board) -> int:
    """
    Probe WDL using only probe_dtm(), return White POV in {-1,0,+1}.
    """
    if board.is_checkmate():
        # Side to move is mated -> loss for STM.
        return -1 if board.turn == chess.WHITE else 1
    if board.is_stalemate():
        return 0
    dtm_stm = tb.probe_dtm(board)
    return wdl_white_from_dtm(dtm_stm, board.turn == chess.WHITE)


def outcome_for_player(wdl_white: int, player_is_white: bool) -> int:
    """
    Convert White POV wdl to the player's POV (same scale: -1/0/+1).
    """
    return wdl_white if player_is_white else -wdl_white


# ----------------------------
# Output writer
# ----------------------------

def write_month_tsv(
    out_path: Path,
    month: str,
    per_key_games: Dict[str, int],
    per_key_errors: Dict[str, int],
    games_seen: int,
    games_used: int,
    games_with_any_target: int,
    games_skipped_short: int,
    games_ended_in_5men: int,
    games_ended_in_any_target: int,
    games_with_tracking: int,
    games_with_errors: int,
    errors_move_total: int,
    errors_result_total: int,
    tb_probe_failures: int,
) -> None:
    """
    Write a full TSV (all target keys, including zeros), atomically.
    games_pct_over_hit removed as requested.
    """
    denom_used = games_used if games_used > 0 else 1

    lines: List[str] = []
    lines.append(f"# month={month}")
    lines.append(f"# games_seen={games_seen}")
    lines.append(f"# games_used={games_used}")
    lines.append(f"# games_skipped_short_plycount<35={games_skipped_short}")
    lines.append(f"# games_with_any_target={games_with_any_target}")
    lines.append(f"# pct_hit_over_games_used={games_with_any_target/denom_used*100.0:.6f}")
    lines.append(f"# games_ended_in_5men={games_ended_in_5men}")
    lines.append(f"# games_ended_in_any_target={games_ended_in_any_target}")
    lines.append(f"# games_with_tracking={games_with_tracking}")
    lines.append(f"# games_with_errors={games_with_errors}")
    lines.append(f"# errors_move_total={errors_move_total}")
    lines.append(f"# errors_result_total={errors_result_total}")
    lines.append(f"# errors_total={errors_move_total + errors_result_total}")
    lines.append(f"# tb_probe_failures={tb_probe_failures}")
    lines.append("material\tgames\tgames_pct_over_used\terrors\terrors_per_game")

    for k in sorted(TARGET_KEYS):
        g = per_key_games.get(k, 0)
        e = per_key_errors.get(k, 0)
        pct_used = (g / denom_used) * 100.0
        e_per_game = (e / g) if g > 0 else 0.0
        lines.append(f"{k}\t{g}\t{pct_used:.6f}\t{e}\t{e_per_game:.6f}")

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(out_path)


# ----------------------------
# Main
# ----------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Per-month endgame stats (once per game per type), with TB-based error counting, write TSV continuously."
    )
    ap.add_argument("--month", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--elo-min", type=int, default=1500)
    ap.add_argument("--exclude-bullet", action="store_true")
    ap.add_argument("--sample-every", type=int, default=1)
    ap.add_argument("--log-every", type=int, default=60)
    ap.add_argument("--pgn", default="-")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    # Open TB up-front (fail fast).
    gaviota_dirs = find_gaviota_dirs(GAVIOTA_ROOT)
    tb = open_tablebase_native_fixed(gaviota_dirs)

    per_key_games: Dict[str, int] = {k: 0 for k in TARGET_KEYS}
    per_key_errors: Dict[str, int] = {k: 0 for k in TARGET_KEYS}

    games_seen = 0
    games_used = 0
    games_with_any_target = 0
    games_skipped_short = 0

    games_ended_in_5men = 0
    games_ended_in_any_target = 0

    games_with_tracking = 0
    games_with_errors = 0
    errors_move_total = 0
    errors_result_total = 0
    tb_probe_failures = 0

    if args.pgn == "-":
        pgn_stream = sys.stdin
    else:
        pgn_stream = open(args.pgn, "r", encoding="utf-8")

    t0 = time.time()
    last_log = t0

    try:
        for game in iter_games(pgn_stream):
            games_seen += 1
            if args.sample_every > 1 and (games_seen - 1) % args.sample_every != 0:
                continue

            headers = game.headers

            # Fixed policy: rated only, standard only.
            if not is_rated_game(headers):
                continue
            if not is_standard_variant(headers):
                continue
            if args.exclude_bullet and is_bullet(headers):
                continue

            # Length filter: header PlyCount if present, otherwise compute from parsed game.
            ply_count = parse_int(headers.get("PlyCount"))
            if ply_count is None:
                ply_count = game.end().ply()
            if ply_count < MIN_PLYCOUNT:
                games_skipped_short += 1
                continue

            we = parse_elo(headers.get("WhiteElo"))
            be = parse_elo(headers.get("BlackElo"))
            if we is None or be is None:
                continue
            if we < args.elo_min or be < args.elo_min:
                continue

            games_used += 1

            actual_white = result_to_white_outcome(headers.get("Result", ""))

            board = game.board()
            seen_keys_in_game: Set[str] = set()

            tracking_active = False
            tracking_seen = False
            game_errors = 0

            for move in game.mainline_moves():
                # Record current position key (before move) if in 5-men.
                k_before: Optional[str] = None
                if total_pieces(board) <= PIECE_THRESHOLD:
                    k_before = map_to_target_key(material_key_white_first(board))
                    if k_before is not None:
                        seen_keys_in_game.add(k_before)

                # If tracking is active and we are in 5-men, evaluate mover outcome before move.
                before_mover_outcome: Optional[int] = None
                before_mover_is_white: Optional[bool] = None
                before_key_for_error: Optional[str] = None

                if tracking_active and total_pieces(board) <= PIECE_THRESHOLD:
                    try:
                        wdl_white_before = probe_wdl_white(tb, board)
                        before_mover_is_white = (board.turn == chess.WHITE)
                        before_mover_outcome = outcome_for_player(wdl_white_before, before_mover_is_white)
                        before_key_for_error = k_before
                    except Exception:
                        tb_probe_failures += 1
                        before_mover_outcome = None

                # Push move.
                board.push(move)

                # Record position key after move if in 5-men (this fixes missing "final move reaches 5-men").
                k_after: Optional[str] = None
                if total_pieces(board) <= PIECE_THRESHOLD:
                    k_after = map_to_target_key(material_key_white_first(board))
                    if k_after is not None:
                        seen_keys_in_game.add(k_after)

                # Activate tracking on first time we reach a tracked 5-men position.
                if not tracking_active and k_after is not None:
                    tracking_active = True
                    tracking_seen = True

                # If we evaluated before, evaluate after and compare (mover POV).
                if before_mover_outcome is not None and before_mover_is_white is not None:
                    if total_pieces(board) <= PIECE_THRESHOLD:
                        try:
                            wdl_white_after = probe_wdl_white(tb, board)
                            after_mover_outcome = outcome_for_player(wdl_white_after, before_mover_is_white)
                            if after_mover_outcome < before_mover_outcome:
                                errors_move_total += 1
                                game_errors += 1
                                if before_key_for_error is not None:
                                    per_key_errors[before_key_for_error] += 1
                        except Exception:
                            tb_probe_failures += 1

            # End-of-game "ended in endgame" metrics.
            if total_pieces(board) <= PIECE_THRESHOLD:
                games_ended_in_5men += 1
                k_end = map_to_target_key(material_key_white_first(board))
                if k_end is not None:
                    games_ended_in_any_target += 1

            # Per-game presence counters.
            if seen_keys_in_game:
                games_with_any_target += 1
                for k in seen_keys_in_game:
                    per_key_games[k] += 1

            if tracking_seen:
                games_with_tracking += 1

            # Result error: only if tracking was seen and final position is 5-men and result known.
            if tracking_seen and actual_white is not None and total_pieces(board) <= PIECE_THRESHOLD:
                try:
                    wdl_white_end = probe_wdl_white(tb, board)
                    if actual_white != wdl_white_end:
                        # Exactly one side underperformed.
                        errors_result_total += 1
                        game_errors += 1
                        k_end = map_to_target_key(material_key_white_first(board))
                        if k_end is not None:
                            per_key_errors[k_end] += 1
                except Exception:
                    tb_probe_failures += 1

            if game_errors > 0:
                games_with_errors += 1

            # Periodic log + full TSV rewrite.
            now = time.time()
            if args.log_every > 0 and (now - last_log) >= args.log_every:
                denom = games_used if games_used > 0 else 1
                pct_hit = games_with_any_target / denom * 100.0
                print(
                    "progress:\n"
                    f"  month={args.month} elapsed={(now - t0)/60:.1f}m games_seen={fmt_int(games_seen)} "
                    f"games_used={fmt_int(games_used)} skipped_short={fmt_int(games_skipped_short)} "
                    f"games_with_any_target={fmt_int(games_with_any_target)} pct_hit={pct_hit:.3f}% "
                    f"errors_total={fmt_int(errors_move_total + errors_result_total)} tb_fail={fmt_int(tb_probe_failures)}\n",
                    file=sys.stderr,
                    flush=True,
                )

                write_month_tsv(
                    out_path=args.out,
                    month=args.month,
                    per_key_games=per_key_games,
                    per_key_errors=per_key_errors,
                    games_seen=games_seen,
                    games_used=games_used,
                    games_with_any_target=games_with_any_target,
                    games_skipped_short=games_skipped_short,
                    games_ended_in_5men=games_ended_in_5men,
                    games_ended_in_any_target=games_ended_in_any_target,
                    games_with_tracking=games_with_tracking,
                    games_with_errors=games_with_errors,
                    errors_move_total=errors_move_total,
                    errors_result_total=errors_result_total,
                    tb_probe_failures=tb_probe_failures,
                )

                last_log = now

    finally:
        if args.pgn != "-":
            pgn_stream.close()
        try:
            tb.close()
        except Exception:
            pass

    # Final write
    write_month_tsv(
        out_path=args.out,
        month=args.month,
        per_key_games=per_key_games,
        per_key_errors=per_key_errors,
        games_seen=games_seen,
        games_used=games_used,
        games_with_any_target=games_with_any_target,
        games_skipped_short=games_skipped_short,
        games_ended_in_5men=games_ended_in_5men,
        games_ended_in_any_target=games_ended_in_any_target,
        games_with_tracking=games_with_tracking,
        games_with_errors=games_with_errors,
        errors_move_total=errors_move_total,
        errors_result_total=errors_result_total,
        tb_probe_failures=tb_probe_failures,
    )

    denom = games_used if games_used > 0 else 1
    pct_hit = games_with_any_target / denom * 100.0
    print(
        "done:\n"
        f"  month={args.month}\n"
        f"  games_seen={fmt_int(games_seen)}\n"
        f"  games_used={fmt_int(games_used)}\n"
        f"  skipped_short={fmt_int(games_skipped_short)}\n"
        f"  games_with_any_target={fmt_int(games_with_any_target)}\n"
        f"  pct_hit={pct_hit:.6f}%\n"
        f"  games_ended_in_5men={fmt_int(games_ended_in_5men)}\n"
        f"  games_ended_in_any_target={fmt_int(games_ended_in_any_target)}\n"
        f"  games_with_tracking={fmt_int(games_with_tracking)}\n"
        f"  games_with_errors={fmt_int(games_with_errors)}\n"
        f"  errors_total={fmt_int(errors_move_total + errors_result_total)}\n"
        f"  tb_probe_failures={fmt_int(tb_probe_failures)}\n"
        f"  out={args.out}\n",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
