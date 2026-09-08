"""
sync_private.py — push this project **plus your holdings** to your PRIVATE GitHub repo,
which runs the scheduled scans and e-mails them while your laptop is off.

    python scripts/sync_private.py                 # sync code + holdings
    python scripts/sync_private.py --holdings-only # fastest: just refresh holdings after a buy/sell
    python scripts/sync_private.py --dry-run       # show what would be sent

What IS sent: all code, the workflow, and data/holdings.json.
What is NEVER sent: .env, data/alerts_config.json and data/llm_config.json (they hold your SMTP
password and API keys — those live only in the repo's encrypted Actions secrets), data/scans/,
the virtual-env and node folders.

The public repo is a different target and never receives any of this — use push_to_github.py for that.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
def private_repo_url() -> str:
    """--repo > MM_PRIVATE_REPO env > 'private_repo' in data/alerts_config.json. Never a default:
    this must be YOUR repo, so a fresh clone of the public project simply has the feature off."""
    url = os.environ.get("MM_PRIVATE_REPO", "").strip()
    if url:
        return url
    cfg = ROOT / "data" / "alerts_config.json"
    if cfg.exists():
        try:
            return (json.loads(cfg.read_text(encoding="utf-8")).get("private_repo") or "").strip()
        except Exception:
            pass
    return ""

SKIP_DIRS = {"__pycache__", ".claude", ".git", ".venv", "venv", "scans", "node22", "node_modules"}
SKIP_FILES = {".env", "llm_config.json", "alerts_config.json", ".mcp.json", "push_to_github.py",
              "package.json", "package-lock.json", ".DS_Store", "Thumbs.db"}
SKIP_EXTS = {".pyc", ".pyo", ".zip", ".log"}

# The public .gitignore deliberately excludes data/holdings.json — but the private repo NEEDS it,
# otherwise the cloud scan silently falls back to the sample portfolio. So the private copy gets
# its own .gitignore that hides only credentials and build junk.
PRIVATE_GITIGNORE = """# Private repo — holdings ARE tracked here on purpose (the scheduled scan reads them).
# Credentials never are: they live in this repo's encrypted Actions secrets.
.env
data/llm_config.json
data/alerts_config.json
.mcp.json
.venv/
venv/
tools/node22/
tools/node_modules/
tools/package.json
tools/package-lock.json
__pycache__/
*.pyc
*.log
.claude/
.DS_Store
Thumbs.db
"""


def copy_project(dest: Path):
    for root, dirs, files in os.walk(ROOT):
        rel_root = Path(root).relative_to(ROOT)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or Path(f).suffix in SKIP_EXTS:
                continue
            if f.endswith(".csv") and str(rel_root) == "imports" and not f.startswith("SAMPLE_"):
                continue
            src = Path(root) / f
            dst = dest / rel_root / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def git(*args, cwd, env=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, env=env,
                          capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None)
    ap.add_argument("--message", default=None)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--holdings-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    a.repo = a.repo or private_repo_url()
    if not a.repo:
        sys.exit("No private repo configured. This feature is optional: set \"private_repo\" in "
                 "data/alerts_config.json (or the MM_PRIVATE_REPO environment variable) to your own "
                 "private GitHub repository, e.g. https://github.com/<you>/market-monitor-private.git")
    holdings = ROOT / "data" / "holdings.json"
    if not holdings.exists():
        sys.exit("data/holdings.json not found.")
    h = json.loads(holdings.read_text(encoding="utf-8"))
    summary = f"{len(h.get('stocks', []))} stocks, {len(h.get('mutual_funds', []))} funds"

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    work = Path(tempfile.mkdtemp(prefix="mm_private_"))
    try:
        r = git("clone", "-q", "--branch", a.branch, "--single-branch", a.repo, str(work), cwd=ROOT, env=env, check=False)
        fresh = r.returncode != 0
        if fresh:
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir()
            git("init", "-q", "-b", a.branch, cwd=work, env=env)
            git("remote", "add", "origin", a.repo, cwd=work, env=env)
            print("private repo is empty — creating the first commit")

        if a.holdings_only and not fresh:
            (work / "data").mkdir(exist_ok=True)
            shutil.copy2(holdings, work / "data" / "holdings.json")
        else:
            for item in work.iterdir():
                if item.name in (".git", "data"):
                    continue
                shutil.rmtree(item) if item.is_dir() else item.unlink()
            # keep the archived scan reports the workflow committed
            for item in (work / "data").iterdir() if (work / "data").exists() else []:
                if item.name != "scans":
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
            copy_project(work)
        # private-repo .gitignore so holdings are actually tracked
        (work / ".gitignore").write_text(PRIVATE_GITIGNORE, encoding="utf-8")

        files = sorted(str(p.relative_to(work)).replace("\\", "/") for p in work.rglob("*")
                       if p.is_file() and ".git/" not in str(p.relative_to(work)).replace("\\", "/"))
        leaked = [f for f in files if Path(f).name in (".env", "alerts_config.json", "llm_config.json")]
        if leaked:
            sys.exit(f"refusing to sync — credential files present: {leaked}")

        if a.dry_run:
            print(f"{len(files)} files would be synced to {a.repo}")
            print("  holdings:", summary)
            print("  includes:", ", ".join(f for f in files if f.startswith("data/") or f.startswith(".github/")))
            return

        git("add", "-A", cwd=work, env=env)
        git("add", "-f", "data/holdings.json", cwd=work, env=env)      # belt and braces
        tracked = git("ls-files", "data/holdings.json", cwd=work, env=env).stdout.strip()
        if not tracked:
            sys.exit("ERROR: data/holdings.json is not being tracked — the cloud scan would use the sample portfolio.")
        if not git("status", "--porcelain", cwd=work, env=env).stdout.strip():
            print("already up to date — nothing to sync")
            return
        msg = a.message or (f"Update holdings ({summary})" if a.holdings_only else f"Sync from laptop ({summary})")
        git("-c", "user.name=Market Monitor", "-c", "user.email=noreply@example.com",
            "commit", "-q", "-m", msg, cwd=work, env=env)
        git("push", "-u", "origin", a.branch, cwd=work, env=env)
        print(f"synced to private repo: {msg}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
