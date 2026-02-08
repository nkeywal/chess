import { Chess } from "https://cdn.jsdelivr.net/npm/chess.js@1.0.0-beta.7/+esm";

// KRP_KR policy (computer plays Black): White has K+R+P, Black has K+R.
// This policy is for Black moves.
//
// Outcomes:
// - In losing positions, dtm is always available.
// - In drawn positions, dtm is not used (no mate).
//
// Goals:
// - DRAW: maximize practical difficulty for White (keep the pawn alive if possible, active rook/king geometry,
//         force decisions with safe checks and safe rook pressure) while staying TB-correct.
// - LOSS: maximize swindle chances (avoid the low-DTM collapse tail; prefer forcing checks / active rook pressure).

const GAP_BREAK = 12;

// --- Helpers ---

// Lichess tablebase move.category is the result for the side to move AFTER the move.
// Convert it to the mover's result (mover is Black).
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

function promoSquareWhite(pawnSq) {
  const f = getFile(pawnSq);
  return f + "8";
}

function blockSquareWhite(pawnSq) {
  const f = getFile(pawnSq);
  const r = getRank(pawnSq);
  if (r >= 8) return null;
  return f + (r + 1);
}

function shortSideForPawnFile(pawnFileIdx) {
  const left = pawnFileIdx;
  const right = 7 - pawnFileIdx;
  if (left < right) return "left";
  if (right < left) return "right";
  return "left"; // deterministic for center files
}

// --- Feature Extraction (local, 1-ply only) ---

function analyzePosition(fen, moveUci) {
  const chessPre = new Chess(fen);
  const BK0 = findPieceSquare(chessPre, "b", "k");
  const WP0 = findPieceSquare(chessPre, "w", "p");
  const preBK_WP = (BK0 && WP0) ? kdist(BK0, WP0) : Infinity;

  const chess = new Chess(fen);

  const from = moveUci.slice(0, 2);
  const to = moveUci.slice(2, 4);
  const promotion = moveUci.length > 4 ? moveUci[4] : undefined;

  const moveRes = chess.move({ from, to, promotion: promotion || "q" });
  if (!moveRes) return null;

  // After Black's move, it's White to move.
  const WK = findPieceSquare(chess, "w", "k");
  const WR = findPieceSquare(chess, "w", "r");
  const WP = findPieceSquare(chess, "w", "p"); // may be null if Black captured it
  const BK = findPieceSquare(chess, "b", "k");
  const BR = findPieceSquare(chess, "b", "r");
  if (!WK || !WR || !BK || !BR) return null;

  const whiteMoves = chess.moves({ verbose: true });

  // Immediate capture of Black rook (by king/rook/pawn).
  const R_hang = whiteMoves.some(w => w.to === BR);

  // Immediate rook trade offered: White rook can capture Black rook now.
  const Trade = whiteMoves.some(w => w.piece === "r" && w.to === BR);

  // Check and safe check (safe = rook not hanging and no immediate rook trade offered)
  const Check = chess.isCheck(); // side to move is White -> "is White in check?"
  const SafeCheck = Check && !R_hang && !Trade;

  const pawnGone = (WP === null);

  // Safe rook pressure on White rook (forcing tactic potential)
  const attacksWR = rookAttacksSquare(chess, BR, WR);
  const safeAttackWR = attacksWR && !R_hang && !Trade;

  // Pawn-related features (only if pawn exists)
  let pawnRank = null;
  let pawnFileIdx = null;
  let promoSq = null;
  let blockSq = null;

  let rookAttacksPawn = false;
  let rookBehindPawn = false;
  let rookAttacksPromo = false;
  let rookAttacksBlock = false;
  let rookOnShortSide = false;

  let BKInFrontOfPawn = false;
  let BKDistToPromo = Infinity;
  let BKDistToBlock = Infinity;
  let WKDistToPawn = Infinity;
  let WKDistToBlock = Infinity;

  let whitePawnPromotesNow = false;
  let whitePawnAdvancesNow = false;

  if (!pawnGone) {
    pawnRank = getRank(WP);
    pawnFileIdx = getFileIdx(WP);
    promoSq = promoSquareWhite(WP);
    blockSq = blockSquareWhite(WP);

    rookAttacksPawn = rookAttacksSquare(chess, BR, WP);
    rookBehindPawn = (getFile(BR) === getFile(WP) && getRank(BR) > pawnRank);
    rookAttacksPromo = rookAttacksSquare(chess, BR, promoSq);
    rookAttacksBlock = blockSq ? rookAttacksSquare(chess, BR, blockSq) : false;

    const shortSide = shortSideForPawnFile(pawnFileIdx);
    const brFileIdx = getFileIdx(BR);
    if (shortSide === "left") rookOnShortSide = (brFileIdx <= pawnFileIdx);
    else rookOnShortSide = (brFileIdx >= pawnFileIdx);

    // King geometry relative to pawn
    BKInFrontOfPawn = (getFile(BK) === getFile(WP) && getRank(BK) >= pawnRank + 1);
    BKDistToPromo = kdist(BK, promoSq);
    BKDistToBlock = blockSq ? kdist(BK, blockSq) : Infinity;
    WKDistToPawn = kdist(WK, WP);
    WKDistToBlock = blockSq ? kdist(WK, blockSq) : Infinity;

    // Immediate pawn moves by White
    const pawnMoves = whiteMoves.filter(w => w.piece === "p");
    whitePawnPromotesNow = pawnMoves.some(w => !!w.promotion);
    whitePawnAdvancesNow = pawnMoves.some(w => w.from === WP); // any pawn move from the pawn square
  }

  // Useful move filter to avoid drift:
  // - rook moves must be forcing or change pawn geometry (check / pressure WR / behind pawn / attack pawn/promo/block)
  // - king moves should get closer to the pawn or its block/promo squares, or get in front
  const postBK_WP = (!pawnGone && BK && WP) ? kdist(BK, WP) : preBK_WP;
  const kingCloserToPawn = (!pawnGone && postBK_WP < preBK_WP);

  let Useful = true;
  if (moveRes.piece === "r") {
    if (pawnGone) {
      Useful = SafeCheck || safeAttackWR;
    } else {
      Useful = SafeCheck || safeAttackWR || rookBehindPawn || rookAttacksPawn || rookAttacksPromo || rookAttacksBlock;
      // Note: rookOnShortSide is NOT enough to be "useful" by itself (avoid drift-to-short-side moves).
    }
  } else if (moveRes.piece === "k") {
    if (pawnGone) Useful = true;
    else Useful = kingCloserToPawn || BKInFrontOfPawn || (BKDistToBlock <= 2) || (BKDistToPromo <= 2);
  }

  // Phase by pawn rank (only meaningful if pawn exists)
  // A: ranks 2-4, B: 5-6, C: 7, NOPAWN otherwise
  let phase = "NOPAWN";
  if (!pawnGone) {
    if (pawnRank === 7) phase = "C";
    else if (pawnRank >= 5) phase = "B";
    else phase = "A";
  }

  return {
    pawnGone,
    R_hang,
    Trade,
    SafeCheck,
    safeAttackWR,
    rookAttacksPawn,
    rookBehindPawn,
    rookAttacksPromo,
    rookAttacksBlock,
    rookOnShortSide,
    BKInFrontOfPawn,
    BKDistToPromo,
    BKDistToBlock,
    WKDistToPawn,
    WKDistToBlock,
    whitePawnPromotesNow,
    whitePawnAdvancesNow,
    Useful,
    phase,
    pawnRank,
  };
}

// --- Main Policy ---

export function krpKrPolicy(input) {
  const { fen, tbData } = input;
  if (!tbData || !tbData.moves || tbData.moves.length === 0) return null;

  const candidates = tbData.moves.map((m) => ({
    original: m,
    uci: m.uci,
    outcome: getMoverResult(m.category),
    dtm: Number.isFinite(m.dtm) ? Math.abs(m.dtm) : null,
    features: null,
  }));

  // Select target set: prefer DRAW if available, else LOSS (WIN is not expected but handled).
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

  // Compute local features
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

  // D1: don't hang the rook
  if (C.some(c => !c.features.R_hang)) {
    C = C.filter(c => !c.features.R_hang);
  }

  // D2: don't allow immediate promotion if avoidable
  if (C.some(c => !c.features.whitePawnPromotesNow)) {
    C = C.filter(c => !c.features.whitePawnPromotesNow);
  }

  // D3: don't offer an immediate rook trade if avoidable
  if (C.some(c => !c.features.Trade)) {
    C = C.filter(c => !c.features.Trade);
  }

  // D4: avoid drift moves if avoidable
  if (C.some(c => c.features.Useful)) {
    C = C.filter(c => c.features.Useful);
  }

  // DRAW: avoid "too simple" immediate simplification by capturing the pawn, if we can still draw without doing so.
  // (In this trainer, keeping the pawn tends to force White into precise technique and creates more practical pitfalls.)
  if (target === "DRAW") {
    const keepPawn = C.filter(c => !c.features.pawnGone);
    if (keepPawn.length > 0) C = keepPawn;
  }

  function phaseRank(phase) {
    if (phase === "C") return 3;
    if (phase === "B") return 2;
    if (phase === "A") return 1;
    return 0; // NOPAWN
  }

  const prefer = (feat) => (a, b) => {
    const av = !!a.features[feat];
    const bv = !!b.features[feat];
    return av === bv ? 0 : (av ? -1 : 1);
  };

  C.sort((a, b) => {
    const af = a.features;
    const bf = b.features;

    // 1) In LOSS, safe checks first (swindle tool)
    if (target === "LOSS") {
      const d1 = prefer("SafeCheck")(a, b);
      if (d1 !== 0) return d1;
    }

    // 2) If pawn exists, prioritize active defensive geometry.
    if (!af.pawnGone && !bf.pawnGone) {
      const aPhase = phaseRank(af.phase);
      const bPhase = phaseRank(bf.phase);
      if (aPhase !== bPhase) return bPhase - aPhase; // C > B > A

      if (af.phase === "C") {
        // Pawn on 7th: stop promotion squares; force checks/decisions; king in front.
        let d = prefer("rookAttacksPromo")(a, b); if (d) return d;
        d = prefer("rookAttacksBlock")(a, b); if (d) return d;
        d = prefer("SafeCheck")(a, b); if (d) return d;
        d = prefer("safeAttackWR")(a, b); if (d) return d;
        d = prefer("rookBehindPawn")(a, b); if (d) return d;
        d = prefer("BKInFrontOfPawn")(a, b); if (d) return d;
      } else if (af.phase === "B") {
        // Classic defense patterns: behind pawn + short side; checks/pressure; king activity.
        let d = prefer("rookBehindPawn")(a, b); if (d) return d;
        d = prefer("rookOnShortSide")(a, b); if (d) return d;
        d = prefer("SafeCheck")(a, b); if (d) return d;
        d = prefer("safeAttackWR")(a, b); if (d) return d;
        d = prefer("rookAttacksPawn")(a, b); if (d) return d;
        d = prefer("BKInFrontOfPawn")(a, b); if (d) return d;
      } else {
        // Early: build pressure and geometry that creates tactical pitfalls.
        let d = prefer("SafeCheck")(a, b); if (d) return d;
        d = prefer("safeAttackWR")(a, b); if (d) return d;
        d = prefer("rookAttacksPawn")(a, b); if (d) return d;
        d = prefer("rookOnShortSide")(a, b); if (d) return d;
        d = prefer("BKInFrontOfPawn")(a, b); if (d) return d;
      }

      // Distance tie-breaks: keep BK closer to promo/block; push WK away from pawn/block
      if (af.BKDistToPromo !== bf.BKDistToPromo) return af.BKDistToPromo - bf.BKDistToPromo;
      if (af.BKDistToBlock !== bf.BKDistToBlock) return af.BKDistToBlock - bf.BKDistToBlock;
      if (af.WKDistToPawn !== bf.WKDistToPawn) return bf.WKDistToPawn - af.WKDistToPawn;
      if (af.WKDistToBlock !== bf.WKDistToBlock) return bf.WKDistToBlock - af.WKDistToBlock;
    } else {
      // Pawn gone: KR vs KR. Prefer safe forcing pressure (checks / safe rook attacks).
      let d = prefer("SafeCheck")(a, b); if (d) return d;
      d = prefer("safeAttackWR")(a, b); if (d) return d;
    }

    return a.uci.localeCompare(b.uci);
  });

  return C[0].original;
}
