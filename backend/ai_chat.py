"""
ai_chat.py
GitHub Models (GPT-4o / GPT-4.1 / Llama / Mistral) with tool-calling so the AI
fetches live prices, the full technical engine, scanner results, option chain,
FII/DII flows and news BEFORE answering.

Two endpoints are tried in order (GitHub migrated the free inference API):
  1. https://models.inference.ai.azure.com/chat/completions   (model: gpt-4o)
  2. https://models.github.ai/inference/chat/completions       (model: openai/gpt-4o)
"""

import os
import json
import httpx
from datetime import date
from fastapi import HTTPException

ENDPOINTS = [
    "https://models.inference.ai.azure.com/chat/completions",
    "https://models.github.ai/inference/chat/completions",
]
GITHUB_API_URL = ENDPOINTS[0]
MODEL_PREFERENCE = ["gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini",
                    "Meta-Llama-3.1-70B-Instruct", "Mistral-large"]

# model ids differ between the two endpoints
_PUBLISHER = {
    "gpt-4o": "openai/gpt-4o", "gpt-4.1": "openai/gpt-4.1", "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4.1-mini": "openai/gpt-4.1-mini", "o4-mini": "openai/o4-mini",
    "Meta-Llama-3.1-70B-Instruct": "meta/Meta-Llama-3.1-70B-Instruct",
    "Mistral-large": "mistral-ai/Mistral-large",
}

_ACTIVE = {"endpoint": None}

# ── Provider presets (all OpenAI-compatible chat/completions) ─────────────
from pathlib import Path as _Path
LLM_CONFIG_FILE = _Path(__file__).parent.parent / "data" / "llm_config.json"
LLM_PRESETS = {
    "groq": {"label": "Groq — free, very fast", "base_url": "https://api.groq.com/openai/v1",
             "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3-32b",
                        "meta-llama/llama-4-scout-17b-16e-instruct"],
             "key_url": "https://console.groq.com/keys",
             "note": "Sign in with GitHub or Google → Create API key. Free tier ≈ 1,000 requests/day, no card. Tool calling supported."},
    "gemini": {"label": "Google Gemini — free", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "models": ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-2.5-pro", "gemini-3.1-pro"],
               "key_url": "https://aistudio.google.com/apikey",
               "note": "Google account → Get API key. Free tier ≈ 1,500 requests/day on Flash, no card. Function calling supported."},
    "openrouter": {"label": "OpenRouter — free models", "base_url": "https://openrouter.ai/api/v1",
                   "models": ["openai/gpt-oss-120b:free", "meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-235b-a22b:free"],
                   "key_url": "https://openrouter.ai/keys",
                   "note": "Free models are capped at ~50 requests/day until you add $10 of credit (then 1,000/day)."},
    "ollama": {"label": "Ollama — local, offline", "base_url": "http://localhost:11434/v1",
               "models": ["llama3.1", "qwen2.5:14b", "mistral"],
               "key_url": "https://ollama.com/download",
               "note": "No key. Install Ollama, run `ollama pull llama3.1`. Quality depends on your laptop; tool calling works on llama3.1 / qwen2.5."},
    "openai": {"label": "OpenAI — paid", "base_url": "https://api.openai.com/v1",
               "models": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"],
               "key_url": "https://platform.openai.com/api-keys", "note": "Requires a funded OpenAI account."},
    "custom": {"label": "Custom OpenAI-compatible", "base_url": "", "models": [], "key_url": "", "note": "Any server exposing /chat/completions."},
}


def load_llm_config() -> dict:
    cfg = {"preset": "", "base_url": "", "api_key": "", "model": ""}
    if LLM_CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(LLM_CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_llm_config(new: dict) -> dict:
    cfg = load_llm_config()
    for k in ("preset", "base_url", "api_key", "model"):
        if k in new and new[k] is not None:
            if k == "api_key" and new[k] == "********":
                continue
            cfg[k] = str(new[k]).strip()
    preset = LLM_PRESETS.get(cfg.get("preset") or "")
    if preset and preset["base_url"] and not cfg.get("base_url"):
        cfg["base_url"] = preset["base_url"]
    if preset and preset["models"] and not cfg.get("model"):
        cfg["model"] = preset["models"][0]
    LLM_CONFIG_FILE.parent.mkdir(exist_ok=True)
    LLM_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    _ACTIVE["endpoint"] = None
    return public_llm_config()


def public_llm_config() -> dict:
    cfg = load_llm_config()
    out = dict(cfg)
    if out.get("api_key"):
        out["api_key"] = "********"
    out["presets"] = [{"key": k, **v} for k, v in LLM_PRESETS.items()]
    out["configured"] = bool(cfg.get("base_url"))
    out["env_configured"] = bool(os.environ.get("LLM_BASE_URL"))
    out["models"] = LLM_PRESETS.get(cfg.get("preset") or "", {}).get("models", [])
    if cfg.get("model") and cfg["model"] not in out["models"]:
        out["models"] = [cfg["model"]] + out["models"]
    return out


def _model_for(endpoint: str, model: str) -> str:
    if "models.github.ai" in endpoint:
        return _PUBLISHER.get(model, model if "/" in model else f"openai/{model}")
    return model.split("/", 1)[1] if "/" in model else model


def _providers() -> list:
    """
    Ordered list of chat-completions providers.
    1. Any OpenAI-compatible server from .env  (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL):
         OpenAI      https://api.openai.com/v1          gpt-4.1-mini
         Groq        https://api.groq.com/openai/v1     llama-3.3-70b-versatile   (free tier)
         OpenRouter  https://openrouter.ai/api/v1       openai/gpt-4.1-mini
         Ollama      http://localhost:11434/v1          llama3.1                  (no key)
    2. GitHub Models (being retired by GitHub — brownouts, then shutdown).
    """
    provs = []
    cfg = load_llm_config()
    if cfg.get("base_url"):
        base = cfg["base_url"].strip().rstrip("/")
        provs.append({"url": base + "/chat/completions", "key": cfg.get("api_key", ""),
                      "model": cfg.get("model", ""), "name": base.split("/")[2] if "//" in base else base, "github": False})
    base = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    if base:
        provs.append({"url": base + "/chat/completions", "key": os.environ.get("LLM_API_KEY", "").strip(),
                      "model": os.environ.get("LLM_MODEL", "").strip(), "name": base.split("/")[2], "github": False})
    gh = os.environ.get("GITHUB_TOKEN", "")
    if gh and gh != "your_github_pat_here":
        for ep in ENDPOINTS:
            provs.append({"url": ep, "key": gh, "model": "", "name": ep.split("/")[2], "github": True})
    return provs


def _is_local(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def is_local_provider() -> bool:
    provs = _providers()
    return bool(provs) and _is_local(provs[0]["url"])


def _compact_tools() -> list:
    """Same tools, one-line descriptions — keeps the prompt small for CPU-bound local models."""
    short = {
        "get_stock_price": "Live price and day change for an NSE stock or index.",
        "get_stock_history": "Price change summary over a period (1M/3M/6M/1Y).",
        "get_full_analysis": "Full technical analysis: score, verdict, setup, trade plan (entry/stop/targets), forecast. Use for buy/sell/hold questions.",
        "scan_stocks": "Rank the best setups in a universe (nifty50, nifty100, midcap, portfolio, sector:IT ...).",
        "get_latest_scan_report": "Latest scheduled scan: regime, top buys, holdings actions.",
        "get_stock_news": "Recent news for a stock.",
        "get_mf_nav_history": "Mutual fund NAV performance by AMFI scheme code.",
        "get_market_overview": "Nifty, Sensex, BankNifty, VIX, breadth, top movers.",
        "get_option_chain": "NIFTY/BANKNIFTY option chain: PCR, max pain, sentiment.",
        "get_fii_dii": "Latest FII and DII net flows.",
    }
    out = []
    for t in TOOLS:
        fn = dict(t["function"])
        fn["description"] = short.get(fn["name"], fn["description"][:120])
        out.append({"type": "function", "function": fn})
    return out


async def _post_chat(payload: dict, token: str = "") -> dict:
    """POST to the first provider that accepts the request."""
    provs = _providers()
    if not provs:
        raise HTTPException(503, "No LLM configured — set LLM_BASE_URL/LLM_API_KEY/LLM_MODEL or GITHUB_TOKEN in .env")
    if _ACTIVE["endpoint"]:
        provs.sort(key=lambda p: p["url"] != _ACTIVE["endpoint"])
    last = None
    first_err = None   # the configured (primary) provider's error is the one worth showing
    # local CPU inference can take minutes per round — give it room
    timeout = 900 if any(_is_local(p["url"]) for p in provs) else 120
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        for p in provs:
            body = dict(payload)
            if _is_local(p["url"]) and body.get("tools"):
                body["tools"] = _compact_tools()
                body["max_tokens"] = min(body.get("max_tokens", 900), 900)
            req_model = payload.get("model") or ""
            if p["github"]:
                body["model"] = _model_for(p["url"], req_model or MODEL_PREFERENCE[0])
            else:
                # dropdown picks a GitHub-era id or "auto" → use the provider's configured model
                generic = (not req_model) or req_model == "auto" or req_model in MODEL_PREFERENCE
                body["model"] = (p["model"] if generic else req_model) or req_model or "llama-3.3-70b-versatile"
            headers = {"Content-Type": "application/json"}
            if p["key"]:
                headers["Authorization"] = f"Bearer {p['key']}"
            try:
                r = await client.post(p["url"], headers=headers, json=body)
            except Exception as e:
                last = HTTPException(502, f"Cannot reach {p['name']}: {e}")
            else:
                if r.status_code == 200:
                    _ACTIVE["endpoint"] = p["url"]
                    return r.json()
                if r.status_code == 401:
                    last = HTTPException(401, f"{p['name']} rejected the API key (401) — check the key in Settings")
                elif r.status_code == 429:
                    last = HTTPException(429, f"Rate limit reached at {p['name']}. Try again in a minute.")
                elif r.status_code == 404:
                    last = HTTPException(404, f"{p['name']}: model '{body['model']}' not found (404) — pick another model")
                else:
                    last = HTTPException(r.status_code, f"{p['name']} error {r.status_code}: {r.text[:300]}")
            if first_err is None and not p["github"]:
                first_err = last
    raise first_err or last or HTTPException(502, "LLM provider unreachable")


# ── Tools the AI can call ─────────────────────────────────────────────────
TOOLS = [
    {"type": "function", "function": {
        "name": "get_stock_price",
        "description": "Live price, day change, high/low, 52-week range for an NSE stock or index (^NSEI, ^NSEBANK).",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "NSE symbol e.g. INFY, RELIANCE, ^NSEI"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "get_stock_history",
        "description": "Historical price summary for an NSE stock for a period.",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"},
                       "period": {"type": "string", "enum": ["1D", "1W", "1M", "3M", "6M", "1Y", "2Y", "3Y"]}}, "required": ["symbol", "period"]}}},
    {"type": "function", "function": {
        "name": "get_full_analysis",
        "description": "Full technical analysis engine: 30+ indicators (trend, momentum, money-flow, relative strength vs Nifty, Supertrend, Ichimoku, squeeze, Minervini template), 0-100 score, verdict, setup type, trade plan (entry/stop/targets/R:R) and a historical forecast for the next 21 sessions. Use this for ANY buy/sell/hold, target, stop-loss or 'should I' question.",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "scan_stocks",
        "description": "Run the signal scanner on a universe (nifty50, nifty100, next50, midcap, smallcap, portfolio, sector:Banking, sector:IT, ... or a comma-separated symbol list) and return the top ranked setups with trade plans. Use for 'what should I buy', 'best stocks now', 'top breakouts'.",
        "parameters": {"type": "object", "properties": {"universe": {"type": "string", "description": "e.g. nifty50, nifty100, midcap, portfolio, sector:Banking, or 'TCS,INFY,WIPRO'"},
                       "top_n": {"type": "integer", "description": "How many to return (default 8)"}}, "required": ["universe"]}}},
    {"type": "function", "function": {
        "name": "get_latest_scan_report",
        "description": "The most recent scheduled scan report (market regime, top buys, watch-list, holdings actions). Fast — use before running a new scan.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_stock_news",
        "description": "Recent news articles about an NSE stock or company.",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "company_name": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "get_mf_nav_history",
        "description": "Historical NAV performance for a mutual fund by AMFI scheme code (1W/1M/3M/6M/1Y).",
        "parameters": {"type": "object", "properties": {"scheme_code": {"type": "string"}, "fund_name": {"type": "string"}}, "required": ["scheme_code"]}}},
    {"type": "function", "function": {
        "name": "get_market_overview",
        "description": "Nifty50, Sensex, BankNifty, Midcap, India VIX levels, breadth and top movers.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_option_chain",
        "description": "NIFTY / BANKNIFTY option chain summary: spot, PCR, max pain, ATM, sentiment (NSE; works best in market hours).",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "enum": ["NIFTY", "BANKNIFTY"]}}, "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "get_fii_dii",
        "description": "Latest FII/FPI and DII cash-market buy/sell/net figures (₹ crore).",
        "parameters": {"type": "object", "properties": {}}}},
]


def get_token() -> str:
    """Kept for compatibility: returns the GitHub token, or '' when another provider is configured."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not _providers():
        raise HTTPException(503, "No AI provider configured — open Settings → AI Chat provider (Groq / Gemini are free)")
    return token if token != "your_github_pat_here" else ""


def build_portfolio_context(portfolio: dict) -> str:
    if not portfolio:
        return ""
    s = portfolio.get("summary", {})
    lines = [
        "=== USER LIVE PORTFOLIO ===",
        f"Invested: ₹{s.get('total_invested', 0):,.0f} | Current: ₹{s.get('total_current', 0):,.0f} | "
        f"Overall P&L: ₹{s.get('pnl', 0):,.0f} ({s.get('pnl_pct', 0):+.1f}%)",
        f"Today's Change: ₹{s.get('day_change', 0):,.0f}",
        "\nSTOCKS:",
    ]
    for st in portfolio.get("stocks", []):
        lines.append(f"  {st['symbol']} | Qty:{st['quantity']} | Avg:₹{st['avg_buy_price']} | LTP:₹{st['current_price']} | "
                     f"Today:{st['day_change_pct']:+.1f}% | P&L:₹{st['pnl']:,.0f}({st['pnl_pct']:+.1f}%)")
    lines.append("\nMUTUAL FUNDS:")
    for mf in portfolio.get("mutual_funds", []):
        lines.append(f"  {mf['name'][:40]} (code {mf.get('scheme_code')}) | Units:{mf['units']} | NAV:₹{mf['current_nav']} | "
                     f"P&L:₹{mf['pnl']:,.0f}({mf['pnl_pct']:+.1f}%)")
    lines.append("=== END PORTFOLIO ===")
    return "\n".join(lines)


def _analysis_text(p: dict) -> str:
    if "error" in p:
        return f"{p.get('symbol')}: analysis failed — {p['error']}"
    tp = p.get("trade_plan") or {}
    fc = p.get("forecast") or {}
    rs = p.get("relative_strength") or {}
    cats = p.get("categories") or {}
    lines = [
        f"{p['symbol']} @ ₹{p['price']} (as of {p.get('as_of')}) — SCORE {p['score']}/100 = {p['verdict']} | Setup: {p['setup']}",
        "Category scores: " + ", ".join(f"{k} {v['score']}" for k, v in cats.items()),
        f"Trend: SMA20 {p.get('sma20')} SMA50 {p.get('sma50')} SMA200 {p.get('sma200')} (200 rising: {p.get('sma200_rising')}), EMA ribbon {p.get('ema_ribbon')}, "
        f"Supertrend {'bullish' if (p.get('supertrend') or {}).get('bullish') else 'bearish'} @ {(p.get('supertrend') or {}).get('line')}",
        f"Momentum: RSI {p.get('rsi')}, MACD hist {(p.get('macd') or {}).get('histogram')}, ADX {(p.get('adx') or {}).get('adx')}, Stoch K {(p.get('stochastic') or {}).get('k')}, "
        f"1M {(p.get('momentum') or {}).get('ret_1m')}% 3M {(p.get('momentum') or {}).get('ret_3m')}% 6M {(p.get('momentum') or {}).get('ret_6m')}%",
        f"Money flow: MFI {p.get('mfi')}, CMF {p.get('cmf')}, OBV rising {(p.get('obv') or {}).get('rising')}, vol ratio {p.get('volume_ratio')}x, accum/dist days {p.get('accum_days')}/{p.get('dist_days')}",
        f"Relative strength vs Nifty: 1M {rs.get('excess_1m')}% 3M {rs.get('excess_3m')}% | beta {rs.get('beta')} | leader {rs.get('leader')}",
        f"52w: {p.get('low_52w')}–{p.get('high_52w')} (position {p.get('position_52w')}%), support {p.get('support')}, resistance {p.get('resistance')}, ATR {p.get('atr')}",
        f"Minervini template {(p.get('trend_template') or {}).get('passed')}/{(p.get('trend_template') or {}).get('total')}, structure {(p.get('structure') or {}).get('label')}, patterns {[x['name'] for x in p.get('patterns', [])]}",
    ]
    if tp:
        lines.append(f"TRADE PLAN: {tp.get('bias')} | entry ₹{tp.get('entry')} ({tp.get('entry_note')}) | stop ₹{tp.get('stop')} ({tp.get('stop_pct')}%) | "
                     f"T1 ₹{tp.get('target1')} ({tp.get('target1_pct')}%) | T2 ₹{tp.get('target2')} ({tp.get('target2_pct')}%) | R:R {tp.get('risk_reward')} | {tp.get('horizon')} | {tp.get('trail')}")
    if fc:
        lines.append(f"FORECAST (own history, {fc.get('samples')} similar days): avg {fc.get('expected_return_pct')}% in {fc.get('horizon_days')} sessions, "
                     f"hit-rate {fc.get('hit_rate_pct')}%, median {fc.get('median_return_pct')}%, best {fc.get('best_pct')}% worst {fc.get('worst_pct')}% (baseline {fc.get('baseline_return_pct')}%)")
    if p.get("notes"):
        lines.append("Notes: " + "; ".join(p["notes"]))
    sig = p.get("signals", [])
    lines.append("Top bullish: " + " | ".join(f"{s['name']} {s['verdict']}" for s in sorted(sig, key=lambda s: -s['weight'])[:5]))
    lines.append("Top bearish: " + " | ".join(f"{s['name']} {s['verdict']}" for s in sorted(sig, key=lambda s: s['weight'])[:4]))
    return "\n".join(lines)


async def execute_tool(name: str, args: dict) -> str:
    """Execute a tool call and return result as string for AI."""
    from market_data import fetch_quotes_batch, fetch_history, fetch_market_overview, fetch_top_movers
    from news import fetch_stock_news
    from predict import fetch_prediction, batch_predict

    try:
        if name == "get_stock_price":
            sym = args["symbol"].upper()
            quotes = await fetch_quotes_batch([sym])
            q = quotes.get(sym)
            if not q:
                return f"Could not fetch price for {sym}"
            return (f"{sym} ({q.get('name')}): ₹{q['price']} | Change: {q['change']:+.2f} ({q['change_pct']:+.2f}%) | "
                    f"High: ₹{q['day_high']} | Low: ₹{q['day_low']} | 52w: {q.get('low_52w')}–{q.get('high_52w')} | Market: {q['market_state']}")

        elif name == "get_stock_history":
            sym = args["symbol"].upper()
            period = args.get("period", "1Y")
            hist = await fetch_history(sym, period)
            candles = hist.get("candles", [])
            if not candles:
                return f"No history data for {sym}"
            first, last = candles[0], candles[-1]
            chg = round(((last['c'] - first['c']) / first['c']) * 100, 2)
            return (f"{sym} {period} history: {len(candles)} data points | Start: ₹{first['c']} → Current: ₹{last['c']} | "
                    f"Change: {chg:+.2f}% | Period High: ₹{max(c['h'] for c in candles)} | Period Low: ₹{min(c['l'] for c in candles)}")

        elif name == "get_full_analysis":
            p = await fetch_prediction(args["symbol"].upper(), with_forecast=True)
            return _analysis_text(p)

        elif name == "scan_stocks":
            from alerts import universe_symbols
            syms = universe_symbols(args.get("universe", "nifty50"))[:120]
            top_n = int(args.get("top_n") or 8)
            res = await batch_predict(syms, max_concurrent=8, with_forecast=False)
            ok = sorted([r for r in res if "error" not in r], key=lambda r: -r["score"])
            lines = [f"Scanned {len(ok)}/{len(syms)} stocks. Distribution: " + ", ".join(
                f"{v} {sum(1 for r in ok if r['verdict'] == v)}" for v in ("Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell"))]
            for r in ok[:top_n]:
                tp = r.get("trade_plan") or {}
                lines.append(f"- {r['symbol']}: {r['score']}/100 {r['verdict']} [{r['setup']}] ₹{r['price']} | stop {tp.get('stop')} T1 {tp.get('target1')} ({tp.get('target1_pct')}%) R:R {tp.get('risk_reward')} | RSI {r.get('rsi')} ADX {(r.get('adx') or {}).get('adx')}")
            lines.append("Weakest: " + ", ".join(f"{r['symbol']} {r['score']}" for r in ok[-5:]))
            return "\n".join(lines)

        elif name == "get_latest_scan_report":
            from alerts import latest_report
            rep = latest_report()
            if not rep:
                return "No scan report yet — run scan_stocks or trigger a scan from the Buy Ideas tab."
            reg = rep["market"]["regime"]
            lines = [f"Report {rep['generated_label']} ({rep['universe_label']}, {rep['ok']} analysed). Regime {reg['label']} {reg['score']}/100 — {reg['desc']}"]
            for p in rep.get("top_buys", []):
                tp = p.get("trade_plan") or {}
                fc = p.get("forecast") or {}
                lines.append(f"BUY {p['symbol']} {p['score']} [{p['setup']}] ₹{p['price']} stop {tp.get('stop')} T1 {tp.get('target1')} T2 {tp.get('target2')} R:R {tp.get('risk_reward')} | model 1M {fc.get('expected_return_pct')}% hit {fc.get('hit_rate_pct')}%")
            for h in rep.get("portfolio", []):
                lines.append(f"HOLDING {h['symbol']}: {h.get('score')} {h.get('verdict')} → {h.get('action')} (P&L {h.get('pnl_pct')}%)")
            return "\n".join(lines)

        elif name == "get_stock_news":
            sym = args["symbol"].upper()
            articles = await fetch_stock_news(sym, args.get("company_name", ""))
            if not articles:
                return f"No recent news found for {sym}"
            return "\n".join([f"Recent news for {sym}:"] + [f"  [{a['source']}] {a['title']} — {a['description'][:150]}" for a in articles[:6]])

        elif name == "get_mf_nav_history":
            code = args.get("scheme_code", "").strip()
            fname = args.get("fund_name", code)
            async with httpx.AsyncClient(timeout=10) as cl:
                r = await cl.get(f"https://api.mfapi.in/mf/{code}")
            data = r.json()
            navs = data.get("data", [])[:400]
            if not navs:
                return f"No NAV history found for scheme {code}"
            latest = float(navs[0]["nav"])

            def at(i):
                return float(navs[i]["nav"]) if len(navs) > i else float(navs[-1]["nav"])

            def pct(new, old):
                return round((new - old) / old * 100, 2) if old else 0
            pts = [("1 Week", 5), ("1 Month", 21), ("3 Months", 63), ("6 Months", 126), ("1 Year", 248)]
            out = [f"{data.get('meta', {}).get('scheme_name', fname)} NAV ₹{latest} (as of {navs[0]['date']})"]
            for label, i in pts:
                out.append(f"  {label}: {pct(latest, at(i)):+.2f}%")
            return "\n".join(out)

        elif name == "get_market_overview":
            overview = await fetch_market_overview()
            movers = await fetch_top_movers()
            lines = ["Market Overview:"]
            for idx, d in overview.items():
                lines.append(f"  {idx}: {d['price']:,.2f} ({d['change_pct']:+.2f}%)")
            lines.append(f"Nifty 50 breadth: {movers.get('advances')} advancing / {movers.get('declines')} declining")
            lines.append("Top Gainers: " + " | ".join(f"{g['symbol']} +{g['change_pct']}%" for g in movers["gainers"]))
            lines.append("Top Losers: " + " | ".join(f"{l['symbol']} {l['change_pct']}%" for l in movers["losers"]))
            return "\n".join(lines)

        elif name == "get_option_chain":
            from fno import fetch_option_chain
            d = await fetch_option_chain(args.get("symbol", "NIFTY"))
            if d.get("error"):
                return f"Option chain unavailable: {d['error']}"
            return (f"{d['symbol']} spot {d['spot']} | PCR {d['pcr']} | ATM {d['atm']} | max pain {d['max_pain']} | "
                    f"CE OI {d['total_ce_oi']:,} PE OI {d['total_pe_oi']:,} | expiry {d['expiry']} | {d['sentiment']}")

        elif name == "get_fii_dii":
            from fno import fetch_fii_dii
            d = await fetch_fii_dii()
            if d.get("error"):
                return f"FII/DII unavailable: {d['error']}"
            f, di = d.get("fii") or {}, d.get("dii") or {}
            return (f"FII/DII ({d.get('date')}): FII net ₹{f.get('net_value', 0):,.0f} Cr (buy {f.get('buy_value', 0):,.0f} / sell {f.get('sell_value', 0):,.0f}); "
                    f"DII net ₹{di.get('net_value', 0):,.0f} Cr (buy {di.get('buy_value', 0):,.0f} / sell {di.get('sell_value', 0):,.0f})")

    except Exception as e:
        return f"Tool error: {str(e)}"

    return "Unknown tool"


SYSTEM_PERSONA = (
    "You are a seasoned Indian equity trader-advisor — an experienced market BULL: you hunt for opportunity, "
    "favour leaders, breakouts and pullbacks in uptrends, and you believe strength begets strength. But you are a "
    "professional: every idea comes with an entry, a stop-loss, targets and a reward-to-risk; you never chase "
    "extended moves, you respect the 200-DMA regime, and you say plainly when the tape is bad and cash is a position. "
    "Use ₹ for amounts and be specific and data-driven. ALWAYS call tools for anything about prices, technicals, "
    "signals, scans, targets, news or performance — never answer from memory. For buy/sell/hold questions use "
    "get_full_analysis (single stock) or scan_stocks (lists). Reference the user's actual holdings when relevant. "
    "Format answers with short headers, bullets and a clear 'Plan' section (entry / stop / T1 / T2 / size)."
)


async def chat(message: str, history: list, portfolio: dict, model: str = MODEL_PREFERENCE[0]) -> dict:
    token = get_token()
    if not model or model == "auto":
        model = load_llm_config().get("model") or MODEL_PREFERENCE[0]
    system = f"{SYSTEM_PERSONA}\nToday: {date.today().strftime('%d %b %Y')}.\n\n" + build_portfolio_context(portfolio)
    local = is_local_provider()
    if local:   # smaller prompt for CPU-bound models
        system = ("You are a disciplined Indian equity trader-advisor with a bullish bias. Use ₹. ALWAYS call a tool for prices, "
                  "analysis, scans or news — never guess. For buy/sell/hold use get_full_analysis. Answer with short bullets and a "
                  "'Plan' section (entry / stop / T1 / T2). " + f"Today: {date.today().strftime('%d %b %Y')}.\n\n"
                  + build_portfolio_context(portfolio))
    messages = [{"role": "system", "content": system}]
    for h in history[-(4 if local else 8):]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    total_tokens = 0
    tool_calls_made = []
    for _ in range(4 if local else 6):
        payload = {"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto",
                   "max_tokens": 1800, "temperature": 0.6}
        data = await _post_chat(payload, token)
        choice = data["choices"][0]
        msg = choice["message"]
        total_tokens += data.get("usage", {}).get("total_tokens", 0)
        if choice.get("finish_reason") != "tool_calls" or not msg.get("tool_calls"):
            return {"reply": msg.get("content") or "No response", "model": model,
                    "tokens": total_tokens, "tools_used": tool_calls_made}
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": msg["tool_calls"]})
        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                fn_args = {}
            tool_calls_made.append(fn_name)
            result = await execute_tool(fn_name, fn_args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    return {"reply": "I've gathered the data but ran into a processing issue. Please try again.",
            "model": model, "tokens": total_tokens, "tools_used": tool_calls_made}


async def generate_morning_briefing(portfolio: dict) -> str:
    """Morning briefing: market overview + portfolio + latest scan picks."""
    token = get_token()
    from market_data import fetch_market_overview
    from alerts import latest_report
    overview = await fetch_market_overview()
    mkt_lines = [f"{idx}: {d['price']:,.2f} ({d['change_pct']:+.2f}%)" for idx, d in overview.items()]
    scan_txt = ""
    rep = latest_report()
    if rep:
        reg = rep["market"]["regime"]
        scan_txt = f"\n\nLatest scan ({rep['generated_label']}): regime {reg['label']} {reg['score']}/100. Top buys: " + \
            ", ".join(f"{p['symbol']} ({p['score']}, {p['setup']}, T1 {(p.get('trade_plan') or {}).get('target1_pct')}%)" for p in rep.get("top_buys", [])[:5]) + \
            ". Holdings: " + ", ".join(f"{h['symbol']} {h.get('score')}→{h.get('action')}" for h in rep.get("portfolio", []))
    prompt = (f"Give a concise morning briefing (6-8 bullet points) for this investor, in the voice of an experienced but disciplined market bull. "
              f"Today: {date.today().strftime('%d %b %Y')}.\n\nMarket:\n" + "\n".join(mkt_lines) + "\n\n"
              + build_portfolio_context(portfolio) + scan_txt +
              "\n\nCover: market regime & what a bull should do today, which holdings are up/down most and the action for each, "
              "the 2-3 best new ideas from the scan with entry/stop, key risks, one actionable suggestion.")
    data = await _post_chat({"model": MODEL_PREFERENCE[0], "messages": [{"role": "user", "content": prompt}],
                             "max_tokens": 700, "temperature": 0.6}, token)
    return data["choices"][0]["message"]["content"]


async def check_health() -> dict:
    provs = _providers()
    token_set = bool(provs)
    connected = False
    active_model = load_llm_config().get("model") or os.environ.get("LLM_MODEL") or MODEL_PREFERENCE[0]
    error = None
    if token_set:
        try:
            await _post_chat({"model": active_model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
            connected = True
        except HTTPException as e:
            error = e.detail
            if e.status_code in (403, 404) and not os.environ.get("LLM_BASE_URL"):
                try:
                    active_model = "gpt-4o-mini"
                    await _post_chat({"model": active_model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
                    connected = True
                    error = None
                except Exception as e2:
                    error = getattr(e2, "detail", str(e2))
        except Exception as e:
            error = str(e)
    ep = _ACTIVE["endpoint"] or (provs[0]["url"] if provs else GITHUB_API_URL)
    return {"token_set": token_set, "connected": connected, "active_model": active_model,
            "endpoint": ep, "provider": ep.split("/")[2] if "//" in ep else ep,
            "providers": [p["name"] for p in provs], "error": error}


async def test_llm() -> dict:
    """One tiny tool-free chat to verify the configured provider."""
    try:
        cfg = load_llm_config()
        data = await _post_chat({"model": cfg.get("model") or "auto",
                                 "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                                 "max_tokens": 10, "temperature": 0})
        reply = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "reply": reply, "endpoint": _ACTIVE["endpoint"], "model": data.get("model")}
    except HTTPException as e:
        return {"ok": False, "error": e.detail}
    except Exception as e:
        return {"ok": False, "error": str(e)}
