"""
parse_groww.py
==============
Reads CSV files from imports/ folder and updates data/holdings.json.

SUPPORTED FORMATS (auto-detected):
  - Groww stocks CSV export
  - Groww MF CSV export  
  - CAMS / KFintech statement CSV
  - MF Central CAS CSV
  - Any CSV with recognisable column names

USAGE:
  Auto mode (reads everything in imports/ folder):
    python scripts/parse_groww.py

  Manual mode (specify files):
    python scripts/parse_groww.py --stocks path/to/stocks.csv
    python scripts/parse_groww.py --mf path/to/mf.csv
    python scripts/parse_groww.py --stocks s.csv --mf m.csv
"""

import csv, json, re, sys, argparse
from pathlib import Path
from datetime import date

ROOT       = Path(__file__).parent.parent
DATA_FILE  = ROOT / "data" / "holdings.json"
IMPORT_DIR = ROOT / "imports"

SECTOR_MAP = {
    "RELIANCE":"Energy",    "ONGC":"Energy",       "COALINDIA":"Energy",
    "BPCL":"Energy",        "IOC":"Energy",         "GAIL":"Energy",
    "NTPC":"Utilities",     "POWERGRID":"Utilities","TATAPOWER":"Utilities",
    "INFY":"IT",            "TCS":"IT",             "WIPRO":"IT",
    "HCLTECH":"IT",         "TECHM":"IT",           "LTIM":"IT",
    "MPHASIS":"IT",         "PERSISTENT":"IT",      "COFORGE":"IT",
    "HDFCBANK":"Banking",   "ICICIBANK":"Banking",  "AXISBANK":"Banking",
    "SBIN":"Banking",       "KOTAKBANK":"Banking",  "INDUSINDBK":"Banking",
    "BANDHANBNK":"Banking", "FEDERALBNK":"Banking", "IDFCFIRSTB":"Banking",
    "BAJFINANCE":"NBFC",    "BAJAJFINSV":"NBFC",    "CHOLAFIN":"NBFC",
    "TITAN":"Consumer",     "ASIANPAINT":"Consumer","HINDUNILVR":"FMCG",
    "ITC":"FMCG",           "NESTLEIND":"FMCG",     "DABUR":"FMCG",
    "MARICO":"FMCG",        "GODREJCP":"FMCG",
    "MARUTI":"Auto",        "TATAMOTORS":"Auto",    "BAJAJ-AUTO":"Auto",
    "HEROMOTOCO":"Auto",    "EICHERMOT":"Auto",
    "SUNPHARMA":"Pharma",   "DRREDDY":"Pharma",     "DIVISLAB":"Pharma",
    "CIPLA":"Pharma",       "APOLLOHOSP":"Pharma",
    "ULTRACEMCO":"Materials","JSWSTEEL":"Materials","TATASTEEL":"Materials",
    "HINDALCO":"Materials", "LT":"Infra",           "ADANIPORTS":"Infra",
    "BHARTIARTL":"Telecom", "ADANIENT":"Conglomerate",
    # Gold ETFs / Commodity ETFs
    "TATAGOLD":"Gold ETF",   "GOLDBEES":"Gold ETF",   "AXISGOLD":"Gold ETF",
    "HDFCGOLD":"Gold ETF",   "SBIGOLD":"Gold ETF",    "NIPGOLD":"Gold ETF",
    "GOLDSHARE":"Gold ETF",  "CRMFGOLD":"Gold ETF",   "ABSLGOLD":"Gold ETF",
    "LICSGOLD":"Gold ETF",   "MGOLD":"Gold ETF",      "QGOLDHALF":"Gold ETF",
    # Silver ETFs
    "SILVERETF":"Silver ETF","SILVERBEES":"Silver ETF","TATASILV":"Silver ETF",
    # Index ETFs
    "NIFTYBEES":"Index ETF", "JUNIORBEES":"Index ETF","BANKBEES":"Index ETF",
    "SETFNIF50":"Index ETF", "ICICIB22":"Index ETF",  "MOM100":"Index ETF",
}


def clean(s):
    """Strip ₹ , spaces % from a number string."""
    return re.sub(r"[₹,\s%]", "", str(s or "0")).strip()


def nk(row):
    """Normalise dict keys to lowercase stripped strings."""
    return {k.strip().lower(): str(v).strip() for k, v in row.items()}


def pick(row, *keys):
    """Return first non-empty value from multiple possible column names."""
    for k in keys:
        v = row.get(k.lower(), "")
        if v and v.lower() not in ("nan", "none", "-", "n/a", "", "0.0"):
            return v
    return ""


def detect_type(filepath: Path) -> str:
    """
    Returns 'stocks', 'mf', or 'unknown' by scanning the file content.
    """
    try:
        with open(filepath, encoding="utf-8-sig", errors="ignore") as f:
            content = f.read(3000).lower()

        mf_hits = sum(1 for kw in [
            "scheme name", "fund name", "scheme code", "amfi",
            "nav", "units", "folio", "balance units", "mutual fund",
            "direct growth", "direct plan"
        ] if kw in content)

        stock_hits = sum(1 for kw in [
            "symbol", "ticker", "scrip", "avg buy price",
            "average price", "quantity", "qty", "nse", "bse",
            "ltp", "shares"
        ] if kw in content)

        if mf_hits > stock_hits:
            return "mf"
        elif stock_hits > 0:
            return "stocks"
        else:
            return "unknown"
    except Exception:
        return "unknown"


def find_header_row(lines: list, keywords: list) -> int:
    """Find the row index that looks like a CSV header."""
    for i, line in enumerate(lines[:30]):
        ll = line.lower()
        hits = sum(1 for kw in keywords if kw in ll)
        if hits >= 2:
            return i
    return 0


# ── Parse stocks ──────────────────────────────────────────────────────────
def parse_stocks(filepath: Path) -> list:
    print(f"\n  Parsing stocks: {filepath.name}")

    with open(filepath, encoding="utf-8-sig", errors="ignore") as f:
        lines = f.readlines()

    hi = find_header_row(lines, ["symbol", "scrip", "stock", "qty", "quantity", "price", "avg"])
    data_lines = lines[hi:]

    try:
        reader = list(csv.DictReader(data_lines))
    except Exception as e:
        print(f"  ✗ Could not read CSV: {e}")
        return []

    stocks, skipped = [], 0
    for raw in reader:
        r = nk(raw)

        # Symbol — try many column name variants
        symbol = pick(r,
            "symbol", "ticker", "scrip", "scrip code",
            "nse symbol", "bse symbol", "trading symbol",
            "stock symbol", "instrument"
        ).upper().replace(" ", "").replace("-EQ", "").replace("-BE", "")

        # Name
        name = pick(r,
            "company name", "stock name", "name",
            "scrip name", "instrument name", "security name",
            "stock"
        ) or symbol

        # Quantity
        qty_s = pick(r,
            "quantity", "qty", "shares", "net quantity",
            "total quantity", "no. of shares", "no of shares",
            "balance qty", "holdings"
        )

        # Average buy price — this is the most important field
        avg_s = pick(r,
            "avg buy price", "avg. buy price",
            "average price", "avg price",
            "average buy price", "buy avg price",
            "avg cost", "average cost",
            "purchase price", "cost price",
            "avg purchase price", "invested price",
            "buy price", "cost"
        )

        # Skip if no price — fall back to ltp only if nothing else
        if not avg_s:
            avg_s = pick(r, "ltp", "last price", "current price")

        # Clean and parse
        try:
            qty = int(float(clean(qty_s))) if qty_s else 0
            avg = float(clean(avg_s)) if avg_s else 0.0
        except (ValueError, ZeroDivisionError):
            skipped += 1
            continue

        # Skip invalid rows
        if qty <= 0 or avg <= 0 or not symbol or len(symbol) < 2:
            skipped += 1
            continue

        # Skip header-like rows that slipped through
        if symbol in ("SYMBOL", "TICKER", "SCRIP", "NSE", "BSE", "STOCK"):
            continue

        # Auto-detect sector
        sector = SECTOR_MAP.get(symbol, "Others")

        stocks.append({
            "symbol":        symbol,
            "name":          name.strip(),
            "exchange":      "NSE",
            "quantity":      qty,
            "avg_buy_price": round(avg, 2),
            "sector":        sector,
        })

    print(f"  ✓ {len(stocks)} stocks parsed  ({skipped} rows skipped)")

    if not stocks:
        print("\n  ⚠  No stocks could be parsed. Possible reasons:")
        print("     - File has different column names than expected")
        print("     - File uses a different format")
        print("\n  Column names found in your file:")
        if data_lines:
            try:
                header = list(csv.DictReader(data_lines[:2]))[0] if len(data_lines) > 1 else {}
                for k in header.keys():
                    print(f"     '{k}'")
            except:
                pass
        print("\n  See SAMPLE_stocks_format.csv in imports/ for the expected format.")

    return stocks


# ── Parse MF ──────────────────────────────────────────────────────────────
def parse_mf(filepath: Path) -> list:
    print(f"\n  Parsing mutual funds: {filepath.name}")

    with open(filepath, encoding="utf-8-sig", errors="ignore") as f:
        lines = f.readlines()

    hi = find_header_row(lines, ["scheme", "fund", "units", "nav", "folio"])
    data_lines = lines[hi:]

    try:
        reader = list(csv.DictReader(data_lines))
    except Exception as e:
        print(f"  ✗ Could not read CSV: {e}")
        return []

    mfs, skipped = [], 0
    for raw in reader:
        r = nk(raw)

        # Fund name
        name = pick(r,
            "scheme name", "fund name", "name", "scheme",
            "plan name", "product name", "mutual fund name",
            "fund", "scheme description"
        ).strip()

        # Units
        units_s = pick(r,
            "units", "balance units", "unit balance",
            "total units", "closing units", "net units",
            "available units", "no. of units", "no of units",
            "units held"
        )

        # Average NAV / cost
        avg_s = pick(r,
            "avg nav", "average nav", "avg cost nav",
            "average cost", "purchase nav", "buy nav",
            "avg purchase nav", "cost nav",
            "average purchase price", "avg. nav",
            "avg buy nav", "cost per unit"
        )

        # Folio
        folio = pick(r,
            "folio number", "folio no", "folio",
            "account number", "account no"
        ) or "N/A"

        # Scheme code (AMFI code)
        code = pick(r,
            "scheme code", "amfi code", "amfi no",
            "scheme no", "fund code", "amfi scheme code"
        )

        # ISIN fallback (not same as scheme code but better than nothing)
        if not code:
            code = pick(r, "isin")

        try:
            units = round(float(clean(units_s)), 3) if units_s else 0.0
            avg   = round(float(clean(avg_s)), 4) if avg_s else 0.0
        except (ValueError, ZeroDivisionError):
            skipped += 1
            continue

        if units <= 0 or not name or len(name) < 4:
            skipped += 1
            continue

        # Skip header-like rows
        if name.lower() in ("scheme name", "fund name", "name", "total", "grand total"):
            continue

        mfs.append({
            "scheme_code": code,
            "name":        name,
            "units":       units,
            "avg_nav":     avg,
            "folio":       folio.strip(),
        })

    # Warn about missing scheme codes with lookup URLs
    missing = [m for m in mfs if not m["scheme_code"]]
    if missing:
        print(f"\n  ⚠  scheme_code missing for {len(missing)} fund(s)")
        print("     Paste these URLs in browser to find the code:")
        for m in missing:
            q = "+".join(m["name"].split()[:5])
            print(f"\n     Fund: {m['name'][:60]}")
            print(f"     URL:  https://api.mfapi.in/mf/search?q={q}")
        print("\n     Then add the schemeCode to data/holdings.json manually.")

    print(f"  ✓ {len(mfs)} MFs parsed  ({skipped} rows skipped)")

    if not mfs:
        print("\n  ⚠  No MFs could be parsed. Possible reasons:")
        print("     - File has different column names")
        print("\n  Column names found in your file:")
        if data_lines:
            try:
                header = list(csv.DictReader(data_lines[:2]))[0] if len(data_lines) > 1 else {}
                for k in header.keys():
                    print(f"     '{k}'")
            except:
                pass
        print("\n  See SAMPLE_mf_format.csv in imports/ for the expected format.")

    return mfs


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Import CSV files into data/holdings.json"
    )
    ap.add_argument("--stocks", help="Stocks CSV path")
    ap.add_argument("--mf",     help="Mutual funds CSV path")
    ap.add_argument("--auto",   action="store_true",
                    help="Auto-scan the imports/ folder (default if no args given)")
    ap.add_argument("--out",    default=str(DATA_FILE))
    args = ap.parse_args()

    # Load existing holdings to preserve unchanged side
    existing = {"stocks": [], "mutual_funds": [], "last_updated": ""}
    out = Path(args.out)
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            pass

    processed = False

    # Manual mode
    if args.stocks:
        result = parse_stocks(Path(args.stocks))
        if result:
            existing["stocks"] = result
            processed = True

    if args.mf:
        result = parse_mf(Path(args.mf))
        if result:
            existing["mutual_funds"] = result
            processed = True

    # Auto mode — scan imports/ folder
    if args.auto or (not args.stocks and not args.mf):
        IMPORT_DIR.mkdir(exist_ok=True)
        # Find CSVs, skip the sample files
        csv_files = [
            f for f in list(IMPORT_DIR.glob("*.csv")) + list(IMPORT_DIR.glob("*.CSV"))
            if not f.name.startswith("SAMPLE_")
        ]

        if not csv_files:
            print(f"\n  No CSV files found in:  {IMPORT_DIR}")
            print(f"\n  How to add your data:")
            print(f"  Option 1 — Drop your Groww CSV into the imports/ folder and run this script")
            print(f"  Option 2 — Open http://localhost:8080/edit_holdings.html to enter manually")
            print(f"  Option 3 — Edit data/holdings.json directly")
            print(f"\n  Sample CSV formats are in imports/SAMPLE_*.csv")
            return

        print(f"\n  Found {len(csv_files)} CSV file(s) in imports/")
        for f in csv_files:
            ftype = detect_type(f)
            print(f"\n  Detected type: {ftype.upper()} — {f.name}")
            if ftype == "stocks":
                result = parse_stocks(f)
                if result:
                    existing["stocks"] = result
                    processed = True
            elif ftype == "mf":
                result = parse_mf(f)
                if result:
                    existing["mutual_funds"] = result
                    processed = True
            else:
                print(f"  ⚠  Cannot detect if this is stocks or MF.")
                print(f"     Run manually: python scripts/parse_groww.py --stocks {f.name}")
                print(f"     Or:           python scripts/parse_groww.py --mf {f.name}")

    if not processed:
        return

    existing["last_updated"] = date.today().isoformat()
    out.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"\n{'='*55}")
    print(f"  ✅  holdings.json updated successfully!")
    print(f"  Stocks : {len(existing['stocks'])}")
    print(f"  MFs    : {len(existing['mutual_funds'])}")
    print(f"  File   : {out}")
    print(f"{'='*55}")
    print(f"\n  → Refresh browser: http://localhost:8080")
    print(f"  → Or use the editor: http://localhost:8080/edit_holdings.html\n")


if __name__ == "__main__":
    main()
