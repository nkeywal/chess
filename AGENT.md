# Agent Instructions

This document outlines the rules and environment constraints for any AI agent working on this repository.

## Environment & Tools
- **Python Virtual Environment**: A virtual environment is located in `.venv/`. Always ensure it is activated or use the interpreters within `.venv/bin/` for running scripts.
- **Tablebases**: Gaviota tablebases are expected in the `gaviota/` directory for any data generation tasks.

## Constraints
- **Git Usage**: Do NOT execute any `git` commands (commit, push, etc.) unless explicitly requested by the user. The user normally manages the repository state.
- **Language**: All source code, documentation, and code comments MUST be in **English**.
- **Coding Style**: Do NOT write unnecessary defensive code. Focus on clarity and performance while assuming valid internal state where appropriate.
- **Data Integrity**: Never modify files in `data/` manually. Always use the provided generation and downsampling scripts to maintain consistency.

## Workflow
1. Use `generate_positions.py` for adding new endgame types.
2. Use `downsample_positions.py` to keep file sizes under 600KB.
3. Always run `gen_versions.py` after any data change to update `manifest.json`.
