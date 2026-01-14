class PositionGenerator {
    constructor() {
        // Ensure Chess.js is loaded
        if (typeof Chess === 'undefined') {
            throw new Error("Chess.js library is not loaded.");
        }
    }

    generate(whitePieces, blackPieces) {
        // Normalize
        whitePieces = whitePieces.map(p => p.toLowerCase()).filter(p => p !== 'k');
        blackPieces = blackPieces.map(p => p.toLowerCase()).filter(p => p !== 'k');
        
        // We handle Kings explicitly
        const files = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
        let fen = "";
        let valid = false;
        let attempts = 0;
        const maxAttempts = 5000;

        const chess = new Chess();

        while (!valid && attempts < maxAttempts) {
            attempts++;
            chess.clear();
            const occupiedSquares = new Set();

            const getRandomSquare = () => {
                let sq;
                do {
                    let f = Math.floor(Math.random() * 8);
                    let r = Math.floor(Math.random() * 8) + 1;
                    sq = files[f] + r;
                } while (occupiedSquares.has(sq));
                return sq;
            };

            // 1. Place Kings
            let wK = getRandomSquare();
            occupiedSquares.add(wK);
            chess.put({ type: 'k', color: 'w' }, wK);

            let bK = getRandomSquare();
            // Check touching immediately
            if (this.areKingsTouching(wK, bK)) {
                 continue; // Retry placement
            }
            occupiedSquares.add(bK);
            chess.put({ type: 'k', color: 'b' }, bK);

            // 2. Place other pieces
            let allPieces = [
                ...whitePieces.map(type => ({ type: type, color: 'w' })),
                ...blackPieces.map(type => ({ type: type, color: 'b' }))
            ];

            // LIMIT: Lichess only supports 7 pieces total (including 2 kings)
            // So we can only have 5 additional pieces max.
            if (allPieces.length > 5) {
                // Shuffle and take only 5
                allPieces = allPieces.sort(() => 0.5 - Math.random()).slice(0, 5);
            }

            let placementFailed = false;
            for (let piece of allPieces) {
                let sq;
                let tries = 0;
                do {
                    sq = getRandomSquare();
                    let rank = parseInt(sq[1]);
                    if (piece.type === 'p' && (rank === 1 || rank === 8)) sq = null;
                    tries++;
                } while (!sq && tries < 50);

                if (!sq) {
                    placementFailed = true;
                    break;
                }
                occupiedSquares.add(sq);
                chess.put({ type: piece.type, color: piece.color }, sq);
            }

            if (placementFailed) continue;

            // Generate FEN
            let rawFen = chess.fen().split(' ')[0];
            fen = `${rawFen} w - - 0 1`;

            // 3. Validate
            let validator = new Chess();
            const validation = validator.validate_fen(fen);
            if (!validation.valid) {
                if(attempts % 1000 === 0) console.log("Invalid FEN:", fen, validation.error);
                continue;
            }
            
            // Check if Opponent (Black) is in check. This is illegal if it's White's turn.
            // We construct a FEN with Black to move to test if Black is in check.
            let blackFen = `${rawFen} b - - 0 1`;
            let blackValidator = new Chess();
            blackValidator.load(blackFen);
            if (blackValidator.in_check()) {
                 // Black is in check, but it's supposed to be White's turn. Illegal.
                 if(attempts % 1000 === 0) console.log("Opposite check (Black in check)");
                 continue;
            }

            validator.load(fen);
            if (validator.game_over()) continue;
            
            // Also check if White is already in Checkmate? 
            // validator.game_over() covers Mate and Stalemate.
            
            valid = true;
        }

        if (!valid) {
            console.error("Last FEN tried:", fen);
            throw new Error("Could not generate a legal position after " + maxAttempts + " attempts.");
        }
        return fen;
    }

    findPieceSquare(chessInstance, type, color) {
        const board = chessInstance.board();
        for (let r = 0; r < 8; r++) {
            for (let c = 0; c < 8; c++) {
                let p = board[r][c];
                if (p && p.type === type && p.color === color) {
                    return this.coordsToSquare(r, c);
                }
            }
        }
        return null;
    }

    coordsToSquare(row, col) {
        const files = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
        return files[col] + (8 - row);
    }

    areKingsTouching(sq1, sq2) {
        let f1 = sq1.charCodeAt(0), r1 = parseInt(sq1[1]);
        let f2 = sq2.charCodeAt(0), r2 = parseInt(sq2[1]);
        return Math.abs(f1 - f2) <= 1 && Math.abs(r1 - r2) <= 1;
    }
}
// Expose
window.PositionGenerator = PositionGenerator;