"""
metals.py
Live precious & base metal prices via Yahoo Finance.
Prices are converted to Indian market units (INR):
  Gold / Platinum / Palladium  → per 10g
  Silver / Copper / Aluminum   → per kg
"""

import httpx
import asyncio
from datetime import datetime, timezone

_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Troy oz → Indian unit conversion factors
_OZ_TO_10G  = 10 / 31.1035       # gold / platinum / palladium  (per 10g)
_OZ_TO_KG   = 1000 / 31.1035     # silver                        (per kg)
_LB_TO_KG   = 1 / 0.453592       # copper / aluminium            (per kg, Yahoo quotes USD/lb)

METALS_CONFIG = [
    {
        "symbol": "GC=F",  "name": "Gold",      "icon": "🥇",
        "unit": "per 10g", "unit_short": "10g", "type": "precious",
        "conv": _OZ_TO_10G,  "note": "MCX aligned (24K ~995 purity)",
    },
    {
        "symbol": "SI=F",  "name": "Silver",    "icon": "🥈",
        "unit": "per kg",  "unit_short": "kg",  "type": "precious",
        "conv": _OZ_TO_KG,   "note": "MCX aligned (999 purity)",
    },
    {
        "symbol": "HG=F",  "name": "Copper",    "icon": "🟫",
        "unit": "per kg",  "unit_short": "kg",  "type": "base",
        "conv": _LB_TO_KG,   "note": "LME / MCX aligned",
    },
    {
        "symbol": "PL=F",  "name": "Platinum",  "icon": "⬜",
        "unit": "per 10g", "unit_short": "10g", "type": "precious",
        "conv": _OZ_TO_10G,  "note": "LBMA spot",
    },
    {
        "symbol": "PA=F",  "name": "Palladium", "icon": "🔷",
        "unit": "per 10g", "unit_short": "10g", "type": "precious",
        "conv": _OZ_TO_10G,  "note": "LBMA spot",
    },
    {
        "symbol": "ALI=F", "name": "Aluminium", "icon": "🔩",
        "unit": "per kg",  "unit_short": "kg",  "type": "base",
        "conv": _LB_TO_KG,   "note": "LME / MCX aligned",
    },
]


async def _yf_quote(symbol: str, client: httpx.AsyncClient):
    try:
        r = await client.get(
            f"{YF_BASE}/{symbol}?interval=1d&range=5d",
            headers={"User-Agent": _BROWSER},
            timeout=12,
        )
        r.raise_for_status()
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice") or 0
        prev  = meta.get("previousClose") or meta.get("chartPreviousClose") or price
        return {
            "price":        float(price),
            "prev_close":   float(prev),
            "day_high":     float(meta.get("regularMarketDayHigh") or price),
            "day_low":      float(meta.get("regularMarketDayLow")  or price),
            "market_state": meta.get("marketState", "CLOSED"),
        }
    except Exception:
        return None


async def fetch_metals() -> dict:
    """Return live metal prices converted to INR per Indian unit."""
    all_syms = [m["symbol"] for m in METALS_CONFIG] + ["USDINR=X"]

    async with httpx.AsyncClient(timeout=15) as client:
        tasks   = [_yf_quote(sym, client) for sym in all_syms]
        results = await asyncio.gather(*tasks)

    quotes  = dict(zip(all_syms, results))
    fx_q    = quotes.get("USDINR=X")
    usd_inr = (fx_q["price"] if fx_q else 84.5) or 84.5

    metals = []
    for m in METALS_CONFIG:
        q = quotes.get(m["symbol"])
        if not q or not q["price"]:
            metals.append({
                **{k: m[k] for k in ("symbol","name","icon","unit","unit_short","type","note")},
                "price_inr": 0, "change_inr": 0, "change_pct": 0,
                "day_high_inr": 0, "day_low_inr": 0, "price_usd": 0,
                "market_state": "CLOSED", "error": True,
            })
            continue

        conv   = m["conv"]
        p_inr  = q["price"]      * conv * usd_inr
        pv_inr = q["prev_close"] * conv * usd_inr
        hi_inr = q["day_high"]   * conv * usd_inr
        lo_inr = q["day_low"]    * conv * usd_inr
        chg    = p_inr - pv_inr
        pct    = (chg / pv_inr * 100) if pv_inr else 0

        metals.append({
            "symbol":       m["symbol"],
            "name":         m["name"],
            "icon":         m["icon"],
            "unit":         m["unit"],
            "unit_short":   m["unit_short"],
            "type":         m["type"],
            "note":         m["note"],
            "price_usd":    round(q["price"], 2),
            "price_inr":    round(p_inr, 2),
            "prev_inr":     round(pv_inr, 2),
            "change_inr":   round(chg, 2),
            "change_pct":   round(pct, 4),
            "day_high_inr": round(hi_inr, 2),
            "day_low_inr":  round(lo_inr, 2),
            "market_state": q["market_state"],
        })

    return {
        "metals":  metals,
        "usd_inr": round(usd_inr, 2),
        "updated": datetime.now(timezone.utc).strftime("%H:%M UTC"),
    }


async def fetch_metal_history(symbol: str, period: str = "6mo") -> dict:
    """OHLCV history for a metal symbol, prices converted to INR."""
    valid_periods = {"1mo", "3mo", "6mo", "1y", "2y"}
    yf_period = period if period in valid_periods else "6mo"

    metal_cfg = next((m for m in METALS_CONFIG if m["symbol"] == symbol), None)
    conv = metal_cfg["conv"] if metal_cfg else _OZ_TO_10G

    async with httpx.AsyncClient(timeout=18) as client:
        m_r, u_r = await asyncio.gather(
            client.get(f"{YF_BASE}/{symbol}?interval=1d&range={yf_period}",
                       headers={"User-Agent": _BROWSER}),
            client.get(f"{YF_BASE}/USDINR=X?interval=1d&range={yf_period}",
                       headers={"User-Agent": _BROWSER}),
        )

    try:
        md = m_r.json()["chart"]["result"][0]
        ud = u_r.json()["chart"]["result"][0]

        ts_metal  = md["timestamp"]
        cl_metal  = md["indicators"]["quote"][0]["close"]
        ts_uinr   = ud["timestamp"]
        cl_uinr   = ud["indicators"]["quote"][0]["close"]

        # Build day-keyed USD/INR map
        uinr_map = {t // 86400: v for t, v in zip(ts_uinr, cl_uinr) if v}

        candles = []
        for t, c in zip(ts_metal, cl_metal):
            if c is None:
                continue
            rate = uinr_map.get(t // 86400, 84.5)
            candles.append({"time": t, "price": round(c * conv * rate, 2)})

        return {"symbol": symbol, "period": period, "candles": candles}
    except Exception as e:
        return {"symbol": symbol, "period": period, "candles": [], "error": str(e)}
