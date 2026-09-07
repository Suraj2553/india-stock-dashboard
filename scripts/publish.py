"""
publish.py — publish an enhancement to BOTH repositories in one command.

    python scripts/publish.py -m "what changed"
    python scripts/publish.py -m "..." --dry-run     # show what each repo would receive
    python scripts/publish.py -m "..." --public-only / --private-only

Why both, always:
  * PUBLIC  Suraj2553/india-stock-dashboard   — sanitized code, no personal data.
    Runs push_to_github.py, whose privacy scan aborts on any e-mail, token,
    password, folio number or config secret.
  * PRIVATE Suraj2553/market-monitor-private  — the same code PLUS data/holdings.json
    and the Actions workflow. This is the copy that runs the twice-daily scans and
    e-mails them while the laptop is off, so leaving it behind means stale e-mails.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(script: str, args: list) -> int:
    print(f"\n{'=' * 62}\n  {script} {' '.join(args)}\n{'=' * 62}")
    return subprocess.run([sys.executable, str(ROOT / script), *args], cwd=str(ROOT)).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message", default="Update Market Monitor")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--public-only", action="store_true")
    ap.add_argument("--private-only", action="store_true")
    a = ap.parse_args()

    do_public = not a.private_only
    do_private = not a.public_only
    failures = []

    if do_public:
        args = ["--stored", "--message", a.message] + (["--dry-run"] if a.dry_run else [])
        if run("push_to_github.py", args) != 0:
            failures.append("PUBLIC repo (push_to_github.py)")

    if do_private:
        args = ["--message", a.message] + (["--dry-run"] if a.dry_run else [])
        if run("scripts/sync_private.py", args) != 0:
            failures.append("PRIVATE repo (sync_private.py)")

    print(f"\n{'=' * 62}")
    if failures:
        print("  INCOMPLETE — these targets failed:")
        for f in failures:
            print("   -", f)
        print("  Fix and re-run: the two repos must not drift apart.")
        sys.exit(1)
    if a.dry_run:
        print("  dry run finished — nothing was pushed")
    else:
        print("  Published to both repositories.")
        print("  public : https://github.com/Suraj2553/india-stock-dashboard  (no personal data)")
        print("  private: https://github.com/Suraj2553/market-monitor-private (holdings + scheduled scans)")


if __name__ == "__main__":
    main()
