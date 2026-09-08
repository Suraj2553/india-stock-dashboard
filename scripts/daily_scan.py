"""
daily_scan.py — run one scheduled scan (+ e-mail) without the dashboard open.

    python scripts\daily_scan.py                 # auto: picks the pending slot (08:45 / 15:45)
    python scripts\daily_scan.py --universe nifty50 --no-email
    python scripts\daily_scan.py --slot 15:45

Register it with Windows Task Scheduler via scripts\install_scheduler.ps1 so it
runs twice a day whenever the laptop is on (missed runs start at next boot).
When the dashboard server is running, it already does this by itself — the
shared state file prevents duplicate e-mails.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def _load_env():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def main():
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="auto")
    ap.add_argument("--universe", default=None)
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--email", action="store_true")
    ap.add_argument("--skip-if-done", action="store_true",
                    help="exit quietly if this slot already ran today (lets a cron retry safely)")
    ap.add_argument("--quiet", action="store_true", help="print only counts (safe for public CI logs: no symbols, no e-mail address)")
    a = ap.parse_args()
    email = None
    if a.no_email:
        email = False
    elif a.email:
        email = True

    import alerts
    if a.skip_if_done and a.slot != "auto":
        from datetime import datetime
        today = datetime.now(alerts.IST).strftime("%Y-%m-%d")
        if a.slot in alerts._load_state().get(today, []):
            print(f"slot {a.slot} already ran today ({today}) — nothing to do")
            return
    rep = asyncio.run(alerts.run_cli(slot=a.slot, universe=a.universe, email=email))
    if "error" in rep:
        print("SCAN FAILED:", rep["error"])
        sys.exit(1)
    reg = rep["market"]["regime"]
    if a.quiet:
        em = rep.get("email", {})
        print(f"{rep['generated_label']} | {rep['universe_label']} | {rep['ok']}/{rep['scanned']} analysed | regime {reg['label']} {reg['score']}/100 | "
              f"{len(rep.get('top_buys', []))} buys, {len(rep.get('low_price_picks', []))} low-price, {len(rep.get('consensus', []))} consensus | "
              f"e-mail: {'sent' if em.get('sent') else (em.get('error') or 'not requested')}")
        return
    print(f"{rep['generated_label']} | {rep['universe_label']} | regime {reg['label']} {reg['score']}/100")
    for i, p in enumerate(rep.get("top_buys", []), 1):
        tp = p.get("trade_plan") or {}
        print(f"  {i}. {p['symbol']:12} {p['score']:3} {p['verdict']:12} {p['setup']:20} "
              f"@ {p['price']} stop {tp.get('stop')} T1 {tp.get('target1')} R:R {tp.get('risk_reward')}")
    if not rep.get("top_buys"):
        print("  (no setup cleared the quality bar)")
    em = rep.get("email", {})
    print("e-mail:", "sent to " + str(em.get("to")) if em.get("sent") else (em.get("error") or "not requested"))
    print("saved:", alerts.LATEST_FILE)


if __name__ == "__main__":
    main()
