"""
fno.py
F&O data and FII/DII from NSE public API.
All requests use a single async-with block per function — no client reuse.
NSE requires cookie from homepage before accepting API calls.

Option chain: NSE retired /api/option-chain-indices and /api/option-chain-equities.
Use /api/option-chain-contract-info (expiries) + /api/option-chain-v3?type=…&expiry=…
"""

import asyncio
import httpx

NSE_BASE = "https://www.nseindia.com"

# 60-second cache so repeated clicks don't re-trigger NSE's bot detection
import time as _time
_NSE_CACHE: dict = {}


def _cget(key):
    hit = _NSE_CACHE.get(key)
    return hit[1] if hit and hit[0] > _time.time() else None


def _cset(key, value, ttl=60):
    _NSE_CACHE[key] = (_time.time() + ttl, value)
    return value

_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

_API_HEADERS = {
    "User-Agent":      _BROWSER,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/option-chain",
    "Connection":      "keep-alive",
}

_HTML_HEADERS = {
    **_API_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Index underlyings use type=Indices on option-chain-v3; everything else Equity
_INDEX_OC_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "NIFTYIT", "SENSEX",  # SENSEX may 404 on NSE; listed for completeness
}


def _oc_type(symbol: str) -> str:
    return "Indices" if symbol.upper() in _INDEX_OC_SYMBOLS else "Equity"


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


async def _nse_get_oc(endpoint: str, referer: str | None = None) -> dict | list:
    """
    Specialised fetch for option-chain endpoints.
    NSE bot detection requires: homepage → option-chain page → API call.
    """
    url = f"{NSE_BASE}{endpoint}"
    headers = {
        **_API_HEADERS,
        "Accept-Encoding": "gzip, deflate",  # no br — httpx may lack brotli
        "sec-fetch-dest":  "empty",
        "sec-fetch-mode":  "cors",
        "sec-fetch-site":  "same-origin",
        "DNT": "1",
    }
    if referer:
        headers["Referer"] = referer
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=30,
    ) as client:
        try:
            await client.get(NSE_BASE + "/", headers=_HTML_HEADERS)
            await asyncio.sleep(0.4)
            await client.get(NSE_BASE + "/option-chain", headers=_HTML_HEADERS)
            await asyncio.sleep(0.6)
        except Exception:
            pass
        r = await client.get(url)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "json" not in ctype:
            raise RuntimeError(
                f"NSE returned non-JSON ({r.status_code}) — often a block/CAPTCHA page. "
                "Retry in a minute, or open nseindia.com/option-chain in a browser first."
            )
        return r.json()


async def _fetch_oc_v3(symbol: str, expiry: str | None = None) -> dict:
    """Fetch option chain via NSE v3 API (requires expiry)."""
    sym = symbol.upper()
    oc_type = _oc_type(sym)
    ref = f"{NSE_BASE}/option-chain"

    if not expiry:
        info = await _nse_get_oc(f"/api/option-chain-contract-info?symbol={sym}", referer=ref)
        dates = info.get("expiryDates") or []
        if not dates:
            raise RuntimeError(
                f"NSE contract-info returned no expiry dates for {sym}. "
                "Symbol may be invalid or NSE is blocking this IP."
            )
        expiry = dates[0]

    endpoint = f"/api/option-chain-v3?type={oc_type}&symbol={sym}&expiry={expiry}"
    data = await _nse_get_oc(endpoint, referer=ref)
    if not isinstance(data, dict) or not data.get("records"):
        raise RuntimeError(
            f"NSE option-chain-v3 returned empty data for {sym} expiry={expiry}. "
            "Market may be closed or NSE is rate-limiting — retry shortly."
        )
    # Attach the expiry we asked for (useful if records omit it)
    data.setdefault("_requested_expiry", expiry)
    return data


# ─── Option Chain ────────────────────────────────────────────────────────────
async def fetch_option_chain(symbol: str = "NIFTY") -> dict:
    symbol = symbol.upper()
    cached = _cget(("oc", symbol))
    if cached:
        return cached
    try:
        data = await _fetch_oc_v3(symbol)

        records  = data.get("records",  {}) or {}
        filtered = data.get("filtered", {}) or {}

        spot    = _to_float(records.get("underlyingValue") or 0)
        expiries = records.get("expiryDates", []) or []
        # Prefer the expiry we requested if records list is empty
        if not expiries and data.get("_requested_expiry"):
            expiries = [data["_requested_expiry"]]
        strikes  = sorted({_to_float(x) for x in (records.get("strikePrices") or []) if x is not None})

        # PCR (filtered totals for the selected expiry)
        total_ce_oi = _to_float((filtered.get("CE") or {}).get("totOI", 0))
        total_pe_oi = _to_float((filtered.get("PE") or {}).get("totOI", 0))
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0

        # Prefer filtered.data (current expiry) when present
        rows = filtered.get("data") or records.get("data") or []

        # ATM + window of ±6 strikes (coerce types — NSE sometimes sends strings)
        atm = min(strikes, key=lambda x: abs(x - spot)) if strikes and spot else 0.0
        if not atm and rows and spot:
            atm = min((_to_float(r.get("strikePrice", 0)) for r in rows), key=lambda x: abs(x - spot))
        strike_list = strikes or sorted({_to_float(r.get("strikePrice", 0)) for r in rows if r.get("strikePrice") is not None})
        idx = strike_list.index(atm) if atm in strike_list else len(strike_list) // 2
        window = set(strike_list[max(0, idx - 6): idx + 7]) if strike_list else set()

        chain = []
        for row in rows:
            s = _to_float(row.get("strikePrice", 0))
            if window and s not in window:
                continue
            ce, pe = row.get("CE") or {}, row.get("PE") or {}
            # Skip empty strike rows (no CE and no PE payload)
            if not ce and not pe:
                continue
            chain.append({
                "strike":    s,
                "is_atm":    s == atm,
                "ce_ltp":    _to_float(ce.get("lastPrice", 0)),
                "ce_oi":     _to_float(ce.get("openInterest", 0)),
                "ce_chg_oi": _to_float(ce.get("changeinOpenInterest", 0)),
                "ce_iv":     _to_float(ce.get("impliedVolatility", 0)),
                "ce_volume": _to_float(ce.get("totalTradedVolume", 0)),
                "pe_ltp":    _to_float(pe.get("lastPrice", 0)),
                "pe_oi":     _to_float(pe.get("openInterest", 0)),
                "pe_chg_oi": _to_float(pe.get("changeinOpenInterest", 0)),
                "pe_iv":     _to_float(pe.get("impliedVolatility", 0)),
                "pe_volume": _to_float(pe.get("totalTradedVolume", 0)),
            })
        chain.sort(key=lambda x: x["strike"])

        max_pain = _max_pain(rows)

        # Fallback: if NSE blocked spot price, fetch from Yahoo Finance
        if spot == 0:
            yf_map = {
                "NIFTY": "^NSEI",
                "BANKNIFTY": "^NSEBANK",
                "FINNIFTY": "^CNXFIN",
                "MIDCPNIFTY": "^NSEMDCP50",
            }
            yf_sym = yf_map.get(symbol)
            if yf_sym:
                try:
                    yf_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1m&range=1d"
                    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as yf:
                        yr = await yf.get(yf_url, headers={"User-Agent": _BROWSER})
                        yd = yr.json()
                        spot = float(yd["chart"]["result"][0]["meta"]["regularMarketPrice"] or 0)
                except Exception:
                    pass

        # Recompute ATM with actual spot
        if spot and strike_list:
            atm = min(strike_list, key=lambda x: abs(x - spot))
            idx = strike_list.index(atm) if atm in strike_list else len(strike_list) // 2
            window = set(strike_list[max(0, idx - 6): idx + 7])
            for row in chain:
                row["is_atm"] = row["strike"] == atm
            chain = [r for r in chain if r["strike"] in window]

        # Only compute sentiment when we have real PCR data
        if total_ce_oi == 0 and total_pe_oi == 0:
            sentiment = "NSE data unavailable — market may be closed or API blocked"
        elif pcr >= 1.3:
            sentiment = "Bullish — heavy put writing by sellers"
        elif pcr <= 0.7:
            sentiment = "Bearish — heavy call writing by sellers"
        else:
            sentiment = "Neutral — balanced option activity"

        out = {
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
        if chain:
            _cset(("oc", symbol), out, 60)
        elif not chain:
            out["error"] = (
                f"NSE returned option-chain metadata for {symbol} but no strike rows. "
                "Try again shortly."
            )
        return out
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else "?"
        return {
            "error": (
                f"NSE HTTP {code} for {symbol}. "
                "If this persists, NSE may be blocking automated requests — "
                "open https://www.nseindia.com/option-chain in a browser and retry."
            ),
            "symbol": symbol,
        }
    except Exception as e:
        msg = str(e)
        if "404" in msg or "Not Found" in msg:
            msg = (
                f"NSE option-chain endpoint not found for {symbol}. "
                "The dashboard uses /api/option-chain-v3; NSE may have changed again."
            )
        return {"error": msg, "symbol": symbol}


def _max_pain(option_rows: list) -> float:
    try:
        oi: dict[float, dict] = {}
        for row in option_rows:
            s = _to_float(row.get("strikePrice", 0))
            oi.setdefault(s, {"ce": 0, "pe": 0})
            oi[s]["ce"] += _to_float((row.get("CE") or {}).get("openInterest", 0))
            oi[s]["pe"] += _to_float((row.get("PE") or {}).get("openInterest", 0))
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


# ─── Most-active F&O stocks ──────────────────────────────────────────────────
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


# ─── FII / DII ───────────────────────────────────────────────────────────────
async def fetch_fii_dii() -> dict:
    cached = _cget(("fiidii",))
    if cached:
        return cached
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
        if result["fii"] or result["dii"]:
            _cset(("fiidii",), result, 300)
        return result
    except Exception as e:
        return {"error": str(e)}


# ─── All NSE indices ─────────────────────────────────────────────────────────
async def fetch_all_indices() -> list:
    cached = _cget(("indices",))
    if cached:
        return cached
    try:
        data = await _nse_get("/api/allIndices")
        return _cset(("indices",), [
            {
                "name":       i.get("index", ""),
                "last":       i.get("last", 0),
                "change":     i.get("variation", 0),
                "change_pct": i.get("percentChange", 0),
                "high":       i.get("high", 0),
                "low":        i.get("low", 0),
                "open":       i.get("open", 0),
                "prev_close": i.get("previousClose", 0),
                "advances":   int(_to_float(i.get("advances", 0))),
                "declines":   int(_to_float(i.get("declines", 0))),
                "unchanged":  int(_to_float(i.get("unchanged", 0))),
            }
            for i in data.get("data", [])
            if i.get("index")
        ], 60)
    except Exception as e:
        return [{"error": str(e)}]


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return 0.0
