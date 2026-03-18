"""
main.py — Market Monitor FastAPI backend
Real-time SSE stream pushes portfolio updates every 5 seconds.
"""

import sys
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

# ── Manual .env loader (no python-dotenv needed) ──────────────────────────
def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val and key not in os.environ:
                os.environ[key] = val

_load_env()

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from portfolio import get_portfolio
from news import fetch_all_news, fetch_stock_news
from market_data import (fetch_history, fetch_technicals,
                          fetch_market_overview, fetch_top_movers,
                          fetch_quotes_batch, NIFTY50_SYMBOLS)
from ai_chat import check_health, chat, generate_morning_briefing, MODEL_PREFERENCE
from screener import (fetch_screener_stocks, fetch_etf_board, fetch_mf_category,
                       SECTOR_SYMBOLS, INDEX_MAP, MF_SCHEMES, ETF_CATEGORIES)
from fno import fetch_option_chain, fetch_fii_dii, fetch_all_indices, fetch_most_active_fno
from predict import fetch_prediction, batch_predict

app = FastAPI(title="Market Monitor", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"


# ── Health ────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return await check_health()


# ── Portfolio ─────────────────────────────────────────────────────────────
@app.get("/api/portfolio")
async def portfolio():
    try:
        return await get_portfolio()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── SSE stream — pushes portfolio every 5 seconds ─────────────────────────
@app.get("/api/stream")
async def stream():
    """Server-Sent Events: push live portfolio data every 5 seconds."""
    async def generator():
        while True:
            try:
                data = await get_portfolio()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(5)
    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ── Market overview ───────────────────────────────────────────────────────
@app.get("/api/market/overview")
async def market_overview():
    overview = await fetch_market_overview()
    movers   = await fetch_top_movers(NIFTY50_SYMBOLS[:20])
    return {"indices": overview, "movers": movers}


# ── Stock detail ──────────────────────────────────────────────────────────
@app.get("/api/stock/{symbol}")
async def stock_detail(symbol: str):
    symbol = symbol.upper()
    quotes, tech = await asyncio.gather(
        fetch_quotes_batch([symbol]),
        fetch_technicals(symbol),
    )
    return {
        "quote":      quotes.get(symbol),
        "technicals": tech,
    }


# ── History ───────────────────────────────────────────────────────────────
@app.get("/api/history/{symbol}")
async def history(symbol: str, period: str = "1Y"):
    return await fetch_history(symbol.upper(), period)


# ── Nifty50 history (for portfolio vs index comparison) ───────────────────
@app.get("/api/history/index/nifty50")
async def nifty_history(period: str = "1Y"):
    from market_data import BASE, HEADERS
    import httpx
    PERIOD_MAP = {
        "1D": ("1d","2m"), "1W": ("5d","15m"), "1M": ("1mo","1d"),
        "3M": ("3mo","1d"), "6M": ("6mo","1d"), "1Y": ("1y","1d"),
    }
    range_str, interval = PERIOD_MAP.get(period, ("1y", "1d"))
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                f"{BASE}/v8/finance/chart/%5ENSEI",
                headers=HEADERS,
                params={"range": range_str, "interval": interval}
            )
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        q0 = result["indicators"]["quote"][0]
        closes  = q0.get("close",  [])
        opens   = q0.get("open",   [])
        highs   = q0.get("high",   [])
        lows    = q0.get("low",    [])
        candles = []
        for i, ts in enumerate(timestamps):
            c = closes[i] if i < len(closes) else None
            o = opens[i]  if i < len(opens)  else None
            h = highs[i]  if i < len(highs)  else None
            l = lows[i]   if i < len(lows)   else None
            if c:
                candles.append({"t": ts * 1000, "o": round(o or c, 2),
                                 "h": round(h or c, 2), "l": round(l or c, 2),
                                 "c": round(c, 2)})
        return {"symbol": "NIFTY50", "period": period, "candles": candles}
    except Exception as e:
        return {"symbol": "NIFTY50", "period": period, "candles": [], "error": str(e)}


# ── News ──────────────────────────────────────────────────────────────────
@app.get("/api/news")
async def news(limit: int = 40):
    return await fetch_all_news(limit)


@app.get("/api/news/stock/{symbol}")
async def stock_news(symbol: str, company: str = ""):
    articles = await fetch_stock_news(symbol.upper(), company)
    return {"symbol": symbol, "articles": articles}



# ── Screener ─────────────────────────────────────────────────────────────
@app.get("/api/screener")
async def screener_stocks(index: str = "nifty50", sector: str = "all"):
    return await fetch_screener_stocks(index, sector)

@app.get("/api/screener/meta")
async def screener_meta():
    return {
        "indices":  list(INDEX_MAP.keys()),
        "sectors":  list(SECTOR_SYMBOLS.keys()),
        "etf_cats": list(ETF_CATEGORIES.keys()),
        "mf_cats":  list(MF_SCHEMES.keys()),
    }

@app.get("/api/etfs")
async def etfs():
    return await fetch_etf_board()

@app.get("/api/mf/category")
async def mf_category(category: str = "Flexi Cap"):
    funds = await fetch_mf_category(category)
    return {"category": category, "funds": funds}

@app.get("/api/mf/search-live")
async def mf_search_live(q: str = ""):
    """Search AMFI and return top results (without NAV — fast)."""
    import httpx as _hx
    async with _hx.AsyncClient(timeout=8) as cl:
        r = await cl.get(f"https://api.mfapi.in/mf/search?q={q}")
    return r.json()[:30]

# ── F&O ───────────────────────────────────────────────────────────────────
@app.get("/api/fno/{symbol}")
async def fno(symbol: str = "NIFTY"):
    return await fetch_option_chain(symbol.upper())

@app.get("/api/fno-active")
async def fno_active():
    return await fetch_most_active_fno()

# ── FII / DII ─────────────────────────────────────────────────────────────
@app.get("/api/fiidii")
async def fiidii():
    return await fetch_fii_dii()

# ── All NSE Indices ──────────────────────────────────────────────────────
@app.get("/api/indices/all")
async def all_indices():
    data = await fetch_all_indices()
    return {"indices": data, "count": len(data)}

# ── Prediction / Technical Analysis ──────────────────────────────────────
@app.get("/api/predict/{symbol}")
async def predict(symbol: str):
    """Full technical analysis for a single symbol."""
    return await fetch_prediction(symbol.upper())

@app.post("/api/predict/batch")
async def predict_batch(body: dict):
    """Batch predictions for a list of symbols. Body: {"symbols": ["TCS","INFY",...]}"""
    symbols = body.get("symbols", [])[:30]   # cap at 30
    if not symbols:
        return []
    results = await batch_predict(symbols, max_concurrent=5)
    return results

# ── Holdings read/write (for the editor page) ────────────────────────────
@app.get("/api/holdings")
async def get_holdings_raw():
    import json
    data_file = Path(__file__).parent.parent / "data" / "holdings.json"
    with open(data_file, encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/holdings/save")
async def save_holdings(data: dict):
    import json
    data_file = Path(__file__).parent.parent / "data" / "holdings.json"
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return {"ok": True, "stocks": len(data.get("stocks", [])),
            "mutual_funds": len(data.get("mutual_funds", []))}

# ── MF search proxy ───────────────────────────────────────────────────────
@app.get("/api/mf/search")
async def mf_search(q: str = ""):
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.mfapi.in/mf/search?q={q}")
        return r.json()


# ── Morning briefing ──────────────────────────────────────────────────────
@app.get("/api/briefing")
async def briefing():
    try:
        p = await get_portfolio()
        text = await generate_morning_briefing(p)
        return {"briefing": text, "generated_at": datetime.now().isoformat()}
    except Exception as e:
        return {"briefing": f"Unavailable: {e}", "generated_at": datetime.now().isoformat()}


# ── Chat ──────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:          str
    history:          list = []
    include_portfolio: bool = True
    model:            str  = MODEL_PREFERENCE[0]


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    p = await get_portfolio() if req.include_portfolio else {}
    return await chat(req.message, req.history, p, req.model)


# ── Frontend ──────────────────────────────────────────────────────────────
@app.get("/")
async def frontend():
    return FileResponse(str(FRONTEND))



@app.get("/edit_holdings.html")
async def editor():
    editor_file = Path(__file__).parent.parent / "edit_holdings.html"
    return FileResponse(str(editor_file))

# ── Run ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print(f"Python {sys.version}")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or token == "your_github_pat_here":
        print("⚠  GITHUB_TOKEN not set — AI chat will not work")
    else:
        print("✓ GitHub token loaded")
    print("✓ Market Monitor v3.0 — Screener, F&O, FII/DII, ETFs, MF Explorer")
    print("✓ Starting on http://localhost:8080\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
