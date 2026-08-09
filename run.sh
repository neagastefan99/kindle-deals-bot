#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$HOME/kindle-deals-bot"
cd "$PROJECT_DIR"

# Activate venv and run
"$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/scraper.py"
