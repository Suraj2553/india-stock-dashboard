# India Stock Dashboard

A self-hosted, real-time Indian stock market dashboard — portfolio tracker, live charts, NSE screener, F&O option chains, mutual funds, AI-powered signal scanner, and AI chat. No paid APIs required.

---

## Features

| Tab | What it does |
|---|---|
| **Dashboard** | Portfolio summary, live P&L, allocation pie chart, top movers |
| **Portfolio** | Full holdings table with live prices, returns, and sector breakdown |
| **Charts** | Candlestick / area charts for any NSE stock, 1D–1Y timeframes, portfolio vs Nifty comparison |
| **Market** | Nifty 50 live chart, sector heatmap, market breadth, FII/DII data |
| **News** | Live headlines from Economic Times, MoneyControl, LiveMint — filter by source |
| **AI Chat** | Ask anything — portfolio analysis, stock research, market questions (needs GitHub PAT) |
| **Screener** | All NSE stocks filtered by index/sector, ETFs, Mutual Funds, F&O option chains, FII/DII flows, All Indices |
| **Signal Scanner** | 15-indicator technical analysis (RSI, MACD, Ichimoku, ADX, OBV, Fibonacci, Stochastic, Parabolic SAR + more) with batch scan across Nifty 50/100, Midcap, sector lists |
| **Settings** | Add/edit/delete holdings directly in the browser |

---

## Requirements

| Requirement | Version |
|---|---|
| **Python** | **3.10 or higher** (see version notes below for 3.8 / 3.9) |
| Browser | Any modern browser (Chrome, Edge, Firefox) |
| Internet | Required — fetches live data from Yahoo Finance, NSE, AMFI |
| GitHub account | Required for AI Chat only |

---

## Quick Start

### 1. Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Set up your GitHub PAT (for AI Chat)

A `.env` file is included in the root folder. Open it and replace the placeholder:

```
GITHUB_TOKEN=your_github_pat_here   ← replace this
```

To get a token:
1. Go to **github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Give it any name, select **no scopes** (no checkboxes), click **Generate token**
4. Copy the token (starts with `ghp_...`) and paste it into `.env`

> If you skip this step, everything works **except** the AI Chat tab.

### 3. Add your holdings (optional)

Edit `data/holdings.json` with your stocks and mutual funds, or use the **Settings tab** inside the app.

See `imports/SAMPLE_stocks_format.csv` and `imports/SAMPLE_mf_format.csv` for the format.

To import from a Groww CSV export, run:
```bash
python scripts/parse_groww.py your_groww_export.csv
```

### 4. Run the server

**Windows:**
```
start.bat
```

**Mac / Linux:**
```bash
bash start.sh
```

Then open your browser at: **http://localhost:8080**

---

## Python Version Compatibility

### Python 3.12 / 3.11 / 3.10 — Recommended
Everything works out of the box. No changes needed.

---

### Python 3.9
The codebase uses `X | Y` union type hints (e.g. `dict | list`) which require 3.10+. Fix with one line at the top of two files:

**`backend/fno.py`** and **`backend/screener.py`** and **`backend/predict.py`** — add at the very top:
```python
from __future__ import annotations
```

Also in **`backend/market_data.py`**, change:
```python
async def fetch_quotes_batch(symbols: list[str]) -> dict:
async def fetch_top_movers(symbols: list[str]) -> dict:
```
to:
```python
async def fetch_quotes_batch(symbols: list) -> dict:
async def fetch_top_movers(symbols: list) -> dict:
```

---

### Python 3.8
Do all of the Python 3.9 steps above, **plus** these additional changes:

**`backend/fno.py`** — change:
```python
async def _nse_get(endpoint: str) -> dict | list:
async def _nse_get_oc(endpoint: str) -> dict | list:
```
to:
```python
from typing import Union
async def _nse_get(endpoint: str) -> Union[dict, list]:
async def _nse_get_oc(endpoint: str) -> Union[dict, list]:
```

**`backend/screener.py`** — change:
```python
async def get_fund_data(scheme: dict) -> dict | None:
```
to:
```python
from typing import Optional
async def get_fund_data(scheme: dict) -> Optional[dict]:
```

**`backend/predict.py`** — change all `Optional[float]` and `Optional[dict]` return hints (they already use `Optional` from `typing` — just ensure the import is present at the top):
```python
from typing import Optional
```

> **Tip:** The simplest solution is to upgrade Python. Python 3.12 is the current stable release and is a free download from [python.org](https://python.org).

---

## Signal Scanner

The Signal Scanner tab runs a full technical analysis on any list of stocks using **15 institutional-grade indicators**:

| Indicator | What it detects |
|---|---|
| RSI (14) | Overbought / Oversold momentum |
| MACD (12,26,9) | Trend crossovers — high-probability entries |
| Bollinger Bands | Volatility squeeze + mean reversion |
| Stochastic %K/%D | Short-term reversal + crossover signals |
| ADX + DI | Trend strength — filters choppy vs trending |
| CCI (20) | Commodity channel divergences |
| OBV | Smart money accumulation / distribution |
| Parabolic SAR | Trailing stop + trend reversal trigger |
| Ichimoku Cloud | Triple confirmation (price, TK cross, cloud) |
| Fibonacci (60d) | Key retracement levels |
| SMA 20 / 50 / 200 | Short / medium / long-term trend |
| Golden / Death Cross | SMA50 vs SMA200 structural signal |
| Volume analysis | Institutional buying/selling confirmation |
| Momentum | 1M / 3M / 6M price returns |

Each stock gets a **score from 0–100** and a verdict: **Strong Buy / Buy / Neutral / Sell / Strong Sell**.

Click any row to open the full analysis popup with Fibonacci levels, Ichimoku values, and ATR-based price targets.

---

## F&O Option Chain Notes

NSE India's option chain API requires browser-like session cookies and works best during **market hours (Mon–Fri, 9:15–15:30 IST)**. Outside market hours, the app shows the Nifty/BankNifty spot price (sourced from Yahoo Finance as fallback) but the option chain table may be unavailable.

To get option chain data:
1. Open the **Screener → F&O** tab during market hours
2. Click the **refresh button** (↻) — the app re-seeds the NSE session on each refresh

---

## File Structure

```
india-stock-dashboard/
├── .env                 # Add your GitHub PAT here
├── backend/
│   ├── main.py          # FastAPI server + all API endpoints
│   ├── market_data.py   # Yahoo Finance integration
│   ├── portfolio.py     # Portfolio P&L calculations
│   ├── screener.py      # NSE stock screener, ETF, mutual fund data
│   ├── fno.py           # F&O option chains, FII/DII, NSE indices
│   ├── predict.py       # 15-indicator technical analysis engine
│   ├── ai_chat.py       # GitHub Models / GPT-4o AI chat
│   ├── news.py          # RSS news aggregator
│   ├── utils.py         # Shared helpers
│   └── requirements.txt
├── frontend/
│   └── index.html       # Entire frontend (single file, no build step)
├── data/
│   └── holdings.json    # Your portfolio (edit this or use Settings tab)
├── imports/
│   ├── SAMPLE_stocks_format.csv
│   └── SAMPLE_mf_format.csv
├── scripts/
│   └── parse_groww.py   # Import holdings from Groww CSV export
├── start.bat            # Windows launcher
└── start.sh             # Mac / Linux launcher
```

---

## Troubleshooting

**Port already in use**
Add `--port 8081` to the uvicorn command in `start.bat` / `start.sh`, then open `http://localhost:8081`.

**NSE data not loading (F&O / All Indices)**
NSE blocks automated requests intermittently. Click the refresh button and try again. Works most reliably during market hours.

**Mutual fund data missing**
AMFI (mfapi.in) is a free public API and can be slow. Give it 10–15 seconds on first open.

**AI Chat says "unauthorized"**
Your GitHub PAT has expired or is invalid. Generate a new one (no scopes needed) and update `.env`.

**Signal Scanner is slow**
It fetches 1 year of daily OHLCV history for each stock and runs 15 indicators per stock. Nifty 50 scan (~50 stocks) typically takes 20–40 seconds depending on your connection.

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| Yahoo Finance | Stock prices, OHLCV history, indices | Free |
| AMFI / mfapi.in | Mutual fund NAV | Free |
| NSE India (public API) | F&O option chains, FII/DII, all indices | Free |
| RSS feeds | Market news (ET, MoneyControl, LiveMint) | Free |
| GitHub Models (GPT-4o) | AI chat | Free with GitHub account |

**No paid API keys required.**
