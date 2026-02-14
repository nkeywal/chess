# Trivial Endgames

Trivial Endgames is a web-based tool for training and analyzing chess endgames with 3 to 5 pieces. It uses Gaviota tablebases and the Lichess Tablebase API to provide theoretical evaluations and perfect defense.

## 1. Project Overview

The project provides a platform to evaluate endgame positions and play them against an engine that follows tablebase-optimal moves. It supports various material configurations (e.g., KRP vs KR, KP vs K) and tracks user performance through a streak system. The system relies on pre-generated position files and real-time API probes for move validation and analysis.

## 2. HTML Interface and Frontend

The frontend is a single-page application built with standard web technologies (HTML, CSS, JavaScript) and utilizes `cm-chessboard` for the UI and `chess.js` for move logic.

- **Interaction**: Users can evaluate a position as a Win, Draw, or Loss. They can also play the position manually against the computer. The "guess" evaluation mode is fully functional offline once the position data for the selected endgame has been loaded into the browser.
- **Analysis Mode**: When enabled, the interface overlays move evaluations on the board squares. It displays the outcome (Win/Draw/Loss) and the Distance to Mate (DTM) for each legal move using SVG markers.
- **State Management**: The application uses `localStorage` to persist the current endgame selection and the user's score streak.
- **Data Loading**: Positions are loaded from a `data/` directory. A `manifest.json` file is used to manage versioning and cache busting for the position files.
- **Service Worker**: A service worker (`sw.js`) provides offline capabilities and caching for the application assets.
- **Move Policies**: Specific JavaScript modules (`policy_*.js`) implement move selection logic for the computer. These policies handle tie-breaking when multiple optimal moves are available, such as preferring non-capture draws or specific defensive setups.

## 3. Backend and Data Generation

The backend consists of Python scripts used to generate and filter the endgame positions served to the frontend.

- **Position Generation**: `generate_positions.py` iterates through legal square placements for a given material set. It enforces chess legality (no adjacent kings, no pawns on 1st/8th ranks, side to move not in check).
- **Tablebase Probing**: The generator probes Gaviota tablebases using the `python-chess` library to retrieve WDL and DTM data for each position.
- **Filtering**: `filters.py` contains logic to exclude trivial or redundant positions, ensuring the generated datasets are relevant for training.
- **Data Format**: Positions are stored in a compact format where each piece's square is represented by a single character from a 64-character alphabet, followed by a single character representing the outcome (W, L, or D).
