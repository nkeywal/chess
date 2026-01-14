// Globals from scripts
// window.Chess
// window.PositionGenerator
// window.TablebaseAPI
// window.Board

// Global State
let chess = null;
let board = null;
let currentTablebaseData = null;
let generator = null;
let isUserTurn = false;
let gameActive = false;
let currentObjective = null;

// DOM Elements
const statusText = document.getElementById('status-text');
const btnWin = document.getElementById('btn-win');
const btnDraw = document.getElementById('btn-draw');
const btnLoss = document.getElementById('btn-loss');
const btnNext = document.getElementById('btn-next');
const btnSkip = document.getElementById('btn-skip');
const analyzeLink = document.getElementById('analyze-link');
const streakVal = document.getElementById('streak-val');

let streak = 0;

document.addEventListener('DOMContentLoaded', () => {
    // Ensure Chess.js is available
    if (typeof Chess === 'undefined') {
        statusText.textContent = "Error: Chess.js library not loaded.";
        return;
    }

    chess = new Chess();
    generator = new window.PositionGenerator();
    board = new window.Board('board', handleUserMove);

    // Event Listeners
    btnWin.addEventListener('click', () => handleGuess('win'));
    btnDraw.addEventListener('click', () => handleGuess('draw'));
    btnLoss.addEventListener('click', () => handleGuess('loss'));
    btnNext.addEventListener('click', startNewGame);
    btnSkip.addEventListener('click', () => {
        startNewGame();
    });

    startNewGame();
});

function getSelectedPieces() {
    const whiteChecked = Array.from(document.querySelectorAll('input[name="white-pieces"]:checked')).map(cb => cb.value);
    const blackChecked = Array.from(document.querySelectorAll('input[name="black-pieces"]:checked')).map(cb => cb.value);
    return { w: whiteChecked, b: blackChecked };
}

async function startNewGame() {
    gameActive = false;
    isUserTurn = false;
    currentTablebaseData = null;
    toggleGuessControls(false); 
    btnNext.style.display = 'none';
    statusText.textContent = "Generating position...";
    statusText.style.color = "var(--text)";
    
    if (board) board.enableInput(false);

    const pieces = getSelectedPieces();
    
    // Safety check: Needs kings
    if (!pieces.w.includes('K')) pieces.w.push('K');
    if (!pieces.b.includes('K')) pieces.b.push('K');
    
    try {
        let fen;
        let validParams = false;
        let attempts = 0;

        while (!validParams && attempts < 5) {
            attempts++;
            fen = generator.generate(pieces.w, pieces.b);
            
            const data = await window.TablebaseAPI.fetchResult(fen);
            
            if (typeof data.wdl === 'undefined') continue;

            if (data.wdl !== 0 && data.dtm !== null && Math.abs(data.dtm) <= 2) continue; 
            
            validParams = true;
            currentTablebaseData = data;
        }

        if (!validParams) {
             if(!currentTablebaseData) {
                 currentTablebaseData = await window.TablebaseAPI.fetchResult(fen);
             }
        }

        // Setup Game
        chess.load(fen);
        board.render(fen);
        updateLinks(fen);
        
        const wdl = currentTablebaseData.wdl;
        if (wdl > 0) currentObjective = 'win';
        else if (wdl < 0) currentObjective = 'loss'; 
        else currentObjective = 'draw';

        statusText.textContent = "Your Turn: Guess the result or Play a Move!";
        statusText.style.color = "var(--text)";
        toggleGuessControls(true);
        gameActive = true;
        isUserTurn = true;
        
        if (board) board.enableInput(true);

    } catch (e) {
        console.error(e);
        statusText.textContent = "Error: " + e.message;
        statusText.style.color = "var(--highlight-loss)";
    }
}

async function handleUserMove(from, to) {
    if (!gameActive || !isUserTurn) return;

    // Check legality
    const move = chess.move({ from, to, promotion: 'q' });
    if (move === null) {
        // Illegal
        return; 
    }

    // Visual Update
    board.render(chess.fen(), { from, to });
    
    isUserTurn = false;
    board.enableInput(false);
    statusText.textContent = "Computer is thinking...";

    // 1. Evaluate Move quality
    
    const prevWDL = currentTablebaseData.wdl;
    try {
        const newFen = chess.fen();
        const newData = await window.TablebaseAPI.fetchResult(newFen);
        
        let failed = false;
        let failReason = "";

        if (prevWDL === 2) { 
            if (newData.wdl !== -2) { 
                failed = true;
                failReason = "You turned a Win into a Draw/Loss.";
            }
        } else if (prevWDL === 0) { 
            if (newData.wdl === 2) { 
                failed = true;
                failReason = "You turned a Draw into a Loss.";
            }
        }
        
        if (failed) {
            endGame(false, failReason);
            return;
        }

        if (chess.game_over()) {
             checkGameOver();
             return;
        }

        // 2. Computer Reply
        if (newData.moves && newData.moves.length > 0) {
            const bestMoveUCI = newData.moves[0].uci; 
            const fromC = bestMoveUCI.substring(0, 2);
            const toC = bestMoveUCI.substring(2, 4);
            
            chess.move({ from: fromC, to: toC, promotion: 'q' });
            board.render(chess.fen(), { from: fromC, to: toC });

            if (chess.game_over()) {
                checkGameOver();
                return;
            }
            
            const postComputerFen = chess.fen();
            const postComputerData = await window.TablebaseAPI.fetchResult(postComputerFen);
            currentTablebaseData = postComputerData;
            isUserTurn = true;
            statusText.textContent = "Your Turn";
            board.enableInput(true);

        } else {
             checkGameOver();
        }

    } catch (e) {
        console.error(e);
        statusText.textContent = "Error processing move.";
    }
}

function checkGameOver() {
    if (chess.in_checkmate()) {
        if (chess.turn() === 'b') { 
            endGame(true, "Checkmate! You Won!");
        } else {
             const success = currentObjective === 'loss';
             endGame(success, success ? "Checkmate! (Expected)" : "Checkmate! You lost.");
        }
    } else if (chess.in_stalemate()) {
         const success = currentObjective !== 'win'; 
         endGame(success, "Stalemate!");
    } else if (chess.in_draw()) {
         const success = currentObjective !== 'win';
         endGame(success, "Draw!");
    } else {
         endGame(true, "Game Over.");
    }
}

function handleGuess(guess) {
    if (!gameActive) return;

    let correct = false;
    let actual = 'draw';
    
    if (currentTablebaseData.wdl > 0) actual = 'win';
    else if (currentTablebaseData.wdl < 0) actual = 'loss';

    if (guess === actual) {
        correct = true;
    }

    if (correct) {
        streak++;
        streakVal.textContent = streak;
        statusText.textContent = `Correct! It is a ${actual.toUpperCase()}. Keep playing if you want!`;
        statusText.style.color = "var(--highlight-win)";
        btnNext.style.display = 'inline-block';
        toggleGuessControls(false);
    } else {
        streak = 0;
        streakVal.textContent = streak;
        statusText.innerHTML = `Wrong. Tablebase says <b>${actual.toUpperCase()}</b> (WDL: ${currentTablebaseData.wdl}).<br>` +
                               `<span style="font-size:0.8em; color:#aaa">FEN: ${chess.fen()}</span>`;
        statusText.style.color = "var(--highlight-loss)";
        gameActive = false;
        toggleGuessControls(false);
        btnNext.style.display = 'inline-block';
        if (board) board.enableInput(false);
    }
}

function endGame(success, message) {
    gameActive = false;
    isUserTurn = false;
    statusText.textContent = message;
    statusText.style.color = success ? "var(--highlight-win)" : "var(--highlight-loss)";
    toggleGuessControls(false);
    btnNext.style.display = 'inline-block';
    if (board) board.enableInput(false);
    if (!success) {
        streak = 0;
        streakVal.textContent = streak;
    }
}

function toggleGuessControls(show) {
    btnWin.disabled = !show;
    btnDraw.disabled = !show;
    btnLoss.disabled = !show;
}

function updateLinks(fen) {
    const link = `https://lichess.org/analysis/${fen.replace(/ /g, '_')}`;
    analyzeLink.href = link;
}