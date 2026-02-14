import { Chess } from "https://cdn.jsdelivr.net/npm/chess.js@1.0.0-beta.7/+esm";

// KP_KP policy (computer plays Black): each side has K+P.
// In winning/losing positions, dtm is always available. In draws, dtm is not used.
// Goal in DRAW / LOSS: maximize practical resistance and keep counterplay, using only local 1-ply features.

const GAP_BREAK = 12;

// --- Helpers ---

// Lichess tablebase move.category is the result for the side to move AFTER the move.
// Convert it to the mover's result (the mover here is Black, since this policy is for Black moves).
function getMoverResult(category) {
  const cat = (category || "").toLowerCase();
  if (cat.includes("loss")) return "WIN";   // opponent loses => mover wins
  if (cat.includes("draw")) return "DRAW";
  if (cat.includes("win"))  return "LOSS";  // opponent wins => mover loses
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

function isDirectOpposition(chess, WK, BK) {
  if (!WK || !BK) return false;

  const wf = getFile(WK), bf = getFile(BK);
  const wr = getRank(WK), br = getRank(BK);
  const wfi = getFileIdx(WK), bfi = getFileIdx(BK);

  // Same file, two ranks apart, middle square must be empty
  if (wf === bf && Math.abs(wr - br) === 2) {
    const mid = wf + ((wr + br) / 2);
    return chess.get(mid) === null;
  }

  // Same rank, two files apart, middle square must be empty
  if (wr === br && Math.abs(wfi - bfi) === 2) {
    const mid = toSquare((wfi + bfi) / 2, wr);
    return chess.get(mid) === null;
  }

  return false;
}

function stepsToProm(pawnSq, color) {
  const r = getRank(pawnSq);
  if (color === "w") return 8 - r;
  return r - 1; // black promotes on rank 1
}

function promoSquare(pawnSq, color) {
  const f = getFile(pawnSq);
  return color === "w" ? (f + "8") : (f + "1");
}

// Square-rule approximation under an empty-board assumption.
// sideToMove is the side to move in the current position ("w" or "b").
// If the pursuer king moves first, allow one extra tempo in the square test.
function kingCatchesPawnApprox(kingSq, pawnSq, pawnColor, sideToMove) {
  const steps = stepsToProm(pawnSq, pawnColor);
  const psq = promoSquare(pawnSq, pawnColor);
  const dist = kdist(kingSq, psq);

  // Pursuer is the opposite color of the pawn.
  const pursuerColor = pawnColor === "w" ? "b" : "w";
  const pursuerToMove = (sideToMove === pursuerColor);

  const allowance = pursuerToMove ? 1 : 0;
  return dist <= (steps + allowance);
}

// --- Feature Extraction (local, 1-ply only) ---

function playVerboseMove(chess, mv) {
  return chess.move({ from: mv.from, to: mv.to, promotion: mv.promotion });
}

function analyzePosition(fen, moveUci) {
  const chessPre = new Chess(fen);
  const BK0 = findPieceSquare(chessPre, "b", "k");
  const BP0 = findPieceSquare(chessPre, "b", "p");
  const WP0 = findPieceSquare(chessPre, "w", "p");
  const preBK_BP = (BK0 && BP0) ? kdist(BK0, BP0) : Infinity;
  const preBK_WP = (BK0 && WP0) ? kdist(BK0, WP0) : Infinity;

  const chess = new Chess(fen);

  const from = moveUci.slice(0, 2);
  const to = moveUci.slice(2, 4);
  const promotion = moveUci.length > 4 ? moveUci[4] : undefined;

  const moveRes = chess.move({ from, to, promotion: promotion || "q" });
  if (!moveRes) return null;

  // After Black's move, it's White to move.
  const WK = findPieceSquare(chess, "w", "k");
  const BK = findPieceSquare(chess, "b", "k");
  const WP = findPieceSquare(chess, "w", "p");
  const BP = findPieceSquare(chess, "b", "p");
  if (!WK || !BK || !WP || !BP) return null;

  const whiteMoves = chess.moves({ verbose: true });

  const whiteCanCaptureBPNow = whiteMoves.some(w => w.to === BP);
  const whiteKingCanCaptureBPNow = whiteMoves.some(w => w.piece === "k" && w.to === BP);
  const whitePawnCanCaptureBPNow = whiteMoves.some(w => w.piece === "p" && w.to === BP);

  // "Free pawn win" for White: exists a capture of BP such that Black cannot immediately capture WP.
  let bpFreeCaptureExists = false;
  let bpFreeKingCaptureExists = false;
  let bpFreePawnCaptureExists = false;

  // If BP is captured, the "obvious simplification" line is: ... KxP and then ... KxP.
  // We detect immediate Black reply KxP (or PxP) on WP.
  const pawnCaptures = whiteMoves.filter(w => w.to === BP);
  for (const wMove of pawnCaptures) {
    const isKingCap = (wMove.piece === "k");
    const isPawnCap = (wMove.piece === "p");

    const wPlayed = playVerboseMove(chess, wMove);
    if (!wPlayed) continue;

    const blackMoves = chess.moves({ verbose: true });
    const blackCanCaptureWPNow = blackMoves.some(b => b.to === WP);

    if (!blackCanCaptureWPNow) {
      bpFreeCaptureExists = true;
      if (isKingCap) bpFreeKingCaptureExists = true;
      if (isPawnCap) bpFreePawnCaptureExists = true;
      chess.undo();
      // If a free king capture exists, that's the simplest "take and keep pawn" pattern.
      if (bpFreeKingCaptureExists) break;
      continue;
    }

    chess.undo();
  }

  // Opposition: good for Black when White is to move and kings are in direct opposition.
  const oppositionGoodForBlack = isDirectOpposition(chess, WK, BK);

  // King pressure on White pawn (forces careful play)
  const BK_attacks_WP = (kdist(BK, WP) <= 1);

  // Black king supports/blocks its pawn and attacks opponent pawn.
  const postBK_BP = kdist(BK, BP);
  const postBK_WP = kdist(BK, WP);
  const kingCloserToBP = postBK_BP < preBK_BP;
  const kingCloserToWP = postBK_WP < preBK_WP;

  // "King in front of pawn" (for Black pawn moving down): BK on same file, strictly ahead.
  const BKInFrontOfBP = (getFile(BK) === getFile(BP) && getRank(BK) < getRank(BP));

  // Pawn advancement (closer to promotion => smaller rank for Black)
  const bpRank = getRank(BP);
  const wpRank = getRank(WP);
  const bpSteps = stepsToProm(BP, "b");
  const wpSteps = stepsToProm(WP, "w");

  // Square-rule tightness (approx). After Black move, side to move is White.
  const whiteCatchesBP = kingCatchesPawnApprox(WK, BP, "b", "w");
  const blackCatchesWP = kingCatchesPawnApprox(BK, WP, "w", "w");

  // Useful-move filter to avoid drift:
  // - king moves must improve something (approach a pawn, gain opposition, or step in front)
  // - pawn moves are useful unless they instantly allow capture (handled by hard constraints)
  let Useful = true;
  if (moveRes.piece === "k") {
    Useful = kingCloserToBP || kingCloserToWP || oppositionGoodForBlack || BKInFrontOfBP;
  }

  // "Race phase" driven by Black pawn (counterplay focus):
  // C: pawn on 2nd rank (1 step), B: pawn on 3rd-4th, A: otherwise.
  let phase = "A";
  if (bpRank <= 2) phase = "C";
  else if (bpRank <= 4) phase = "B";

  return {
    whiteCanCaptureBPNow,
    whiteKingCanCaptureBPNow,
    whitePawnCanCaptureBPNow,
    bpFreeCaptureExists,
    bpFreeKingCaptureExists,
    bpFreePawnCaptureExists,
    oppositionGoodForBlack,
    BK_attacks_WP,
    kingCloserToBP,
    kingCloserToWP,
    BKInFrontOfBP,
    bpRank,
    wpRank,
    bpSteps,
    wpSteps,
    whiteCatchesBP,
    blackCatchesWP,
    Useful,
    phase,
  };
}

// --- Main Policy ---

export function kpKpPolicy(input) {
  const { fen, tbData } = input;
  if (!tbData || !tbData.moves || tbData.moves.length === 0) return null;

  const candidates = tbData.moves.map((m) => ({
    original: m,
    uci: m.uci,
    outcome: getMoverResult(m.category),
    dtm: (m.checkmate || m.dtm === 0) ? 0 : (Number.isFinite(m.dtm) ? Math.abs(m.dtm) : null),
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

  // --- Hard constraints (apply only if they don't empty the set) ---
  // Primary objective in DRAW/LOSS: keep Black pawn alive if possible (counterplay) and avoid "free pawn" captures.

  // D1: if possible, do not allow immediate capture of BP at all.
  if (C.some(c => !c.features.whiteCanCaptureBPNow)) {
    C = C.filter(c => !c.features.whiteCanCaptureBPNow);
  } else {
    // If BP is capturable no matter what, prioritize:
    // - avoid WPxBP (often produces a passed pawn immediately)
    if (C.some(c => !c.features.whitePawnCanCaptureBPNow)) {
      C = C.filter(c => !c.features.whitePawnCanCaptureBPNow);
    }

    // - avoid "free capture" (White takes BP and Black cannot immediately take WP)
    if (C.some(c => !c.features.bpFreeCaptureExists)) {
      C = C.filter(c => !c.features.bpFreeCaptureExists);
    }

    // - if still needed, avoid immediate KxP (simplest human plan)
    if (C.some(c => !c.features.whiteKingCanCaptureBPNow)) {
      C = C.filter(c => !c.features.whiteKingCanCaptureBPNow);
    }
  }

  // D2: avoid drift moves if avoidable (mostly king moves that don't improve anything).
  if (C.some(c => c.features.Useful)) {
    C = C.filter(c => c.features.Useful);
  }

  // Prefer the most advanced Black pawn phase available (more counterplay): C > B > A.
  // (Among already TB-correct moves, this tends to increase practical difficulty for White.)
  const Cphase = C.filter(c => c.features.phase === "C");
  if (Cphase.length) {
    C = Cphase;
  } else {
    const Bphase = C.filter(c => c.features.phase === "B");
    if (Bphase.length) C = Bphase;
  }

  // --- Strict priorities within the chosen phase ---
  // Key order: opposition, king activity, pawn support, then deterministic tie-break.
  const prefer = (feat) => (a, b) => {
    const av = !!a.features[feat];
    const bv = !!b.features[feat];
    return av === bv ? 0 : (av ? -1 : 1);
  };

  const criteria = [];

  // In LOSS, prioritize swindle pressure:
  // - keep opposition / king activity
  // - keep pawn advanced and supported
  if (target === "LOSS") {
    criteria.push(prefer("oppositionGoodForBlack"));
    criteria.push(prefer("BK_attacks_WP"));
    criteria.push(prefer("BKInFrontOfBP"));
    criteria.push(prefer("kingCloserToWP"));
    criteria.push(prefer("kingCloserToBP"));
  } else {
    // DRAW: maximize practical difficulty while staying in draw set.
    criteria.push(prefer("oppositionGoodForBlack"));
    criteria.push(prefer("BK_attacks_WP"));
    criteria.push(prefer("BKInFrontOfBP"));
    criteria.push(prefer("kingCloserToBP"));
    criteria.push(prefer("kingCloserToWP"));
  }

  // Secondary: prefer smaller bpRank (closer to promotion) as a tie-break.
  C.sort((a, b) => {
    for (const fn of criteria) {
      const diff = fn(a, b);
      if (diff !== 0) return diff;
    }

    const ra = a.features.bpRank;
    const rb = b.features.bpRank;
    if (ra !== rb) return ra - rb; // smaller rank is more advanced for Black

    return a.uci.localeCompare(b.uci);
  });

  return C[0].original;
}
