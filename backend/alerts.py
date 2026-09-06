"""
alerts.py — Scheduled market scans + e-mail briefings

What it does
  • Scans a universe (Nifty 50 / 100 / Midcap / sector / everything) with the
    predict.py engine, ranks the setups, re-runs the full engine (with
    forecast) on the best candidates and on every stock you hold.
  • Builds a report (saved to data/scans/latest.json and a dated copy) that the
    dashboard's "Buy Ideas" tab shows instantly.
  • E-mails the report (HTML) through any SMTP server (Gmail app-password works).
  • A scheduler coroutine runs inside the FastAPI server: twice a day at the
    configured IST times, and — if the laptop was off at that time — as soon as
    the server is next running ("catch-up").  A CLI (scripts/daily_scan.py) can
    run the same job from Windows Task Scheduler when the server is not open.

Config lives in data/alerts_config.json; SMTP secrets can also come from .env
(SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO).
"""

import asyncio
import json
import os
import smtplib
import ssl
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
SCAN_DIR = DATA_DIR / "scans"
CONFIG_FILE = DATA_DIR / "alerts_config.json"
STATE_FILE = SCAN_DIR / "state.json"
LATEST_FILE = SCAN_DIR / "latest.json"

DEFAULT_CONFIG = {
    "enabled": True,                # run scheduled scans
    "email_enabled": True,          # e-mail them (needs SMTP settings)
    "email_to": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_pass": "",
    "from_name": "Market Monitor",
    "times": ["08:45", "15:45"],    # IST — pre-open plan and post-close review
    "weekdays_only": True,
    "universe": "nifty100",
    "min_score": 65,
    "max_picks": 8,
    "capital": 100000,              # ₹ used for position-size & profit projections
    "include_portfolio": True,
    "scan_on_start": True,          # run a scan whenever the dashboard starts (if none in the last start_scan_gap_min)
    "start_scan_gap_min": 120,
    "low_price_max": 300,           # "low-price picks" section: price <= this ...
    "low_price_min_score": 75,      # ... and score >= this
}

IST = timezone(timedelta(hours=5, minutes=30))

# live status for the UI
STATUS = {"running": False, "stage": "", "progress": 0, "total": 0,
          "started_at": None, "finished_at": None, "last_error": None, "slot": None}
_LOCK = asyncio.Lock()


# ════════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    # .env fallbacks (only fill blanks)
    env = {
        "smtp_host": os.environ.get("SMTP_HOST"), "smtp_port": os.environ.get("SMTP_PORT"),
        "smtp_user": os.environ.get("SMTP_USER"), "smtp_pass": os.environ.get("SMTP_PASS"),
        "email_to": os.environ.get("ALERT_EMAIL_TO"),
    }
    for k, v in env.items():
        if v and not cfg.get(k):
            cfg[k] = int(v) if k == "smtp_port" else v
    try:
        cfg["smtp_port"] = int(cfg.get("smtp_port") or 587)
    except Exception:
        cfg["smtp_port"] = 587
    if not isinstance(cfg.get("times"), list):
        cfg["times"] = list(DEFAULT_CONFIG["times"])
    return cfg


def save_config(new: dict) -> dict:
    cfg = load_config()
    for k in DEFAULT_CONFIG:
        if k in new and new[k] is not None:
            # keep the stored password when the UI sends the mask back
            if k == "smtp_pass" and new[k] == "********":
                continue
            if k == "smtp_pass" and isinstance(new[k], str):
                new[k] = new[k].replace(" ", "").strip()   # Gmail shows app passwords as "abcd efgh ijkl mnop"
            if k in ("email_to", "smtp_user", "smtp_host") and isinstance(new[k], str):
                new[k] = new[k].strip()
            cfg[k] = new[k]
    cfg["times"] = [t.strip() for t in cfg.get("times", []) if isinstance(t, str) and ":" in t][:4]
    DATA_DIR.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return public_config(cfg)


def public_config(cfg: dict | None = None) -> dict:
    cfg = dict(cfg or load_config())
    if cfg.get("smtp_pass"):
        cfg["smtp_pass"] = "********"
    cfg["smtp_configured"] = bool(load_config().get("smtp_user") and load_config().get("smtp_pass") and load_config().get("email_to"))
    return cfg


# ════════════════════════════════════════════════════════════════════════
# UNIVERSES
# ════════════════════════════════════════════════════════════════════════

def _holdings_symbols() -> list:
    try:
        d = json.loads((DATA_DIR / "holdings.json").read_text(encoding="utf-8"))
        return [s["symbol"] for s in d.get("stocks", [])]
    except Exception:
        return []


def universes() -> dict:
    from screener import NIFTY50, NIFTY_NEXT50, NIFTY_MIDCAP, NIFTY_SMALLCAP, SECTOR_SYMBOLS
    u = {
        "portfolio": {"label": "My Holdings", "symbols": _holdings_symbols()},
        "nifty50": {"label": "Nifty 50", "symbols": list(NIFTY50)},
        "next50": {"label": "Nifty Next 50", "symbols": list(NIFTY_NEXT50)},
        "nifty100": {"label": "Nifty 100", "symbols": list(dict.fromkeys(NIFTY50 + NIFTY_NEXT50))},
        "midcap": {"label": "Midcap", "symbols": list(dict.fromkeys(NIFTY_MIDCAP))},
        "smallcap": {"label": "Smallcap", "symbols": list(dict.fromkeys(NIFTY_SMALLCAP))},
    }
    for sec, syms in SECTOR_SYMBOLS.items():
        key = "sector:" + sec
        u[key] = {"label": sec, "symbols": list(dict.fromkeys(syms))}
    everything = []
    for k in ("nifty50", "next50", "midcap", "smallcap"):
        everything += u[k]["symbols"]
    for sec in SECTOR_SYMBOLS.values():
        everything += sec
    u["all"] = {"label": "Entire watch-universe", "symbols": list(dict.fromkeys(everything))}
    return u


def universe_symbols(name: str) -> list:
    u = universes()
    if name in u:
        return u[name]["symbols"]
    # comma-separated custom list
    return [s.strip().upper() for s in name.split(",") if s.strip()]


def universe_meta(include_symbols: bool = False) -> list:
    out = []
    for k, v in universes().items():
        row = {"key": k, "label": v["label"], "count": len(v["symbols"])}
        if include_symbols:
            row["symbols"] = v["symbols"]
        out.append(row)
    return out


# ════════════════════════════════════════════════════════════════════════
# SCAN
# ════════════════════════════════════════════════════════════════════════

def _lite(p: dict) -> dict:
    """Trim a prediction to what tables / e-mails need."""
    if "error" in p:
        return {"symbol": p.get("symbol"), "error": p["error"]}
    keys = ("symbol", "as_of", "price", "score", "verdict", "verdict_color", "setup", "notes",
            "rsi", "macd", "adx", "supertrend", "support", "resistance", "bull_target",
            "high_52w", "low_52w", "position_52w", "dist_to_52w_high_pct", "atr", "volume_ratio",
            "trade_plan", "forecast", "profit_projection", "categories", "relative_strength",
            "trend_template", "risk", "sma20", "sma50", "sma200", "ema_ribbon", "uptrend", "downtrend",
            "bullish_count", "bearish_count", "neutral_count", "squeeze", "structure", "patterns",
            "momentum", "donchian")
    out = {k: p.get(k) for k in keys}
    sigs = sorted(p.get("signals", []), key=lambda s: -s["weight"])
    out["reasons"] = [f"{s['name']}: {s['verdict']}" for s in sigs if s["weight"] > 0][:4]
    out["risks"] = [f"{s['name']}: {s['verdict']}" for s in sorted(p.get("signals", []), key=lambda s: s["weight"]) if s["weight"] < 0][:3]
    return out


def _holding_action(p: dict, holding: dict) -> str:
    sc = p.get("score", 50)
    if sc >= 70:
        return "Add on dips"
    if sc >= 58:
        return "Hold"
    if sc >= 45:
        return "Hold · tighten stop"
    if sc >= 32:
        return "Trim / protect"
    return "Exit on bounce"


def _regime(overview: dict, nifty_pred: dict | None, movers: dict | None) -> dict:
    n = overview.get("NIFTY50", {})
    vix = overview.get("INDIAVIX", {}).get("price")
    score = 50.0
    parts = []
    if nifty_pred and "score" in nifty_pred:
        score = 0.6 * nifty_pred["score"] + 0.4 * 50
        parts.append(f"Nifty technical score {nifty_pred['score']}/100 ({nifty_pred['verdict']})")
    if movers and movers.get("count"):
        br = movers["advances"] / movers["count"]
        score = score * 0.7 + br * 100 * 0.3
        parts.append(f"Breadth {movers['advances']} up / {movers['declines']} down in Nifty 50")
    if n:
        parts.append(f"Nifty {n['change_pct']:+.2f}% today at {n['price']:,.0f}")
    if vix:
        parts.append(f"India VIX {vix:.1f} " + ("(calm)" if vix < 14 else "(elevated — size down)" if vix > 20 else "(normal)"))
        if vix > 22:
            score -= 8
    score = int(max(0, min(100, round(score))))
    if score >= 65:
        label, desc = "BULL", "Trend is up and breadth confirms — deploy capital, buy breakouts and pullbacks."
    elif score >= 50:
        label, desc = "CAUTIOUS BULL", "Constructive but selective — favour leaders, keep stops tight."
    elif score >= 38:
        label, desc = "NEUTRAL / CHOPPY", "No edge for index longs — trade only A+ setups with small size."
    else:
        label, desc = "BEAR / CORRECTION", "Capital preservation first — build a watch-list, wait for reversal signals."
    return {"label": label, "score": score, "desc": desc, "points": parts,
            "nifty": n, "vix": vix, "nifty_score": nifty_pred.get("score") if nifty_pred else None}


async def run_scan(universe: str | None = None, send_email: bool | None = None,
                   slot: str = "manual", capital: float | None = None) -> dict:
    """Full scheduled-scan pipeline. Returns the report (also saved to disk)."""
    from predict import batch_predict, fetch_prediction, fetch_nifty_closes
    from market_data import fetch_market_overview, fetch_top_movers
    from portfolio import get_portfolio

    cfg = load_config()
    universe = universe or cfg["universe"]
    capital = capital or cfg.get("capital") or 100000
    if send_email is None:
        send_email = bool(cfg.get("email_enabled"))

    if _LOCK.locked():
        return {"error": "A scan is already running"}
    async with _LOCK:
        STATUS.update({"running": True, "stage": "starting", "progress": 0, "total": 0,
                       "started_at": datetime.now(IST).isoformat(), "finished_at": None,
                       "last_error": None, "slot": slot})
        try:
            symbols = universe_symbols(universe)
            STATUS.update({"stage": f"scanning {len(symbols)} stocks", "total": len(symbols)})

            # market context first (cheap)
            overview = await fetch_market_overview()
            movers = await fetch_top_movers()
            nifty_pred = await fetch_prediction("^NSEI", with_forecast=False)

            # pass 1: light scan of the whole universe in chunks (progress for UI)
            results = []
            for i in range(0, len(symbols), 20):
                chunk = symbols[i:i + 20]
                results += await batch_predict(chunk, max_concurrent=8, with_forecast=False)
                STATUS["progress"] = min(len(symbols), i + len(chunk))
            ok = [r for r in results if "error" not in r]
            ok.sort(key=lambda r: -r["score"])

            # pass 2: full engine with forecast on the strongest candidates + low-price candidates
            STATUS["stage"] = "deep analysis of top candidates"
            top_n = max(cfg.get("max_picks", 8) * 3, 20)
            lp_max = float(cfg.get("low_price_max") or 300)
            lp_min = int(cfg.get("low_price_min_score") or 75)
            low_cands = [r for r in ok if (r.get("price") or 0) <= lp_max and r["score"] >= lp_min][:12]
            deep = {}
            for r in list(dict.fromkeys([x["symbol"] for x in ok[:top_n] + low_cands])):
                p = await fetch_prediction(r, with_forecast=True, capital=capital)
                if "error" not in p:
                    deep[r] = p
            good_setups = ("Breakout", "Momentum Leader", "Pullback Buy", "Squeeze Breakout",
                           "Trend Continuation", "Oversold Reversal")
            picks = [_lite(deep[s]) for s in deep if deep[s]["score"] >= cfg.get("min_score", 65)
                     and deep[s]["setup"] in good_setups]
            picks.sort(key=lambda p: (-p["score"], -(p["trade_plan"] or {}).get("risk_reward", 0)))
            top_buys = picks[:cfg.get("max_picks", 8)]
            watch = [_lite(deep[s]) for s in deep if _lite(deep[s]) not in top_buys and deep[s]["score"] >= 55][:8]

            # holdings
            portfolio_rows, port_summary = [], {}
            if cfg.get("include_portfolio", True):
                STATUS["stage"] = "analysing your holdings"
                try:
                    port = await get_portfolio()
                    port_summary = port.get("summary", {})
                    for h in port.get("stocks", []):
                        p = deep.get(h["symbol"]) or await fetch_prediction(h["symbol"], with_forecast=True, capital=capital)
                        lp = _lite(p)
                        lp.update({"name": h.get("name"), "quantity": h["quantity"], "avg_buy_price": h["avg_buy_price"],
                                   "current_price": h.get("current_price"), "pnl": h.get("pnl"), "pnl_pct": h.get("pnl_pct"),
                                   "current_value": h.get("current_value"),
                                   "action": _holding_action(p, h) if "error" not in p else "—"})
                        portfolio_rows.append(lp)
                except Exception as e:
                    STATUS["last_error"] = f"portfolio: {e}"

            # extra views: low-price picks, highest scores overall
            low_price = [_lite(deep.get(r["symbol"], r)) for r in low_cands]
            top_scores = [_lite(deep.get(r["symbol"], r)) for r in ok[:10]]

            # TradingView technical rating as an independent second opinion
            try:
                from tradingview import fetch_ratings
                rows_all = top_buys + watch + portfolio_rows + low_price + top_scores
                tvmap = await fetch_ratings([p["symbol"] for p in rows_all if p.get("symbol")])
                for p in rows_all:
                    t = tvmap.get(p["symbol"])
                    p["tv"] = ({"rating": t["rating"], "rating_label": t["rating_label"], "rsi": t["rsi"],
                                "perf_1m": t["perf_1m"], "sector": t["sector"], "industry": t["industry"]} if t else None)
            except Exception as e:
                STATUS["last_error"] = f"tradingview: {e}"

            # consensus: engine >= 70 AND TradingView Strong Buy
            seen, consensus = set(), []
            for p in top_scores + low_price + top_buys:
                t = p.get("tv") or {}
                if p["symbol"] not in seen and p["score"] >= 70 and (t.get("rating") or 0) >= 0.5:
                    seen.add(p["symbol"]); consensus.append(p)
            consensus.sort(key=lambda p: -(p["score"] + 20 * (p.get("tv") or {}).get("rating", 0)))

            sells = [_lite(r) for r in ok if r["score"] <= 30][:8]
            report = {
                "generated_at": datetime.now(IST).isoformat(),
                "generated_label": datetime.now(IST).strftime("%a %d %b %Y, %H:%M IST"),
                "slot": slot, "universe": universe,
                "universe_label": universes().get(universe, {}).get("label", universe),
                "scanned": len(symbols), "ok": len(ok), "failed": [r["symbol"] for r in results if "error" in r],
                "capital": capital,
                "market": {"indices": overview, "regime": _regime(overview, nifty_pred, movers),
                           "movers": {"gainers": movers.get("gainers", [])[:5], "losers": movers.get("losers", [])[:5]}},
                "top_buys": top_buys, "watchlist": watch, "sells": sells,
                "low_price_picks": low_price, "low_price_rule": {"max_price": lp_max, "min_score": lp_min},
                "top_scores": top_scores, "consensus": consensus[:8],
                "portfolio": portfolio_rows, "portfolio_summary": port_summary,
                "all": [_lite(r) for r in ok],
                "distribution": {v: sum(1 for r in ok if r["verdict"] == v)
                                 for v in ("Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell")},
                "email": {"requested": send_email, "sent": False, "to": cfg.get("email_to"), "error": None},
            }

            # e-mail
            if send_email:
                STATUS["stage"] = "sending e-mail"
                res = await asyncio.to_thread(send_report_email, report, cfg)
                report["email"].update(res)

            _save_report(report)
            STATUS.update({"stage": "done", "progress": len(symbols)})
            return report
        except Exception as e:
            STATUS["last_error"] = f"{e}"
            traceback.print_exc()
            return {"error": str(e)}
        finally:
            STATUS.update({"running": False, "finished_at": datetime.now(IST).isoformat()})


def _save_report(report: dict):
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    stamp = datetime.now(IST).strftime("%Y-%m-%d_%H%M")
    (SCAN_DIR / f"scan_{stamp}.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    # keep the last 40 reports
    files = sorted(SCAN_DIR.glob("scan_*.json"))
    for f in files[:-40]:
        try:
            f.unlink()
        except Exception:
            pass


def latest_report() -> dict | None:
    if LATEST_FILE.exists():
        try:
            return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def list_reports() -> list:
    out = []
    for f in sorted(SCAN_DIR.glob("scan_*.json"), reverse=True)[:40]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"file": f.name, "generated_at": d.get("generated_at"), "label": d.get("generated_label"),
                        "slot": d.get("slot"), "universe": d.get("universe_label"), "scanned": d.get("scanned"),
                        "top_buys": [p["symbol"] for p in d.get("top_buys", [])],
                        "regime": d.get("market", {}).get("regime", {}).get("label"),
                        "email_sent": d.get("email", {}).get("sent")})
        except Exception:
            pass
    return out


def load_report(name: str) -> dict | None:
    f = SCAN_DIR / Path(name).name
    if f.exists() and f.suffix == ".json":
        return json.loads(f.read_text(encoding="utf-8"))
    return None


# ════════════════════════════════════════════════════════════════════════
# E-MAIL
# ════════════════════════════════════════════════════════════════════════

def _fmt(v, pre="₹", nd=2):
    if v is None:
        return "—"
    try:
        return f"{pre}{v:,.{nd}f}"
    except Exception:
        return str(v)


def _pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def build_email_html(rep: dict) -> str:
    reg = rep["market"]["regime"]
    reg_col = {"BULL": "#0a8f6a", "CAUTIOUS BULL": "#3a8f0a", "NEUTRAL / CHOPPY": "#b8860b", "BEAR / CORRECTION": "#c0392b"}.get(reg["label"], "#555")
    cap = rep.get("capital") or 100000
    idx = rep["market"]["indices"]

    def idx_cell(name):
        d = idx.get(name)
        if not d:
            return ""
        col = "#0a8f6a" if d["change_pct"] >= 0 else "#c0392b"
        return f"<td style='padding:6px 10px'><div style='font-size:10px;color:#777'>{name}</div><div style='font-weight:700'>{d['price']:,.2f}</div><div style='color:{col};font-size:12px'>{d['change_pct']:+.2f}%</div></td>"

    def tv_badge(p):
        t = p.get("tv")
        if not t:
            return ""
        col = "#0a8f6a" if (t.get("rating") or 0) >= 0.1 else "#c0392b" if (t.get("rating") or 0) <= -0.1 else "#b8860b"
        return (f"<span style='font-size:11px;background:#f4f4f4;color:{col};border-radius:4px;padding:2px 7px;margin-left:4px' "
                f"title='TradingView technical rating'>TV: {t['rating_label']} ({t['rating']:+.2f})</span>")

    def pick_block(p, rank):
        tp = p.get("trade_plan") or {}
        fc = p.get("forecast") or {}
        pj = p.get("profit_projection") or {}
        t1_profit = cap * (tp.get("target1_pct") or 0) / 100
        t2_profit = cap * (tp.get("target2_pct") or 0) / 100
        loss = cap * (tp.get("stop_pct") or 0) / 100
        reasons = "".join(f"<li style='margin:2px 0'>{r}</li>" for r in p.get("reasons", [])[:4])
        risks = "".join(f"<li style='margin:2px 0;color:#a33'>{r}</li>" for r in p.get("risks", [])[:2])
        fc_line = (f"Model (this stock's last year): <b>{_pct(fc.get('expected_return_pct'))}</b> avg in {fc.get('horizon_days')} sessions, "
                   f"hit-rate <b>{fc.get('hit_rate_pct', 0):.0f}%</b> over {fc.get('samples')} similar days"
                   if fc else "Model forecast: not enough history")
        return f"""
        <div style="border:1px solid #e3e3e3;border-radius:8px;padding:14px 16px;margin:10px 0;background:#fff">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="font-size:17px;font-weight:800">#{rank} {p['symbol']}
              <span style="font-size:11px;background:#e8f7f1;color:#0a8f6a;border-radius:4px;padding:2px 7px;margin-left:6px">{p['verdict']} · {p['score']}/100</span>
              <span style="font-size:11px;background:#eef;color:#335;border-radius:4px;padding:2px 7px;margin-left:4px">{p['setup']}</span>{tv_badge(p)}
            </div>
            <div style="font-size:15px;font-weight:700">{_fmt(p['price'])}</div>
          </div>
          <table style="width:100%;border-collapse:collapse;margin-top:10px;font-size:12px">
            <tr style="background:#f7f7f7">
              <td style="padding:6px 8px"><b>Entry</b><br>{_fmt(tp.get('entry'))}<br><span style="color:#777;font-size:11px">{tp.get('entry_note','')}</span></td>
              <td style="padding:6px 8px"><b>Stop-loss</b><br><span style="color:#c0392b">{_fmt(tp.get('stop'))} ({_pct(tp.get('stop_pct'))})</span></td>
              <td style="padding:6px 8px"><b>Target 1</b><br><span style="color:#0a8f6a">{_fmt(tp.get('target1'))} ({_pct(tp.get('target1_pct'))})</span></td>
              <td style="padding:6px 8px"><b>Target 2</b><br><span style="color:#0a8f6a">{_fmt(tp.get('target2'))} ({_pct(tp.get('target2_pct'))})</span></td>
              <td style="padding:6px 8px"><b>R : R</b><br>{tp.get('risk_reward','—')}</td>
            </tr>
          </table>
          <div style="font-size:12px;margin-top:8px;color:#333">
            <b>Profit projection on {_fmt(cap, nd=0)}:</b> T1 ≈ <b style="color:#0a8f6a">{_fmt(t1_profit, nd=0)}</b>,
            T2 ≈ <b style="color:#0a8f6a">{_fmt(t2_profit, nd=0)}</b>, max loss at stop ≈ <b style="color:#c0392b">{_fmt(loss, nd=0)}</b>
            · horizon {tp.get('horizon','—')} · {tp.get('trail','')}
          </div>
          <div style="font-size:12px;margin-top:4px;color:#555">{fc_line}</div>
          <ul style="font-size:12px;margin:8px 0 0 18px;padding:0">{reasons}{risks}</ul>
        </div>"""

    picks_html = "".join(pick_block(p, i + 1) for i, p in enumerate(rep.get("top_buys", []))) or \
        "<div style='padding:14px;background:#fff8e1;border:1px solid #f0d78c;border-radius:8px'>No setup cleared the quality bar today (score ≥ {} with a valid setup). A bull's discipline: no trade is a position. Watch-list below.</div>".format(load_config().get("min_score", 65))

    def row(p):
        tp = p.get("trade_plan") or {}
        tvl = (p.get("tv") or {}).get("rating_label", "—")
        return (f"<tr><td style='padding:5px 8px;font-weight:700'>{p['symbol']}</td><td style='padding:5px 8px'>{p['score']}</td>"
                f"<td style='padding:5px 8px'>{p['verdict']} <span style='color:#777;font-size:10px'>· TV {tvl}</span></td><td style='padding:5px 8px'>{p['setup']}</td>"
                f"<td style='padding:5px 8px'>{_fmt(p['price'])}</td><td style='padding:5px 8px'>{_fmt(tp.get('stop'))}</td>"
                f"<td style='padding:5px 8px'>{_fmt(tp.get('target1'))}</td></tr>")

    watch_html = "".join(row(p) for p in rep.get("watchlist", []))

    def mini_table(rows_, empty_msg):
        def r2(p):
            tp = p.get("trade_plan") or {}; fc = p.get("forecast") or {}; t = p.get("tv") or {}
            return (f"<tr><td style='padding:5px 8px;font-weight:700'>{p['symbol']}</td><td style='padding:5px 8px'>{_fmt(p['price'])}</td>"
                    f"<td style='padding:5px 8px'>{p['score']} · {p['verdict']}</td><td style='padding:5px 8px'>{t.get('rating_label', '—')}</td>"
                    f"<td style='padding:5px 8px'>{p['setup']}</td><td style='padding:5px 8px;color:#c0392b'>{_fmt(tp.get('stop'))}</td>"
                    f"<td style='padding:5px 8px;color:#0a8f6a'>{_fmt(tp.get('target1'))} ({_pct(tp.get('target1_pct'))})</td>"
                    f"<td style='padding:5px 8px'>{_pct(fc.get('expected_return_pct')) if fc else '—'} / {str(int(fc.get('hit_rate_pct', 0))) + '%' if fc else '—'}</td></tr>")
        body = "".join(r2(p) for p in rows_) or f"<tr><td colspan=8 style='padding:8px;color:#999'>{empty_msg}</td></tr>"
        return f"""<div style="background:#fff;border-radius:8px;border:1px solid #e3e3e3;overflow:hidden">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="background:#f7f7f7"><th style="text-align:left;padding:6px 8px">Symbol</th><th style="text-align:left;padding:6px 8px">Price</th><th style="text-align:left;padding:6px 8px">Score</th><th style="text-align:left;padding:6px 8px">TV</th><th style="text-align:left;padding:6px 8px">Setup</th><th style="text-align:left;padding:6px 8px">Stop</th><th style="text-align:left;padding:6px 8px">T1</th><th style="text-align:left;padding:6px 8px">Model 1M / hit</th></tr>
          {body}
        </table></div>"""

    lpr = rep.get("low_price_rule") or {"max_price": 300, "min_score": 75}
    extra_html = f"""
      <h2 style="font-size:16px;margin:18px 0 6px">🤝 Consensus — engine ≥ 70 and TradingView Strong Buy</h2>
      {mini_table(rep.get('consensus', []), 'No stock has both engines strongly bullish today.')}
      <h2 style="font-size:16px;margin:18px 0 6px">💸 Low-price picks — ≤ {_fmt(lpr['max_price'], nd=0)} and score ≥ {lpr['min_score']}</h2>
      {mini_table(rep.get('low_price_picks', []), 'No low-priced stock clears the bar today.')}
      <h2 style="font-size:16px;margin:18px 0 6px">🏆 Highest scores overall</h2>
      {mini_table(rep.get('top_scores', []), 'Nothing scanned.')}"""
    port_rows = ""
    for h in rep.get("portfolio", []):
        if h.get("error"):
            port_rows += f"<tr><td style='padding:5px 8px;font-weight:700'>{h['symbol']}</td><td colspan=6 style='padding:5px 8px;color:#999'>{h['error']}</td></tr>"
            continue
        col = "#0a8f6a" if (h.get("pnl_pct") or 0) >= 0 else "#c0392b"
        acol = "#0a8f6a" if h["action"].startswith(("Add", "Hold")) else "#c0392b"
        tp = h.get("trade_plan") or {}
        port_rows += (f"<tr><td style='padding:5px 8px;font-weight:700'>{h['symbol']}</td><td style='padding:5px 8px'>{_fmt(h.get('current_price'))}</td>"
                      f"<td style='padding:5px 8px;color:{col}'>{_pct(h.get('pnl_pct'))}</td><td style='padding:5px 8px'>{h['score']} · {h['verdict']}</td>"
                      f"<td style='padding:5px 8px;color:{acol};font-weight:700'>{h['action']}</td><td style='padding:5px 8px'>{_fmt(tp.get('stop'))}</td><td style='padding:5px 8px'>{h['setup']}</td></tr>")
    ps = rep.get("portfolio_summary") or {}
    port_sum = (f"Portfolio {_fmt(ps.get('total_current'), nd=0)} · P&L <b style='color:{'#0a8f6a' if (ps.get('pnl') or 0) >= 0 else '#c0392b'}'>{_fmt(ps.get('pnl'), nd=0)} ({_pct(ps.get('pnl_pct'))})</b>"
                if ps else "")
    dist = rep.get("distribution", {})
    sells_html = ", ".join(f"{p['symbol']} ({p['score']})" for p in rep.get("sells", [])) or "none"

    return f"""<!doctype html><html><body style="margin:0;background:#f2f4f7;font-family:Segoe UI,Roboto,Arial,sans-serif;color:#222">
    <div style="max-width:760px;margin:0 auto;padding:18px">
      <div style="background:#0d1117;color:#fff;border-radius:10px;padding:16px 20px">
        <div style="font-size:20px;font-weight:800">MARKET<span style="color:#00d4aa">.</span>MONITOR — {('Pre-market plan' if rep['slot'] == '08:45' else 'Post-close review' if rep['slot'] == '15:45' else 'Scan report')}</div>
        <div style="font-size:12px;color:#aab">{rep['generated_label']} · Universe: {rep['universe_label']} ({rep['ok']}/{rep['scanned']} analysed)</div>
      </div>
      <div style="background:#fff;border-radius:10px;padding:14px 16px;margin-top:12px;border-left:6px solid {reg_col}">
        <div style="font-size:15px;font-weight:800;color:{reg_col}">Market regime: {reg['label']} ({reg['score']}/100)</div>
        <div style="font-size:12px;margin-top:4px">{reg['desc']}</div>
        <ul style="font-size:12px;margin:6px 0 0 18px;padding:0">{''.join(f'<li>{p}</li>' for p in reg.get('points', []))}</ul>
        <table style="margin-top:6px"><tr>{idx_cell('NIFTY50')}{idx_cell('BANKNIFTY')}{idx_cell('SENSEX')}{idx_cell('NIFTYMID')}{idx_cell('INDIAVIX')}</tr></table>
        <div style="font-size:11px;color:#777;margin-top:4px">Scan distribution — Strong Buy {dist.get('Strong Buy',0)} · Buy {dist.get('Buy',0)} · Neutral {dist.get('Neutral',0)} · Sell {dist.get('Sell',0)} · Strong Sell {dist.get('Strong Sell',0)}</div>
      </div>

      <h2 style="font-size:16px;margin:18px 0 4px">🎯 High-conviction buys ({len(rep.get('top_buys', []))})</h2>
      <div style="font-size:11px;color:#666;margin-bottom:6px">Ranked by score, then reward-to-risk. Every idea comes with a stop — the stop is the plan.</div>
      {picks_html}

      <h2 style="font-size:16px;margin:18px 0 6px">👀 Watch-list (close to a trigger)</h2>
      <div style="background:#fff;border-radius:8px;border:1px solid #e3e3e3;overflow:hidden">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="background:#f7f7f7"><th style="text-align:left;padding:6px 8px">Symbol</th><th style="text-align:left;padding:6px 8px">Score</th><th style="text-align:left;padding:6px 8px">Verdict</th><th style="text-align:left;padding:6px 8px">Setup</th><th style="text-align:left;padding:6px 8px">Price</th><th style="text-align:left;padding:6px 8px">Stop</th><th style="text-align:left;padding:6px 8px">T1</th></tr>
          {watch_html or "<tr><td colspan=7 style='padding:8px;color:#999'>Nothing close to a trigger.</td></tr>"}
        </table>
      </div>

      {extra_html}

      <h2 style="font-size:16px;margin:18px 0 6px">💼 Your holdings — {port_sum}</h2>
      <div style="background:#fff;border-radius:8px;border:1px solid #e3e3e3;overflow:hidden">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="background:#f7f7f7"><th style="text-align:left;padding:6px 8px">Symbol</th><th style="text-align:left;padding:6px 8px">LTP</th><th style="text-align:left;padding:6px 8px">P&L</th><th style="text-align:left;padding:6px 8px">Score</th><th style="text-align:left;padding:6px 8px">Action</th><th style="text-align:left;padding:6px 8px">Stop</th><th style="text-align:left;padding:6px 8px">Setup</th></tr>
          {port_rows or "<tr><td colspan=7 style='padding:8px;color:#999'>No stock holdings.</td></tr>"}
        </table>
      </div>

      <div style="font-size:12px;margin-top:14px;color:#555"><b>Weakest in scan (avoid / short-bias):</b> {sells_html}</div>
      <div style="font-size:10px;color:#888;margin-top:16px;line-height:1.5">
        Generated automatically from Yahoo Finance daily data by your local Market Monitor. Technical signals are probabilities, not promises —
        the "model" numbers are this stock's own historical outcomes on days that scored similarly and can be wrong. Not investment advice;
        position-size so that a stop-out costs ≤ 1% of capital.
      </div>
    </div></body></html>"""


def build_email_text(rep: dict) -> str:
    reg = rep["market"]["regime"]
    lines = [f"Market Monitor — {rep['generated_label']}", f"Regime: {reg['label']} ({reg['score']}/100) — {reg['desc']}", ""]
    lines.append("HIGH-CONVICTION BUYS")
    for i, p in enumerate(rep.get("top_buys", []), 1):
        tp = p.get("trade_plan") or {}
        lines.append(f"{i}. {p['symbol']} {p['score']}/100 {p['verdict']} [{p['setup']}] @ {p['price']} | stop {tp.get('stop')} | T1 {tp.get('target1')} ({tp.get('target1_pct')}%) | T2 {tp.get('target2')} | R:R {tp.get('risk_reward')}")
    if not rep.get("top_buys"):
        lines.append("No setup cleared the quality bar today.")
    for title, key in (("CONSENSUS (engine + TradingView)", "consensus"), ("LOW-PRICE PICKS", "low_price_picks"), ("HIGHEST SCORES", "top_scores")):
        lines += ["", title]
        for p in rep.get(key, []) or []:
            tp = p.get("trade_plan") or {}
            lines.append(f"- {p['symbol']} ₹{p['price']} {p['score']} {p['verdict']} [{p['setup']}] stop {tp.get('stop')} T1 {tp.get('target1')} TV {(p.get('tv') or {}).get('rating_label', '—')}")
        if not rep.get(key):
            lines.append("- none")
    lines += ["", "HOLDINGS"]
    for h in rep.get("portfolio", []):
        lines.append(f"- {h['symbol']}: {h.get('score')} {h.get('verdict')} → {h.get('action')}")
    return "\n".join(lines)


def send_report_email(rep: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    to = cfg.get("email_to")
    if not (cfg.get("smtp_user") and cfg.get("smtp_pass") and to):
        return {"sent": False, "error": "SMTP not configured (need smtp_user, smtp_pass, email_to)"}
    n_buys = len(rep.get("top_buys", []))
    reg = rep["market"]["regime"]["label"]
    nifty = rep["market"]["indices"].get("NIFTY50", {})
    subject = (f"📈 {n_buys} high-conviction buy{'s' if n_buys != 1 else ''} · {reg} · "
               f"Nifty {nifty.get('change_pct', 0):+.2f}% · {datetime.now(IST).strftime('%d %b %H:%M')}")
    try:
        _smtp_send(cfg, to, subject, build_email_text(rep), build_email_html(rep))
        return {"sent": True, "to": to, "error": None, "subject": subject}
    except Exception as e:
        return {"sent": False, "to": to, "error": friendly_smtp_error(e)}


def friendly_smtp_error(e: Exception) -> str:
    """Turn SMTP server codes into an instruction the user can act on."""
    s = str(e)
    if "5.7.9" in s or "Application-specific password required" in s or "InvalidSecondFactor" in s:
        return ("Gmail rejected the login: it needs an App Password, not your normal password. "
                "Turn on 2-Step Verification, then create one at https://myaccount.google.com/apppasswords "
                "and paste the 16-letter code here.")
    if "5.7.8" in s or "Username and Password not accepted" in s or "BadCredentials" in s:
        return ("Username / password not accepted. For Gmail use the 16-letter App Password (not the account password) "
                "and your full address as the SMTP user. Details: " + s[:160])
    if "535" in s and "authentication failed" in s.lower():
        return "Authentication failed (535). Check the SMTP user and password. Details: " + s[:160]
    if "getaddrinfo" in s or "Name or service not known" in s or "11001" in s:
        return "Cannot resolve the SMTP host — check the host name (e.g. smtp.gmail.com) and your internet connection."
    if "timed out" in s.lower() or "10060" in s:
        return "Connection timed out — check the port (587 STARTTLS / 465 SSL) and any firewall / VPN."
    if "SSL" in s and "wrong version" in s.lower():
        return "TLS mismatch — use port 587 (STARTTLS) or 465 (SSL), not the other way round."
    return s


def _smtp_send(cfg: dict, to: str, subject: str, text: str, html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg.get('from_name') or 'Market Monitor'} <{cfg['smtp_user']}>"
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    recipients = [a.strip() for a in to.replace(";", ",").split(",") if a.strip()]
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context()) as s:
            s.login(cfg["smtp_user"], cfg["smtp_pass"])
            s.sendmail(cfg["smtp_user"], recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(cfg["smtp_user"], cfg["smtp_pass"])
            s.sendmail(cfg["smtp_user"], recipients, msg.as_string())


async def send_test_email() -> dict:
    cfg = load_config()
    if not (cfg.get("smtp_user") and cfg.get("smtp_pass") and cfg.get("email_to")):
        return {"sent": False, "error": "Fill SMTP user, password and recipient first"}
    html = ("<div style='font-family:Segoe UI,Arial;padding:16px'><h2>✅ Market Monitor e-mail works</h2>"
            f"<p>Scheduled scans will arrive at <b>{', '.join(cfg['times'])} IST</b> on "
            f"{'weekdays' if cfg.get('weekdays_only') else 'every day'} while the dashboard (or the scheduled task) is running.</p></div>")
    try:
        await asyncio.to_thread(_smtp_send, cfg, cfg["email_to"], "✅ Market Monitor test e-mail",
                                "Market Monitor e-mail works.", html)
        return {"sent": True, "to": cfg["email_to"]}
    except Exception as e:
        return {"sent": False, "error": friendly_smtp_error(e)}


# ════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ════════════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    # keep only the last 10 days
    for k in sorted(state)[:-10]:
        state.pop(k, None)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _due_slots(cfg: dict, now: datetime) -> list:
    today = now.strftime("%Y-%m-%d")
    done = set(_load_state().get(today, []))
    due = []
    for t in cfg.get("times", []):
        try:
            hh, mm = [int(x) for x in t.split(":")]
        except Exception:
            continue
        if (now.hour, now.minute) >= (hh, mm) and t not in done:
            due.append(t)
    return due


def scheduler_status() -> dict:
    cfg = load_config()
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    state = _load_state()
    next_run = None
    for t in sorted(cfg.get("times", [])):
        if t not in state.get(today, []) and t > now.strftime("%H:%M"):
            next_run = f"today {t} IST"
            break
    if not next_run and cfg.get("times"):
        nxt = now + timedelta(days=1)
        while cfg.get("weekdays_only") and nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        next_run = f"{nxt.strftime('%a %d %b')} {sorted(cfg['times'])[0]} IST"
    if not cfg.get("times"):
        next_run = "on dashboard start" if cfg.get("scan_on_start") else "no schedule"
    return {"enabled": cfg.get("enabled"), "times": cfg.get("times"), "weekdays_only": cfg.get("weekdays_only"),
            "scan_on_start": cfg.get("scan_on_start"),
            "universe": cfg.get("universe"), "ran_today": state.get(today, []), "next_run": next_run,
            "now_ist": now.strftime("%H:%M"), "email_configured": public_config(cfg)["smtp_configured"],
            "email_enabled": cfg.get("email_enabled"), **STATUS}


async def _tick():
    cfg = load_config()
    if not cfg.get("enabled"):
        return
    now = datetime.now(IST)
    if cfg.get("weekdays_only") and now.weekday() >= 5:
        return
    due = _due_slots(cfg, now)
    if not due or STATUS["running"]:
        return
    slot = sorted(due)[-1]
    print(f"[alerts] running scheduled scan for slot(s) {due} at {now.strftime('%H:%M IST')}")
    rep = await run_scan(slot=slot)
    state = _load_state()
    today = now.strftime("%Y-%m-%d")
    state.setdefault(today, [])
    for t in due:
        if t not in state[today]:
            state[today].append(t)
    _save_state(state)
    if "error" in rep:
        print(f"[alerts] scan failed: {rep['error']}")
    else:
        em = rep.get("email", {})
        print(f"[alerts] scan done — {len(rep.get('top_buys', []))} buys; e-mail: "
              f"{'sent to ' + str(em.get('to')) if em.get('sent') else (em.get('error') or 'not requested')}")


async def _startup_scan():
    """'Scan when the dashboard starts' — unless a scan already ran recently."""
    cfg = load_config()
    if not cfg.get("scan_on_start"):
        return
    gap = int(cfg.get("start_scan_gap_min") or 120)
    rep = latest_report()
    if rep:
        try:
            age = (datetime.now(IST) - datetime.fromisoformat(rep["generated_at"])).total_seconds() / 60
            if age < gap:
                print(f"[alerts] startup scan skipped — last scan {age:.0f} min ago (< {gap} min)")
                return
        except Exception:
            pass
    print("[alerts] running startup scan …")
    r = await run_scan(slot="startup")
    if "error" in r:
        print(f"[alerts] startup scan failed: {r['error']}")
    else:
        em = r.get("email", {})
        print(f"[alerts] startup scan done — {len(r.get('top_buys', []))} buys, {len(r.get('low_price_picks', []))} low-price picks; "
              f"e-mail: {'sent to ' + str(em.get('to')) if em.get('sent') else (em.get('error') or 'not requested')}")


async def scheduler_loop():
    """Run forever inside the server: startup scan, then check every 60 s whether a slot is due."""
    await asyncio.sleep(8)   # let the server come up first
    try:
        await _startup_scan()
    except Exception as e:
        print(f"[alerts] startup scan error: {e}")
    while True:
        try:
            await _tick()
        except Exception as e:
            print(f"[alerts] scheduler error: {e}")
        await asyncio.sleep(60)


async def run_cli(slot: str = "auto", universe: str | None = None, email: bool | None = None):
    """Used by scripts/daily_scan.py (Windows Task Scheduler)."""
    cfg = load_config()
    now = datetime.now(IST)
    if slot == "auto":
        due = _due_slots(cfg, now)
        if not due:
            # nothing scheduled is pending — still run, label by the nearest slot
            slot = min(cfg.get("times") or ["manual"], key=lambda t: abs(int(t[:2]) * 60 + int(t[3:]) - (now.hour * 60 + now.minute)))
        else:
            slot = sorted(due)[-1]
    rep = await run_scan(universe=universe, send_email=email, slot=slot)
    if "error" not in rep:
        state = _load_state()
        today = now.strftime("%Y-%m-%d")
        state.setdefault(today, [])
        if slot not in state[today]:
            state[today].append(slot)
        _save_state(state)
    return rep
