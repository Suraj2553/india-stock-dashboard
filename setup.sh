#!/usr/bin/env bash
# setup.sh — one-shot installer for Market Monitor (macOS / Linux)
#
#   bash setup.sh                 # full install (Python, Node, Ollama + model, deps, config)
#   bash setup.sh --skip-ollama   # no local AI model
#   bash setup.sh --model qwen2.5:7b
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
MODEL="llama3.2"; SKIP_OLLAMA=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-ollama) SKIP_OLLAMA=1 ;;
    --model) MODEL="$2"; shift ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac; shift
done

step() { printf '\n\033[36m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '   \033[33m[!!]\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
OS="$(uname -s)"

echo
echo "  +------------------------------------------------+"
echo "  |   MARKET MONITOR v4  --  automatic setup       |"
echo "  +------------------------------------------------+"

# ── package manager ─────────────────────────────────────────────────────
pkg_install() {   # pkg_install <brew-name> <apt-name>
  if [ "$OS" = "Darwin" ]; then
    brew list "$1" >/dev/null 2>&1 || brew install "$1"
  elif have apt-get; then
    sudo apt-get install -y "$2"
  elif have dnf; then
    sudo dnf install -y "$2"
  else
    warn "no supported package manager - install $1 manually"; return 1
  fi
}
if [ "$OS" = "Darwin" ] && ! have brew; then
  step "Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi
[ "$OS" = "Linux" ] && have apt-get && sudo apt-get update -qq

# ── 1. Python ───────────────────────────────────────────────────────────
step "Python 3.10+"
PY=""
for c in python3.12 python3.11 python3.10 python3 python; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  pkg_install python@3.12 python3.12 || pkg_install python3 python3
  PY="$(command -v python3.12 || command -v python3)"
fi
[ "$OS" = "Linux" ] && have apt-get && sudo apt-get install -y python3-venv python3-pip >/dev/null 2>&1 || true
ok "using $PY ($("$PY" --version))"

# ── 2. Node.js >= 20 (Groww MCP / mcp-remote) ───────────────────────────
step "Node.js (>= 20)"
NODE_OK=0
if have node; then
  NV="$(node --version | tr -d v | cut -d. -f1)"
  if [ "$NV" -ge 20 ]; then NODE_OK=1; ok "node $(node --version)"; else warn "node $(node --version) too old for mcp-remote (needs 20+)"; fi
fi
if [ "$NODE_OK" = 0 ]; then
  if [ "$OS" = "Darwin" ]; then pkg_install node nodejs
  elif have apt-get; then curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
  else pkg_install node nodejs || true; fi
  have node && ok "node $(node --version)" || warn "Node not installed - install from https://nodejs.org"
fi

# ── 3. venv + dependencies ──────────────────────────────────────────────
step "Python virtual-env + dependencies"
[ -x "$ROOT/.venv/bin/python3" ] || { "$PY" -m venv "$ROOT/.venv"; ok "created .venv"; }
"$ROOT/.venv/bin/python3" -m pip install --upgrade pip --quiet
"$ROOT/.venv/bin/python3" -m pip install -r "$ROOT/backend/requirements.txt" tradingview-mcp-server --quiet
ok "dependencies installed"

# ── 4. Config files ─────────────────────────────────────────────────────
step "Config files"
[ -f "$ROOT/.env" ] || { cp "$ROOT/.env.example" "$ROOT/.env"; ok "created .env"; }
[ -f "$ROOT/data/holdings.json" ] || { cp "$ROOT/data/holdings.sample.json" "$ROOT/data/holdings.json"; ok "created data/holdings.json from sample"; }
mkdir -p "$ROOT/data/scans" "$ROOT/imports"
TV="$ROOT/.venv/bin/tradingview-mcp"
sed "s#__TRADINGVIEW_MCP__#$TV#g" "$ROOT/.mcp.template.json" > "$ROOT/.mcp.json"
ok ".mcp.json generated (Groww / Kite / TradingView connectors for Claude Code & Cursor)"
chmod +x "$ROOT/start.sh" "$ROOT/scripts/"*.py 2>/dev/null || true

# ── 5. Ollama — private local AI ────────────────────────────────────────
if [ "$SKIP_OLLAMA" = 0 ]; then
  step "Ollama (local AI - your portfolio never leaves this machine)"
  if ! have ollama; then
    if [ "$OS" = "Darwin" ]; then brew install ollama; else curl -fsSL https://ollama.com/install.sh | sh; fi
  fi
  if have ollama; then
    if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null; then
      if [ "$OS" = "Darwin" ] && have brew; then brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
      else (nohup ollama serve >/dev/null 2>&1 &); fi
      for i in $(seq 1 30); do sleep 1; curl -s -m 2 http://localhost:11434/api/tags >/dev/null && break; done
    fi
    if curl -s -m 2 http://localhost:11434/api/tags >/dev/null; then
      ok "Ollama server running"
      echo "   pulling model '$MODEL' (about 2 GB, one time) ..."
      ollama pull "$MODEL"
      MF="$ROOT/tools/ollama/Modelfile.$MODEL"
      [ -f "$MF" ] || printf 'FROM %s\nPARAMETER num_ctx 8192\nPARAMETER temperature 0.4\n' "$MODEL" > "$MF"
      ollama create "mm-$MODEL" -f "$MF" >/dev/null
      ok "local model 'mm-$MODEL' ready (8K context)"
      [ -f "$ROOT/data/llm_config.json" ] || {
        printf '{\n  "preset": "ollama",\n  "base_url": "http://localhost:11434/v1",\n  "api_key": "",\n  "model": "mm-%s"\n}\n' "$MODEL" > "$ROOT/data/llm_config.json"
        ok "AI chat set to local Ollama (change in Settings -> AI Chat provider)"
      }
    else warn "Ollama did not start - run 'ollama serve' then 'ollama pull $MODEL'"; fi
  else warn "Ollama not installed - https://ollama.com/download"; fi
else
  warn "Skipped Ollama (--skip-ollama). Configure a provider in Settings -> AI Chat provider."
fi

echo
printf '\033[32m  Setup complete.\033[0m\n'
echo "  1. bash start.sh  ->  http://localhost:8080"
echo "  2. Settings tab: add your holdings, e-mail (Gmail App Password), check the AI provider"
echo "  3. Buy Ideas tab: Run scan now"
echo "  Scans without the dashboard open: add a cron entry, e.g."
echo "     45 8,15 * * 1-5  cd $ROOT && .venv/bin/python3 scripts/daily_scan.py"
echo
