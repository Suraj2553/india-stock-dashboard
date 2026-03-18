"""
market_data.py
All market data fetching from Yahoo Finance (free, no API key).
- Live NSE prices
- Historical OHLCV (1D to 1Y)
- Technical indicators (SMA20, SMA50, SMA200, RSI, MACD)
- Nifty50 / Sensex overview
- Top gainers / losers from Nifty50 basket
"""

import asyncio
import httpx
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# NSE symbol aliases — some stocks have different Yahoo Finance symbols
SYMBOL_ALIASES = {
    "IOCL":        "IOC",       # Indian Oil Corporation
    "M&M":         "M%26M",     # Mahindra & Mahindra
    "BAJAJ-AUTO":  "BAJAJ-AUTO",
    "HDFCLIFE":    "HDFCLIFE",
    "SBILIFE":     "SBILIFE",
    "ICICIGI":     "ICICIGI",
    "HDFCAMC":     "HDFCAMC",
    "MUTHOOTFIN":  "MUTHOOTFIN",
    "TATAGOLD":    "TATAGOLD",
    "GOLDBEES":    "GOLDBEES",
    "NIFTYBEES":   "NIFTYBEES",
}

def resolve_symbol(symbol: str) -> str:
    """Resolve any known aliases before querying Yahoo Finance."""
    return SYMBOL_ALIASES.get(symbol.upper(), symbol.upper())
BASE = "https://query1.finance.yahoo.com"

NIFTY50_SYMBOLS = [
    "RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","HINDUNILVR","ITC",
    "KOTAKBANK","LT","SBIN","AXISBANK","BAJFINANCE","BHARTIARTL","ASIANPAINT",
    "HCLTECH","MARUTI","SUNPHARMA","TITAN","NESTLEIND","WIPRO","ULTRACEMCO",
    "NTPC","POWERGRID","TECHM","TATAMOTORS","BAJAJ-AUTO","DRREDDY","DIVISLAB",
    "CIPLA","COALINDIA","ONGC","JSWSTEEL","TATASTEEL","ADANIENT","ADANIPORTS",
    "INDUSINDBK","BAJAJFINSV","HEROMOTOCO","EICHERMOT","GRASIM"
]


# ── Single stock quote ────────────────────────────────────────────────────
async def fetch_quote(symbol: str, client: httpx.AsyncClient) -> dict | None:
    """Fetch live quote for one NSE stock."""
    try:
        sym = resolve_symbol(symbol)
        url = f"{BASE}/v8/finance/chart/{sym}.NS"
        r = await client.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
        prev  = meta.get("previousClose") or price
        chg   = round(price - prev, 2)
        chg_pct = round((chg / prev * 100), 2) if prev else 0
        return {
            "symbol":        symbol,
            "price":         round(float(price), 2),
            "prev_close":    round(float(prev), 2),
            "change":        chg,
            "change_pct":    chg_pct,
            "day_high":      round(float(meta.get("regularMarketDayHigh") or price), 2),
            "day_low":       round(float(meta.get("regularMarketDayLow") or price), 2),
            "volume":        meta.get("regularMarketVolume", 0),
            "market_state":  meta.get("marketState", "CLOSED"),
            "name":          meta.get("longName") or meta.get("shortName") or symbol,
            "timestamp":     datetime.now().isoformat(),
        }
    except Exception:
        return None


# ── Batch quotes ─────────────────────────────────────────────────────────
async def fetch_quotes_batch(symbols: list[str]) -> dict:
    """Fetch live quotes for multiple symbols concurrently."""
    async with httpx.AsyncClient(timeout=10) as client:
        results = await asyncio.gather(
            *[fetch_quote(s, client) for s in symbols],
            return_exceptions=True
        )
    quotes = {}
    for sym, res in zip(symbols, results):
        if isinstance(res, dict):
            quotes[sym] = res
    return quotes


# ── Historical OHLCV ──────────────────────────────────────────────────────
PERIOD_MAP = {
    "1D":  ("1d",  "2m"),
    "1W":  ("5d",  "15m"),
    "1M":  ("1mo", "1h"),
    "3M":  ("3mo", "1d"),
    "6M":  ("6mo", "1d"),
    "1Y":  ("1y",  "1d"),
    "3Y":  ("3y",  "1wk"),
}

async def fetch_history(symbol: str, period: str = "1Y") -> dict:
    """
    Fetch OHLCV history for a symbol.
    period: 1D | 1W | 1M | 3M | 6M | 1Y | 3Y
    Returns: {symbol, period, candles: [{t, o, h, l, c, v}]}
    """
    range_str, interval = PERIOD_MAP.get(period, ("1y", "1d"))
    sym = resolve_symbol(symbol)
    url = f"{BASE}/v8/finance/chart/{sym}.NS"
    params = {"range": range_str, "interval": interval, "includePrePost": "false"}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers=HEADERS, params=params)
        if r.status_code != 200:
            return {"symbol": symbol, "period": period, "candles": [], "error": "fetch failed"}
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        ohlcv = result["indicators"]["quote"][0]
        opens   = ohlcv.get("open", [])
        highs   = ohlcv.get("high", [])
        lows    = ohlcv.get("low", [])
        closes  = ohlcv.get("close", [])
        volumes = ohlcv.get("volume", [])
        candles = []
        for i, ts in enumerate(timestamps):
            c = closes[i] if i < len(closes) and closes[i] else None
            if c is None:
                continue
            candles.append({
                "t": ts * 1000,  # ms for JS
                "o": round(opens[i] or c, 2),
                "h": round(highs[i] or c, 2),
                "l": round(lows[i] or c, 2),
                "c": round(c, 2),
                "v": volumes[i] or 0,
            })
        return {"symbol": symbol, "period": period, "candles": candles}
    except Exception as e:
        return {"symbol": symbol, "period": period, "candles": [], "error": str(e)}


# ── Technical indicators ──────────────────────────────────────────────────
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
        d = valid[i] - valid[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    if not gains:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def _macd(closes: list) -> dict:
    def ema(data, n):
        if len(data) < n:
            return None
        k = 2 / (n + 1)
        e = sum(data[:n]) / n
        for d in data[n:]:
            e = d * k + e * (1 - k)
        return round(e, 2)
    valid = [c for c in closes if c is not None]
    if len(valid) < 26:
        return {"macd": None, "signal": None, "histogram": None}
    ema12 = ema(valid, 12)
    ema26 = ema(valid, 26)
    if ema12 is None or ema26 is None:
        return {"macd": None, "signal": None, "histogram": None}
    macd_val = round(ema12 - ema26, 2)
    return {"macd": macd_val, "signal": None, "histogram": None}

async def fetch_technicals(symbol: str) -> dict:
    """Compute SMA20/50/200, RSI14, MACD for a symbol using 1Y daily data."""
    hist = await fetch_history(resolve_symbol(symbol), "1Y")
    candles = hist.get("candles", [])
    if not candles:
        return {"symbol": symbol, "error": "no data"}
    closes = [c["c"] for c in candles]
    price  = closes[-1] if closes else None
    sma20  = _sma(closes, 20)
    sma50  = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    rsi    = _rsi(closes, 14)
    macd   = _macd(closes)

    # Signals
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
    if rsi is not None:
        if rsi > 70:
            signals.append({"type": "warning", "msg": f"RSI {rsi} — Overbought, possible pullback"})
        elif rsi < 30:
            signals.append({"type": "bullish", "msg": f"RSI {rsi} — Oversold, possible bounce"})
        else:
            signals.append({"type": "neutral", "msg": f"RSI {rsi} — Neutral zone"})

    overall = "bullish" if sum(1 for s in signals if s["type"] == "bullish") > sum(1 for s in signals if s["type"] == "bearish") else "bearish"

    return {
        "symbol":  symbol,
        "price":   price,
        "sma20":   sma20,
        "sma50":   sma50,
        "sma200":  sma200,
        "rsi":     rsi,
        "macd":    macd["macd"],
        "signals": signals,
        "overall": overall,
        "candles_count": len(candles),
    }


# ── Market overview ───────────────────────────────────────────────────────
async def fetch_market_overview() -> dict:
    """Fetch Nifty50, Sensex, Bank Nifty live data."""
    indices = {
        "NIFTY50":   "^NSEI",
        "SENSEX":    "^BSESN",
        "BANKNIFTY": "^NSEBANK",
        "NIFTYMID":  "^NSEMDCP50",
    }
    results = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for name, ticker in indices.items():
            try:
                r = await client.get(
                    f"{BASE}/v8/finance/chart/{ticker}",
                    headers=HEADERS, timeout=8
                )
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
                    prev  = meta.get("previousClose") or price
                    chg   = round(float(price) - float(prev), 2)
                    chg_pct = round(chg / float(prev) * 100, 2) if prev else 0
                    results[name] = {
                        "price":      round(float(price), 2),
                        "change":     chg,
                        "change_pct": chg_pct,
                        "market_state": meta.get("marketState", "CLOSED"),
                    }
            except Exception:
                pass
    return results


# ── Top movers from Nifty50 basket ────────────────────────────────────────
async def fetch_top_movers(symbols: list[str]) -> dict:
    """Return top 5 gainers and losers from given symbols."""
    quotes = await fetch_quotes_batch(symbols[:25])  # limit to 25 for speed
    sorted_q = sorted(
        [q for q in quotes.values() if q],
        key=lambda x: x["change_pct"]
    )
    return {
        "gainers": list(reversed(sorted_q[-5:])),
        "losers":  sorted_q[:5],
    }


# ── MF NAV ────────────────────────────────────────────────────────────────
async def fetch_mf_nav(scheme_code: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"https://api.mfapi.in/mf/{scheme_code}")
        data = r.json()
        latest = data["data"][0]
        return {"nav": round(float(latest["nav"]), 4), "date": latest["date"]}
    except Exception:
        return None
