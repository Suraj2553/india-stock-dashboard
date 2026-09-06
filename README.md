# Market Monitor v4 — India Stock Dashboard

A self-hosted, real-time Indian stock market dashboard — portfolio tracker, live charts, NSE screener, F&O option chains, mutual funds, a 30-indicator technical engine with **trade plans and forecasts**, a **Buy Ideas** page fed by **scheduled scans that e-mail you twice a day**, and an AI advisor that can run all of it for you. No paid APIs required.

---

## Features

| Tab | What it does |
|---|---|
| **Dashboard** | Portfolio summary, live P&L, latest Buy Ideas strip, indices incl. India VIX, top movers, news, AI morning briefing |
| **🎯 Buy Ideas** | Latest scan: market regime, ranked high-conviction buys with entry / stop / T1 / T2 / R:R / profit projection, watch-list, action plan for every holding, scan history. Run a scan on any universe, e-mail it to yourself |
| **Portfolio** | Holdings with live prices, returns, sector breakdown and an engine **signal score per holding** |
| **Charts** | Candlestick / area charts for any NSE stock, 1D–1Y, full Engine v4 panel, portfolio vs Nifty |
| **Market** | Nifty 50 chart, indices, breadth, gainers / losers |
| **News** | Economic Times, MoneyControl, LiveMint, Business Standard — de-duplicated, newest first |
| **AI Chat** | GPT-4o / GPT-4.1 with tools: runs the scanner, the full engine, option chain, FII/DII, news, your P&L |
| **Screener** | Index / sector screener, ETFs, mutual funds, F&O option chains, FII/DII, all NSE indices, metals |
| **Screener → Signal Scanner** | Engine v4 on any universe (Nifty 50/100, Next 50, Midcap, Smallcap, 14 sectors, your holdings, everything, custom list) with progress, sortable trade-plan columns, setup filters, CSV export |
| **Settings** | GitHub token status, holdings editor, **e-mail alert / scheduler configuration**, data sources |

---

## Quick Start (one-click setup)

The setup script installs everything: **Python 3.12, Node.js LTS, Ollama + a local AI model, all Python dependencies**, and creates the config files (`.env`, `data/holdings.json`, AI provider, `.mcp.json`). Re-running it is safe; it only fills in what is missing.

**Windows 10/11** — double-click `setup.bat` (uses `winget`; allow the UAC prompts):
```
setup.bat                 # full install
setup.bat -Scheduler      # + register the 08:45 / 15:45 scan jobs in Task Scheduler
setup.bat -SkipOllama     # without the local AI model
```

**macOS / Linux** (installs Homebrew on a Mac if missing):
```bash
bash setup.sh                 # full install
bash setup.sh --skip-ollama   # without the local AI model
bash setup.sh --model qwen2.5:7b
```

Then start the dashboard: `start.bat` (Windows) or `bash start.sh` → **http://localhost:8080**. Add your holdings in **Settings → Holdings Editor** (or drop a Groww CSV export into `imports/`), set up e-mail, and hit **Buy Ideas → Run scan now**.

Manual alternative: Python 3.10+, `pip install -r backend/requirements.txt tradingview-mcp-server`, copy `.env.example` → `.env` and `data/holdings.sample.json` → `data/holdings.json`.

### AI Chat provider — local by default

After setup the chat runs on **Ollama** on your own machine (model `mm-llama3.2`, an 8K-context build of llama3.2): private, free, no key — but slow on a laptop without a GPU (1–5 minutes per answer). If you prefer speed over privacy, switch to a cloud provider in **Settings → AI Chat provider**; all of these have free tiers with no card (GitHub Models, the previous backend, was shut down on 30 July 2026):

| Provider | Get a key | Free tier | Base URL (preset in Settings) |
|---|---|---|---|
| **Groq** (recommended, fastest) | [console.groq.com/keys](https://console.groq.com/keys) — sign in with GitHub/Google | ≈1,000 requests/day | `https://api.groq.com/openai/v1` |
| **Google Gemini** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | ≈1,500 requests/day (Flash) | `https://generativelanguage.googleapis.com/v1beta/openai` |
| OpenRouter free models | [openrouter.ai/keys](https://openrouter.ai/keys) | 50 requests/day | `https://openrouter.ai/api/v1` |
| Ollama (local, offline) | [ollama.com](https://ollama.com/download) | unlimited, no key | `http://localhost:11434/v1` |

Open **Settings → AI Chat provider**, choose the preset, paste the key, *Save*, *Test chat*. Any OpenAI-compatible server works (also via `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` in `.env`). Everything except AI Chat / morning briefing works without any provider.

**Privacy note / local-only setup.** The chat sends your holdings and the engine's analysis text to whichever provider is selected. If that must never leave the laptop, use the **Ollama** preset: install Ollama, run `ollama pull llama3.2` (3B, tool-calling capable; ~2 GB), and optionally build the 8K-context variant used by this project: `ollama create mm-llama3.2 -f tools/ollama/Modelfile.llama3.2`. The dashboard starts Ollama automatically when the Ollama preset is selected. On a CPU-only laptop expect 30–90 s per answer; `qwen2.5:7b` is smarter but about half the speed.

### 3. Add your holdings

Use **Settings → Open Holdings Editor** (or `/edit_holdings.html`), edit `data/holdings.json`, or drop a Groww CSV export into `imports/` and restart.

### 4. Run

Windows: `start.bat` · Mac/Linux: `bash start.sh` → open **http://localhost:8080**

---

## Scheduled scans + e-mail (twice a day)

1. **Settings → Scheduled scans & e-mail alerts**: enter the recipient, your SMTP user and password, save, click *Send test e-mail*.
   - Gmail: turn on 2-Step Verification, create an **App Password** (Google Account → Security → App passwords → "Mail") and paste that — your normal password will not work. Host `smtp.gmail.com`, port `587`.
   - Outlook: `smtp.office365.com` : 587 · Yahoo: `smtp.mail.yahoo.com` : 465.
   - The same values can be put in `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `ALERT_EMAIL_TO`).
2. Pick the universe (default Nifty 100), the times (default **08:45** pre-open plan and **15:45** post-close review, IST), the minimum score for a "buy" and the capital used for position sizing / profit projections.
3. That's it. While the dashboard is running, the scheduler checks every minute. **If the laptop was off at a scheduled time, the scan runs as soon as the dashboard is next started** (catch-up), so you still get your two reports on late-boot days. Results also appear instantly in the **Buy Ideas** tab.

To run **without** the dashboard open, register Windows Task Scheduler jobs (they also catch up after a late boot):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_scheduler.ps1
```
Remove with `scripts\uninstall_scheduler.ps1`. Manual run: `python scripts\daily_scan.py` (`--universe nifty50`, `--no-email`).

Each e-mail contains: market regime (Nifty technical score, breadth, VIX), the ranked buys with entry / stop-loss / Target 1 / Target 2 / reward-to-risk / projected profit on your capital / the stock's own historical hit-rate for similar days, a watch-list of names near a trigger, and an action for every holding (Add / Hold / Tighten stop / Trim / Exit).

---

## TradingView data (backend/tradingview.py)

The `tradingview-screener` package reads the same public scanner endpoint TradingView's website uses — no account, no key. One call returns ~700 NSE stocks with TradingView's **Technical Rating** (Strong Buy … Strong Sell, from its MA + oscillator votes), RSI, MACD, ADX, Stochastic, CCI, Williams %R, ATR, 1W/1M/3M/6M/1Y performance, relative volume, SMA/EMA, Bollinger bands, 52-week range, market cap, sector and industry.

Where it shows up: a **📺 TradingView** screener tab with presets (TV Strong Buy, 52-week breakout, momentum leaders, volume surge, golden trend, oversold bounce, tight Bollinger, Strong Sell); a **TV** column in the Signal Scanner; a TV badge on Buy Ideas cards, in stock popups and in the e-mail — an independent second opinion next to Engine v4. API: `GET /api/tv/screen?preset=strong_buy&min_mcap_cr=1000`, `GET /api/tv/ratings?symbols=TCS,INFY`.

## MCP connectors (for Claude Code / Claude Desktop / Cursor)

`.mcp.json` in the project root pre-configures three free MCP servers so an AI assistant working in this folder can reach your real accounts and TradingView:

| Server | What it gives the assistant | Login |
|---|---|---|
| **Groww MCP** — `https://mcp.groww.in/mcp` (official) | Your Groww holdings, portfolio composition, F&O positions with P&L, order placement | Browser OAuth on first use; Node.js 22+ |
| **Kite MCP** — `https://mcp.kite.trade/mcp` (official Zerodha) | Holdings, positions, MF holdings, margins, quotes, historical data, GTT orders | Browser login via Kite; Node.js |
| **tradingview-mcp** (`uvx --from tradingview-mcp-server tradingview-mcp`) | 37 tools: screeners, technical analysis, candlestick patterns, backtests | None; needs `uv` installed |

Remove the entries you do not use. These connectors are for the AI assistant you chat with in your editor — the dashboard itself talks to Yahoo Finance, NSE, AMFI and TradingView directly.

## Engine v4 (backend/predict.py)

Pure Python, no numpy. 2 years of daily OHLCV from Yahoo Finance (10-minute cache). Every stock gets:

| Category (weight) | Indicators |
|---|---|
| **Trend** (32%) | SMA 20/50/150/200 with 200-DMA slope, Golden/Death cross, EMA ribbon 8/13/21/34/55, Supertrend (10,3), Ichimoku with properly displaced cloud + Chikou, ADX/DI, Parabolic SAR |
| **Momentum** (23%) | RSI(14) with divergence, MACD, Stochastic %K/%D, CCI, Williams %R, ROC, Aroon, TTM Squeeze (Bollinger inside Keltner) + momentum, 1W/1M/3M/6M/1Y returns |
| **Money flow** (15%) | OBV + divergence, MFI, Chaikin Money Flow, rolling VWAP, volume vs 20-day average, accumulation/distribution day count |
| **Relative strength** (10%) | 1M/3M/6M excess return vs Nifty 50, RS-line slope, beta, correlation |
| **Setup** (20%) | Donchian 20/55 breakouts, 52-week position, pullback-to-EMA21 in uptrend, higher-high/higher-low structure, Minervini trend template (8 rules), volatility contraction, Larry Williams Blast-Off coil, candlestick patterns |

- **Score 0–100** and verdict (Strong Buy ≥75, Buy ≥62, Neutral, Sell <45, Strong Sell <30). Bear-regime stocks are capped at 55 unless a confirmed reversal setup exists; choppy tape (ADX<15) discounts signals.
- **Setup label**: Breakout · Momentum Leader · Pullback Buy · Squeeze Breakout · Trend Continuation · Oversold Reversal · Wait — Choppy · Avoid — Downtrend.
- **Trade plan**: entry note, stop (tightest of 2.2×ATR / 10-day swing low / Supertrend), Target 1 (≥2 ATR or nearest resistance), Target 2 (≥4 ATR or 52-week high), reward-to-risk, trailing rule, horizon, position size at 1% risk.
- **Forecast**: the same engine is run at ~45 past dates in the last year; the average and hit-rate of the 21-session forward return on days that scored like today are reported alongside the baseline. It is a probability estimate from the stock's own history, not a guarantee.

API: `GET /api/predict/{symbol}?capital=500000`, `POST /api/predict/batch {"symbols":[...],"forecast":true}`, `GET /api/scan/universes`, `POST /api/scan/run`, `GET /api/scan/latest`, `GET /api/scan/status`, `GET/POST /api/alerts/config`.

---

## File Structure

```
├── .env                     # GitHub PAT + optional SMTP settings
├── backend/
│   ├── main.py              # FastAPI server, all endpoints, background scheduler
│   ├── predict.py           # Engine v4: indicators, scoring, trade plan, forecast
│   ├── alerts.py            # Scheduled scans, report builder, e-mail, scheduler
│   ├── market_data.py       # Yahoo Finance quotes/history (cached), indices, VIX
│   ├── portfolio.py         # Portfolio P&L
│   ├── screener.py          # Index / sector / ETF / MF lists
│   ├── fno.py               # NSE option chain, FII/DII, all indices (cached)
│   ├── ai_chat.py           # AI chat with tools (scanner, engine, F&O…) — Groq / Gemini / any OpenAI-compatible
│   ├── tradingview.py       # TradingView technical rating + screener presets (NSE)
│   ├── news.py, metals.py, utils.py
├── frontend/index.html      # Entire UI (single file)
├── data/holdings.json       # Your portfolio
├── data/alerts_config.json  # E-mail / scheduler settings (created on save)
├── data/llm_config.json     # AI provider + key (created on save)
├── .mcp.json                # Groww / Kite / TradingView MCP connectors for AI editors
├── data/scans/              # latest.json + dated reports + scheduler state
├── scripts/
│   ├── daily_scan.py        # CLI scan + e-mail (for Task Scheduler)
│   ├── install_scheduler.ps1 / uninstall_scheduler.ps1
│   └── parse_groww.py       # Import holdings from Groww CSV
├── edit_holdings.html       # Holdings editor
├── start.bat / start.sh
```

---

## Publishing / contributing

`.gitignore` keeps personal data out of git: `.env`, `data/holdings.json`, `data/llm_config.json`, `data/alerts_config.json`, `data/scans/`, the venv, the machine-specific `.mcp.json` and anything under `tools/node*`. Ship `.env.example`, `data/holdings.sample.json` and `.mcp.template.json` instead; setup recreates the real files on each machine. `python push_to_github.py` pushes a clean copy (token from the `GITHUB_PUSH_TOKEN` environment variable — never store tokens in files).

## Troubleshooting

- **Port in use** → change `port=8080` in `backend/main.py` and open that port.
- **NSE data (F&O / All Indices / FII-DII)** → NSE blocks automated requests intermittently; retry, works best in market hours. Responses are cached 60 s.
- **Scan slow** → Nifty 100 with forecasts ≈ 40–60 s; untick *Forecast* in the scanner for a 3× faster pass. Scheduled scans do a fast pass on the whole universe and a full pass on the best 15–25 names.
- **E-mail not sent** → use an App Password (Gmail), check port (587 STARTTLS / 465 SSL), then *Send test e-mail*. The reason is shown in Settings and in the scan report.
- **AI Chat offline** → Settings → AI Chat provider: pick Groq or Gemini, paste a free key, *Test chat*. GitHub Models no longer exists.
- **TradingView tab empty** → `pip install tradingview-screener` in the venv (start.bat does this) or TradingView is rate-limiting; retry in a minute.
- **Symbol not found** → Yahoo uses NSE symbols (`M&M`, `BAJAJ-AUTO`); a few aliases are mapped in `market_data.py`.

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| Yahoo Finance | Prices, OHLCV history, indices, VIX, metals, USD/INR | Free |
| AMFI / mfapi.in | Mutual fund NAV | Free |
| NSE India (public API) | Option chains, FII/DII, all indices | Free |
| RSS feeds | Market news | Free |
| TradingView scanner (public endpoint) | Technical rating, indicators, performance for ~700 NSE stocks | Free, no login |
| Groq / Google Gemini / OpenRouter / Ollama | AI chat / briefing (any OpenAI-compatible API) | Free tiers, no card |
| Your SMTP server | E-mail alerts | Free (Gmail App Password etc.) |

Signals are probabilities, not promises. Not investment advice — size positions so that a stop-out costs ≤1% of capital.
