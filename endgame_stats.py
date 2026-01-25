# endgame_stats.py
# All code/comments in English as requested.

from __future__ import annotations

import argparse
import io
import sys
import time
from dataclasses import dataclass
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple, List, Set

import chess
import chess.pgn
import chess.gaviota


# ----------------------------
# User-tuned constants
# ----------------------------

TRACK_TOTAL_PIECES: Set[int] = {3, 4, 5}
EXCLUDE_TOTAL_PIECES: Set[int] = {2}

MIN_PLYCOUNT = 35  # estimated from headers or movetext
GAVIOTA_ROOT = Path("./gaviota")

NON_KING_PIECES = ["Q", "R", "B", "N", "P"]

PIECE_SYMBOL_TO_LETTER = {
    "k": "K",
    "q": "Q",
    "r": "R",
    "b": "B",
    "n": "N",
    "p": "P",
}


# ----------------------------
# Key encoding / universe
#
# Oriented key: LEFT = side-to-move material, RIGHT = opponent material.
#
# Bishop encoding:
# - Side has 1 bishop -> token "B" by default.
# - Side has 2 bishops -> token "BB" if bishops are on opposite-colored squares, else excluded.
# - Side has >=3 bishops -> excluded.
# - If both sides have exactly 1 bishop:
#     - same square-color -> keep "B"/"B"
#     - opposite square-color -> replace both with "D"/"D".
#
# We precompute a universe for stable output (including zeros).
# Exclusions (insufficient material / trivial finals) are applied at runtime.
# ----------------------------

def _material_string_from_counts(q: int, r: int, bishop_token: str, n: int, p: int) -> str:
    return "K" + ("Q" * q) + ("R" * r) + bishop_token + ("N" * n) + ("P" * p)


def build_key_universe() -> List[str]:
    keys = set()
    for total in sorted(TRACK_TOTAL_PIECES):
        extras = total - 2
        for left_extras in range(extras + 1):
            right_extras = extras - left_extras

            for left_combo in combinations_with_replacement(NON_KING_PIECES, left_extras):
                lc = {p: 0 for p in NON_KING_PIECES}
                for c in left_combo:
                    lc[c] += 1
                if lc["B"] > 2:
                    continue
                left_btok = "" if lc["B"] == 0 else ("B" if lc["B"] == 1 else "BB")
                left_s = _material_string_from_counts(lc["Q"], lc["R"], left_btok, lc["N"], lc["P"])

                for right_combo in combinations_with_replacement(NON_KING_PIECES, right_extras):
                    rc = {p: 0 for p in NON_KING_PIECES}
                    for c in right_combo:
                        rc[c] += 1
                    if rc["B"] > 2:
                        continue
                    right_btok = "" if rc["B"] == 0 else ("B" if rc["B"] == 1 else "BB")
                    right_s = _material_string_from_counts(rc["Q"], rc["R"], right_btok, rc["N"], rc["P"])

                    keys.add(f"{left_s}_{right_s}")

                    # Opposite-colored bishops variant only meaningful for 1v1 bishop.
                    if lc["B"] == 1 and rc["B"] == 1:
                        left_d = _material_string_from_counts(lc["Q"], lc["R"], "D", lc["N"], lc["P"])
                        right_d = _material_string_from_counts(rc["Q"], rc["R"], "D", rc["N"], rc["P"])
                        keys.add(f"{left_d}_{right_d}")

    return sorted(keys)


ALL_KEYS = build_key_universe()


# ----------------------------
# Raw PGN reader (headers-first)
# ----------------------------

def _parse_header_line(line: str) -> Optional[Tuple[str, str]]:
    line = line.strip()
    if not line.startswith("[") or not line.endswith("]"):
        return None
    inner = line[1:-1].strip()
    if " " not in inner:
        return None
    key, rest = inner.split(" ", 1)
    rest = rest.strip()
    if not (rest.startswith('"') and rest.endswith('"')):
        return None
    value = rest[1:-1]
    return key, value


def _fast_ply_count_from_movetext(movetext: str) -> int:
    # Very lightweight ply estimate: count SAN-like tokens that are not move numbers, results, comments, or NAGs.
    tokens = movetext.replace("\n", " ").split()
    ply = 0
    for t in tokens:
        if t.endswith(".") and t[:-1].isdigit():
            continue
        if t in ("1-0", "0-1", "1/2-1/2", "*"):
            continue
        if t.startswith("{") or t.endswith("}"):
            continue
        if t.startswith("$"):
            continue
        ply += 1
    return ply


def _int_or_none(tag_value: Optional[str]) -> Optional[int]:
    if not tag_value:
        return None
    try:
        return int(tag_value)
    except ValueError:
        return None


def read_games_raw(stream: io.TextIOBase) -> Iterable[Tuple[Dict[str, str], int, str]]:
    """Yield (headers, ply_est, raw_pgn) for each game, without SAN parsing.

    This is intentionally simple and fast; it assumes typical Lichess PGN formatting.
    """
    headers: Dict[str, str] = {}
    header_lines: List[str] = []
    movetext_lines: List[str] = []
    in_headers = False
    in_movetext = False

    for line in stream:
        if line.startswith("["):
            if not in_headers:
                # New game begins.
                headers = {}
                header_lines = []
                movetext_lines = []
                in_headers = True
                in_movetext = False
            header_lines.append(line.rstrip("\n"))
            parsed = _parse_header_line(line)
            if parsed:
                k, v = parsed
                headers[k] = v
            continue

        if in_headers and line.strip() == "":
            # End of headers.
            in_headers = False
            in_movetext = True
            movetext_lines.append("")  # preserve the blank line between headers and movetext
            continue

        if in_movetext:
            movetext_lines.append(line.rstrip("\n"))
            continue

    # EOF flush: only if we have a game.
    if header_lines:
        raw_pgn = "\n".join(header_lines) + "\n" + "\n".join(movetext_lines) + "\n"
        ply_est = _int_or_none(headers.get("PlyCount")) or _fast_ply_count_from_movetext("\n".join(movetext_lines))
        yield headers, ply_est, raw_pgn


def is_rated_event(event: str) -> bool:
    return "rated" in (event or "").lower()


def is_standard_variant_tag(variant: str) -> bool:
    v = (variant or "").strip().lower()
    return (not v) or (v == "standard")


def is_bullet(headers: Dict[str, str]) -> bool:
    # Lichess tags often include TimeControl like "60+0" etc.
    tc = (headers.get("TimeControl") or "").strip()
    if not tc or tc == "-":
        return False

    # Rough: base time < 180s => bullet.
    if "+" in tc:
        base = tc.split("+", 1)[0]
        try:
            return int(base) < 180
        except ValueError:
            return False

    try:
        return int(tc) < 180
    except ValueError:
        return False


def termination_is_time_forfeit(headers: Dict[str, str]) -> bool:
    return "time" in (headers.get("Termination") or "").lower()


def in_bucket_soft(we: int, be: int, elo_min: int, elo_max: int) -> bool:
    s = we + be
    if not (2 * elo_min <= s < 2 * elo_max):
        return False
    if not (elo_min - 100 <= we < elo_max + 100):
        return False
    if not (elo_min - 100 <= be < elo_max + 100):
        return False
    return True


def out_path_for(month: str, elo_min: int, elo_max: int, out_dir: Path) -> Path:
    return out_dir / f"endgame_stats_{month}_elo{elo_min}-{elo_max}.tsv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pgn", required=True, help="Input PGN file (or '-' for stdin).")
    p.add_argument("--month", required=True, help="Month tag (e.g., 2025-12).")
    p.add_argument("--elo-min", type=int, required=True)
    p.add_argument("--elo-max", type=int, required=True)
    p.add_argument("--out-dir", type=Path, default=Path("."))
    p.add_argument("--exclude-bullet", action="store_true", help="Exclude bullet games.")
    p.add_argument("--log-every", type=float, default=60.0, help="Seconds between progress logs; 0 disables.")
    return p.parse_args()


# ----------------------------
# Tablebase helpers
# ----------------------------

def find_gaviota_dirs(root: Path) -> List[Path]:
    """Return directories containing gaviota files (*.gtb.cp4)."""
    if not root.exists():
        raise FileNotFoundError(f"Gaviota root does not exist: {root}")
    dirs: List[Path] = []
    for p in root.rglob("*.gtb.cp4"):
        d = p.parent
        if d not in dirs:
            dirs.append(d)
    if not dirs:
        raise FileNotFoundError(f"No gaviota *.gtb.cp4 files found under: {root}")
    return sorted(dirs)


def open_tablebase_native_fixed(dirs: List[Path]) -> chess.gaviota.NativeTablebase:
    """Open Gaviota NativeTablebase with a null-terminated path list (ABI fix for some builds)."""
    import ctypes

    lib = None
    for candidate in chess.gaviota._gaviota_lib_candidates():  # type: ignore[attr-defined]
        try:
            lib = ctypes.cdll.LoadLibrary(candidate)
            break
        except OSError:
            continue
    if lib is None:
        raise RuntimeError("Could not load gaviota native library (libgtb).")

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

        _ = tb.libgtb.tb_restart(verbosity, compression_scheme, c_paths)
        tb.c_paths = c_paths

    tb._tb_restart = _tb_restart_null_terminated  # type: ignore[attr-defined]

    tb.add_directory(str(dirs[0]))
    for d in dirs[1:]:
        tb.add_directory(str(d))

    return tb


def wdl_white_from_dtm(dtm_stm: int, stm_is_white: bool) -> int:
    if dtm_stm == 0:
        return 0
    wdl_stm = 1 if dtm_stm > 0 else -1
    return wdl_stm if stm_is_white else -wdl_stm


def probe_wdl_white(tb: Any, board: chess.Board) -> int:
    if board.is_checkmate():
        return -1 if board.turn == chess.WHITE else 1
    if board.is_stalemate():
        return 0
    dtm_stm = tb.probe_dtm(board)
    return wdl_white_from_dtm(dtm_stm, board.turn == chess.WHITE)


def probe_wdl_white_tb(board: chess.Board, tb: Any) -> int:
    # Normalize FEN-relevant fields to avoid irrelevant probe variance.
    b = board.copy(stack=False)
    b.castling_rights = 0
    return probe_wdl_white(tb, b)


# ----------------------------
# Exclusions / triviality
# ----------------------------

def _count_side(board: chess.Board, color: bool) -> Dict[str, int]:
    counts = {p: 0 for p in "KQRBNP"}
    for piece in board.piece_map().values():
        if piece.color != color:
            continue
        sym = chess.piece_symbol(piece.piece_type)
        letter = PIECE_SYMBOL_TO_LETTER[sym]
        counts[letter] += 1
    return counts


def total_pieces(board: chess.Board) -> int:
    return len(board.piece_map())


def _sq_color_parity(sq: int) -> int:
    # 0 for dark, 1 for light (a1 is dark).
    file = chess.square_file(sq)
    rank = chess.square_rank(sq)
    return (file + rank) & 1


def is_trivial_win_against_bare_king(board: chess.Board) -> bool:
    wc = _count_side(board, chess.WHITE)
    bc = _count_side(board, chess.BLACK)

    def extras(c: Dict[str, int]) -> int:
        return c["Q"] + c["R"] + c["B"] + c["N"] + c["P"]

    def bare_king(c: Dict[str, int]) -> bool:
        return extras(c) == 0

    def is_kbn(c: Dict[str, int]) -> bool:
        return c["B"] == 1 and c["N"] == 1 and c["Q"] == 0 and c["R"] == 0 and c["P"] == 0

    def mate_easy_no_pawns(c: Dict[str, int]) -> bool:
        # Exclude KBN vs K: mate exists but is "special"; keep it.
        if is_kbn(c):
            return False
        if c["P"] != 0:
            return False
        if c["Q"] > 0 or c["R"] > 0:
            return True
        if c["B"] >= 2:
            return True
        return False

    if bare_king(wc) and mate_easy_no_pawns(bc):
        return True
    if bare_king(bc) and mate_easy_no_pawns(wc):
        return True
    return False


def is_trivial_final_for_ended_count(board: chess.Board) -> bool:
    """Exclude final positions from ended_in_345 per user rule:
    - bare king vs (queen or rook), OR
    - bare king vs >=3 non-king units (pieces or pawns).
    """
    if total_pieces(board) not in TRACK_TOTAL_PIECES:
        return False

    wc = _count_side(board, chess.WHITE)
    bc = _count_side(board, chess.BLACK)

    def extras(c: Dict[str, int]) -> int:
        return c["Q"] + c["R"] + c["B"] + c["N"] + c["P"]

    def bare_king(c: Dict[str, int]) -> bool:
        return extras(c) == 0

    if bare_king(wc):
        if bc["Q"] > 0 or bc["R"] > 0 or extras(bc) >= 3:
            return True
    if bare_king(bc):
        if wc["Q"] > 0 or wc["R"] > 0 or extras(wc) >= 3:
            return True

    return False


def _bishop_token_or_none(board: chess.Board, color: bool) -> Optional[str]:
    bishops = list(board.pieces(chess.BISHOP, color))
    n = len(bishops)
    if n == 0:
        return ""
    if n == 1:
        return "B"
    if n == 2:
        c0 = _sq_color_parity(bishops[0])
        c1 = _sq_color_parity(bishops[1])
        if c0 == c1:
            # Same-colored bishops (promotion oddity): exclude.
            return None
        return "BB"
    # 3+ bishops: exclude.
    return None


def _side_counts_no_bishops(board: chess.Board, color: bool) -> Tuple[int, int, int, int]:
    q = r = n = p = 0
    for _, piece in board.piece_map().items():
        if piece.color != color:
            continue
        pt = piece.piece_type
        if pt == chess.QUEEN:
            q += 1
        elif pt == chess.ROOK:
            r += 1
        elif pt == chess.KNIGHT:
            n += 1
        elif pt == chess.PAWN:
            p += 1
    return q, r, n, p


def build_key_for_side_to_move(board: chess.Board) -> Optional[str]:
    """Return oriented key LEFT=side-to-move material, RIGHT=opponent material, or None if excluded."""
    tot = total_pieces(board)
    if tot in EXCLUDE_TOTAL_PIECES:
        return None
    if tot not in TRACK_TOTAL_PIECES:
        return None

    if board.is_insufficient_material():
        return None

    if is_trivial_win_against_bare_king(board):
        return None

    left = board.turn
    right = not left

    left_tok = _bishop_token_or_none(board, left)
    if left_tok is None:
        return None
    right_tok = _bishop_token_or_none(board, right)
    if right_tok is None:
        return None

    # Opposite-colored bishops: only when exactly one bishop each.
    if left_tok == "B" and right_tok == "B":
        lb = next(iter(board.pieces(chess.BISHOP, left)))
        rb = next(iter(board.pieces(chess.BISHOP, right)))
        if _sq_color_parity(lb) != _sq_color_parity(rb):
            left_tok = "D"
            right_tok = "D"

    lq, lr, ln, lp = _side_counts_no_bishops(board, left)
    rq, rr, rn, rp = _side_counts_no_bishops(board, right)

    left_s = "K" + ("Q" * lq) + ("R" * lr) + left_tok + ("N" * ln) + ("P" * lp)
    right_s = "K" + ("Q" * rq) + ("R" * rr) + right_tok + ("N" * rn) + ("P" * rp)
    return f"{left_s}_{right_s}"


# ----------------------------
# Termination classification
# ----------------------------

def result_to_white_outcome(res: str) -> Optional[int]:
    r = (res or "").strip()
    if r == "1-0":
        return 1
    if r == "0-1":
        return -1
    if r == "1/2-1/2":
        return 0
    return None


def termination_is_draw_agreement(headers: Dict[str, str]) -> bool:
    term = (headers.get("Termination") or "").lower()
    # Lichess/PGN variants in the wild.
    return any(x in term for x in ["agreement", "agreed", "accord", "mutual"])


def is_draw_by_agreement(headers: Dict[str, str], board: chess.Board) -> bool:
    """Heuristic: identify 'draw by mutual agreement' to apply extra steps.

    Priority is explicit termination string. Otherwise, we try to avoid misclassifying
    stalemate/insufficient material/checkmate, and also avoid rule-claim draws (50-move / threefold)
    when they are claimable in the final position.
    """
    if termination_is_draw_agreement(headers):
        return True

    term = (headers.get("Termination") or "").lower()
    if term not in ("", "normal"):
        return False

    # If the board itself justifies the draw, do not treat as agreement.
    if board.is_stalemate() or board.is_insufficient_material() or board.is_checkmate():
        return False

    # If a claim is immediately available, treat as "rule draw", not agreement (conservative).
    # Note: this can miss some agreed draws in claimable positions, but matches the spec narrowly.
    try:
        if board.can_claim_fifty_moves() or board.can_claim_threefold_repetition():
            return False
    except Exception:
        # If these APIs fail for any reason, do not assume agreement.
        return False

    # Otherwise, likely a mutual agreement.
    return True


# ----------------------------
# Per-game analysis (core counting)
# ----------------------------

@dataclass
class GameDeltas:
    # Definitions (explicit and stable):
    # - games[type]  = number of games where this material type appears with side-to-move (LEFT).
    # - steps[type]  = number of "decision steps" for this type:
    #       - each tracked ply counts as 1 step
    #       - resignation adds +1 step for the resigning player (if final position is trackable)
    #       - mutual draw agreement adds +1 step for each player (if final position is trackable)
    # - extra_steps[type] = the portion of steps due to resignation/agreement (not real plies)
    # - plies[type]  = steps[type] - extra_steps[type] (published for reference)
    # - errors[type] = number of errors on those steps:
    #       - move error: WDL changes after the played move
    #       - resignation error: resigning while TB says draw/win for the resigning side
    #       - agreement error: agreeing a draw while TB says win for that side
    keys_seen: Set[str]
    keys_with_error: Set[str]
    per_key_steps: Dict[str, int]
    per_key_extra_steps: Dict[str, int]
    per_key_errors: Dict[str, int]
    time_loss_key: Optional[str]
    ended_in_345: bool


def analyze_game(game: chess.pgn.Game, headers: Dict[str, str], tb: Any) -> GameDeltas:
    board = game.board()
    keys_seen: Set[str] = set()
    keys_with_error: Set[str] = set()

    per_key_steps: Dict[str, int] = {}
    per_key_extra_steps: Dict[str, int] = {}
    per_key_errors: Dict[str, int] = {}

    actual_white = result_to_white_outcome(headers.get("Result", ""))
    time_forfeit = termination_is_time_forfeit(headers)

    # Performance: avoid double probing by reusing the previous ply's "after" WDL as the next ply's "before" WDL.
    cached_wdl_white: Optional[int] = None

    def bump(d: Dict[str, int], k: str, n: int = 1) -> None:
        d[k] = d.get(k, 0) + n

    def probe_or_die(ctx: str) -> int:
        try:
            return probe_wdl_white_tb(board, tb)
        except Exception as e:
            raise RuntimeError(f"Tablebase probe failed ({ctx}). FEN={board.fen()}") from e

    def add_terminal_step(color: bool) -> Optional[str]:
        b = board.copy(stack=False)
        b.turn = color
        k = build_key_for_side_to_move(b)
        if k is None:
            return None
        keys_seen.add(k)
        bump(per_key_steps, k, 1)
        bump(per_key_extra_steps, k, 1)
        return k

    def add_terminal_error(k: str) -> None:
        bump(per_key_errors, k, 1)
        keys_with_error.add(k)

    # Main move loop: each tracked ply is 1 step.
    for ply_idx, move in enumerate(game.mainline_moves(), start=1):
        key = build_key_for_side_to_move(board)
        if key is None:
            board.push(move)
            cached_wdl_white = None
            continue

        keys_seen.add(key)
        bump(per_key_steps, key, 1)

        mover_is_white = (board.turn == chess.WHITE)
        fen_before = board.fen()
        try:
            move_san = board.san(move)
        except Exception:
            move_san = "<san_error>"
        move_uci = move.uci()

        used_cache = (cached_wdl_white is not None)
        w_before = cached_wdl_white if cached_wdl_white is not None else probe_or_die(f"before ply={ply_idx}")

        board.push(move)
        fen_after = board.fen()

        w_after = probe_or_die(f"after ply={ply_idx}")
        cached_wdl_white = w_after

        before_mover = w_before if mover_is_white else -w_before
        after_mover = w_after if mover_is_white else -w_after

        # Sanity check: a played move cannot improve the mover WDL outcome
        # because the parent WDL is the best outcome available to the mover.
        if after_mover > before_mover:
            mover = "White" if mover_is_white else "Black"
            msg = (
                "Invariant violation: mover WDL improved after a move.\n"
                f"ply={ply_idx} mover={mover} key={key}\n"
                f"move_uci={move_uci} move_san={move_san}\n"
                f"Event={headers.get('Event', '')} Site={headers.get('Site', '')}\n"
                f"White={headers.get('White', '')} ({headers.get('WhiteElo', '')}) "
                f"Black={headers.get('Black', '')} ({headers.get('BlackElo', '')})\n"
                f"Result={headers.get('Result', '')} Termination={headers.get('Termination', '')}\n"
                f"fen_before={fen_before}\n"
                f"fen_after={fen_after}\n"
                f"used_cache_before={used_cache}\n"
                f"wdl_white_before={w_before} wdl_white_after={w_after}\n"
                f"wdl_mover_before={before_mover} wdl_mover_after={after_mover}\n"
            )
            print(msg, file=sys.stderr, flush=True)
            raise RuntimeError(msg)

        if after_mover != before_mover:
            bump(per_key_errors, key, 1)
            keys_with_error.add(key)

    # ended_in_345:
    ended_in_345 = False
    if total_pieces(board) in TRACK_TOTAL_PIECES:
        final_key = build_key_for_side_to_move(board)
        if final_key is not None and (not is_trivial_final_for_ended_count(board)):
            ended_in_345 = True

    # Time loss attribution (no TB involved): attribute to the side that lost on time.
    time_loss_key: Optional[str] = None
    if time_forfeit and actual_white is not None:
        loser_is_white = (actual_white == -1)
        b2 = board.copy(stack=False)
        b2.turn = chess.WHITE if loser_is_white else chess.BLACK
        k_loser = build_key_for_side_to_move(b2)
        if k_loser is not None:
            time_loss_key = k_loser

    # Terminal extra-steps and terminal errors.
    #
    # Note: we intentionally reuse cached_wdl_white (final position) when available,
    # and only probe once otherwise (the board has not changed between terminal steps).
    if (not time_forfeit) and actual_white is not None and total_pieces(board) in TRACK_TOTAL_PIECES:
        # Only apply terminal logic if the final position is trackable (under our exclusions).
        if build_key_for_side_to_move(board) is not None:
            w_end = cached_wdl_white if cached_wdl_white is not None else probe_or_die("final")

            if actual_white == 0:
                # Mutual draw agreement: +1 step for each player.
                if is_draw_by_agreement(headers, board):
                    k_w = add_terminal_step(chess.WHITE)
                    k_b = add_terminal_step(chess.BLACK)

                    # Error for the side that was winning per TB (if any).
                    if w_end == 1 and k_w is not None:
                        add_terminal_error(k_w)
                    elif w_end == -1 and k_b is not None:
                        add_terminal_error(k_b)

            else:
                # Decisive result: treat as resignation/abandon unless it ended by checkmate.
                if not board.is_checkmate():
                    loser_color = chess.WHITE if actual_white == -1 else chess.BLACK
                    k_loser = add_terminal_step(loser_color)
                    if k_loser is not None:
                        loser_view = w_end if loser_color == chess.WHITE else -w_end
                        # Resigning while TB says draw or win is an additional error.
                        if loser_view >= 0:
                            add_terminal_error(k_loser)

    return GameDeltas(
        keys_seen=keys_seen,
        keys_with_error=keys_with_error,
        per_key_steps=per_key_steps,
        per_key_extra_steps=per_key_extra_steps,
        per_key_errors=per_key_errors,
        time_loss_key=time_loss_key,
        ended_in_345=ended_in_345,
    )


# ----------------------------
# Aggregated stats and output
# ----------------------------

@dataclass
class Stats:
    raw_seen: int = 0
    games_seen: int = 0
    games_used: int = 0
    games_skipped_short: int = 0
    games_skipped_parse: int = 0

    # Games with at least one trackable (non-excluded) "type at turn" (including terminal steps).
    games_with_any_phase: int = 0

    # Games whose final position is 3/4/5 pieces and is "interesting" per is_trivial_final_for_ended_count().
    games_ended_in_345: int = 0

    # Totals:
    steps_total: int = 0          # includes real plies + terminal extra steps
    extra_steps_total: int = 0    # terminal extra steps only
    errors_total: int = 0         # move-errors + terminal errors

    # Games lost on time (attributed to the loser type).
    time_loss_games_total: int = 0


def write_tsv(
    out_path: Path,
    month: str,
    elo_min: int,
    elo_max: int,
    s: Stats,
    keys: List[str],
    per_key_games: Dict[str, int],
    per_key_games_with_error: Dict[str, int],
    per_key_steps_total: Dict[str, int],
    per_key_extra_steps_total: Dict[str, int],
    per_key_errors_total: Dict[str, int],
    per_key_time_losses: Dict[str, int],
) -> None:
    """Write a TSV file with both raw counts and percentages.

    Percentages are always emitted as percentage points (0..100), never as fractions.
    Raw counts are always emitted alongside, enabling exact aggregation across files.
    """
    denom_used = s.games_used if s.games_used > 0 else 1
    steps_total = s.steps_total
    plies_total = s.steps_total - s.extra_steps_total

    def pct(a: int, b: int) -> float:
        return (a / b) * 100.0 if b > 0 else 0.0

    lines: List[str] = []
    lines.append(f"# month={month}")
    lines.append(f"# elo_min={elo_min}")
    lines.append(f"# elo_max={elo_max}")
    lines.append(f"# elo_rule=soft")
    lines.append(f"# raw_seen={s.raw_seen}")
    lines.append(f"# games_seen={s.games_seen}")
    lines.append(f"# games_used={s.games_used}")
    lines.append(f"# games_skipped_short_plycount<{MIN_PLYCOUNT}={s.games_skipped_short}")
    lines.append(f"# games_skipped_parse={s.games_skipped_parse}")

    lines.append(f"# games_with_any_phase={s.games_with_any_phase}")
    lines.append(f"# pct_any_phase_over_games_used={pct(s.games_with_any_phase, denom_used):.6f}")

    lines.append(f"# games_ended_in_3to5={s.games_ended_in_345}")
    lines.append(f"# pct_ended_in_3to5_over_games_used={pct(s.games_ended_in_345, denom_used):.6f}")

    lines.append(f"# steps_total={steps_total}")
    lines.append(f"# extra_steps_total={s.extra_steps_total}")
    lines.append(f"# plies_total={plies_total}")

    lines.append(f"# errors_total={s.errors_total}")
    lines.append(f"# errors_per_step_pct_total={pct(s.errors_total, steps_total):.8f}")
    lines.append(f"# errors_per_ply_pct_total={pct(s.errors_total, plies_total):.8f}")

    lines.append(f"# time_loss_games_total={s.time_loss_games_total}")
    lines.append(f"# pct_time_loss_over_games_used={pct(s.time_loss_games_total, denom_used):.6f}")

    lines.append(
        "material\t"
        "games\tgames_pct_over_used\t"
        "steps\tavg_steps_per_game\t"
        "plies\tavg_plies_per_game\t"
        "games_with_error\terror_game_pct\t"
        "errors\terrors_per_step_pct\terrors_per_ply_pct\t"
        "time_losses\ttime_loss_pct"
    )

    for k in keys:
        g = per_key_games.get(k, 0)
        gerr = per_key_games_with_error.get(k, 0)
        st = per_key_steps_total.get(k, 0)
        ex = per_key_extra_steps_total.get(k, 0)
        pl = st - ex
        errs = per_key_errors_total.get(k, 0)
        tl = per_key_time_losses.get(k, 0)

        pct_used = pct(g, denom_used)
        avg_steps = (st / g) if g > 0 else 0.0
        avg_plies = (pl / g) if g > 0 else 0.0

        err_game_pct = pct(gerr, g)
        err_per_step_pct = pct(errs, st)
        err_per_ply_pct = pct(errs, pl)

        tl_pct = pct(tl, g)

        lines.append(
            f"{k}\t"
            f"{g}\t{pct_used:.6f}\t"
            f"{st}\t{avg_steps:.6f}\t"
            f"{pl}\t{avg_plies:.6f}\t"
            f"{gerr}\t{err_game_pct:.6f}\t"
            f"{errs}\t{err_per_step_pct:.6f}\t{err_per_ply_pct:.6f}\t"
            f"{tl}\t{tl_pct:.6f}"
        )

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(out_path)


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def main() -> None:
    args = parse_args()
    elo_min = args.elo_min
    elo_max = args.elo_max
    if elo_max <= elo_min:
        raise ValueError("elo-max must be strictly greater than elo-min.")

    out_path = out_path_for(args.month, elo_min, elo_max, args.out_dir)

    gaviota_dirs = find_gaviota_dirs(GAVIOTA_ROOT)
    tb = open_tablebase_native_fixed(gaviota_dirs)

    keys = list(ALL_KEYS)
    per_key_games = {k: 0 for k in keys}
    per_key_games_with_error = {k: 0 for k in keys}
    per_key_steps_total = {k: 0 for k in keys}
    per_key_extra_steps_total = {k: 0 for k in keys}
    per_key_errors_total = {k: 0 for k in keys}
    per_key_time_losses = {k: 0 for k in keys}

    s = Stats()

    if args.pgn == "-":
        pgn_stream = sys.stdin
    else:
        pgn_stream = open(args.pgn, "r", encoding="utf-8")

    t0 = time.time()
    last_log = t0

    def ensure_key_exists(k: str) -> None:
        if k in per_key_games:
            return
        keys.append(k)
        keys.sort()
        per_key_games[k] = 0
        per_key_games_with_error[k] = 0
        per_key_steps_total[k] = 0
        per_key_extra_steps_total[k] = 0
        per_key_errors_total[k] = 0
        per_key_time_losses[k] = 0

    def dump_progress(now: float) -> None:
        denom = s.games_used if s.games_used > 0 else 1
        pct_any = (s.games_with_any_phase / denom) * 100.0
        plies_total = s.steps_total - s.extra_steps_total
        err_step = (s.errors_total / s.steps_total) * 100.0 if s.steps_total else 0.0
        err_ply = (s.errors_total / plies_total) * 100.0 if plies_total else 0.0
        tl_pct = (s.time_loss_games_total / denom) * 100.0
        print(
            "progress:\n"
            f"  month={args.month} elo_soft=[{elo_min},{elo_max}[ elapsed={(now - t0)/60:.1f}m "
            f"raw_seen={fmt_int(s.raw_seen)} games_seen={fmt_int(s.games_seen)} games_used={fmt_int(s.games_used)} "
            f"skipped_short={fmt_int(s.games_skipped_short)} skipped_parse={fmt_int(s.games_skipped_parse)} "
            f"games_with_any_phase={fmt_int(s.games_with_any_phase)} pct_any={pct_any:.3f}% "
            f"steps_total={fmt_int(s.steps_total)} extra_steps={fmt_int(s.extra_steps_total)} plies_total={fmt_int(plies_total)} "
            f"errors_total={fmt_int(s.errors_total)} err/step={err_step:.4f}% err/ply={err_ply:.4f}% "
            f"time_losses={fmt_int(s.time_loss_games_total)} tl_pct={tl_pct:.3f}%\n",
            file=sys.stderr,
            flush=True,
        )

    def write_out() -> None:
        write_tsv(
            out_path=out_path,
            month=args.month,
            elo_min=elo_min,
            elo_max=elo_max,
            s=s,
            keys=keys,
            per_key_games=per_key_games,
            per_key_games_with_error=per_key_games_with_error,
            per_key_steps_total=per_key_steps_total,
            per_key_extra_steps_total=per_key_extra_steps_total,
            per_key_errors_total=per_key_errors_total,
            per_key_time_losses=per_key_time_losses,
        )

    try:
        raw_seen = 0
        for headers, ply_est, raw_pgn in read_games_raw(pgn_stream):
            raw_seen += 1
            s.raw_seen = raw_seen
            s.games_seen += 1

            # Early filters.
            if not is_rated_event(headers.get("Event", "")):
                continue
            if not is_standard_variant_tag(headers.get("Variant", "")):
                continue
            if args.exclude_bullet and is_bullet(headers):
                continue

            # Elo filters (soft bucket).
            we = _int_or_none(headers.get("WhiteElo"))
            be = _int_or_none(headers.get("BlackElo"))
            if we is None or be is None:
                continue
            if not in_bucket_soft(we, be, elo_min, elo_max):
                continue

            # Skip short games.
            if ply_est < MIN_PLYCOUNT:
                s.games_skipped_short += 1
                continue

            # Parse PGN.
            try:
                game = chess.pgn.read_game(io.StringIO(raw_pgn))
                if game is None:
                    s.games_skipped_parse += 1
                    continue
            except Exception:
                s.games_skipped_parse += 1
                continue

            s.games_used += 1

            # Analyze (TB probe errors are fatal by design).
            deltas = analyze_game(game, headers, tb)

            if deltas.ended_in_345:
                s.games_ended_in_345 += 1

            if deltas.keys_seen:
                s.games_with_any_phase += 1
                for k in deltas.keys_seen:
                    ensure_key_exists(k)
                    per_key_games[k] += 1
                for k in deltas.keys_with_error:
                    ensure_key_exists(k)
                    per_key_games_with_error[k] += 1

            for k, v in deltas.per_key_steps.items():
                ensure_key_exists(k)
                per_key_steps_total[k] += v
                s.steps_total += v

            for k, v in deltas.per_key_extra_steps.items():
                ensure_key_exists(k)
                per_key_extra_steps_total[k] += v
                s.extra_steps_total += v

            for k, v in deltas.per_key_errors.items():
                ensure_key_exists(k)
                per_key_errors_total[k] += v
                s.errors_total += v

            if deltas.time_loss_key is not None:
                ensure_key_exists(deltas.time_loss_key)
                per_key_time_losses[deltas.time_loss_key] += 1
                s.time_loss_games_total += 1

            now = time.time()
            if args.log_every > 0 and (now - last_log) >= args.log_every:
                dump_progress(now)
                write_out()
                last_log = now

    finally:
        if args.pgn != "-":
            pgn_stream.close()
        try:
            tb.close()
        except Exception:
            pass

    write_out()
    dump_progress(time.time())
    print(f"done: out={out_path}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
