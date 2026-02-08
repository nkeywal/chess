import { Chess } from "https://cdn.jsdelivr.net/npm/chess.js@1.0.0-beta.7/+esm";

// KR_KRP policy (computer plays Black): White has K+R, Black has K+R+P.
// In winning/losing positions, dtm is always available.

const GAP_BREAK = 12;

// --- Helpers ---

// Lichess tablebase move.category is the result for the side to move AFTER the move.
// Convert it to the mover's result.
function getMoverResult(category) {
  const cat = (category || "").toLowerCase();
  if (cat.includes("loss")) return "WIN";  // opponent loses => mover wins
  if (cat.includes("draw")) return "DRAW";
  if (cat.includes("win"))  return "LOSS"; // opponent wins => mover loses
  return "DRAW";
}

function getRank(sq) { return parseInt(sq[1], 10); }
function getFile(sq) { return sq[0]; }
function getFileIdx(sq) { return sq.charCodeAt(0) - 97; }

function kdist(a, b) {
  if (!a || !b) return Infinity;
  const dx = Math.abs(getFileIdx(a) - getFileIdx(b));
  const dy = Math.abs(getRank(a) - getRank(b));
  return Math.max(dx, dy);
}

function toSquare(fIdx, r) {
  return String.fromCharCode(97 + fIdx) + r;
}

function playVerboseMove(chess, mv) {
  // chess.js: safer to pass {from,to,promotion} than the full verbose object.
  return chess.move({ from: mv.from, to: mv.to, promotion: mv.promotion });
}

function findPieceSquare(chess, color, type) {
  const board = chess.board();
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const p = board[r][c];
      if (!p) continue;
      if (p.color === color && p.type === type) {
        return String.fromCharCode(97 + c) + (8 - r);
      }
    }
  }
  return null;
}

function rookAttacksSquare(chess, rookSq, targetSq) {
  if (!rookSq || !targetSq || rookSq === targetSq) return false;

  const rf = getFile(rookSq);
  const rr = getRank(rookSq);
  const rfi = getFileIdx(rookSq);

  const tf = getFile(targetSq);
  const tr = getRank(targetSq);
  const tfi = getFileIdx(targetSq);

  if (rf === tf) {
    const low = Math.min(rr, tr);
    const high = Math.max(rr, tr);
    for (let r = low + 1; r < high; r++) {
      if (chess.get(rf + r) !== null) return false;
    }
    return true;
  }

  if (rr === tr) {
    const low = Math.min(rfi, tfi);
    const high = Math.max(rfi, tfi);
    for (let f = low + 1; f < high; f++) {
      if (chess.get(toSquare(f, rr)) !== null) return false;
    }
    return true;
  }

  return false;
}

// --- Feature Extraction (local, 1-ply only) ---

function analyzePosition(fen, moveUci) {
  const chess = new Chess(fen);

  const from = moveUci.slice(0, 2);
  const to = moveUci.slice(2, 4);
  const promotion = moveUci.length > 4 ? moveUci[4] : undefined;

  const moveRes = chess.move({ from, to, promotion: promotion || "q" });
  if (!moveRes) return null;

  // After Black's move, it's White to move.
  const WK = findPieceSquare(chess, "w", "k");
  const WR = findPieceSquare(chess, "w", "r");
  const BK = findPieceSquare(chess, "b", "k");
  const BR = findPieceSquare(chess, "b", "r");
  const BP = findPieceSquare(chess, "b", "p");

  if (!WK || !WR || !BK || !BR || !BP) return null;

  const pawnFile = getFile(BP);
  const pawnRank = getRank(BP);

  const whiteMoves = chess.moves({ verbose: true });

  // Immediate captures of Black rook (including rook trade)
  const R_hang = whiteMoves.some(w => w.to === BR);
  const Trade  = whiteMoves.some(w => w.piece === "r" && w.to === BR);

  // Pawn is "safely" capturable by White if there exists a capture of BP such that
  // Black has no immediate refutation that wins White's rook NET (i.e. captures WR
  // and White cannot immediately capture Black's rook afterwards).
  let PawnTakeSafe = false;
  const pawnCaptures = whiteMoves.filter(w => w.to === BP);

  for (const wMove of pawnCaptures) {
    const wPlayed = playVerboseMove(chess, wMove);
    if (!wPlayed) continue;

    const WR_after = findPieceSquare(chess, "w", "r");
    const blackMoves = chess.moves({ verbose: true });

    let hasNetRookWinRefutation = false;

    if (WR_after) {
      const capturesWR = blackMoves.filter(b => b.to === WR_after);

      for (const bMove of capturesWR) {
        const bPlayed = playVerboseMove(chess, bMove);
        if (!bPlayed) continue;

        const BR_after = findPieceSquare(chess, "b", "r");
        let whiteCanCaptureBlackRookNow = false;

        if (BR_after) {
          const whiteReplies = chess.moves({ verbose: true });
          whiteCanCaptureBlackRookNow = whiteReplies.some(wr => wr.to === BR_after);
        }

        chess.undo();

        // Refutation exists if Black wins the rook NET (no immediate capture of Black rook).
        if (!whiteCanCaptureBlackRookNow) {
          hasNetRookWinRefutation = true;
          break;
        }
      }
    }

    // If no net-rook-win refutation exists, White's pawn capture is safe.
    if (!hasNetRookWinRefutation) {
      PawnTakeSafe = true;
      chess.undo();
      break;
    }

    chess.undo();
  }

  // Check and safe check (safe = rook not hanging and no immediate rook trade offered)
  const Check = chess.isCheck(); // side-to-move is White -> "is White in check?"
  const SafeCheck = Check && !R_hang && !Trade;

  // Pawn defended (by king adjacency or rook line attack)
  const PawnDefByKing = kdist(BK, BP) <= 1;
  const PawnDefByRook = rookAttacksSquare(chess, BR, BP);
  const PawnDef = PawnDefByKing || PawnDefByRook;

  // Rook behind pawn (black pawn goes downward)
  const Behind = (getFile(BR) === getFile(BP) && getRank(BR) < pawnRank);

  // Strong cutoff: rook controls at least one "approach square" to stop the pawn.
  const approachSquares = [BP];
  if (pawnRank > 1) {
    const blockSq = pawnFile + (pawnRank - 1);
    approachSquares.push(blockSq);

    const bFileIdx = getFileIdx(blockSq);
    const bRank = getRank(blockSq);

    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        const nx = bFileIdx + dx;
        const ny = bRank + dy;
        if (nx < 0 || nx > 7 || ny < 1 || ny > 8) continue;
        approachSquares.push(toSquare(nx, ny));
      }
    }
  }

  let ControlsApproach = false;
  for (const sq of approachSquares) {
    if (sq === BR) continue;
    if (rookAttacksSquare(chess, BR, sq)) { ControlsApproach = true; break; }
  }
  const CutoffOK = ControlsApproach && !R_hang && !Trade;

  // Safe attack of White rook (avoid hanging / immediate trade)
  const AttacksWR = rookAttacksSquare(chess, BR, WR);
  const SafeAttackWR = AttacksWR && !R_hang && !Trade;

  // "Useful" is meant to kill rook-drift moves:
  // - king/pawn moves are assumed purposeful enough in this endgame
  // - rook moves must contribute to a concrete goal (check/cutoff/behind/defend pawn by rook/safe attack)
  let Useful = true;
  if (moveRes.piece === "r") {
    Useful = SafeCheck || CutoffOK || Behind || PawnDefByRook || SafeAttackWR;
  }

  // Phase based on pawn rank after the move:
  // A: 2-4, B: 5-6, C: 7
  let phase = "A";
  if (pawnRank === 7) phase = "C";
  else if (pawnRank >= 5) phase = "B";

  return {
    R_hang,
    Trade,
    PawnTakeSafe,
    SafeCheck,
    PawnDef,
    Behind,
    CutoffOK,
    Useful,
    phase,
  };
}

// --- Main Policy ---

export function krKrpPolicy(input) {
  const { fen, tbData } = input;
  if (!tbData || !tbData.moves || tbData.moves.length === 0) return null;

  // Decorate moves
  const candidates = tbData.moves.map((m) => ({
    original: m,
    uci: m.uci,
    outcome: getMoverResult(m.category),
    dtm: Number.isFinite(m.dtm) ? Math.abs(m.dtm) : null,
    features: null,
  }));

  // Select target set
  let target = "";
  let C = [];

  if (candidates.some(c => c.outcome === "WIN")) {
    target = "WIN";
    C = candidates.filter(c => c.outcome === "WIN");
  } else if (candidates.some(c => c.outcome === "DRAW")) {
    target = "DRAW";
    C = candidates.filter(c => c.outcome === "DRAW");
  } else {
    target = "LOSS";
    C = candidates.filter(c => c.outcome === "LOSS");
  }

  // WIN: shortest DTM
  if (target === "WIN") {
    C.sort((a, b) => {
      const da = a.dtm !== null ? a.dtm : Infinity;
      const db = b.dtm !== null ? b.dtm : Infinity;
      if (da !== db) return da - db;
      return a.uci.localeCompare(b.uci);
    });
    return C[0].original;
  }

  // LOSS: anti-collapse filter on DTM (keep the high plateau before a clear gap)
  if (target === "LOSS") {
    const dtmValues = C.map(c => c.dtm).filter(d => d !== null).sort((a, b) => b - a);
    if (dtmValues.length > 0) {
      let threshold = dtmValues[0];
      for (let i = 0; i < dtmValues.length - 1; i++) {
        if (dtmValues[i] - dtmValues[i + 1] >= GAP_BREAK) {
          threshold = dtmValues[i];
          break;
        }
      }
      const filtered = C.filter(c => c.dtm !== null && c.dtm >= threshold);
      if (filtered.length > 0) C = filtered;
    }
  }

  // Compute local features for DRAW/LOSS
  const analyzed = [];
  for (const cand of C) {
    const feats = analyzePosition(fen, cand.uci);
    if (feats) {
      cand.features = feats;
      analyzed.push(cand);
    }
  }
  if (analyzed.length === 0) return C[0].original;
  C = analyzed;

  // Hard constraints (apply only if they don't empty the set)
  // D1: don't hang the rook
  if (C.some(c => !c.features.R_hang)) {
    C = C.filter(c => !c.features.R_hang);
  }

  // D2: don't allow a safe pawn capture if avoidable
  if (C.some(c => !c.features.PawnTakeSafe)) {
    C = C.filter(c => !c.features.PawnTakeSafe);
  }

  // D3: don't offer an immediate rook trade if avoidable
  if (C.some(c => !c.features.Trade)) {
    C = C.filter(c => !c.features.Trade);
  }

  // D4: avoid rook-drift moves if avoidable
  if (C.some(c => c.features.Useful)) {
    C = C.filter(c => c.features.Useful);
  }

  // Phase-dependent strict priorities, evaluated PER MOVE (no "phase of first candidate" bug).
  function priorityVector(f) {
    // In LOSS (if it ever happens), front-load SafeCheck for swindle chances.
    const head = (target === "LOSS") ? [!!f.SafeCheck] : [];

    if (f.phase === "A") return head.concat([!!f.CutoffOK, !!f.PawnDef, !!f.SafeCheck, !!f.Behind]);
    if (f.phase === "B") return head.concat([!!f.Behind,  !!f.PawnDef, !!f.CutoffOK, !!f.SafeCheck]);
    // phase C
    return head.concat([!!f.PawnDef, !!f.Behind, !!f.CutoffOK, !!f.SafeCheck]);
  }

  function cmpBoolVec(aVec, bVec) {
    for (let i = 0; i < Math.max(aVec.length, bVec.length); i++) {
      const av = !!aVec[i];
      const bv = !!bVec[i];
      if (av !== bv) return av ? -1 : 1; // true first
    }
    return 0;
  }

  C.sort((a, b) => {
    const av = priorityVector(a.features);
    const bv = priorityVector(b.features);
    const diff = cmpBoolVec(av, bv);
    if (diff !== 0) return diff;
    return a.uci.localeCompare(b.uci); // deterministic tie-break
  });

  return C[0].original;
}
