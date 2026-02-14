import { Chess } from "https://cdn.jsdelivr.net/npm/chess.js@1.0.0-beta.7/+esm";

// KR_KP policy (computer plays Black): White has K+R, Black has K+P.
// This policy is for Black moves.
//
// Outcomes:
// - In winning/losing positions, dtm is available.
// - In drawn positions, dtm is not used.
//
// Goal in DRAW / LOSS:
// - Avoid "easy" conversion lines for White (especially any SAFE capture of the pawn).
// - Prefer practical resistance / traps using ONLY local 1-ply features.

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

function promoSquareBlack(pawnSq) {
  const f = getFile(pawnSq);
  return f + "1";
}

function blockSquareBlack(pawnSq) {
  const f = getFile(pawnSq);
  const r = getRank(pawnSq);
  if (r <= 1) return null;
  return f + (r - 1);
}

// --- Feature Extraction (local, 1-ply only) ---

function analyzePosition(fen, moveUci) {
  const chessPre = new Chess(fen);
  const BK0 = findPieceSquare(chessPre, "b", "k");
  const BP0 = findPieceSquare(chessPre, "b", "p");
  const preBK_BP = (BK0 && BP0) ? kdist(BK0, BP0) : Infinity;
  const preBK_promo = (BK0 && BP0) ? kdist(BK0, promoSquareBlack(BP0)) : Infinity;

  const chess = new Chess(fen);

  const from = moveUci.slice(0, 2);
  const to = moveUci.slice(2, 4);
  const promotion = moveUci.length > 4 ? moveUci[4] : undefined;

  const moveRes = chess.move({ from, to, promotion: promotion || "q" });
  if (!moveRes) return null;

  // Detect promotion moves explicitly (even if this dataset typically avoids them).
  const movedToRank = getRank(to);
  const promoted = (moveRes.piece === "p") && (promotion !== undefined || movedToRank === 1);

  // After Black's move, it's White to move.
  const WK = findPieceSquare(chess, "w", "k");
  const WR = findPieceSquare(chess, "w", "r");
  const BK = findPieceSquare(chess, "b", "k");
  const BP = findPieceSquare(chess, "b", "p"); // null after promotion / capture
  if (!WK || !WR || !BK) return null;

  const pawnGone = (BP === null);

  // King pressure on rook: if Black king is adjacent to the rook and White king is NOT adjacent,
  // then the rook is under immediate legal capture threat on Black's next move (forcing rook movement now).
  const kingThreatensRook = (kdist(BK, WR) <= 1) && (kdist(WK, WR) > 1);

  // Pawn-related features (only if pawn exists AND no promotion happened)
  let pawnRank = null;
  let pawnFileIdx = null;
  let isRookPawn = false;
  let promoSq = null;
  let blockSq = null;

  let pawnDefByKing = false;
  let BKInFrontOfPawn = false;
  let BKDistToPawn = Infinity;
  let BKDistToPromo = Infinity;
  let BKDistToBlock = Infinity;

  // White's immediate ability to capture the pawn, and whether such a capture is SAFE.
  let whiteCanCapturePawnNow = false;
  let safePawnCaptureExists = false;
  let safeKingCaptureExists = false;
  let safeRookCaptureExists = false;

  // White's checking resources after Black's move: lower count tends to reduce "automatic" play.
  let whiteCheckingMovesCount = 0;

  if (!pawnGone && !promoted) {
    pawnRank = getRank(BP);
    pawnFileIdx = getFileIdx(BP);
    isRookPawn = (pawnFileIdx === 0 || pawnFileIdx === 7);

    promoSq = promoSquareBlack(BP);
    blockSq = blockSquareBlack(BP);

    pawnDefByKing = (kdist(BK, BP) <= 1);
    BKInFrontOfPawn = (getFile(BK) === getFile(BP) && getRank(BK) < getRank(BP));
    BKDistToPawn = kdist(BK, BP);
    BKDistToPromo = kdist(BK, promoSq);
    BKDistToBlock = blockSq ? kdist(BK, blockSq) : Infinity;

    const whiteMoves = chess.moves({ verbose: true });

    // Count checking moves for White (simulate each move and test if Black is in check).
    // After White plays, it's Black to move; chess.isCheck() then means "is Black in check?"
    for (const wMove of whiteMoves) {
      const played = chess.move({ from: wMove.from, to: wMove.to, promotion: wMove.promotion });
      if (!played) continue;
      if (chess.isCheck()) whiteCheckingMovesCount += 1;
      chess.undo();
    }

    // Pawn captures and safety evaluation
    const pawnCaptures = whiteMoves.filter(w => w.to === BP);
    whiteCanCapturePawnNow = pawnCaptures.length > 0;

    for (const wMove of pawnCaptures) {
      const isKingCap = (wMove.piece === "k");
      const isRookCap = (wMove.piece === "r");

      const playedW = chess.move({ from: wMove.from, to: wMove.to, promotion: wMove.promotion });
      if (!playedW) continue;

      // After White capture, it's Black to move. If Black can immediately capture the White rook (king or pawn),
      // then the pawn capture is NOT safe for White.
      const WR_after = findPieceSquare(chess, "w", "r");
      let blackCanCaptureRookNow = false;

      if (WR_after) {
        const blackMoves = chess.moves({ verbose: true });
        blackCanCaptureRookNow = blackMoves.some(b => b.to === WR_after);
      }

      if (!blackCanCaptureRookNow) {
        safePawnCaptureExists = true;
        if (isKingCap) safeKingCaptureExists = true;
        if (isRookCap) safeRookCaptureExists = true;
        chess.undo();
        // A safe rook capture is the simplest conversion line; if it exists, we can stop early.
        if (safeRookCaptureExists) break;
        continue;
      }

      chess.undo();
    }
  }

  // "Poisoned pawn" motif:
  // White can take the pawn, but there is NO safe capture (any capture loses the rook immediately).
  const pawnPoisoned = (!pawnGone && !promoted && whiteCanCapturePawnNow && !safePawnCaptureExists);

  // Useful move filter to avoid drift:
  // - pawn moves are useful (they change the objective and often force calculation)
  // - king moves must be forcing (threaten rook) OR improve distance to pawn/promo (relative to pre-move)
  const postBK_BP = (!pawnGone && !promoted && BK && BP) ? kdist(BK, BP) : preBK_BP;
  const postBK_promo = (!pawnGone && !promoted && BK && BP) ? kdist(BK, promoSquareBlack(BP)) : preBK_promo;

  const kingCloserToPawn = (!pawnGone && !promoted && postBK_BP < preBK_BP);
  const kingCloserToPromo = (!pawnGone && !promoted && postBK_promo < preBK_promo);

  let Useful = true;
  if (moveRes.piece === "k") {
    Useful = kingThreatensRook || kingCloserToPawn || kingCloserToPromo;
  } // pawn moves remain Useful=true

  // Phase by pawn rank (if pawn exists)
  // C: pawn on 2nd rank or 1st (promotion imminent), B: 3-4, A: otherwise.
  let phase = "NOPAWN";
  if (!pawnGone && !promoted) {
    if (pawnRank <= 2) phase = "C";
    else if (pawnRank <= 4) phase = "B";
    else phase = "A";
  }

  return {
    promoted,
    pawnGone,
    pawnRank,
    isRookPawn,
    whiteCanCapturePawnNow,
    safePawnCaptureExists,
    safeKingCaptureExists,
    safeRookCaptureExists,
    pawnPoisoned,
    pawnDefByKing,
    BKInFrontOfPawn,
    BKDistToPawn,
    BKDistToPromo,
    BKDistToBlock,
    kingThreatensRook,
    whiteCheckingMovesCount,
    Useful,
    phase,
  };
}

// --- Main Policy ---

export function krKpPolicy(input) {
  const { fen, tbData } = input;
  if (!tbData || !tbData.moves || tbData.moves.length === 0) return null;

  const candidates = tbData.moves.map((m) => ({
    original: m,
    uci: m.uci,
    outcome: getMoverResult(m.category),
    dtm: (m.checkmate || m.dtm === 0) ? 0 : (Number.isFinite(m.dtm) ? Math.abs(m.dtm) : null),
    features: null,
  }));

  // Select target set: prefer DRAW if available, else LOSS. (WIN is rare but supported.)
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

  // Avoid promotion moves when alternatives exist (keeps the policy within KR_KP behavior and avoids "surprise simplifications").
  if (target !== "WIN" && C.some(c => !c.features.promoted)) {
    C = C.filter(c => !c.features.promoted);
  }

  // --- Hard constraints (apply only if they don't empty the set) ---

  // D1: avoid allowing a SAFE pawn capture (easy conversion) if avoidable.
  if (C.some(c => !c.features.safePawnCaptureExists)) {
    C = C.filter(c => !c.features.safePawnCaptureExists);
  } else {
    // If safe pawn capture cannot be avoided, at least avoid safe ROOK capture first (most trivial),
    // then safe KING capture.
    if (C.some(c => !c.features.safeRookCaptureExists)) {
      C = C.filter(c => !c.features.safeRookCaptureExists);
    }
    if (C.some(c => !c.features.safeKingCaptureExists)) {
      C = C.filter(c => !c.features.safeKingCaptureExists);
    }
  }

  // D2: avoid drift if avoidable
  if (C.some(c => c.features.Useful)) {
    C = C.filter(c => c.features.Useful);
  }

  // --- Preferences (strict order) ---

  // Prefer "poisoned pawn" situations (pawn looks takable but isn't), if available.
  if (C.some(c => c.features.pawnPoisoned)) {
    const poisoned = C.filter(c => c.features.pawnPoisoned);
    if (poisoned.length > 0) C = poisoned;
  }

  // Prefer forcing the rook to move (king threatens rook), if available.
  if (C.some(c => c.features.kingThreatensRook)) {
    const forcing = C.filter(c => c.features.kingThreatensRook);
    if (forcing.length > 0) C = forcing;
  }

  // Prefer more advanced pawn phase (more urgency) as a tie-break only.
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

    // 1) Prefer pawn phases with higher urgency (C > B > A), but keep it a light preference.
    const prA = phaseRank(af.phase);
    const prB = phaseRank(bf.phase);
    if (prA !== prB) return prB - prA;

    // 2) Geometry: king in front / pawn defended
    let d = preferBool("BKInFrontOfPawn")(a, b); if (d) return d;
    d = preferBool("pawnDefByKing")(a, b); if (d) return d;

    // 3) Rook-pawn special: getting the king close to the promotion corner is critical for draws.
    if (af.isRookPawn && bf.isRookPawn) {
      if (af.BKDistToPromo !== bf.BKDistToPromo) return af.BKDistToPromo - bf.BKDistToPromo;
    }

    // 4) Minimize White's immediate checking options (less "automatic" play).
    if (af.whiteCheckingMovesCount !== bf.whiteCheckingMovesCount) {
      return af.whiteCheckingMovesCount - bf.whiteCheckingMovesCount;
    }

    // 5) Advance pawn (smaller rank is closer to promotion for Black) as final tie-break.
    if (af.pawnRank !== null && bf.pawnRank !== null && af.pawnRank !== bf.pawnRank) {
      return af.pawnRank - bf.pawnRank;
    }

    // Deterministic tie-break
    return a.uci.localeCompare(b.uci);
  });

  return C[0].original;
}
