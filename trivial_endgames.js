if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").then(reg => {
    reg.update();
  }).catch(console.error);
}

import { Chessboard, COLOR, BORDER_TYPE } from "https://cdn.jsdelivr.net/npm/cm-chessboard@8.11.5/src/Chessboard.js";
import { Arrows, ARROW_TYPE } from "https://cdn.jsdelivr.net/npm/cm-chessboard@8.11.5/src/extensions/arrows/Arrows.js";
import { PromotionDialog } from "https://cdn.jsdelivr.net/npm/cm-chessboard@8.11.5/src/extensions/promotion-dialog/PromotionDialog.js";
import { Chess } from "https://cdn.jsdelivr.net/npm/chess.js@1.0.0-beta.7/+esm";
import { krKrpPolicy } from "./policy_kr_krp.js";
import { kpKpPolicy } from "./policy_kp_kp.js";
import { krpKrPolicy } from "./policy_krp_kr.js";
import { krKpPolicy } from "./policy_kr_kp.js";
import { kbpKbPolicy } from "./policy_kbp_kb.js";

class OverlayMarkers {
  constructor(chessboard, containerEl) {
    this.chessboard = chessboard;
    this.containerEl = containerEl;
    this.keyToNodes = new Map();

    try {
      const cs = window.getComputedStyle(this.containerEl);
      if (cs.position === 'static') this.containerEl.style.position = 'relative';
    } catch (e) {}

    this.svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.svg.setAttribute("id", "markerOverlay");
    this.svg.setAttribute("aria-hidden", "true");
    this.svg.style.position = "absolute";
    this.svg.style.inset = "0";
    this.svg.style.width = "100%";
    this.svg.style.height = "100%";
    this.svg.style.pointerEvents = "none";
    this.svg.style.zIndex = "60";
    this.svg.style.overflow = "visible";

    this.gMarkers = document.createElementNS("http://www.w3.org/2000/svg", "g");
    this.gText = document.createElementNS("http://www.w3.org/2000/svg", "g");
    this.svg.appendChild(this.gMarkers);
    this.svg.appendChild(this.gText);
    this.containerEl.appendChild(this.svg);

    this._mo = new MutationObserver(() => this.sync());
    this._startObservers();
    window.addEventListener('resize', () => this.sync(), { passive: true });
    this.sync();
    requestAnimationFrame(() => this.sync());
  }

  _startObservers() {
    const tryAttach = () => {
      const mainSvg = this.chessboard?.view?.svg;
      if (mainSvg) {
        this._mo.observe(mainSvg, { attributes: true, attributeFilter: ['viewBox', 'width', 'height', 'preserveAspectRatio'] });
        return true;
      }
      return false;
    };
    if (!tryAttach()) {
      let tries = 0;
      const tick = () => {
        tries += 1;
        if (tryAttach() || tries > 20) return;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
  }

  sync() {
    const mainSvg = this.chessboard?.view?.svg;
    if (!mainSvg) return;
    const vb = mainSvg.getAttribute('viewBox');
    if (vb) this.svg.setAttribute('viewBox', vb);
    const par = mainSvg.getAttribute('preserveAspectRatio');
    if (par) this.svg.setAttribute('preserveAspectRatio', par);
  }

  clear() {
    this.keyToNodes.clear();
    while (this.gMarkers.firstChild) this.gMarkers.removeChild(this.gMarkers.firstChild);
    while (this.gText.firstChild) this.gText.removeChild(this.gText.firstChild);
  }

  add(markerDef, square) {
    if (!markerDef || !square) return;
    this.sync();
    const view = this.chessboard?.view;
    if (!view || typeof view.squareToPoint !== 'function') return;

    const point = view.squareToPoint(square);
    const w = view.squareWidth;
    const h = view.squareHeight;
    const minDim = Math.min(w, h);
    const key = `${markerDef.class || 'marker'}:${square}`;

    const existing = this.keyToNodes.get(key);
    if (existing) {
      if (existing.shape && existing.shape.parentNode) existing.shape.parentNode.removeChild(existing.shape);
      if (existing.text && existing.text.parentNode) existing.text.parentNode.removeChild(existing.text);
      this.keyToNodes.delete(key);
    }

    const slice = String(markerDef.slice || 'markerDot');
    let shape = null;
    if (slice.toLowerCase().includes('square')) {
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute('x', point.x);
      rect.setAttribute('y', point.y);
      rect.setAttribute('width', w);
      rect.setAttribute('height', h);
      rect.classList.add('marker-square');
      if (markerDef.class) rect.classList.add(markerDef.class);
      rect.style.pointerEvents = 'none';
      shape = rect;
    } else {
      const cx = point.x + w / 2;
      const cy = point.y + h / 2;
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute('cx', cx);
      circle.setAttribute('cy', cy);
      circle.setAttribute('r', minDim * 0.23);
      circle.classList.add('marker-dot');
      if (markerDef.class) circle.classList.add(markerDef.class);
      circle.style.pointerEvents = 'none';
      shape = circle;
    }
    this.gMarkers.appendChild(shape);

    let textEl = null;
    const dtm = markerDef.dtm;
    if (Number.isFinite(dtm) && dtm !== 0) {
      const cx = point.x + w / 2;
      const cy = point.y + h / 2;
      textEl = document.createElementNS("http://www.w3.org/2000/svg", "text");
      textEl.setAttribute('x', cx);
      textEl.setAttribute('y', cy);
      textEl.setAttribute('text-anchor', 'middle');
      textEl.setAttribute('dominant-baseline', 'central');
      textEl.setAttribute('class', 'marker-dtm-text');
      textEl.textContent = String(dtm);
      textEl.style.pointerEvents = 'none';
      textEl.style.fontFamily = 'ui-sans-serif, system-ui, sans-serif';
      textEl.style.fontWeight = '900';
      textEl.style.fontSize = (minDim * 0.23) + 'px';
      textEl.style.fill = '#ffffff';
      textEl.style.stroke = '#000000';
      textEl.style.strokeWidth = (minDim * 0.055) + 'px';
      textEl.style.paintOrder = 'stroke';
      this.gText.appendChild(textEl);
    }
    this.keyToNodes.set(key, { shape, text: textEl });
  }
}

const MARKER_SOURCE = { class: "marker-source", slice: "markerSquare" };
const MARKER_DEST = { class: "marker-dest", slice: "markerDot" };
const MARKER_GOD_WIN = { class: "marker-god-win", slice: "markerDot" };
const MARKER_GOD_DRAW = { class: "marker-god-draw", slice: "markerDot" };
const MARKER_GOD_LOSS = { class: "marker-god-loss", slice: "markerDot" };
const ARROW_WIN = { ...ARROW_TYPE.default, class: "arrow-win" };
const ARROW_DRAW = { ...ARROW_TYPE.default, class: "arrow-draw" };
const ARROW_LOSS = { ...ARROW_TYPE.default, class: "arrow-loss" };

const ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-";
const PIECE_ORDER = "KQRBNP";
const TABLEBASE_URL = "https://tablebase.lichess.ovh/standard?fen=";

// Loader with manifest + cache session
let MANIFEST = null;
const mem = new Map();

async function getManifest() {
  if (MANIFEST) return MANIFEST;
  const res = await fetch("data/manifest.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`manifest.json load failed: ${res.status}`);
  const m = await res.json();
  if (!m || typeof m !== "object" || !m.files || typeof m.files !== "object") {
    throw new Error("Invalid manifest format");
  }
  MANIFEST = m;
  return MANIFEST;
}

async function buildDataUrl(filename) {
  const m = await getManifest();
  const hash = m.files[filename];
  if (!hash) throw new Error(`Missing hash for ${filename} in manifest`);
  return `data/${filename}?v=${encodeURIComponent(hash)}`;
}

async function loadDense(filename) {
  const url = await buildDataUrl(filename);
  if (mem.has(url)) return mem.get(url);
  const p = (async () => {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), 5000);
    try {
      const res = await fetch(url, { signal: controller.signal });
      clearTimeout(id);
      if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`);
      return await res.text();
    } catch (e) {
      clearTimeout(id);
      mem.delete(url);
      throw e;
    }
  })();
  mem.set(url, p);
  return p;
}

const el = {
  status: document.getElementById("status"),
  streak: document.getElementById("streak"),
  btnWin: document.getElementById("btnWin"),
  btnDraw: document.getElementById("btnDraw"),
  btnLoss: document.getElementById("btnLoss"),
  btnNew: document.getElementById("btnNew"),
  analyze: document.getElementById("analyzeLink"),
  guessControls: document.getElementById("guessControls"),
  btnUndo: document.getElementById("btnUndo"),
  godModeBtn: document.getElementById("godModeBtn"),
};

let chess = new Chess();
let board = null;
let currentFen = "";
let initialFen = "";
let currentTbData = null;
let streak = 0;
let isUserTurn = false;
let gameActive = false;
let selectedSquare = null;
let analysisMode = false;
let streakInvalidated = false;
let guessLock = false;
let gameId = 0;
const tbCache = new Map();

function setStatus(kind, text) { 
  el.status.textContent = text; 
  el.status.className = "status " + (kind || ""); 
}

function isReallyGameOver() {
  return chess.isCheckmate() || (chess.isDraw() && !chess.isThreefoldRepetition());
}

board = new Chessboard(document.getElementById("board"), {
  assetsUrl: "https://cdn.jsdelivr.net/npm/cm-chessboard@8.11.5/assets/",
  position: "8/8/8/8/8/8/8/8",
  orientation: COLOR.white,
  style: {
    pieces: { file: "pieces/standard.svg" },
    borderType: BORDER_TYPE.none
  },
  extensions: [{ class: Arrows }, { class: PromotionDialog }]
});

const overlayMarkers = new OverlayMarkers(board, document.getElementById("board"));

board.enableMoveInput((event) => {
  if (!isUserTurn || !gameActive) return false;
  if (!analysisMode && (!currentTbData || !currentTbData.moves || currentTbData.moves.length === 0)) {
     // Offline or evaluation only: don't allow moves
     return false;
  }
  if (event.type === "moveInputStarted") clearArrows();
  
  switch (event.type) {
    case "moveInputStarted":
      const p = chess.get(event.square);
      const isTurnColor = p && p.color === chess.turn();
      const isOurPiece = p && p.color === 'w';
      if (analysisMode ? isTurnColor : isOurPiece) {
        if (selectedSquare === event.square) {
          selectSquare(null);
          return false;
        } else {
          selectSquare(event.square);
          return true;
        }
      }
      return false;
    case "validateMoveInput":
      if (!gameActive && !analysisMode) return false;
      try {
        const temp = new Chess(chess.fen());
        const piece = temp.get(event.squareFrom);
        const isPawn = piece && piece.type === 'p';
        const targetRank = event.squareTo[1];
        const isPromo = isPawn && (targetRank === '1' || targetRank === '8');
        if (isPromo) {
          const m = temp.move({ from: event.squareFrom, to: event.squareTo, promotion: 'q' });
          return !!m;
        }
        const m = temp.move({ from: event.squareFrom, to: event.squareTo, promotion: 'q' });
        return !!m;
      } catch (e) { return false; }
    case "moveInputFinished":
      setTimeout(() => selectSquare(null), 10);
      const temp = new Chess(chess.fen());
      const sqFrom = event.squareFrom;
      const sqTo = event.squareTo;
      const piece = temp.get(sqFrom);
      const isPawn = piece && piece.type === 'p';
      const targetRank = sqTo[1];
      const isPromo = isPawn && (targetRank === '1' || targetRank === '8');
      if (event.legalMove) {
        if (isPromo) {
          const side = piece.color === 'w' ? COLOR.white : COLOR.black;
          board.showPromotionDialog(sqTo, side, (result) => {
            if (result && result.piece) {
              const promoChar = result.piece[1].toLowerCase();
              handleUserMove(sqFrom, sqTo, promoChar).then(success => {
                if (success) board.setPosition(chess.fen(), true);
                else board.setPosition(chess.fen(), true);
              });
            } else {
              board.setPosition(chess.fen(), true);
            }
          });
          return true;
        } else {
          void handleUserMove(sqFrom, sqTo).catch(console.error);
        }
      }
      return true;
  }
});

el.btnWin.addEventListener("click", () => handleGuess(2));
el.btnDraw.addEventListener("click", () => handleGuess(0));
el.btnLoss.addEventListener("click", () => handleGuess(-2));
el.btnNew.addEventListener("click", startNewGame);
el.btnUndo.addEventListener("click", undoLastMove);

if (el.godModeBtn) {
  el.godModeBtn.addEventListener("click", async () => {
    if (analysisMode) return;
    analysisMode = true;
    el.godModeBtn.classList.add("active");
    selectSquare(null);
    clearArrows();
    guessLock = false;
    setStatus("warn", "Loading analysis...");
    try {
      currentFen = chess.fen();
      currentTbData = await fetchTablebase(currentFen);
      gameActive = true;
      isUserTurn = true;
      streakInvalidated = true;
      const turnStr = chess.turn() === 'w' ? "White" : "Black";
      setStatus(null, `Analysis: ${turnStr} to move.`);
      setGuessEnabled(false);
    } catch(e) {
      console.error(e);
      setStatus("bad", "Network issue: can't access tablebase/lichess");
    }
  });
}

async function undoLastMove(updateStatus = true) {
  if (!currentTbData || !currentTbData.moves || currentTbData.moves.length === 0) return;
  if (chess.turn() === 'b') chess.undo();
  else { chess.undo(); chess.undo(); }
  currentFen = chess.fen();
  await board.setPosition(currentFen, true);
  
  // User performed an undo: reset error states and clear arrows
  guessLock = false;
  clearArrows();

  try {
    currentTbData = await fetchTablebase(currentFen);
    gameActive = true;
    isUserTurn = true;
    
    if (updateStatus) {
        if (analysisMode) {
          const turnStr = chess.turn() === 'w' ? "White" : "Black";
          setStatus(null, `Analysis: ${turnStr} to move.`);
        } else {
          const turnStr = chess.turn() === 'w' ? "White" : "Black";
          setStatus(null, `${turnStr} to move.`);
        }
    }
    
    if (chess.history().length === 0) {
      el.btnUndo.classList.add("hidden");
      el.btnUndo.style.display = 'none';
    } else {
      el.btnUndo.classList.remove("hidden");
      el.btnUndo.style.display = '';
    }
  } catch(e) { console.error(e); }
}

function selectSquare(sq) {
  clearArrows();
  overlayMarkers.clear();
  selectedSquare = sq;
  if (selectedSquare) {
    overlayMarkers.add(MARKER_SOURCE, selectedSquare);
    if (analysisMode && currentTbData && currentTbData.moves) {
      const moves = currentTbData.moves.filter(m => m.uci.startsWith(selectedSquare));
      let bestDtmForSquare = Infinity;
      const winningMoves = moves.filter(m => tbMoveCategoryToMoverWdl(m.category) === -2);
      winningMoves.forEach(m => {
        if (m.dtm !== undefined && m.dtm !== null) {
          const dist = Math.abs(m.dtm);
          if (dist < bestDtmForSquare) bestDtmForSquare = dist;
        }
      });
      const destMap = new Map();
      moves.forEach(m => {
        const dest = m.uci.slice(2, 4);
        const moverWdl = tbMoveCategoryToMoverWdl(m.category);
        const dtm = m.dtm !== undefined && m.dtm !== null ? Math.abs(m.dtm) : Infinity;
        if (!destMap.has(dest)) destMap.set(dest, {moverWdl, dtm});
        else {
          const current = destMap.get(dest);
          if (moverWdl < current.moverWdl) destMap.set(dest, {moverWdl, dtm});
          else if (moverWdl === current.moverWdl && dtm < current.dtm) destMap.set(dest, {moverWdl, dtm});
        }
      });
      destMap.forEach((data, dest) => {
        let markerDef = MARKER_GOD_DRAW;
        if (data.moverWdl === -2) markerDef = MARKER_GOD_WIN;
        else if (data.moverWdl === 2) markerDef = MARKER_GOD_LOSS;
        const marker = { ...markerDef };
        if (data.dtm !== undefined && data.dtm !== Infinity) marker.dtm = data.dtm;
        overlayMarkers.add(marker, dest);
      });
    } else {
      const moves = chess.moves({ square: selectedSquare, verbose: true });
      moves.forEach(m => overlayMarkers.add(MARKER_DEST, m.to));
    }
  }
}

function saveSettings() {
  try {
    const val = document.getElementById("endgameSelect").value;
    localStorage.setItem("endgame-trainer-selection", val);
    localStorage.setItem("endgame-trainer-streak", streak);
  } catch(e) { console.warn("Could not save settings", e); }
}

function loadSettings() {
  try {
    const val = localStorage.getItem("endgame-trainer-selection");
    if (val) {
      const sel = document.getElementById("endgameSelect");
      // Only set if the option exists
      if (sel && [...sel.options].some(o => o.value === val)) {
         sel.value = val;
      }
    }
    const savedStreak = localStorage.getItem("endgame-trainer-streak");
    if (savedStreak) {
       streak = parseInt(savedStreak, 10) || 0;
       updateStreak();
    }
  } catch(e) { console.warn("Could not load settings", e); }
}

async function startNewGame() {
  const localGameId = ++gameId;
  gameActive = false;
  isUserTurn = false;
  currentTbData = null;
  tbCache.clear();
  clearArrows();
  selectSquare(null);
  setGuessEnabled(false);
  if(el.btnUndo) el.btnUndo.classList.add("hidden");
  if(el.godModeBtn) el.godModeBtn.classList.remove("active");
  analysisMode = false;
  streakInvalidated = false;
  guessLock = false;
  setStatus("warn", "Setting up the board...");
  try {
    const { fen, outcome } = await generateValidPosition();
    if (localGameId !== gameId) return;
    currentFen = fen;
    initialFen = fen;
    chess.load(fen);
    await board.setPosition(fen, true);
    if (localGameId !== gameId) return;

    // Use local outcome immediately for guess, no need to wait for API
    currentTbData = { wdl: outcome, moves: [] };
    const turnStr = chess.turn() === 'w' ? "White" : "Black";
    setStatus(null, `${turnStr} to move: Evaluate or play.`);
    
    // Fetch moves in background for play/analysis
    fetchTablebase(fen).then(data => {
      if (localGameId !== gameId) return;
      if (data) {
        data.wdl = outcome; // Source of truth is the local outcome
        currentTbData = data;
      }
    }).catch(e => {
       console.warn("Tablebase fetch failed (offline?), play/analysis disabled.", e);
    });

    updateLinks(initialFen);
    setGuessEnabled(true);
    gameActive = true;
    isUserTurn = true;
    if (el.btnUndo) {
      el.btnUndo.classList.add("hidden");
      el.btnUndo.style.display = 'none';
    }
  } catch (e) {
    if (localGameId !== gameId) return;
    console.error(e);
    if (e.message.includes("fetch positions")) {
       setStatus("bad", e.message);
    } else {
       setStatus("bad", "Network issue: can't access tablebase/lichess");
    }
  }
}

async function handleUserMove(from, to, promotion = 'q') {
  clearArrows();
  selectSquare(null);
  if (!gameActive) return;
  let moveResult = null;
  try {
    moveResult = chess.move({ from, to, promotion });
    if (!moveResult) return false;
  } catch (err) { return false; }
  await handleUserMovePostProcess(moveResult);
  return true;
}

// --- Computer Move Selection Engine ---

const MovePolicies = {
    registry: {}, // key: endgameKey, value: policyFn

    register(key, fn) {
        this.registry[key] = fn;
    },

    getPolicy(key) {
        return this.registry[key] || this.defaultPolicy;
    },

    /**
     * Default policy: matches original behavior.
     * 1. Pick first move from tablebase (Lichess returns best moves first).
     * 2. If it's a draw, try to find a non-capture draw (tie-break).
     */
    defaultPolicy(input) {
        const { tbData } = input;
        if (!tbData || !tbData.moves || tbData.moves.length === 0) return null;

        let bestMove = tbData.moves[0];
        const bestMoverWdl = tbMoveCategoryToMoverWdl(bestMove.category);
        
        // Default behavior: if best move is draw, prefer non-capture draw
        if (bestMoverWdl === 0) {
            const drawMoves = tbData.moves.filter(m => tbMoveCategoryToMoverWdl(m.category) === 0);
            const nonCapture = drawMoves.find(m => m.san && !m.san.includes('x'));
            if (nonCapture) bestMove = nonCapture;
        }
        return bestMove;
    }
};

MovePolicies.register("KR_KRP", krKrpPolicy);
MovePolicies.register("KP_KP", kpKpPolicy);
MovePolicies.register("KRP_KR", krpKrPolicy);
MovePolicies.register("KR_KP", krKpPolicy);
MovePolicies.register("KBP_KB", kbpKbPolicy);

/**
 * Main entry point for selecting the computer's move.
 * Dispatches to specialized policies based on endgameKey.
 */
function selectComputerMove(input) {
    const { endgameKey, tbData } = input;
    
    // Safety check
    if (!tbData || !tbData.moves || tbData.moves.length === 0) {
        return null;
    }

    const policy = MovePolicies.getPolicy(endgameKey);
    let selectedMove = null;

    try {
        selectedMove = policy(input);
    } catch (e) {
        console.error("Policy error, falling back to default:", e);
    }

    // Fallback if policy failed or returned null
    if (!selectedMove) {
        selectedMove = MovePolicies.defaultPolicy(input);
    }

    // Invariant check: selected move must be in tbData.moves
    const isValid = selectedMove && tbData.moves.some(m => m.uci === selectedMove.uci);
    if (!isValid) {
        console.warn("Selected move not in legal moves, falling back.");
        selectedMove = MovePolicies.defaultPolicy(input);
    }

    return selectedMove;
}

async function handleUserMovePostProcess(lastMove) {
  const localGameId = gameId;
  if (analysisMode) {
    currentFen = chess.fen();
    await board.setPosition(currentFen, true);
    if (localGameId !== gameId) return;

    if (el.btnUndo && chess.history().length > 0) {
      el.btnUndo.classList.remove("hidden");
      el.btnUndo.style.display = '';
    }
    try {
      currentTbData = await fetchTablebase(currentFen);
      if (localGameId !== gameId) return;

      const turnStr = chess.turn() === 'w' ? "White" : "Black";
      setStatus(null, `Analysis: ${turnStr} to move.`);
      if (isReallyGameOver()) handleGameOver();
    } catch(e) { console.error(e); }
    isUserTurn = true;
    return true;
  }

  isUserTurn = false;
  setStatus("warn", "Thinking...");
  
  if (!currentTbData || typeof currentTbData.wdl !== 'number') {
    setStatus('bad', 'Internal error: missing tablebase data.');
    endGame(false, 'Internal error: missing tablebase data.');
    return;
  }

  // Check for blunder locally using existing data (Lichess returns all legal moves)
  if (lastMove && currentTbData.moves) {
    const uci = lastMove.from + lastMove.to + (lastMove.promotion || "");
    const moveData = currentTbData.moves.find(m => m.uci === uci) || 
                     currentTbData.moves.find(m => m.san === lastMove.san);

    if (moveData) {
       const cat = (moveData.category || "").toLowerCase();
       const isDrawingForMe = cat.includes("draw");
       const isLosingForMe = cat.includes("win"); // Opponent wins

       const prevWdl = currentTbData.wdl;
       let failed = false;
       let msg = "";

       if (prevWdl === 2) {
         if (isDrawingForMe) { failed = true; msg = `It was winning, it is now a draw.`; }
         else if (isLosingForMe) { failed = true; msg = `A winning position is now lost.`; }
       } else if (prevWdl === 0 && isLosingForMe) {
         failed = true; msg = "A drawn position is now lost.";
       }
       
       if (failed) {
         guessLock = true;
         chess.undo();
         await board.setPosition(chess.fen(), true);
         if (localGameId !== gameId) return;
         
         if (!streakInvalidated) {
             streak = 0;
             streakInvalidated = true;
             updateStreak();
         }
         setStatus("bad", msg);
         setGuessEnabled(false);
         
         // Allow user to retry immediately
         gameActive = true;
         isUserTurn = true;
         
         drawOutcomeArrows(currentTbData);
         
         if (chess.history().length > 0) {
           el.btnUndo.classList.remove("hidden");
           el.btnUndo.style.display = '';
         } else {
           el.btnUndo.classList.add("hidden");
           el.btnUndo.style.display = 'none';
         }
         return; // Stop here: don't fetch anything if the user blundered.
       }
    }
  }

  currentTbData = null;

  try {
    const fenBeforeFetch = chess.fen();
    const newData = await fetchTablebase(fenBeforeFetch);
    if (localGameId !== gameId) return;
    if (chess.fen() !== fenBeforeFetch) return; // Race condition check: user used undo

    currentTbData = newData;

    if (isReallyGameOver()) { handleGameOver(); return; }
    if (newData.moves && newData.moves.length > 0) {
      const endgameKey = document.getElementById("endgameSelect").value;
      const input = {
          endgameKey,
          fen: chess.fen(),
          tbData: newData,
          context: {
              history: chess.history({ verbose: true }),
              gameMode: analysisMode ? 'analysis' : 'normal'
          }
      };

      const bestMove = selectComputerMove(input);

      if (bestMove) {
          const uci = bestMove.uci;
          const promoC = uci.length > 4 ? uci[4] : undefined;
          chess.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: promoC || 'q' });
          await board.setPosition(chess.fen(), true);
          if (localGameId !== gameId) return;

          if (isReallyGameOver()) { handleGameOver(); return; }
          currentFen = chess.fen();
          currentTbData = await fetchTablebase(currentFen);
          if (localGameId !== gameId) return;

          isUserTurn = true;
          if (!analysisMode) {
            const turnStr = chess.turn() === 'w' ? "White" : "Black";
            const msg = guessLock ? `${turnStr} to move.` : `${turnStr} to move: Evaluate or play.`;
            setStatus(null, msg);
          } else setStatus(null, "");
          if (el.btnUndo && chess.history().length > 0) {
            el.btnUndo.classList.remove("hidden");
            el.btnUndo.style.display = '';
          }
      } else {
         handleGameOver();
      }
    } else handleGameOver();
  } catch (e) {
    if (localGameId !== gameId) return;
    console.error(e);
    setStatus("bad", "Network issue: can't access tablebase/lichess");
  }
}

async function handleGuess(guessWdl) {
  if (!gameActive) return;
  
  try {
      // If data is loading (user played, computer thinking), undo to evaluate the previous position.
      if (!currentTbData || !currentTbData.moves) {
         await undoLastMove(false);
      }
      
      if (!currentTbData) return; 

      const actualWdl = currentTbData.wdl;
      const correct = (guessWdl === actualWdl);
      
      // Update UI state first
      setGuessEnabled(false);
      
      if (correct) {
        if (!streakInvalidated) { 
          streak++; 
          streakInvalidated = true; 
          updateStreak(); 
        }
        setStatus("ok", `Spot on! This is indeed a ${wdlToString(actualWdl)}.`);
      } else {
        if (!streakInvalidated) {
            streak = 0;
            streakInvalidated = true;
            updateStreak();
        }
        guessLock = true;
        setStatus("bad", `Nope. This was a ${wdlToString(actualWdl)}.`);
      }

      // Then draw arrows (might fail or take time)
      drawOutcomeArrows(currentTbData);
      
  } catch (e) {
      console.error("Error in handleGuess:", e);
      setStatus("bad", "Network issue: can't access tablebase/lichess");
  }
}

async function handleGameOver() {
  let success = false;
  let msg = "";
  let actualResult = 0; 
  if (chess.isCheckmate()) {
    if (chess.turn() === 'b') actualResult = 1;
    else actualResult = -1;
  }
  let initialWdl = 0;
  try {
    const initData = await fetchTablebase(initialFen);
    if (initData.wdl === 2) initialWdl = 1;
    else if (initData.wdl === -2) initialWdl = -1;
  } catch(e) {}

  if (analysisMode) {
    const resStr = actualResult === 1 ? "Win" : (actualResult === -1 ? "Loss" : "Draw");
    const initStr = initialWdl === 1 ? "Win" : (initialWdl === 0 ? "Draw" : "Loss");
    msg = `${resStr} (Theoretical ${initStr}).`;
    success = true; 
  } else {
    success = (actualResult >= initialWdl);
    
    if (actualResult === 1 && initialWdl === 1) {
       msg = "You won a winning position.";
    } else if (actualResult === 0 && initialWdl === 0) {
       msg = "You drew a drawn position.";
    } else if (actualResult === -1 && initialWdl === -1) {
       msg = "The end. It was a lost position.";
    } else {
       const resStr = actualResult === 1 ? "Win" : (actualResult === -1 ? "Loss" : "Draw");
       const initStr = initialWdl === 1 ? "Win" : (initialWdl === 0 ? "Draw" : "Loss");
       msg = `${resStr} (Theoretical ${initStr}).`;
    }
  }
  
  if (success && !streakInvalidated) { 
    streak++; 
    updateStreak(); 
    setStatus("ok", msg); 
  }
  else { 
    if (!success && !streakInvalidated) {
       streak = 0; 
       updateStreak(); 
    }
    setStatus(success ? "ok" : "bad", msg); 
  }
  
  streakInvalidated = true;
  gameActive = false;
  isUserTurn = false;
  setGuessEnabled(false);
}

function endGame(success, msg) {
  gameActive = false;
  isUserTurn = false;
  setStatus(success ? "ok" : "bad", msg);
  setGuessEnabled(false);
  if (!success) { streak = 0; updateStreak(); }
}

async function generateValidPosition() {
  let { w, b } = getSelectedPieces();
  if(!w.includes('K')) w.push('K');
  if(!b.includes('K')) b.push('K');
  const sortOrder = (p) => PIECE_ORDER.indexOf(p);
  w.sort((a, b) => sortOrder(a) - sortOrder(b));
  b.sort((a, b) => sortOrder(a) - sortOrder(b));
  const filename = `${w.join('')}_${b.join('')}.txt`;
  
  let text = "";
  try {
    setStatus("warn", "Fetching position...");
    text = await loadDense(filename);
  } catch (e) {
    const label = generateEndgameLabel(filename.replace(".txt", "")) || filename;
    throw new Error(`Failed to fetch positions for ${label}`);
  }

  const piecesCount = w.length + b.length;
  const recordLength = piecesCount + 1;
  const totalRecords = Math.floor(text.length / recordLength);
  const randomIndex = Math.floor(Math.random() * totalRecords);
  const start = randomIndex * recordLength;
  const record = text.substring(start, start + recordLength);
  const grid = Array(8).fill(null).map(() => Array(8).fill(""));
  const flatList = [...w.map(p => ({type: p, color: 'w'})), ...b.map(p => ({type: p, color: 'b'}))];
  for (let i = 0; i < piecesCount; i++) {
    const val = ALPHABET.indexOf(record[i]);
    const piece = flatList[i];
    grid[7 - Math.floor(val / 8)][val % 8] = piece.color === 'w' ? piece.type.toUpperCase() : piece.type.toLowerCase();
  }
  const outcomeChar = record[piecesCount];
  let outcomeWdl = 0;
  if (outcomeChar === 'W') outcomeWdl = 2;
  else if (outcomeChar === 'L') outcomeWdl = -2;

  const fen = grid.map(row => {
    let empty = 0, str = "";
    row.forEach(c => {
      if (c === "") empty++;
      else { if (empty > 0) str += empty; empty = 0; str += c; }
    });
    if (empty > 0) str += empty;
    return str;
  }).join("/") + " w - - 0 1";

  return { fen, outcome: outcomeWdl };
}

function clearArrows() { if (typeof board.removeArrows === "function") board.removeArrows(); }
function uciToArrow(uci) { return { from: uci.slice(0, 2), to: uci.slice(2, 4) }; }

/**
 * Converts a move's category (from tablebase) to a WDL score from the perspective 
 * of the player making the move ("mover").
 *
 * Contract:
 * - moves[i].category is the result for the side to move AFTER the move.
 * - "loss" for the next player means "win" for the mover.
 * - "win" for the next player means "loss" for the mover.
 *
 * Returns:
 * - -2: Mover wins (Best outcome)
 * -  0: Draw
 * -  2: Mover loses (Worst outcome)
 */
function tbMoveCategoryToMoverWdl(category) {
  const c = String(category || "").toLowerCase();
  if (c.includes("loss")) return -2; // Opponent loses -> Mover wins
  if (c.includes("draw")) return 0;
  if (c.includes("win")) return 2;   // Opponent wins -> Mover loses
  
  // Fallback/Error case - prevent silent failures
  console.warn(`Unknown category: ${category}. Defaulting to draw (0).`);
  return 0;
}

function drawOutcomeArrows(tbData) {
  clearArrows();
  const moves = Array.isArray(tbData?.moves) ? tbData.moves : [];
  if (!moves.length) return;

  const mappedMoves = moves.map(m => ({
    uci: m.uci,
    moverWdl: tbMoveCategoryToMoverWdl(m.category), // -2(Win), 0(Draw), 2(Loss)
    dtm: typeof m.dtm === 'number' ? Math.abs(m.dtm) : Infinity
  }));

  const hasWin = mappedMoves.some(m => m.moverWdl === -2);
  const hasDraw = mappedMoves.some(m => m.moverWdl === 0);
  const hasLoss = mappedMoves.some(m => m.moverWdl === 2);

  // Rule: Do not show if all options lose (only losses available)
  if (!hasWin && !hasDraw) return;
  
  // Rule: Do not show if all options draw (only draws available, no win possible)
  // Note: We implies !hasWin. If we had a win, we would show it.
  if (!hasWin && !hasLoss) return;

  let candidates = [];

  if (hasWin) {
    // Rule: For wins, show only best DTM
    const wins = mappedMoves.filter(m => m.moverWdl === -2);
    let bestDtm = Infinity;
    
    wins.forEach(m => {
       if (m.dtm < bestDtm) bestDtm = m.dtm;
    });

    // Filter by best DTM (if DTM info is missing/Infinity, show all wins)
    candidates = wins.filter(m => bestDtm === Infinity || m.dtm === bestDtm)
                     .map(m => ({ ...uciToArrow(m.uci), type: ARROW_WIN }));
  } else {
    // No win exists, but we have draws (and losses, otherwise we returned above).
    // Show saving moves (Draws).
    candidates = mappedMoves.filter(m => m.moverWdl === 0)
                            .map(m => ({ ...uciToArrow(m.uci), type: ARROW_DRAW }));
  }

  // Rule: Limit to first 3
  if (candidates.length > 3) {
    candidates = candidates.slice(0, 3);
  }

  candidates.forEach(a => board.addArrow(a.type, a.from, a.to));
}

function getSelectedPieces() {
  const sel = document.getElementById("endgameSelect");
  let val = sel.value;
  if (!val || !val.includes('_')) {
    // Fallback to first option if value is invalid/empty
    if (sel.options.length > 0) {
        val = sel.options[0].value;
        sel.value = val;
    } else {
        return { w: [], b: [] }; // Should not happen
    }
  }
  return { w: val.split('_')[0].split(''), b: val.split('_')[1].split('') };
}

async function fetchTablebase(fen) {
  if (tbCache.has(fen)) {
    return tbCache.get(fen);
  }
  const res = await fetch(TABLEBASE_URL + encodeURIComponent(fen));
  const data = await res.json();

  if (typeof data.wdl === 'undefined' && data.category) {
    if (data.category === 'win') data.wdl = 2;
    else if (data.category === 'loss') data.wdl = -2;
    else data.wdl = 0;
  }
  tbCache.set(fen, data);
  return data;
}

function setGuessEnabled(en) { el.btnWin.disabled = !en; el.btnDraw.disabled = !en; el.btnLoss.disabled = !en; }
function updateStreak() { 
  el.streak.textContent = streak; 
  saveSettings();
}
function wdlToString(wdl) { return wdl > 0 ? "Win" : (wdl < 0 ? "Loss" : "Draw"); }
function updateLinks(fen) { el.analyze.href = `https://lichess.org/analysis/${fen.replace(/ /g, '_')}`; el.analyze.removeAttribute("aria-disabled"); }

function generateEndgameLabel(value) {
  const m = /^([KQRBNP]+)_([KQRBNP]+)$/.exec(value);
  if (!m) return null;

  const MAP_W = { K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘", P: "♙" };
  const MAP_B = { K: "♚", Q: "♛", R: "♜", B: "♝", N: "♞", P: "♟" };

  const left = m[1].split("").map(c => MAP_W[c] || c).join("");
  const right = m[2].split("").map(c => MAP_B[c] || c).join("");
  return `${left} vs ${right}`;
}

async function populateEndgameOptions() {
  const sel = document.getElementById("endgameSelect");
  if (!sel) return;
  try {
    const m = await getManifest();
    const files = Object.keys(m.files).sort();
    sel.innerHTML = "";
    for (const f of files) {
      if (!f.endsWith(".txt")) continue;
      const val = f.replace(".txt", "");
      const opt = document.createElement("option");
      opt.value = val;
      const label = generateEndgameLabel(val);
      opt.textContent = label || val;
      sel.appendChild(opt);
    }
  } catch (e) { console.error(e); }
}

document.getElementById('endgameSelect').addEventListener('change', (e) => { 
    saveSettings(); 
    startNewGame(); 
    e.target.blur();
});

// Info Modal Logic
const modal = document.getElementById('infoModal');
const btnInfo = document.getElementById('btnInfo');
const btnCloseInfo = document.getElementById('btnCloseInfo');
const btnShare = document.getElementById('btnShare');

if (btnShare) {
  btnShare.addEventListener('click', async () => {
    const url = 'https://nkeywal.github.io/chess/trivial_endgames.html';
    if (navigator.share) {
      try {
        await navigator.share({
          title: 'Trivial Endgames',
          text: 'These endgames are trivial.',
          url: url
        });
      } catch (err) {
        if (err.name !== 'AbortError') console.warn("Share failed", err);
      }
    } else {
      try {
        await navigator.clipboard.writeText(url);
        const oldStatus = el.status.textContent;
        const oldClass = el.status.className;
        setStatus("ok", "Link copied to clipboard!");
        setTimeout(() => {
          el.status.textContent = oldStatus;
          el.status.className = oldClass;
        }, 2000);
      } catch (err) {
        console.error("Clipboard failed", err);
      }
    }
  });
}

function openModal() { modal.classList.add('open'); }
function closeModal() { modal.classList.remove('open'); }

if(btnInfo) btnInfo.addEventListener('click', openModal);
if(btnCloseInfo) btnCloseInfo.addEventListener('click', closeModal);
if(modal) modal.addEventListener('click', (e) => {
    if (e.target === modal) closeModal();
});
// Close on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
    if (e.key === 'ArrowLeft') {
       if (el.btnUndo && !el.btnUndo.classList.contains('hidden') && el.btnUndo.style.display !== 'none') {
         undoLastMove();
       }
    }
});

(async function init() {
    await populateEndgameOptions();
    loadSettings();
    startNewGame();
})();
