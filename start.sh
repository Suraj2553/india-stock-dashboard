#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "  +------------------------------------------+"
echo "  |   MARKET MONITOR  v4.0                  |"
echo "  |   Engine v4 | Buy Ideas | E-mail scans   |"
echo "  +------------------------------------------+"
echo ""

# Check Python is installed
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 not found. Install it with: brew install python3 (macOS) or sudo apt install python3 (Linux)"
    exit 1
fi
echo "[OK] $(python3 --version)"

# Detect broken venv (e.g. after OS reinstall / Python upgrade)
if [ -d "$ROOT/.venv" ]; then
    if ! "$ROOT/.venv/bin/python3" -c "import sys" &>/dev/null; then
        echo "[!] Virtual environment is broken (Python was reinstalled). Recreating..."
        rm -rf "$ROOT/.venv"
    fi
fi

# Create venv if missing
if [ ! -f "$ROOT/.venv/bin/python3" ]; then
    echo "[.] Creating virtual environment..."
    python3 -m venv "$ROOT/.venv"
    echo "[OK] Virtual environment created"
fi

echo "[.] Checking dependencies..."
"$ROOT/.venv/bin/python3" -m pip install --upgrade pip --quiet
"$ROOT/.venv/bin/python3" -m pip install fastapi "uvicorn[standard]" httpx "pydantic>=2.11.0" tradingview-screener --quiet
echo "[OK] Dependencies ready"

# Config files (created from the samples on first run)
[ -f "$ROOT/.env" ] || { cp "$ROOT/.env.example" "$ROOT/.env"; echo "[OK] created .env from .env.example"; }
[ -f "$ROOT/data/holdings.json" ] || { cp "$ROOT/data/holdings.sample.json" "$ROOT/data/holdings.json"; echo "[OK] created data/holdings.json from sample - add your holdings in Settings"; }
if [ ! -f "$ROOT/.mcp.json" ] && [ -f "$ROOT/.mcp.template.json" ]; then
    sed "s#__TRADINGVIEW_MCP__#$ROOT/.venv/bin/tradingview-mcp#g" "$ROOT/.mcp.template.json" > "$ROOT/.mcp.json"
fi
mkdir -p "$ROOT/data/scans"

# Auto-import any Groww CSV files
mkdir -p "$ROOT/imports"
CSV_COUNT=$(ls "$ROOT/imports/"*.csv "$ROOT/imports/"*.CSV 2>/dev/null | wc -l)
if [ "$CSV_COUNT" -gt 0 ]; then
    echo "[.] Found CSV files in imports/ - importing..."
    "$ROOT/.venv/bin/python3" "$ROOT/scripts/parse_groww.py" --auto
    echo ""
fi

echo ""
echo "  Dashboard  -> http://localhost:8080"
echo "  Portfolio  -> http://localhost:8080  (Portfolio tab)"
echo "  Charts     -> http://localhost:8080  (Charts tab)"
echo "  AI Chat    -> http://localhost:8080  (AI Chat tab)"
echo ""
echo "  Prices update every 5 seconds automatically."
echo "  Press Ctrl+C to stop."
echo ""

cd "$ROOT/backend"
"$ROOT/.venv/bin/python3" main.py
