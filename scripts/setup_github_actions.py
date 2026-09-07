"""
setup_github_actions.py — upload the e-mail settings (and optionally holdings) from this
machine to the repository's ENCRYPTED Actions secrets, so .github/workflows/scan.yml can
run the twice-daily scan when your laptop is off.

    python scripts/setup_github_actions.py                  # uses Git's stored GitHub credential
    python scripts/setup_github_actions.py --no-holdings    # e-mail secrets only (report skips the holdings plan)
    python scripts/setup_github_actions.py --repo owner/name

Secrets are encrypted in your browser-equivalent here (libsodium sealed box with the repo's
public key) and are never visible in the repo, the workflow file or the logs.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
DEFAULT_REPO = "Suraj2553/market-monitor-private"   # PRIVATE repo — never the public one


def _token() -> str:
    t = os.environ.get("GITHUB_PUSH_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True).stdout
    cred = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
    if cred.get("password"):
        return cred["password"]
    sys.exit("No GitHub credential found. Set GITHUB_PUSH_TOKEN (repo scope) and re-run.")


def _api(method: str, url: str, token: str, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "market-monitor", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--no-holdings", action="store_true")
    ap.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args()

    try:
        from nacl import encoding, public
    except ImportError:
        sys.exit("Run first:  pip install pynacl")

    import alerts
    cfg = alerts.load_config()
    missing = [k for k in ("smtp_user", "smtp_pass", "email_to") if not cfg.get(k)]
    if missing:
        sys.exit(f"E-mail is not configured locally ({', '.join(missing)}). Set it in the dashboard Settings first.")

    secrets = {
        "SMTP_HOST": cfg.get("smtp_host") or "smtp.gmail.com",
        "SMTP_PORT": str(cfg.get("smtp_port") or 587),
        "SMTP_USER": cfg["smtp_user"],
        "SMTP_PASS": cfg["smtp_pass"],
        "ALERT_EMAIL_TO": cfg["email_to"],
        "ALERT_UNIVERSE": cfg.get("universe") or "nifty100",
        "ALERT_CAPITAL": str(cfg.get("capital") or 100000),
        "ALERT_MIN_SCORE": str(cfg.get("min_score") or 65),
        "ALERT_LOW_PRICE_MAX": str(cfg.get("low_price_max") or 300),
        "ALERT_LOW_PRICE_MIN_SCORE": str(cfg.get("low_price_min_score") or 75),
    }
    holdings = ROOT / "data" / "holdings.json"
    if not a.no_holdings and holdings.exists():
        secrets["HOLDINGS_JSON"] = holdings.read_text(encoding="utf-8")

    print(f"Repository: {a.repo}")
    # refuse to write secrets into a public repository
    try:
        info = _api("GET", f"https://api.github.com/repos/{a.repo}", _token())
        if not info.get("private"):
            sys.exit(f"REFUSING: {a.repo} is PUBLIC. Secrets belong only in a private repo.")
        print("visibility: private ✓")
    except SystemExit:
        raise
    except Exception as e:
        print("could not verify visibility:", e)
    print("Secrets to upload (encrypted):", ", ".join(secrets))
    print("Holdings included:", "yes" if "HOLDINGS_JSON" in secrets else "no")
    if not a.yes and sys.stdin.isatty():
        try:
            if input("Proceed? [y/N] ").strip().lower() != "y":
                sys.exit("cancelled")
        except EOFError:
            pass

    token = _token()
    key = _api("GET", f"https://api.github.com/repos/{a.repo}/actions/secrets/public-key", token)
    pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    box = public.SealedBox(pk)
    for name, value in secrets.items():
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
        _api("PUT", f"https://api.github.com/repos/{a.repo}/actions/secrets/{name}", token,
             {"encrypted_value": enc, "key_id": key["key_id"]})
        print(f"  set {name}")
    print("\nDone. GitHub will run .github/workflows/scan.yml at 09:00 and 15:45 IST on weekdays.")
    print("Test it: repo -> Actions -> 'Market scan + e-mail' -> Run workflow.")
    print("Non-credential settings (universe, capital, score thresholds) are better kept as Actions "
          "VARIABLES than secrets, so ordinary words are not masked in the logs.")


if __name__ == "__main__":
    main()
