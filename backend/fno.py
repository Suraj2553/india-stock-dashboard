"""
fno.py
F&O data and FII/DII from NSE public API.
All requests use a single async-with block per function — no client reuse.
NSE requires cookie from homepage before accepting API calls.
"""

import asyncio
import httpx

NSE_BASE = "https://www.nseindia.com"

_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_API_HEADERS = {
    "User-Agent":      _BROWSER,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}

_HTML_HEADERS = {
    **_API_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def _nse_get(endpoint: str) -> dict | list:
    """
    Fetch a single NSE API endpoint.
    Opens a fresh client, seeds cookies from the homepage, then fetches.
    """
    url = f"{NSE_BASE}{endpoint}"
    async with httpx.AsyncClient(
        headers=_API_HEADERS,
        follow_redirects=True,
        timeout=25,
    ) as client:
        # Seed cookies — NSE rejects API calls without a prior page visit
        try:
            await client.get(NSE_BASE + "/", headers=_HTML_HEADERS)
            await asyncio.sleep(0.4)
        except Exception:
            pass
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def _nse_get_oc(endpoint: str) -> dict | list:
    """
    Specialised fetch for option-chain endpoints.
    NSE bot detection requires: homepage → option-chain page → API call.
    """
    url = f"{NSE_BASE}{endpoint}"
    # Determine which page to warm up based on endpoint
    warmup_page = (
        NSE_BASE + "/option-chain"
        if "option-chain-indices" in endpoint
        else NSE_BASE + "/market-data/equity-derivatives-watch"
    )
    async with httpx.AsyncClient(
        headers={
            **_API_HEADERS,
            "Accept-Encoding": "gzip, deflate, br",
            "sec-fetch-dest":  "empty",
            "sec-fetch-mode":  "cors",
            "sec-fetch-site":  "same-origin",
            "DNT": "1",
        },
        follow_redirects=True,
        timeout=30,
    ) as client:
        try:
            await client.get(NSE_BASE + "/", headers=_HTML_HEADERS)
            await asyncio.sleep(0.5)
            await client.get(warmup_page, headers=_HTML_HEADERS)
            await asyncio.sleep(0.8)
        except Exception:
            pass
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


# ── Option Chain ─────────────────────────────────────────────────────────────
async def fetch_option_chain(symbol: str = "NIFTY") -> dict:
    symbol = symbol.upper()
    try:
        data = await _nse_get_oc(f"/api/option-chain-indices?symbol={symbol}")

        records  = data.get("records",  {})
        filtered = data.get("filtered", {})

        spot    = float(records.get("underlyingValue") or 0)
        expiries = records.get("expiryDates", [])
        strikes  = sorted(records.get("strikePrices", []))

        # PCR
        total_ce_oi = filtered.get("CE", {}).get("totOI", 0)
        total_pe_oi = filtered.get("PE", {}).get("totOI", 0)
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0

        # ATM + window of ±6 strikes
        atm = min(strikes, key=lambda x: abs(x - spot)) if strikes else 0
        idx = strikes.index(atm) if atm in strikes else len(strikes) // 2
        window = set(strikes[max(0, idx - 6): idx + 7])

        chain = []
        for row in records.get("data", []):
            s = row.get("strikePrice", 0)
            if s not in window:
                continue
            ce, pe = row.get("CE", {}), row.get("PE", {})
            chain.append({
                "strike":    s,
                "is_atm":    s == atm,
                "ce_ltp":    ce.get("lastPrice", 0),
                "ce_oi":     ce.get("openInterest", 0),
                "ce_chg_oi": ce.get("changeinOpenInterest", 0),
                "ce_iv":     ce.get("impliedVolatility", 0),
                "ce_volume": ce.get("totalTradedVolume", 0),
                "pe_ltp":    pe.get("lastPrice", 0),
                "pe_oi":     pe.get("openInterest", 0),
                "pe_chg_oi": pe.get("changeinOpenInterest", 0),
                "pe_iv":     pe.get("impliedVolatility", 0),
                "pe_volume": pe.get("totalTradedVolume", 0),
            })
        chain.sort(key=lambda x: x["strike"])

        max_pain = _max_pain(records.get("data", []))

        # Fallback: if NSE blocked spot price, fetch from Yahoo Finance
        if spot == 0:
            yf_sym = "^NSEI" if symbol == "NIFTY" else "^NSEBANK"
            try:
                yf_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1m&range=1d"
                async with httpx.AsyncClient(timeout=8, follow_redirects=True) as yf:
                    yr = await yf.get(yf_url, headers={"User-Agent": _BROWSER})
                    yd = yr.json()
                    spot = float(yd["chart"]["result"][0]["meta"]["regularMarketPrice"] or 0)
            except Exception:
                pass

        # Recompute ATM with actual spot
        if spot and strikes:
            atm = min(strikes, key=lambda x: abs(x - spot))
            idx = strikes.index(atm) if atm in strikes else len(strikes) // 2
            window = set(strikes[max(0, idx - 6): idx + 7])
            chain = [r for r in chain if r["strike"] in window]

        # Only compute sentiment when we have real PCR data
        if total_ce_oi == 0 and total_pe_oi == 0:
            sentiment = "NSE data unavailable — market may be closed or API blocked"
        elif pcr >= 1.3:   sentiment = "Bullish — heavy put writing by sellers"
        elif pcr <= 0.7: sentiment = "Bearish — heavy call writing by sellers"
        else:            sentiment = "Neutral — balanced option activity"

        return {
            "symbol":      symbol,
            "spot":        spot,
            "pcr":         pcr,
            "atm":         atm,
            "max_pain":    max_pain,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "sentiment":   sentiment,
            "expiry":      expiries[0] if expiries else "",
            "expiries":    expiries[:4],
            "chain":       chain,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _max_pain(option_rows: list) -> float:
    try:
        oi: dict[float, dict] = {}
        for row in option_rows:
            s = row.get("strikePrice", 0)
            oi.setdefault(s, {"ce": 0, "pe": 0})
            oi[s]["ce"] += row.get("CE", {}).get("openInterest", 0)
            oi[s]["pe"] += row.get("PE", {}).get("openInterest", 0)
        best, strike = float("inf"), 0.0
        for test in sorted(oi):
            pain = sum(
                (test - k) * v["ce"] if test > k else (k - test) * v["pe"] if test < k else 0
                for k, v in oi.items()
            )
            if pain < best:
                best, strike = pain, test
        return strike
    except Exception:
        return 0.0


# ── Most-active F&O stocks ───────────────────────────────────────────────────
async def fetch_most_active_fno() -> dict:
    try:
        data = await _nse_get("/api/live-analysis-most-active-securities?index=derivative")
        return {
            "active": [
                {
                    "symbol":     i.get("symbol", ""),
                    "ltp":        i.get("lastPrice", 0),
                    "change_pct": i.get("pChange", 0),
                    "turnover":   i.get("totalTurnover", 0),
                }
                for i in (data.get("data", [])[:15])
            ]
        }
    except Exception as e:
        return {"error": str(e)}


# ── FII / DII ────────────────────────────────────────────────────────────────
async def fetch_fii_dii() -> dict:
    try:
        rows = await _nse_get("/api/fiidiiTradeReact")
        result = {"fii": None, "dii": None, "date": ""}
        for row in (rows if isinstance(rows, list) else []):
            cat = (row.get("category") or "").upper()
            entry = {
                "buy_value":  _to_float(row.get("buyValue",  0)),
                "sell_value": _to_float(row.get("sellValue", 0)),
                "net_value":  _to_float(row.get("netValue",  0)),
                "date":       row.get("date", ""),
            }
            if not result["date"] and entry["date"]:
                result["date"] = entry["date"]
            if "FII" in cat or "FPI" in cat:
                result["fii"] = entry
            elif "DII" in cat:
                result["dii"] = entry
        return result
    except Exception as e:
        return {"error": str(e)}


# ── All NSE indices ──────────────────────────────────────────────────────────
async def fetch_all_indices() -> list:
    try:
        data = await _nse_get("/api/allIndices")
        return [
            {
                "name":       i.get("index", ""),
                "last":       i.get("last", 0),
                "change":     i.get("variation", 0),
                "change_pct": i.get("percentChange", 0),
                "high":       i.get("high", 0),
                "low":        i.get("low", 0),
                "open":       i.get("open", 0),
                "prev_close": i.get("previousClose", 0),
                "advances":   i.get("advances", 0),
                "declines":   i.get("declines", 0),
                "unchanged":  i.get("unchanged", 0),
            }
            for i in data.get("data", [])
            if i.get("index")
        ]
    except Exception as e:
        return [{"error": str(e)}]


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return 0.0
