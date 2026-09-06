"""
tradingview.py — TradingView screener data for NSE stocks.

Uses the `tradingview-screener` package (free, unofficial, no login) which
talks to the same scanner endpoint the TradingView website uses.  Gives us
TradingView's own Technical Rating (Recommend.All / MA / Oscillators) plus a
consistent set of indicator values for ~all NSE-listed companies in one call.

Everything is optional: if the package is missing or TradingView is down, the
functions return {} / [] and the rest of the app carries on.
"""

import asyncio
import time

try:
    from tradingview_screener import Query, Column
    AVAILABLE = True
except Exception:  # pragma: no cover
    AVAILABLE = False

COLUMNS = [
    "name", "description", "close", "change", "volume", "relative_volume_10d_calc",
    "Recommend.All", "Recommend.MA", "Recommend.Other",
    "RSI", "MACD.macd", "MACD.signal", "ADX", "Stoch.K", "CCI20", "W.R", "ATR", "Mom",
    "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.Y", "Perf.YTD", "Volatility.D",
    "SMA20", "SMA50", "SMA200", "EMA20", "BB.upper", "BB.lower",
    "price_52_week_high", "price_52_week_low", "market_cap_basic", "sector", "industry",
]

_CACHE: dict = {}
_TTL = 300  # seconds


def tv_symbol(sym: str) -> str:
    """NSE symbol → TradingView name (BAJAJ-AUTO → BAJAJ_AUTO, M&M → M_M)."""
    return sym.upper().replace("&", "_").replace("-", "_")


def rating_label(v) -> str:
    if v is None:
        return "—"
    if v >= 0.5:
        return "Strong Buy"
    if v >= 0.1:
        return "Buy"
    if v > -0.1:
        return "Neutral"
    if v > -0.5:
        return "Sell"
    return "Strong Sell"


def _clean(v):
    """pandas NaN / numpy scalars → plain JSON-safe Python values."""
    try:
        if v is None:
            return None
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return None
        return v
    except Exception:
        return None


def _row(r: dict) -> dict:
    r = {k: _clean(v) for k, v in r.items()}

    def f(k, nd=2):
        v = r.get(k)
        try:
            return round(float(v), nd) if v is not None else None
        except Exception:
            return None
    ticker = r.get("ticker", "")
    return {
        "symbol": ticker.split(":", 1)[1] if ":" in ticker else ticker,
        "tv_name": r.get("name"), "name": r.get("description"),
        "price": f("close"), "change_pct": f("change"), "volume": int(r["volume"]) if r.get("volume") is not None else None,
        "rel_volume": f("relative_volume_10d_calc"),
        "rating": f("Recommend.All", 3), "rating_label": rating_label(r.get("Recommend.All")),
        "rating_ma": f("Recommend.MA", 3), "rating_osc": f("Recommend.Other", 3),
        "rsi": f("RSI", 1), "macd": f("MACD.macd", 3), "macd_signal": f("MACD.signal", 3),
        "adx": f("ADX", 1), "stoch_k": f("Stoch.K", 1), "cci": f("CCI20", 1), "williams_r": f("W.R", 1),
        "atr": f("ATR"), "momentum": f("Mom"),
        "perf_1w": f("Perf.W"), "perf_1m": f("Perf.1M"), "perf_3m": f("Perf.3M"),
        "perf_6m": f("Perf.6M"), "perf_1y": f("Perf.Y"), "perf_ytd": f("Perf.YTD"),
        "volatility_d": f("Volatility.D"),
        "sma20": f("SMA20"), "sma50": f("SMA50"), "sma200": f("SMA200"), "ema20": f("EMA20"),
        "bb_upper": f("BB.upper"), "bb_lower": f("BB.lower"),
        "high_52w": f("price_52_week_high"), "low_52w": f("price_52_week_low"),
        "market_cap_cr": round(float(r["market_cap_basic"]) / 1e7, 0) if r.get("market_cap_basic") else None,
        "sector": r.get("sector"), "industry": r.get("industry"),
    }


def _fetch_universe_sync(limit: int = 700, min_mcap: float = 5e9) -> list:
    """All NSE stocks above `min_mcap` (₹), sorted by market cap. One HTTP call."""
    cols = list(COLUMNS)
    for attempt in range(3):
        try:
            n, df = (Query().set_markets("india").select(*cols)
                     .where(Column("exchange") == "NSE", Column("market_cap_basic") >= min_mcap)
                     .order_by("market_cap_basic", ascending=False)
                     .limit(limit).get_scanner_data())
            # drop rights entitlements / odd instruments such as "XYZ.RR"
            return [_row(r) for r in df.to_dict("records") if "." not in str(r.get("name", ""))]
        except Exception as e:
            msg = str(e)
            # unknown column → drop it and retry
            dropped = [c for c in cols if c in msg and c not in ("name", "close")]
            if dropped and attempt < 2:
                cols = [c for c in cols if c not in dropped]
                continue
            raise
    return []


async def fetch_universe(force: bool = False) -> list:
    if not AVAILABLE:
        return []
    hit = _CACHE.get("universe")
    if hit and hit[0] > time.time() and not force:
        return hit[1]
    try:
        rows = await asyncio.to_thread(_fetch_universe_sync)
    except Exception as e:
        print(f"[tradingview] universe fetch failed: {e}")
        return hit[1] if hit else []
    _CACHE["universe"] = (time.time() + _TTL, rows)
    return rows


async def fetch_ratings(symbols: list) -> dict:
    """{symbol: row} for the requested NSE symbols (served from the cached universe,
    falling back to a targeted query for anything not in it)."""
    if not AVAILABLE or not symbols:
        return {}
    want = {tv_symbol(s): s for s in symbols}
    uni = await fetch_universe()
    out = {}
    for r in uni:
        if r["tv_name"] in want:
            out[want[r["tv_name"]]] = r
    missing = [tv for tv in want if want[tv] not in out]
    if missing:
        try:
            def q():
                n, df = (Query().set_markets("india").select(*COLUMNS)
                         .where(Column("exchange") == "NSE", Column("name").isin(missing))
                         .limit(len(missing) + 5).get_scanner_data())
                return [_row(r) for r in df.to_dict("records")]
            for r in await asyncio.to_thread(q):
                if r["tv_name"] in want:
                    out[want[r["tv_name"]]] = r
        except Exception as e:
            print(f"[tradingview] ratings fetch failed: {e}")
    return out


# ── Screener presets (computed locally on the cached universe) ────────────
PRESETS = {
    "strong_buy":  {"label": "TV Strong Buy",       "desc": "TradingView technical rating ≥ 0.5 (both MAs and oscillators bullish)"},
    "buy":         {"label": "TV Buy",              "desc": "Rating between 0.1 and 0.5"},
    "breakout_52w": {"label": "52-week breakout",   "desc": "Within 3% of the 52-week high and up today"},
    "momentum":    {"label": "Momentum leaders",    "desc": "Best 1-month performers with price above SMA50 > SMA200"},
    "volume_surge": {"label": "Volume surge",       "desc": "Volume ≥ 2× 10-day average and price up"},
    "golden_trend": {"label": "Golden trend",       "desc": "Close > SMA50 > SMA200 with RSI 50–70 (healthy uptrend)"},
    "oversold_bounce": {"label": "Oversold bounce", "desc": "RSI < 32 with the daily MACD turning up"},
    "squeeze":     {"label": "Tight Bollinger",     "desc": "Bollinger band width in the tightest 15% (coiled)"},
    "strong_sell": {"label": "TV Strong Sell",      "desc": "Rating ≤ −0.5 — avoid / short bias"},
}


def _apply_preset(rows: list, key: str) -> list:
    g = lambda r, k: r.get(k) if r.get(k) is not None else 0
    if key == "strong_buy":
        out = [r for r in rows if g(r, "rating") >= 0.5]
        out.sort(key=lambda r: -g(r, "rating"))
    elif key == "buy":
        out = [r for r in rows if 0.1 <= g(r, "rating") < 0.5]
        out.sort(key=lambda r: -g(r, "rating"))
    elif key == "breakout_52w":
        out = [r for r in rows if r.get("high_52w") and r["price"] and r["price"] >= r["high_52w"] * 0.97 and g(r, "change_pct") > 0]
        out.sort(key=lambda r: -(r["price"] / r["high_52w"]))
    elif key == "momentum":
        out = [r for r in rows if r.get("sma50") and r.get("sma200") and r["price"] > r["sma50"] > r["sma200"]]
        out.sort(key=lambda r: -g(r, "perf_1m"))
    elif key == "volume_surge":
        out = [r for r in rows if g(r, "rel_volume") >= 2 and g(r, "change_pct") > 0]
        out.sort(key=lambda r: -g(r, "rel_volume"))
    elif key == "golden_trend":
        out = [r for r in rows if r.get("sma50") and r.get("sma200") and r["price"] > r["sma50"] > r["sma200"] and 50 <= g(r, "rsi") <= 70]
        out.sort(key=lambda r: -g(r, "rating"))
    elif key == "oversold_bounce":
        out = [r for r in rows if 0 < g(r, "rsi") < 32 and r.get("macd") is not None and r.get("macd_signal") is not None and r["macd"] > r["macd_signal"]]
        out.sort(key=lambda r: g(r, "rsi"))
    elif key == "squeeze":
        cand = [r for r in rows if r.get("bb_upper") and r.get("bb_lower") and r["price"]]
        for r in cand:
            r["_bw"] = (r["bb_upper"] - r["bb_lower"]) / r["price"]
        cand.sort(key=lambda r: r["_bw"])
        out = cand[: max(10, len(cand) * 15 // 100)]
        for r in out:
            r["bb_width_pct"] = round(r["_bw"] * 100, 2)
    elif key == "strong_sell":
        out = [r for r in rows if g(r, "rating") <= -0.5]
        out.sort(key=lambda r: g(r, "rating"))
    else:
        out = sorted(rows, key=lambda r: -g(r, "rating"))
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in out]


async def screen(preset: str = "strong_buy", limit: int = 60, min_mcap_cr: float = 1000) -> dict:
    rows = await fetch_universe()
    rows = [r for r in rows if (r.get("market_cap_cr") or 0) >= min_mcap_cr]
    out = _apply_preset([dict(r) for r in rows], preset)
    return {"preset": preset, "label": PRESETS.get(preset, {}).get("label", preset),
            "desc": PRESETS.get(preset, {}).get("desc", ""), "universe": len(rows),
            "count": len(out), "rows": out[:limit], "available": AVAILABLE,
            "presets": [{"key": k, **v} for k, v in PRESETS.items()]}
