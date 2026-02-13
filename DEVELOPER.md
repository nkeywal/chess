# Developer Guide

This document describes the workflow for regenerating and updating the endgame position datasets.

## Data Regeneration Workflow

The process consists of three main steps: generating the raw positions, downsampling large files for web performance, and updating the manifest file for the frontend.

### 1. Generate Positions

Use `generate_positions.py` to create new position files for specific material combinations. This script requires Gaviota tablebases to be available in the `gaviota/` directory.

```bash
# Example: Generate positions for Rook vs Pawn
python3 generate_positions.py --w KR --b KP
```

The output will be saved in the `data/` directory (e.g., `data/KR_KP.txt`).

### 2. Downsample Positions

To ensure the web application remains responsive and avoids excessive memory usage, large position files should be downsampled. The `downsample_positions.py` script renames files exceeding a threshold to `*.full.txt` and creates a smaller version with a random selection of records.

The recommended threshold and target size is 600KB.

```bash
# Downsample files in the data directory
python3 downsample_positions.py --max-bytes 600000 --target-bytes 600000
```

### 3. Update Manifest

The frontend relies on `data/manifest.json` to identify available endgame files and their versions (hashes). This file must be regenerated whenever files in the `data/` directory are added or modified.

```bash
python3 gen_versions.py
```

## Architecture and Design

### Position Filtering (`filters.py`)

The filtering system is the core of the data selection process. It ensures that generated positions are pedagogically valuable and prevents the dataset from being flooded with trivial or repetitive scenarios. Filtering happens in two main stages:

1.  **Stage A: No-TB Filtering (`filter_notb_generic` and material-specific equivalents)**:
    - Executed *before* probing the tablebases.
    - Used for cheap checks like ensuring a minimum number of legal moves.
    - Material-specific filters (e.g., `filter_notb_kp_vs_k`) apply specific positional constraints (e.g., king distance to pawn).

2.  **Stage B: TB Filtering (`filter_tb_generic` and material-specific equivalents)**:
    - Executed *after* the tablebase outcome (WDL/DTM) is known.
    - Allows for logic based on the "quality" of the win or draw.
    - Can probe child moves (via `probe_move`) to identify blunders, saving moves, or unique solutions.

### Generation Hints

The "hints" mechanism is a performance optimization defined in `filters.py` via functions named `gen_hints_<material_key>`. These hints are used by `generate_positions.py` to prune the square placement search space *before* a board object is even created.

Supported hints include:
- `piece_masks`: Bitmasks to restrict pieces to specific squares or ranks.
- `wk_to_pawn_cheb` / `bk_to_pawn_cheb`: Chebyshev distance constraints between kings and pawns.
- `bishops_same_color`: Ensures bishops are on the same square color for relevant endgames.

## Summary of Tools

- **`generate_positions.py`**: Generates valid chess positions and probes tablebases for outcomes.
- **`downsample_positions.py`**: Reduces file sizes for web deployment by random sampling.
- **`gen_versions.py`**: Calculates SHA-256 hashes for all `.txt` files in `data/` and updates `manifest.json`.
