// Custom Board Implementation (No external library to ensure file:// compatibility)

class Board {
    constructor(elementId, onMoveCallback) {
        this.elementId = elementId;
        this.boardEl = document.getElementById(elementId);
        this.onMove = onMoveCallback;
        this.selectedSquare = null;
        this.fen = "start";
        this.orientation = 'w';
        this.pieces = {};
        this.lastMove = null;
        
        // Load Piece Images (Lichess Cburnett style)
        const colors = ['w', 'b'];
        const types = ['P', 'N', 'B', 'R', 'Q', 'K'];
        const repo = "https://raw.githubusercontent.com/lichess-org/lila/master/public/piece/cburnett";
        
        colors.forEach(c => {
            types.forEach(t => {
                this.pieces[c + t] = `${repo}/${c}${t}.svg`;
            });
        });
    }

    async init() {
        // No-op for compatibility
        return Promise.resolve();
    }

    async render(fen, lastMove = null) {
        this.fen = fen;
        this.lastMove = lastMove;
        this.boardEl.innerHTML = '';
        this.selectedSquare = null;

        const table = document.createElement('div');
        table.className = 'board-grid'; // Use grid layout
        
        // FEN parsing
        const rows = fen.split(' ')[0].split('/');
        
        for (let r = 0; r < 8; r++) {
            let fileIdx = 0;
            const rankStr = rows[r];
            for (let i = 0; i < rankStr.length; i++) {
                const char = rankStr[i];
                if (isNaN(char)) {
                    this.createSquare(table, r, fileIdx, char);
                    fileIdx++;
                } else {
                    let count = parseInt(char);
                    for (let k = 0; k < count; k++) {
                        this.createSquare(table, r, fileIdx, null);
                        fileIdx++;
                    }
                }
            }
        }
        this.boardEl.appendChild(table);
    }

    createSquare(container, rankIdx, fileIdx, pieceChar) {
        const isDark = (rankIdx + fileIdx) % 2 === 1;
        const squareDiv = document.createElement('div');
        squareDiv.className = `square ${isDark ? 'dark' : 'light'}`;
        
        const rankNum = 8 - rankIdx; 
        const fileChar = String.fromCharCode(97 + fileIdx);
        const squareId = fileChar + rankNum;

        squareDiv.dataset.square = squareId;

        // Last move highlight
        if (this.lastMove && (this.lastMove.from === squareId || this.lastMove.to === squareId)) {
            squareDiv.classList.add('last-move');
        }

        // Coordinates
        if (fileIdx === 0) {
            const span = document.createElement('span');
            span.className = 'coord rank';
            span.textContent = rankNum;
            squareDiv.appendChild(span);
        }
        if (rankIdx === 7) {
            const span = document.createElement('span');
            span.className = 'coord file';
            span.textContent = fileChar;
            squareDiv.appendChild(span);
        }

        // Piece
        if (pieceChar) {
            let color = (pieceChar === pieceChar.toUpperCase()) ? 'w' : 'b';
            let type = pieceChar.toUpperCase();
            let key = color + type;
            let imgUrl = this.pieces[key];
            
            const pieceDiv = document.createElement('div');
            pieceDiv.className = 'piece';
            pieceDiv.style.backgroundImage = `url('${imgUrl}')`;
            squareDiv.appendChild(pieceDiv);
        }

        // Interaction
        squareDiv.addEventListener('mousedown', (e) => {
            e.preventDefault(); // Prevent drag default
            this.handleSquareClick(squareId);
        });

        container.appendChild(squareDiv);
    }

    handleSquareClick(squareId) {
        if (!this.onMove) return;

        // If nothing selected, select if it's a piece
        if (!this.selectedSquare) {
            // Check if square has a piece by looking at DOM or FEN (DOM is easier here since we just rendered)
            const hasPiece = document.querySelector(`div[data-square="${squareId}"] .piece`);
            if (hasPiece) {
                this.selectSquare(squareId);
            }
        } else {
            // If same square, deselect
            if (this.selectedSquare === squareId) {
                this.deselectSquare();
            } else {
                // Attempt move
                const from = this.selectedSquare;
                const to = squareId;
                
                // Allow "changing selection" if clicking another piece of same color?
                // For now simple click-click move
                
                // We call onMove. If it's invalid, main.js will handle it (or we could check validity here if we had chess instance)
                // Let's assume onMove handles logic.
                this.onMove(from, to);
                this.deselectSquare();
            }
        }
    }

    selectSquare(squareId) {
        this.deselectSquare(); // Clear prev
        this.selectedSquare = squareId;
        const el = document.querySelector(`div[data-square="${squareId}"]`);
        if (el) el.classList.add('selected');
    }

    deselectSquare() {
        if (this.selectedSquare) {
            const el = document.querySelector(`div[data-square="${this.selectedSquare}"]`);
            if (el) el.classList.remove('selected');
            this.selectedSquare = null;
        }
    }

    enableInput(enabled) {
        this.inputEnabled = enabled;
        this.boardEl.style.pointerEvents = enabled ? 'auto' : 'none';
    }
}

window.Board = Board;