import { Chess } from "https://cdn.jsdelivr.net/npm/chess.js@1.0.0-beta.7/+esm";

// KBP_KB policy (computer plays Black): White has K+B+P, Black has K+B.
// This policy is for Black moves.
//
// Outcomes:
// - In winning/losing positions, dtm is available.
// - In drawn positions, dtm is not used.
//
// Goal in DRAW / LOSS:
// - Avoid "easy" winning technique for White (especially simplifying bishop trades or allowing clean pawn pushes).
// - Prefer practical resistance: keep the pawn alive if possible, keep pieces on board, force decisions with checks
//   and with threats against the pawn or White bishop, using ONLY local 1–2 ply legality analysis (no TB lookahead).

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

function toSquare(fIdx, r) {
  return String.fromCharCode(97 + fIdx) + r;
}

function bishopAttacksSquare(chess, bishopSq, targetSq) {
  if (!bishopSq || !targetSq || bishopSq === targetSq) return false;
  const bf = getFileIdx(bishopSq);
  const br = getRank(bishopSq);
  const tf = getFileIdx(targetSq);
  const tr = getRank(targetSq);

  const df = tf - bf;
  const dr = tr - br;
  if (Math.abs(df) !== Math.abs(dr)) return false;

  const stepF = df > 0 ? 1 : -1;
  const stepR = dr > 0 ? 1 : -1;

  let f = bf + stepF;
  let r = br + stepR;
  while (f !== tf && r !== tr) {
    if (chess.get(toSquare(f, r)) !== null) return false;
    f += stepF;
    r += stepR;
  }
  return true;
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

// --- Feature Extraction (local, 1–2 ply legality only) ---

function analyzePosition(fen, moveUci) {
  const chessPre = new Chess(fen);
  const BK0 = findPieceSquare(chessPre, "b", "k");
  const WP0 = findPieceSquare(chessPre, "w", "p");

  const preBK_WP = (BK0 && WP0) ? kdist(BK0, WP0) : Infinity;
  const preBK_promo = (BK0 && WP0) ? kdist(BK0, promoSquareWhite(WP0)) : Infinity;
  const preBK_block = (BK0 && WP0) ? kdist(BK0, blockSquareWhite(WP0)) : Infinity;

  const chess = new Chess(fen);
  const from = moveUci.slice(0, 2);
  const to = moveUci.slice(2, 4);
  const promotion = moveUci.length > 4 ? moveUci[4] : undefined;

  const moveRes = chess.move({ from, to, promotion: promotion || "q" });
  if (!moveRes) return null;

  // After Black's move, it's White to move.
  const WK = findPieceSquare(chess, "w", "k");
  const WB = findPieceSquare(chess, "w", "b");
  const WP = findPieceSquare(chess, "w", "p");
  const BK = findPieceSquare(chess, "b", "k");
  const BB = findPieceSquare(chess, "b", "b");
  if (!WK || !WB || !BK || !BB) return null;

  const pawnGone = (WP === null);

  // Check on White king (side to move is White).
  const Check = chess.isCheck();
  const SafeCheck = Check; // bishop safety handled separately as a hard constraint

  const whiteMoves = chess.moves({ verbose: true });

  // Identify whether Black bishop is immediately capturable, and whether recapture is available.
  let bishopHang = false;
  let bishopLost = false;        // White can win the bishop without immediate recapture.
  let bishopTradeOffered = false; // White bishop can capture and Black can recapture (simple exchange).

  for (const w of whiteMoves) {
    if (w.to !== BB) continue;
    bishopHang = true;

    // Simulate White capture of BB
    const playedW = chess.move({ from: w.from, to: w.to, promotion: w.promotion });
    if (!playedW) continue;

    const captureSq = BB;
    const blackMoves = chess.moves({ verbose: true });
    const canRecaptureOnSq = blackMoves.some(bm => bm.to === captureSq);

    if (w.piece === "b" && canRecaptureOnSq) bishopTradeOffered = true;
    if (!canRecaptureOnSq) bishopLost = true;

    chess.undo();
    if (bishopLost) break;
  }

  // Pawn properties (if pawn exists)
  let pawnRank = null;
  let pawnFileIdx = null;
  let isRookPawn = false;
  let promoSq = null;
  let blockSq = null;

  let pawnCanAdvanceNow = false;
  let pawnPromotesNow = false;

  let BBControlsPawn = false;
  let BBControlsBlock = false;
  let BBControlsPromo = false;

  let WBControlsPromo = false; // "wrong bishop" test for rook pawn cases

  let BKDistToPawn = Infinity;
  let BKDistToPromo = Infinity;
  let BKDistToBlock = Infinity;
  let BKOnBlock = false;
  let BKOnPromo = false;

  let threatensPawn = false;
  let attacksWhiteBishop = false;

  if (!pawnGone) {
    pawnRank = getRank(WP);
    pawnFileIdx = getFileIdx(WP);
    isRookPawn = (pawnFileIdx === 0 || pawnFileIdx === 7);
    promoSq = promoSquareWhite(WP);
    blockSq = blockSquareWhite(WP);

    // White pawn moves after Black's move
    const pawnMoves = whiteMoves.filter(m => m.piece === "p");
    pawnCanAdvanceNow = pawnMoves.some(m => m.from === WP);
    pawnPromotesNow = pawnMoves.some(m => !!m.promotion);

    BBControlsPawn = bishopAttacksSquare(chess, BB, WP);
    BBControlsBlock = blockSq ? bishopAttacksSquare(chess, BB, blockSq) : false;
    BBControlsPromo = bishopAttacksSquare(chess, BB, promoSq);

    WBControlsPromo = bishopAttacksSquare(chess, WB, promoSq);

    BKDistToPawn = kdist(BK, WP);
    BKDistToPromo = kdist(BK, promoSq);
    BKDistToBlock = blockSq ? kdist(BK, blockSq) : Infinity;
    BKOnBlock = (blockSq && BK === blockSq);
    BKOnPromo = (BK === promoSq);

    threatensPawn = BBControlsPawn || (BKDistToPawn <= 1);
    attacksWhiteBishop = bishopAttacksSquare(chess, BB, WB);
  }

  // Useful move filter to avoid drift:
  // - bishop moves should give check OR threaten pawn OR control block/promo OR attack WB
  // - king moves should reduce distance to pawn/block/promo OR step onto block/promo squares
  const postBK_WP = (!pawnGone && BK && WP) ? kdist(BK, WP) : preBK_WP;
  const postBK_promo = (!pawnGone && BK && WP) ? kdist(BK, promoSquareWhite(WP)) : preBK_promo;
  const postBK_block = (!pawnGone && BK && WP) ? kdist(BK, blockSquareWhite(WP)) : preBK_block;

  const kingCloserToPawn = (!pawnGone && postBK_WP < preBK_WP);
  const kingCloserToPromo = (!pawnGone && postBK_promo < preBK_promo);
  const kingCloserToBlock = (!pawnGone && postBK_block < preBK_block);

  let Useful = true;
  if (moveRes.piece === "b") {
    Useful = SafeCheck || threatensPawn || BBControlsBlock || BBControlsPromo || attacksWhiteBishop;
  } else if (moveRes.piece === "k") {
    Useful = kingCloserToPawn || kingCloserToBlock || kingCloserToPromo || BKOnBlock || BKOnPromo;
  }

  // Phase by pawn rank (if pawn exists)
  // C: pawn on 7th, B: 5-6, A: otherwise.
  let phase = "NOPAWN";
  if (!pawnGone) {
    if (pawnRank === 7) phase = "C";
    else if (pawnRank >= 5) phase = "B";
    else phase = "A";
  }

  // "Wrong bishop" flag only relevant for rook pawn (fortress draw when attacker bishop doesn't control promo square).
  const wrongBishopForRookPawn = (!pawnGone && isRookPawn && !WBControlsPromo);

  // White freedom: number of legal moves.
  const whiteMovesCount = whiteMoves.length;

  return {
    pawnGone,
    pawnRank,
    isRookPawn,
    wrongBishopForRookPawn,
    promoSq,
    blockSq,
    pawnCanAdvanceNow,
    pawnPromotesNow,
    BBControlsPawn,
    BBControlsBlock,
    BBControlsPromo,
    WBControlsPromo,
    BKDistToPawn,
    BKDistToPromo,
    BKDistToBlock,
    BKOnBlock,
    BKOnPromo,
    threatensPawn,
    attacksWhiteBishop,
    SafeCheck,
    bishopHang,
    bishopLost,
    bishopTradeOffered,
    Useful,
    phase,
    whiteMovesCount,
  };
}

// --- Main Policy ---

export function kbpKbPolicy(input) {
  const { fen, tbData } = input;
  if (!tbData || !tbData.moves || tbData.moves.length === 0) return null;

  const candidates = tbData.moves.map((m) => ({
    original: m,
    uci: m.uci,
    outcome: getMoverResult(m.category),
    dtm: Number.isFinite(m.dtm) ? Math.abs(m.dtm) : null,
    features: null,
  }));

  // Choose target outcome set:
  // - Prefer DRAW if available (defensive side wants to hold).
  // - Else LOSS.
  // - WIN is handled for completeness (rare/impossible here without promotion tactics).
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

  // --- Hard constraints (only applied if they don't empty the set) ---

  // D1: never lose the bishop if avoidable
  if (C.some(c => !c.features.bishopLost)) {
    C = C.filter(c => !c.features.bishopLost);
  }

  // D2: do not allow immediate pawn promotion if avoidable
  if (C.some(c => !c.features.pawnPromotesNow)) {
    C = C.filter(c => !c.features.pawnPromotesNow);
  }

  // D3: avoid immediate bishop exchange ("too simple") if avoidable
  if (C.some(c => !c.features.bishopTradeOffered)) {
    C = C.filter(c => !c.features.bishopTradeOffered);
  }

  // D4: in DRAW, avoid capturing the pawn (simplifies) if other DRAW moves keep it alive
  if (target === "DRAW" && C.some(c => !c.features.pawnGone)) {
    C = C.filter(c => !c.features.pawnGone);
  }

  // D5: avoid drift if avoidable
  if (C.some(c => c.features.Useful)) {
    C = C.filter(c => c.features.Useful);
  }

  function phaseRank(phase) {
    if (phase === "C") return 3;
    if (phase === "B") return 2;
    if (phase === "A") return 1;
    return 0;
  }

  const preferBool = (feat) => (a, b) => {
    const av = !!a.features[feat];
    const bv = !!b.features[feat];
    return av === bv ? 0 : (av ? -1 : 1);
  };

  C.sort((a, b) => {
    const af = a.features;
    const bf = b.features;

    // 0) In LOSS, prefer checks first (forcing chances / swindles)
    if (target === "LOSS") {
      let d = preferBool("SafeCheck")(a, b); if (d) return d;
    }

    // 1) If pawn exists: prioritize defense of promotion / blockade, then forcing threats.
    if (!af.pawnGone && !bf.pawnGone) {
      // Prefer later pawn phase (more urgent), but only as a mild ordering.
      const prA = phaseRank(af.phase);
      const prB = phaseRank(bf.phase);
      if (prA !== prB) return prB - prA;

      // Special rook-pawn "wrong bishop" fortress: drive king to the promotion corner.
      if (af.wrongBishopForRookPawn && bf.wrongBishopForRookPawn) {
        // Best is king on promo square / corner, then closer.
        if (af.BKOnPromo !== bf.BKOnPromo) return af.BKOnPromo ? -1 : 1;
        if (af.BKDistToPromo !== bf.BKDistToPromo) return af.BKDistToPromo - bf.BKDistToPromo;
        // If both equal: prefer forcing checks / pawn threats.
        let d = preferBool("SafeCheck")(a, b); if (d) return d;
        d = preferBool("threatensPawn")(a, b); if (d) return d;
        d = preferBool("attacksWhiteBishop")(a, b); if (d) return d;
      } else {
        // General case:
        // - If pawn on 7th: control promo square or occupy it.
        if (af.phase === "C") {
          let d = preferBool("BBControlsPromo")(a, b); if (d) return d;
          d = preferBool("BKOnPromo")(a, b); if (d) return d;
          d = preferBool("BBControlsBlock")(a, b); if (d) return d;
          d = preferBool("SafeCheck")(a, b); if (d) return d;
          d = preferBool("threatensPawn")(a, b); if (d) return d;
        } else {
          // Blockade building: king on block, bishop controls block, then pawn threat / checks.
          let d = preferBool("BKOnBlock")(a, b); if (d) return d;
          d = preferBool("BBControlsBlock")(a, b); if (d) return d;
          d = preferBool("threatensPawn")(a, b); if (d) return d;
          d = preferBool("SafeCheck")(a, b); if (d) return d;
          d = preferBool("attacksWhiteBishop")(a, b); if (d) return d;
        }

        // Distance tie-breaks: bring BK closer to block/promo.
        if (af.BKDistToBlock !== bf.BKDistToBlock) return af.BKDistToBlock - bf.BKDistToBlock;
        if (af.BKDistToPromo !== bf.BKDistToPromo) return af.BKDistToPromo - bf.BKDistToPromo;
      }

      // Prefer positions where White has MORE legal moves (more decision points / more error surface).
      if (af.whiteMovesCount !== bf.whiteMovesCount) return bf.whiteMovesCount - af.whiteMovesCount;
    } else {
      // Pawn gone: KB vs KB. Prefer checks / bishop activity, then deterministic.
      let d = preferBool("SafeCheck")(a, b); if (d) return d;
      d = preferBool("attacksWhiteBishop")(a, b); if (d) return d;
    }

    return a.uci.localeCompare(b.uci);
  });

  return C[0].original;
}
