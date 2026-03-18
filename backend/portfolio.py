"""
portfolio.py
Loads holdings.json, enriches with live prices, calculates P&L.
"""

import json
import asyncio
from pathlib import Path
from utils import calc_pnl
from market_data import fetch_quotes_batch, fetch_mf_nav

DATA_FILE = Path(__file__).parent.parent / "data" / "holdings.json"


def load_holdings() -> dict:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


async def get_portfolio() -> dict:
    holdings = load_holdings()

    # Fetch all stock quotes concurrently
    symbols  = [s["symbol"] for s in holdings["stocks"]]
    quotes   = await fetch_quotes_batch(symbols) if symbols else {}

    stocks = []
    ti_s = tc_s = 0.0
    for stock in holdings["stocks"]:
        sym   = stock["symbol"]
        q     = quotes.get(sym)
        cp    = q["price"] if q else stock["avg_buy_price"]
        chg   = q["change"] if q else 0
        chgp  = q["change_pct"] if q else 0
        invested = round(stock["quantity"] * stock["avg_buy_price"], 2)
        current  = round(stock["quantity"] * cp, 2)
        pnl      = calc_pnl(invested, current)
        ti_s += invested
        tc_s += current
        stocks.append({
            **stock,
            "current_price":    round(cp, 2),
            "day_change":       chg,
            "day_change_pct":   chgp,
            "day_high":         q["day_high"] if q else cp,
            "day_low":          q["day_low"]  if q else cp,
            "invested":         invested,
            "current_value":    current,
            "market_state":     q["market_state"] if q else "CLOSED",
            **pnl,
        })

    # Fetch all MF NAVs concurrently
    navs = await asyncio.gather(
        *[fetch_mf_nav(m["scheme_code"]) for m in holdings["mutual_funds"]]
    )

    mfs = []
    ti_m = tc_m = 0.0
    for mf, nav_data in zip(holdings["mutual_funds"], navs):
        cn = nav_data["nav"]  if nav_data else mf["avg_nav"]
        nd = nav_data["date"] if nav_data else ""
        invested = round(mf["units"] * mf["avg_nav"], 2)
        current  = round(mf["units"] * cn, 2)
        pnl      = calc_pnl(invested, current)
        ti_m += invested
        tc_m += current
        mfs.append({
            **mf,
            "current_nav":   round(cn, 4),
            "nav_date":      nd,
            "invested":      invested,
            "current_value": current,
            **pnl,
        })

    ti = ti_s + ti_m
    tc = tc_s + tc_m

    # Sector breakdown
    sector_map: dict = {}
    for s in stocks:
        sec = s.get("sector", "Others")
        sector_map[sec] = sector_map.get(sec, 0) + s["current_value"]

    return {
        "stocks":       stocks,
        "mutual_funds": mfs,
        "sectors":      [{"name": k, "value": round(v, 2)} for k, v in sector_map.items()],
        "summary": {
            "total_invested":   round(ti, 2),
            "total_current":    round(tc, 2),
            **calc_pnl(ti, tc),
            "stocks_invested":  round(ti_s, 2),
            "stocks_current":   round(tc_s, 2),
            "mf_invested":      round(ti_m, 2),
            "mf_current":       round(tc_m, 2),
            "day_change":       round(sum(s["day_change"] * s["quantity"] for s in stocks), 2),
            "last_updated":     __import__("datetime").datetime.now().isoformat(),
        },
    }
