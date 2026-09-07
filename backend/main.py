"""
main.py — Market Monitor FastAPI backend (v4)
- Real-time SSE stream (5 s in market hours, 30 s otherwise)
- Prediction engine v4 with trade plans + forecasts
- Scheduled scans + e-mail alerts (background scheduler, catch-up on start)
"""

import sys
import os
import asyncio
import json
from contextlib import asynccontextmanager
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
from market_data import (fetch_history, fetch_technicals, fetch_market_overview,
                         fetch_top_movers, fetch_quotes_batch, NIFTY50_SYMBOLS, is_market_hours)
from ai_chat import check_health, chat, generate_morning_briefing, MODEL_PREFERENCE
import ai_chat
import tradingview
from screener import (fetch_screener_stocks, fetch_etf_board, fetch_mf_category,
                      SECTOR_SYMBOLS, INDEX_MAP, MF_SCHEMES, ETF_CATEGORIES)
from fno import fetch_option_chain, fetch_fii_dii, fetch_all_indices, fetch_most_active_fno
from predict import fetch_prediction, batch_predict
from metals import fetch_metals, fetch_metal_history, METALS_CONFIG
import alerts

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend" / "index.html"
DATA_FILE = ROOT / "data" / "holdings.json"


def _ensure_ollama():
    """If the AI provider is a local Ollama and it is not running, start it (privacy-first setup)."""
    try:
        cfg = ai_chat.load_llm_config()
        base = (cfg.get("base_url") or os.environ.get("LLM_BASE_URL", "")).lower()
        if "localhost:11434" not in base and "127.0.0.1:11434" not in base:
            return
        import httpx, shutil, subprocess
        try:
            if httpx.get("http://localhost:11434/api/tags", timeout=2).status_code == 200:
                print("+ Ollama already running (local AI, nothing leaves this machine)")
                return
        except Exception:
            pass
        exe = shutil.which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
        if not Path(exe).exists():
            print("!  Ollama not installed — AI chat will be offline (https://ollama.com/download)")
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)
        print(f"+ Started Ollama ({cfg.get('model') or 'local model'}) — AI chat runs on this laptop only")
    except Exception as e:
        print(f"!  Could not start Ollama: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_ollama()
    task = asyncio.create_task(alerts.scheduler_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Market Monitor", version="4.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Health ────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    h = await check_health()
    h["market_hours"] = is_market_hours()
    h["server_time"] = datetime.now().isoformat()
    return h


# ── Portfolio ─────────────────────────────────────────────────────────────
@app.get("/api/portfolio")
async def portfolio():
    try:
        return await get_portfolio()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── SSE stream ────────────────────────────────────────────────────────────
@app.get("/api/stream")
async def stream():
    """Server-Sent Events: live portfolio every 5 s in market hours, 30 s otherwise."""
    async def generator():
        while True:
            try:
                data = await get_portfolio()
                data["market_hours"] = is_market_hours()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(5 if is_market_hours() else 30)
    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Market overview ───────────────────────────────────────────────────────
@app.get("/api/market/overview")
async def market_overview():
    overview, movers = await asyncio.gather(fetch_market_overview(), fetch_top_movers())
    return {"indices": overview, "movers": movers, "market_hours": is_market_hours()}


# ── Stock detail ──────────────────────────────────────────────────────────
@app.get("/api/stock/{symbol}")
async def stock_detail(symbol: str):
    symbol = symbol.upper()
    quotes, tech = await asyncio.gather(fetch_quotes_batch([symbol]), fetch_technicals(symbol))
    return {"quote": quotes.get(symbol), "technicals": tech}


# ── History ───────────────────────────────────────────────────────────────
@app.get("/api/history/index/nifty50")
async def nifty_history(period: str = "1Y"):
    d = await fetch_history("^NSEI", period)
    d["symbol"] = "NIFTY50"
    return d


@app.get("/api/history/{symbol}")
async def history(symbol: str, period: str = "1Y"):
    return await fetch_history(symbol.upper(), period)


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
    return {"indices": list(INDEX_MAP.keys()), "sectors": list(SECTOR_SYMBOLS.keys()),
            "etf_cats": list(ETF_CATEGORIES.keys()), "mf_cats": list(MF_SCHEMES.keys())}


@app.get("/api/etfs")
async def etfs():
    return await fetch_etf_board()


@app.get("/api/mf/category")
async def mf_category(category: str = "Flexi Cap"):
    funds = await fetch_mf_category(category)
    return {"category": category, "funds": funds}


@app.get("/api/mf/search-live")
async def mf_search_live(q: str = ""):
    import httpx as _hx
    async with _hx.AsyncClient(timeout=8) as cl:
        r = await cl.get(f"https://api.mfapi.in/mf/search?q={q}")
    return r.json()[:30]


@app.get("/api/mf/search")
async def mf_search(q: str = ""):
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.mfapi.in/mf/search?q={q}")
        return r.json()


# ── F&O / FII-DII / Indices ───────────────────────────────────────────────
@app.get("/api/fno/{symbol}")
async def fno(symbol: str = "NIFTY"):
    return await fetch_option_chain(symbol.upper())


@app.get("/api/fno-active")
async def fno_active():
    return await fetch_most_active_fno()


@app.get("/api/fiidii")
async def fiidii():
    return await fetch_fii_dii()


@app.get("/api/indices/all")
async def all_indices():
    data = await fetch_all_indices()
    return {"indices": data, "count": len(data)}


# ── Prediction / Technical Analysis ──────────────────────────────────────
@app.get("/api/predict/{symbol:path}")
async def predict(symbol: str, forecast: int = 1, capital: float | None = None):
    """Full technical analysis + trade plan + forecast for one symbol."""
    return await fetch_prediction(symbol.upper(), with_forecast=bool(forecast), capital=capital)


class BatchRequest(BaseModel):
    symbols: list[str] = []
    forecast: bool = False
    capital: float | None = None


@app.post("/api/predict/batch")
async def predict_batch(body: BatchRequest):
    """Batch predictions (max 120 per call — the UI chunks bigger universes)."""
    symbols = body.symbols[:120]
    if not symbols:
        return []
    return await batch_predict(symbols, max_concurrent=8, with_forecast=body.forecast, capital=body.capital)


# ── Scans & alerts ────────────────────────────────────────────────────────
@app.get("/api/scan/universes")
async def scan_universes(symbols: int = 0):
    return alerts.universe_meta(bool(symbols))


@app.get("/api/scan/latest")
async def scan_latest():
    rep = alerts.latest_report()
    return rep or {"empty": True}


@app.get("/api/scan/history")
async def scan_history():
    return alerts.list_reports()


@app.get("/api/scan/report/{name}")
async def scan_report(name: str):
    rep = alerts.load_report(name)
    return rep or JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/scan/status")
async def scan_status():
    return alerts.scheduler_status()


class ScanRequest(BaseModel):
    universe: str | None = None
    email: bool | None = None
    capital: float | None = None


@app.post("/api/scan/run")
async def scan_run(body: ScanRequest):
    """Start a scan in the background; poll /api/scan/status, then read /api/scan/latest."""
    if alerts.STATUS["running"]:
        return {"started": False, "reason": "A scan is already running"}
    asyncio.create_task(alerts.run_scan(universe=body.universe, send_email=body.email,
                                        slot="manual", capital=body.capital))
    return {"started": True}


@app.get("/api/alerts/config")
async def alerts_config_get():
    return alerts.public_config()


@app.post("/api/alerts/config")
async def alerts_config_set(body: dict):
    return alerts.save_config(body)


@app.post("/api/alerts/test-email")
async def alerts_test_email():
    return await alerts.send_test_email()


@app.post("/api/alerts/email-latest")
async def alerts_email_latest():
    rep = alerts.latest_report()
    if not rep:
        return {"sent": False, "error": "No report to send — run a scan first"}
    res = await asyncio.to_thread(alerts.send_report_email, rep)
    rep["email"].update(res)
    alerts.LATEST_FILE.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return res


# ── AI provider config ───────────────────────────────────────────────────
@app.get("/api/llm/config")
async def llm_config_get():
    return ai_chat.public_llm_config()


@app.post("/api/llm/config")
async def llm_config_set(body: dict):
    return ai_chat.save_llm_config(body)


@app.post("/api/llm/test")
async def llm_test():
    return await ai_chat.test_llm()


# ── TradingView screener data ─────────────────────────────────────────────
@app.get("/api/tv/ratings")
async def tv_ratings(symbols: str = ""):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:300]
    return await tradingview.fetch_ratings(syms)


@app.get("/api/tv/screen")
async def tv_screen(preset: str = "strong_buy", limit: int = 80, min_mcap_cr: float = 1000):
    return await tradingview.screen(preset, limit, min_mcap_cr)


# ── Holdings read/write (for the editor page) ────────────────────────────
@app.get("/api/holdings")
async def get_holdings_raw():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def _sync_private(holdings_only: bool = True) -> dict:
    """Push holdings (and optionally the code) to the PRIVATE GitHub repo. Never the public one."""
    import subprocess
    script = ROOT / "scripts" / "sync_private.py"
    if not script.exists():
        return {"ok": False, "error": "scripts/sync_private.py not found"}
    cmd = [sys.executable, str(script)] + (["--holdings-only"] if holdings_only else [])
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
        out = (r.stdout or "").strip().splitlines()
        return {"ok": r.returncode == 0, "message": out[-1] if out else "", "error": (r.stderr or "").strip()[:300] or None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/sync/private")
async def sync_private(body: dict | None = None):
    """Manual 'Sync now' — pushes holdings (or everything with {'full': true}) to the private repo."""
    full = bool((body or {}).get("full"))
    return await asyncio.to_thread(_sync_private, not full)


@app.post("/api/holdings/save")
async def save_holdings(data: dict):
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    out = {"ok": True, "stocks": len(data.get("stocks", [])), "mutual_funds": len(data.get("mutual_funds", []))}
    if alerts.load_config().get("auto_sync_private"):
        out["sync"] = await asyncio.to_thread(_sync_private, True)
    return out


# ── Morning briefing ──────────────────────────────────────────────────────
@app.get("/api/briefing")
async def briefing():
    try:
        p = await get_portfolio()
        text = await generate_morning_briefing(p)
        return {"briefing": text, "generated_at": datetime.now().isoformat()}
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        return {"briefing": f"Unavailable: {detail}", "generated_at": datetime.now().isoformat()}


# ── Chat ──────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list = []
    include_portfolio: bool = True
    model: str = MODEL_PREFERENCE[0]


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    p = await get_portfolio() if req.include_portfolio else {}
    return await chat(req.message, req.history, p, req.model)


# ── Metals ────────────────────────────────────────────────────────────────
@app.get("/api/metals")
async def metals():
    try:
        return await fetch_metals()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metals/history/{symbol:path}")
async def metal_history(symbol: str, period: str = "6mo"):
    try:
        return await fetch_metal_history(symbol, period)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Frontend ──────────────────────────────────────────────────────────────
@app.get("/")
async def frontend():
    return FileResponse(str(FRONTEND), headers={"Cache-Control": "no-cache"})


@app.get("/landing")
async def landing():
    return FileResponse(str(ROOT / "frontend" / "landing.html"))


@app.get("/edit_holdings.html")
async def editor():
    return FileResponse(str(ROOT / "edit_holdings.html"), headers={"Cache-Control": "no-cache"})


# ── Run ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print(f"Python {sys.version}")
    _llm = ai_chat.public_llm_config()
    if _llm.get("configured") or _llm.get("env_configured"):
        print(f"+ AI chat provider: {_llm.get('preset') or 'custom'} / {_llm.get('model') or os.environ.get('LLM_MODEL', '')}")
    else:
        print("!  AI chat: no provider yet (Settings -> AI Chat provider; local Ollama recommended)")
    st = alerts.scheduler_status()
    print(f"+ Scheduled scans: {'ON' if st['enabled'] else 'OFF'} at {', '.join(st['times'] or [])} IST "
          f"| e-mail {'configured' if st['email_configured'] else 'NOT configured (Settings tab)'} | next: {st['next_run']}")
    print("+ Market Monitor v4 - Engine v4, Buy Ideas, Scheduled scans + e-mail")
    print("+ Starting on http://localhost:8080\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
