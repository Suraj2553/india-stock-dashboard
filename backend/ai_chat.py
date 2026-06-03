"""
ai_chat.py
GitHub Models (gpt-4o) with tool-calling so the AI fetches
live prices, history, technicals and news BEFORE answering.
"""

import os
import json
import httpx
from datetime import date
from fastapi import HTTPException

GITHUB_API_URL = "https://models.inference.ai.azure.com/chat/completions"
MODEL_PREFERENCE = ["gpt-4o", "gpt-4o-mini", "Meta-Llama-3.1-70B-Instruct", "Mistral-large"]

# ── Tools the AI can call ─────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "Get the current live price, day change, high/low for an NSE stock",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE stock symbol e.g. INFY, RELIANCE, TCS"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_history",
            "description": "Get historical price data for an NSE stock for charting or analysis",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE stock symbol"},
                    "period": {"type": "string", "enum": ["1D","1W","1M","3M","6M","1Y","3Y"],
                               "description": "Time period for history"}
                },
                "required": ["symbol", "period"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_technicals",
            "description": "Get technical indicators: SMA20/50/200, RSI, MACD, buy/sell signals for a stock",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE stock symbol"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_news",
            "description": "Get recent news articles specifically about an NSE stock or company",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol":       {"type": "string"},
                    "company_name": {"type": "string", "description": "Full company name for better matching"}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_mf_nav_history",
            "description": "Get historical NAV data for a mutual fund using its scheme code. Use this for MF performance questions like 'how has Parag Parikh done in 1 year'",
            "parameters": {
                "type": "object",
                "properties": {
                    "scheme_code": {"type": "string", "description": "AMFI scheme code e.g. 120503"},
                    "fund_name":   {"type": "string", "description": "Fund name for display"}
                },
                "required": ["scheme_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_overview",
            "description": "Get current Nifty50, Sensex, BankNifty levels and top market movers",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or token == "your_github_pat_here":
        raise HTTPException(503, "GITHUB_TOKEN not set in .env")
    return token


def build_portfolio_context(portfolio: dict) -> str:
    if not portfolio:
        return ""
    s = portfolio.get("summary", {})
    lines = [
        "=== USER LIVE PORTFOLIO ===",
        f"Invested: ₹{s.get('total_invested',0):,.0f} | "
        f"Current: ₹{s.get('total_current',0):,.0f} | "
        f"Overall P&L: ₹{s.get('pnl',0):,.0f} ({s.get('pnl_pct',0):+.1f}%)",
        f"Today's Change: ₹{s.get('day_change',0):,.0f}",
        "\nSTOCKS:"
    ]
    for st in portfolio.get("stocks", []):
        lines.append(
            f"  {st['symbol']} | Qty:{st['quantity']} | "
            f"Avg:₹{st['avg_buy_price']} | LTP:₹{st['current_price']} | "
            f"Today:{st['day_change_pct']:+.1f}% | "
            f"P&L:₹{st['pnl']:,.0f}({st['pnl_pct']:+.1f}%)"
        )
    lines.append("\nMUTUAL FUNDS:")
    for mf in portfolio.get("mutual_funds", []):
        lines.append(
            f"  {mf['name'][:40]} | Units:{mf['units']} | "
            f"NAV:₹{mf['current_nav']} | P&L:₹{mf['pnl']:,.0f}({mf['pnl_pct']:+.1f}%)"
        )
    lines.append("=== END PORTFOLIO ===")
    return "\n".join(lines)


async def execute_tool(name: str, args: dict) -> str:
    """Execute a tool call and return result as string for AI."""
    from market_data import (fetch_quotes_batch, fetch_history,
                              fetch_technicals, fetch_market_overview,
                              fetch_top_movers, NIFTY50_SYMBOLS)
    from news import fetch_stock_news

    try:
        if name == "get_stock_price":
            sym    = args["symbol"].upper()
            quotes = await fetch_quotes_batch([sym])
            q      = quotes.get(sym)
            if not q:
                return f"Could not fetch price for {sym}"
            return (f"{sym}: ₹{q['price']} | Change: {q['change']:+.2f} ({q['change_pct']:+.2f}%) | "
                    f"High: ₹{q['day_high']} | Low: ₹{q['day_low']} | "
                    f"Market: {q['market_state']}")

        elif name == "get_stock_history":
            sym    = args["symbol"].upper()
            period = args.get("period", "1Y")
            hist   = await fetch_history(sym, period)
            candles = hist.get("candles", [])
            if not candles:
                return f"No history data for {sym}"
            first = candles[0]
            last  = candles[-1]
            chg   = round(((last['c'] - first['c']) / first['c']) * 100, 2)
            high  = max(c['h'] for c in candles)
            low   = min(c['l'] for c in candles)
            return (f"{sym} {period} history: {len(candles)} data points | "
                    f"Start: ₹{first['c']} → Current: ₹{last['c']} | "
                    f"Change: {chg:+.2f}% | Period High: ₹{high} | Period Low: ₹{low}")

        elif name == "get_technicals":
            sym  = args["symbol"].upper()
            tech = await fetch_technicals(sym)
            if "error" in tech:
                return f"Could not compute technicals for {sym}"
            signals_txt = " | ".join(s["msg"] for s in tech.get("signals", []))
            return (f"{sym} Technicals: Price ₹{tech['price']} | "
                    f"SMA20: ₹{tech['sma20']} | SMA50: ₹{tech['sma50']} | "
                    f"SMA200: ₹{tech['sma200']} | RSI: {tech['rsi']} | "
                    f"MACD: {tech['macd']} | Overall: {tech['overall'].upper()} | "
                    f"Signals: {signals_txt}")

        elif name == "get_stock_news":
            sym  = args["symbol"].upper()
            name_arg = args.get("company_name", "")
            articles = await fetch_stock_news(sym, name_arg)
            if not articles:
                return f"No recent news found for {sym}"
            lines = [f"Recent news for {sym}:"]
            for a in articles[:5]:
                lines.append(f"  [{a['source']}] {a['title']} — {a['description'][:150]}")
            return "\n".join(lines)

        elif name == "get_mf_nav_history":
            code = args.get("scheme_code","").strip()
            fname = args.get("fund_name", code)
            try:
                import httpx as _hx
                async with _hx.AsyncClient(timeout=10) as cl:
                    r = await cl.get(f"https://api.mfapi.in/mf/{code}")
                data = r.json()
                navs = data.get("data", [])[:365]  # last 1 year
                if not navs:
                    return f"No NAV history found for scheme {code}"
                latest = float(navs[0]["nav"])
                oldest = float(navs[-1]["nav"])
                one_week = float(navs[6]["nav"]) if len(navs) > 6 else latest
                one_month = float(navs[29]["nav"]) if len(navs) > 29 else latest
                three_month = float(navs[89]["nav"]) if len(navs) > 89 else latest
                six_month = float(navs[179]["nav"]) if len(navs) > 179 else latest
                def pct(new, old): return round((new-old)/old*100, 2) if old else 0
                return (
                    f"{fname} NAV History:\n"
                    f"  Current NAV  : ₹{latest}  (as of {navs[0]['date']})\n"
                    f"  1 Week ago   : ₹{one_week}  → Change: {pct(latest,one_week):+.2f}%\n"
                    f"  1 Month ago  : ₹{one_month} → Change: {pct(latest,one_month):+.2f}%\n"
                    f"  3 Months ago : ₹{three_month} → Change: {pct(latest,three_month):+.2f}%\n"
                    f"  6 Months ago : ₹{six_month} → Change: {pct(latest,six_month):+.2f}%\n"
                    f"  1 Year ago   : ₹{oldest} → Change: {pct(latest,oldest):+.2f}%\n"
                    f"  Total data points: {len(navs)} days"
                )
            except Exception as e:
                return f"Could not fetch MF NAV history: {e}"

        elif name == "get_market_overview":
            overview = await fetch_market_overview()
            movers   = await fetch_top_movers(NIFTY50_SYMBOLS[:20])
            lines = ["Market Overview:"]
            for idx, data in overview.items():
                lines.append(f"  {idx}: {data['price']:,.2f} ({data['change_pct']:+.2f}%)")
            lines.append("Top Gainers: " + " | ".join(
                f"{g['symbol']} +{g['change_pct']}%" for g in movers["gainers"]
            ))
            lines.append("Top Losers: " + " | ".join(
                f"{l['symbol']} {l['change_pct']}%" for l in movers["losers"]
            ))
            return "\n".join(lines)

    except Exception as e:
        return f"Tool error: {str(e)}"

    return "Unknown tool"


async def chat(message: str, history: list, portfolio: dict,
               model: str = MODEL_PREFERENCE[0]) -> dict:
    token = get_token()

    system = (
        f"You are an expert Indian stock market and mutual fund advisor. "
        f"Today: {date.today().strftime('%d %b %Y')}. "
        f"Use ₹ for amounts. Give specific, data-driven, personalised advice. "
        f"ALWAYS use your tools to fetch live data before answering any question "
        f"about prices, performance, news or technicals — never guess from memory. "
        f"For mutual fund NAV history use get_mf_nav_history with the scheme_code. "
        f"For stock price history use get_stock_history. "
        f"For Nifty/Sensex/market overview use get_market_overview. "
        f"Reference the user's actual holdings when relevant.\n\n"
        + build_portfolio_context(portfolio)
    )

    messages = [{"role": "system", "content": system}]
    for h in history[-8:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    total_tokens = 0
    tool_calls_made = []

    # Agentic loop — keep going until AI stops calling tools (max 5 rounds)
    for _ in range(5):
        payload = {
            "model":       model,
            "messages":    messages,
            "tools":       TOOLS,
            "tool_choice": "auto",
            "max_tokens":  1500,
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(GITHUB_API_URL, headers=headers, json=payload)

        if r.status_code == 401:
            raise HTTPException(401, "Invalid GitHub token")
        if r.status_code == 429:
            raise HTTPException(429, "Rate limit reached. Try again in a minute.")
        if r.status_code != 200:
            raise HTTPException(r.status_code, f"GitHub Models error: {r.text[:200]}")

        data    = r.json()
        choice  = data["choices"][0]
        msg     = choice["message"]
        total_tokens += data.get("usage", {}).get("total_tokens", 0)

        # No more tool calls — we have the final answer
        if choice["finish_reason"] != "tool_calls" or not msg.get("tool_calls"):
            return {
                "reply":      msg["content"] or "No response",
                "model":      model,
                "tokens":     total_tokens,
                "tools_used": tool_calls_made,
            }

        # Execute all tool calls
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": msg["tool_calls"]})

        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]
            fn_args = json.loads(tc["function"]["arguments"] or "{}")
            tool_calls_made.append(fn_name)
            result  = await execute_tool(fn_name, fn_args)
            messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result,
            })

    return {
        "reply":      "I've gathered the data but ran into a processing issue. Please try again.",
        "model":      model,
        "tokens":     total_tokens,
        "tools_used": tool_calls_made,
    }


async def generate_morning_briefing(portfolio: dict) -> str:
    """Generate a morning briefing with market overview + portfolio status."""
    token = get_token()

    from market_data import fetch_market_overview, fetch_quotes_batch
    overview = await fetch_market_overview()
    symbols  = [s["symbol"] for s in portfolio.get("stocks", [])]
    quotes   = await fetch_quotes_batch(symbols) if symbols else {}

    context  = build_portfolio_context(portfolio)
    mkt_lines = []
    for idx, d in overview.items():
        mkt_lines.append(f"{idx}: {d['price']:,.2f} ({d['change_pct']:+.2f}%)")

    prompt = (
        f"Give a concise morning briefing (5-7 bullet points) for this investor. "
        f"Today: {date.today().strftime('%d %b %Y')}.\n\n"
        f"Market:\n" + "\n".join(mkt_lines) + "\n\n"
        + context +
        "\n\nCover: market sentiment today, which of their holdings are up/down most, "
        "any key risks or opportunities, one actionable suggestion."
    )

    payload = {
        "model":       MODEL_PREFERENCE[0],
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  600,
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(GITHUB_API_URL, headers=headers, json=payload)

    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"]
    return "Market briefing unavailable — check your GitHub token."


async def check_health() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    token_set = bool(token) and token != "your_github_pat_here"
    connected = False
    active_model = MODEL_PREFERENCE[0]
    if token_set:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(
                    GITHUB_API_URL,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                    json={"model": active_model,
                          "messages": [{"role": "user", "content": "hi"}],
                          "max_tokens": 5},
                )
                connected = r.status_code == 200
                if r.status_code == 403:
                    active_model = "gpt-4o-mini"
        except Exception:
            pass
    return {
        "token_set":    token_set,
        "connected":    connected,
        "active_model": active_model,
        "endpoint":     GITHUB_API_URL,
    }
