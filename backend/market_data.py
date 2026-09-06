"""
market_data.py
All market data fetching from Yahoo Finance (free, no API key).
- Live NSE prices (5-second shared cache so many clients don't hammer Yahoo)
- Historical OHLCV (1D to 3Y)
- Basic technicals (SMA20/50/200, RSI, MACD) — the full engine lives in predict.py
- Nifty50 / Sensex / BankNifty / Midcap / India VIX overview
- Top gainers / losers from the full Nifty 50 basket (60-second cache)
- Mutual fund NAV via AMFI/mfapi.in (30-minute cache — NAV only changes daily)
"""

import asyncio
import time
import httpx
from datetime import datetime
from urllib.parse import quote

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
BASE = "https://query1.finance.yahoo.com"

# NSE symbol aliases — some stocks have different Yahoo Finance symbols
SYMBOL_ALIASES = {
    "IOCL": "IOC",            # Indian Oil Corporation
    "MOTHERSUMI": "MOTHERSON",
    "ADANIGAS": "ATGL",
    "CADILAHC": "ZYDUSLIFE",
    "MINDTREE": "LTIM",
    "L&TFH": "LTF",
}


def resolve_symbol(symbol: str) -> str:
    """Resolve known aliases; index (^NSEI) and futures (GC=F) symbols pass through."""
    s = (symbol or "").strip().upper()
    return SYMBOL_ALIASES.get(s, s)


def yahoo_ticker(symbol: str) -> str:
    """Full Yahoo ticker: NSE stocks get '.NS', indices / futures / FX are used as-is."""
    s = resolve_symbol(symbol)
    if s.startswith("^") or "=" in s or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"


def chart_url(symbol: str) -> str:
    return f"{BASE}/v8/finance/chart/{quote(yahoo_ticker(symbol), safe='^=.')}"


NIFTY50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC",
    "KOTAKBANK", "LT", "SBIN", "AXISBANK", "BAJFINANCE", "BHARTIARTL", "ASIANPAINT",
    "HCLTECH", "MARUTI", "SUNPHARMA", "TITAN", "NESTLEIND", "WIPRO", "ULTRACEMCO",
    "NTPC", "POWERGRID", "TECHM", "TATAMOTORS", "BAJAJ-AUTO", "DRREDDY", "DIVISLAB",
    "CIPLA", "COALINDIA", "ONGC", "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS",
    "INDUSINDBK", "BAJAJFINSV", "HEROMOTOCO", "EICHERMOT", "GRASIM",
    "HDFCLIFE", "SBILIFE", "APOLLOHOSP", "TATACONSUM", "BEL",
    "HINDALCO", "LTIM", "M&M", "SHRIRAMFIN", "TRENT",
]

# ── tiny TTL cache ────────────────────────────────────────────────────────
_CACHE: dict = {}


def _cache_get(key):
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _cache_set(key, value, ttl):
    _CACHE[key] = (time.time() + ttl, value)
    return value


# ── Single stock quote ────────────────────────────────────────────────────
async def fetch_quote(symbol: str, client: httpx.AsyncClient) -> dict | None:
    """Fetch live quote for one NSE stock / index (5-second cache)."""
    key = ("quote", symbol.upper())
    cached = _cache_get(key)
    if cached:
        return cached
    try:
        r = await client.get(chart_url(symbol), headers=HEADERS, timeout=8,
                             params={"range": "1d", "interval": "1d"})
        if r.status_code != 200:
            return None
        result = r.json()["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
        prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
        chg = round(float(price) - float(prev), 2)
        chg_pct = round((chg / float(prev) * 100), 2) if prev else 0
        q = {
            "symbol": symbol,
            "price": round(float(price), 2),
            "prev_close": round(float(prev), 2),
            "change": chg,
            "change_pct": chg_pct,
            "day_high": round(float(meta.get("regularMarketDayHigh") or price), 2),
            "day_low": round(float(meta.get("regularMarketDayLow") or price), 2),
            "volume": meta.get("regularMarketVolume", 0),
            "market_state": meta.get("marketState", "CLOSED"),
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "high_52w": meta.get("fiftyTwoWeekHigh"),
            "low_52w": meta.get("fiftyTwoWeekLow"),
            "timestamp": datetime.now().isoformat(),
        }
        return _cache_set(key, q, 5)
    except Exception:
        return None


# ── Batch quotes ─────────────────────────────────────────────────────────
async def fetch_quotes_batch(symbols: list[str]) -> dict:
    """Fetch live quotes for multiple symbols concurrently (max 12 in flight)."""
    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient(timeout=10) as client:
        async def one(s):
            async with sem:
                return await fetch_quote(s, client)
        results = await asyncio.gather(*[one(s) for s in symbols], return_exceptions=True)
    quotes = {}
    for sym, res in zip(symbols, results):
        if isinstance(res, dict):
            quotes[sym] = res
    return quotes


# ── Historical OHLCV ──────────────────────────────────────────────────────
PERIOD_MAP = {
    "1D": ("1d", "2m"),
    "1W": ("5d", "15m"),
    "1M": ("1mo", "1h"),
    "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1d"),
    "2Y": ("2y", "1d"),
    "3Y": ("3y", "1wk"),
}


async def fetch_history(symbol: str, period: str = "1Y") -> dict:
    """
    Fetch OHLCV history for a symbol (stock or index).
    period: 1D | 1W | 1M | 3M | 6M | 1Y | 2Y | 3Y
    Returns: {symbol, period, candles: [{t, o, h, l, c, v}]}
    """
    range_str, interval = PERIOD_MAP.get(period, ("1y", "1d"))
    key = ("hist", symbol.upper(), period)
    cached = _cache_get(key)
    if cached:
        return cached
    params = {"range": range_str, "interval": interval, "includePrePost": "false"}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(chart_url(symbol), headers=HEADERS, params=params)
        if r.status_code != 200:
            return {"symbol": symbol, "period": period, "candles": [], "error": f"fetch failed ({r.status_code})"}
        result = r.json()["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        ohlcv = result["indicators"]["quote"][0]
        opens, highs = ohlcv.get("open", []), ohlcv.get("high", [])
        lows, closes, volumes = ohlcv.get("low", []), ohlcv.get("close", []), ohlcv.get("volume", [])
        candles = []
        for i, ts in enumerate(timestamps):
            c = closes[i] if i < len(closes) and closes[i] else None
            if c is None:
                continue
            candles.append({
                "t": ts * 1000,
                "o": round(opens[i] or c, 2),
                "h": round(highs[i] or c, 2),
                "l": round(lows[i] or c, 2),
                "c": round(c, 2),
                "v": volumes[i] or 0,
            })
        out = {"symbol": symbol, "period": period, "candles": candles}
        ttl = 30 if period in ("1D", "1W") else 300
        return _cache_set(key, out, ttl)
    except Exception as e:
        return {"symbol": symbol, "period": period, "candles": [], "error": str(e)}


# ── Basic technical indicators (kept for /api/stock and the chart tab) ────
def _sma(closes: list, n: int) -> float | None:
    valid = [c for c in closes if c is not None]
    if len(valid) < n:
        return None
    return round(sum(valid[-n:]) / n, 2)


def _rsi(closes: list, period: int = 14) -> float | None:
    valid = [c for c in closes if c is not None]
    if len(valid) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(valid)):
        d = valid[i] - valid[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


def _macd(closes: list) -> dict:
    def ema_series(data, n):
        k = 2 / (n + 1)
        e = sum(data[:n]) / n
        out = [e]
        for d in data[n:]:
            e = d * k + e * (1 - k)
            out.append(e)
        return out
    valid = [c for c in closes if c is not None]
    if len(valid) < 35:
        return {"macd": None, "signal": None, "histogram": None}
    e12, e26 = ema_series(valid, 12), ema_series(valid, 26)
    line = [e12[i + 14] - e26[i] for i in range(len(e26))]
    sig = ema_series(line, 9)
    return {"macd": round(line[-1], 2), "signal": round(sig[-1], 2), "histogram": round(line[-1] - sig[-1], 2)}


async def fetch_technicals(symbol: str) -> dict:
    """Compute SMA20/50/200, RSI14, MACD for a symbol using 1Y daily data."""
    hist = await fetch_history(symbol, "1Y")
    candles = hist.get("candles", [])
    if not candles:
        return {"symbol": symbol, "error": hist.get("error", "no data")}
    closes = [c["c"] for c in candles]
    price = closes[-1] if closes else None
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    rsi = _rsi(closes, 14)
    macd = _macd(closes)

    signals = []
    if price and sma50 and sma200:
        if sma50 > sma200:
            signals.append({"type": "bullish", "msg": "Golden Cross — SMA50 above SMA200 (bullish trend)"})
        else:
            signals.append({"type": "bearish", "msg": "Death Cross — SMA50 below SMA200 (bearish trend)"})
    if price and sma200:
        if price > sma200:
            signals.append({"type": "bullish", "msg": f"Price ₹{price} above 200 DMA ₹{sma200} — long-term uptrend"})
        else:
            signals.append({"type": "bearish", "msg": f"Price ₹{price} below 200 DMA ₹{sma200} — long-term downtrend"})
    if price and sma50:
        if price > sma50:
            signals.append({"type": "bullish", "msg": f"Price ₹{price} above 50 DMA ₹{sma50} — medium-term bullish"})
        else:
            signals.append({"type": "bearish", "msg": f"Price ₹{price} below 50 DMA ₹{sma50} — medium-term bearish"})
    if macd["histogram"] is not None:
        if macd["histogram"] > 0:
            signals.append({"type": "bullish", "msg": f"MACD {macd['macd']} above signal {macd['signal']}"})
        else:
            signals.append({"type": "bearish", "msg": f"MACD {macd['macd']} below signal {macd['signal']}"})
    if rsi is not None:
        if rsi > 70:
            signals.append({"type": "warning", "msg": f"RSI {rsi} — Overbought, possible pullback"})
        elif rsi < 30:
            signals.append({"type": "bullish", "msg": f"RSI {rsi} — Oversold, possible bounce"})
        else:
            signals.append({"type": "neutral", "msg": f"RSI {rsi} — Neutral zone"})

    bulls = sum(1 for s in signals if s["type"] == "bullish")
    bears = sum(1 for s in signals if s["type"] == "bearish")
    overall = "bullish" if bulls > bears else "bearish"

    return {
        "symbol": symbol, "price": price,
        "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "rsi": rsi, "macd": macd["macd"], "macd_signal": macd["signal"], "macd_hist": macd["histogram"],
        "signals": signals, "overall": overall, "candles_count": len(candles),
    }


# ── Market overview ───────────────────────────────────────────────────────
INDEX_TICKERS = {
    "NIFTY50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "NIFTYMID": "^NSEMDCP50",
    "INDIAVIX": "^INDIAVIX",
}


async def fetch_market_overview() -> dict:
    """Fetch Nifty50, Sensex, Bank Nifty, Midcap, India VIX live data (concurrent, 10s cache)."""
    cached = _cache_get(("overview",))
    if cached:
        return cached
    results = {}
    async with httpx.AsyncClient(timeout=10) as client:
        async def one(name, ticker):
            try:
                r = await client.get(f"{BASE}/v8/finance/chart/{quote(ticker, safe='^')}",
                                     headers=HEADERS, timeout=8, params={"range": "1d", "interval": "1d"})
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
                    prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
                    chg = round(float(price) - float(prev), 2)
                    results[name] = {
                        "price": round(float(price), 2),
                        "change": chg,
                        "change_pct": round(chg / float(prev) * 100, 2) if prev else 0,
                        "market_state": meta.get("marketState", "CLOSED"),
                        "ticker": ticker,
                    }
            except Exception:
                pass
        await asyncio.gather(*[one(n, t) for n, t in INDEX_TICKERS.items()])
    # keep a stable order
    ordered = {k: results[k] for k in INDEX_TICKERS if k in results}
    return _cache_set(("overview",), ordered, 10)


# ── Top movers from the full Nifty50 basket ───────────────────────────────
async def fetch_top_movers(symbols: list[str] | None = None, top: int = 5) -> dict:
    """Return top gainers and losers from the given symbols (default full Nifty 50), 60s cache."""
    symbols = symbols or NIFTY50_SYMBOLS
    key = ("movers", tuple(symbols), top)
    cached = _cache_get(key)
    if cached:
        return cached
    quotes = await fetch_quotes_batch(symbols)
    sorted_q = sorted([q for q in quotes.values() if q], key=lambda x: x["change_pct"])
    out = {
        "gainers": list(reversed(sorted_q[-top:])),
        "losers": sorted_q[:top],
        "advances": sum(1 for q in sorted_q if q["change_pct"] > 0),
        "declines": sum(1 for q in sorted_q if q["change_pct"] < 0),
        "count": len(sorted_q),
    }
    return _cache_set(key, out, 60)


# ── MF NAV ────────────────────────────────────────────────────────────────
async def fetch_mf_nav(scheme_code: str) -> dict | None:
    key = ("nav", str(scheme_code))
    cached = _cache_get(key)
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"https://api.mfapi.in/mf/{scheme_code}")
        data = r.json()
        latest = data["data"][0]
        out = {"nav": round(float(latest["nav"]), 4), "date": latest["date"],
               "name": data.get("meta", {}).get("scheme_name")}
        return _cache_set(key, out, 1800)
    except Exception:
        return None


def is_market_hours() -> bool:
    """NSE regular session, Mon–Fri 09:15–15:30 IST (computed from UTC, no tz lib needed)."""
    from datetime import timezone, timedelta
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if ist.weekday() >= 5:
        return False
    mins = ist.hour * 60 + ist.minute
    return 9 * 60 + 10 <= mins <= 15 * 60 + 35
