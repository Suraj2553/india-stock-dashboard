#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
echo ""
echo "  ┌──────────────────────────────────────┐"
echo "  │   MARKET MONITOR  v2.0               │"
echo "  │   Real-time | Charts | AI Analysis   │"
echo "  └──────────────────────────────────────┘"
echo ""
if [ ! -d "$ROOT/.venv" ]; then python3 -m venv "$ROOT/.venv"; fi
source "$ROOT/.venv/bin/activate"
pip install -q fastapi "uvicorn[standard]" httpx "pydantic>=2.11.0"
mkdir -p "$ROOT/imports"
CSV_COUNT=$(ls "$ROOT/imports/"*.csv "$ROOT/imports/"*.CSV 2>/dev/null | wc -l)
if [ "$CSV_COUNT" -gt 0 ]; then
    echo "[.] Found CSV files in imports/ — importing..."
    python "$ROOT/scripts/parse_groww.py" --auto
fi
echo ""
echo "  Dashboard → http://localhost:8080"
echo "  Prices update every 5 seconds."
echo "  Ctrl+C to stop"
echo ""
cd "$ROOT/backend"
python main.py
