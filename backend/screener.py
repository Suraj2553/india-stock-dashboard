"""
screener.py
Comprehensive Indian market screener — symbol lists for all major NSE indices,
sectors, ETFs, and top mutual fund scheme codes.
"""

import asyncio
from market_data import fetch_quotes_batch

# ── Nifty 50 ────────────────────────────────────────────────────────────────
NIFTY50 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "HINDUNILVR", "ITC", "KOTAKBANK", "LT", "SBIN",
    "AXISBANK", "BAJFINANCE", "BHARTIARTL", "ASIANPAINT", "HCLTECH",
    "MARUTI", "SUNPHARMA", "TITAN", "NESTLEIND", "WIPRO",
    "ULTRACEMCO", "NTPC", "POWERGRID", "TECHM", "TATAMOTORS",
    "BAJAJ-AUTO", "DRREDDY", "DIVISLAB", "CIPLA", "COALINDIA",
    "ONGC", "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS",
    "INDUSINDBK", "BAJAJFINSV", "HEROMOTOCO", "EICHERMOT", "GRASIM",
    "HDFCLIFE", "SBILIFE", "APOLLOHOSP", "TATACONSUM", "BEL",
    "HINDALCO", "LTIM", "M&M", "SHRIRAMFIN", "TRENT",
]

# ── Nifty Next 50 ───────────────────────────────────────────────────────────
NIFTY_NEXT50 = [
    "ABB", "AMBUJACEM", "ATGL", "BAJAJHLDNG", "BANKBARODA",
    "BERGEPAINT", "BOSCHLTD", "CANBK", "CHOLAFIN", "COLPAL",
    "DLF", "DMART", "GAIL", "GODREJCP", "HAVELLS",
    "ICICIGI", "ICICIPRULI", "INDHOTEL", "INDUSTOWER", "IRCTC",
    "IRFC", "JIOFIN", "LODHA", "LUPIN", "MAXHEALTH",
    "MARICO", "MUTHOOTFIN", "NAUKRI", "NMDC", "OFSS",
    "PAYTM", "PNB", "PIDILITIND", "SRF", "SHREECEM",
    "SIEMENS", "TATAPOWER", "TORNTPHARM", "TVSMOTOR", "UNIONBANK",
    "VBL", "VEDL", "ZOMATO", "RECLTD", "PFC",
    "GODREJPROP", "ZYDUSLIFE", "BHARAT22ETF", "BPCL", "PETRONET",
]

# ── Nifty Midcap 100 (sample top 60) ────────────────────────────────────────
NIFTY_MIDCAP = [
    "ABCAPITAL", "ASTRAL", "AUBANK", "AUROPHARMA", "BALKRISIND",
    "BANDHANBNK", "BATAINDIA", "BHARATFORG", "BIOCON", "CUMMINSIND",
    "COROMANDEL", "DEEPAKNTR", "ESCORTS", "EXIDEIND", "FEDERALBNK",
    "GMRAIRPORT", "GLAXO", "GUJGASLTD", "HINDPETRO", "IDFCFIRSTB",
    "INDIAMART", "INDIGO", "KANSAINER", "LICHSGFIN", "MGL",
    "MOTHERSON", "MPHASIS", "OBEROIRLTY", "PAGEIND", "PERSISTENT",
    "PIIND", "POLICYBZR", "PRESTIGE", "PGHH", "SAIL",
    "SUNTV", "SUPREMEIND", "TORNTPOWER", "TATACOMM", "UBL",
    "VOLTAS", "COFORGE", "LTTS", "CROMPTON", "KAJARIACER",
    "PETRONET", "ALKYLAMINE", "BLUEDART", "CASTROLIND", "CESC",
    "CRAFTSMAN", "DALBHARAT", "DIXON", "EMAMILTD", "FINPIPE",
    "GLAXO", "GNFC", "GRANULES", "GRINDWELL", "GSPL",
]

# ── Nifty Smallcap 100 (sample top 50) ─────────────────────────────────────
NIFTY_SMALLCAP = [
    "AIAENG", "AJANTPHARMA", "ALOKINDS", "AMARAJABAT", "AMBER",
    "ANANTRAJ", "ANGELONE", "APARINDS", "APLLTD", "APTUS",
    "ASAHIINDIA", "ASHIANA", "ASTERDM", "BASF", "BBTC",
    "BIRLATYRES", "CAMPUS", "CAPLIPOINT", "CARBORUNIV", "CCL",
    "CERA", "DCMSHRIRAM", "DEEPAKFERT", "DELTACORP", "DHARAMSI",
    "EKC", "ELECON", "EPIGRAL", "EQUITASBNK", "ESABINDIA",
    "FANTAIN", "FINEORG", "FORCEMOT", "GLENMARK", "GPIL",
    "GRAPHITE", "HFCL", "HINDCOPPER", "INDIAGLYCO", "INDRAMEDCO",
    "INTELLECT", "IPCALAB", "IRCON", "JBMA", "JCHAC",
    "JKPAPER", "JTEKTINDIA", "JUBLINGREA", "KALPATPOWR", "KIMS",
]

# ── Sector-wise symbols ──────────────────────────────────────────────────────
SECTOR_SYMBOLS = {
    "Banking": [
        "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN",
        "INDUSINDBK", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK",
        "BANKBARODA", "CANBK", "PNB", "UNIONBANK", "YESBANK",
        "IDBI", "KARNATAKABK", "SOUTHBANK", "DCBBANK", "CSBBANK",
    ],
    "IT": [
        "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
        "LTIM", "MPHASIS", "OFSS", "PERSISTENT", "COFORGE",
        "TATAELXSI", "KPITTECH", "LTTS", "CYIENT", "MASTEK",
        "NAUKRI", "NEWGEN", "INTELLECT", "BIRLASOFT", "NIITTECH",
    ],
    "Pharma & Healthcare": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
        "LUPIN", "BIOCON", "AUROPHARMA", "TORNTPHARM", "ALKEM",
        "GLENMARK", "GRANULES", "GLAXO", "ZYDUSLIFE", "ABBOTINDIA",
        "IPCALAB", "AJANTPHARMA", "APLLTD", "CAPLIPOINT", "NATCOPHARM",
    ],
    "Auto & Ancillaries": [
        "MARUTI", "TATAMOTORS", "M&M", "HEROMOTOCO", "EICHERMOT",
        "BAJAJ-AUTO", "ASHOKLEY", "TVSMOTOR", "MOTHERSON", "BALKRISIND",
        "BHARATFORG", "ESCORTS", "BOSCHLTD", "EXIDEIND", "AMARAJABAT",
        "CRAFTSMAN", "ENDURANCE", "GABRIEL", "LUMAXIND", "SUPRAJIT",
    ],
    "FMCG": [
        "HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM",
        "DABUR", "MARICO", "GODREJCP", "EMAMILTD", "RADICO",
        "VBL", "COLPAL", "PGHH", "JYOTHYLAB", "BAJAJCON",
        "VARUNBEV", "CCL", "DEVYANI", "JUBLFOOD", "VSTIND",
    ],
    "Metal & Mining": [
        "JSWSTEEL", "TATASTEEL", "HINDALCO", "VEDL", "COALINDIA",
        "NMDC", "SAIL", "NATIONALUM", "HINDCOPPER", "WELCORP",
        "APL", "RATNAMANI", "JINDALSAW", "JSL", "GPIL",
        "MOIL", "SANDUMA", "SURAJEST", "TINPLATE", "MIRZAINT",
    ],
    "Realty": [
        "DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "BRIGADE",
        "SOBHA", "SUNTECK", "PHOENIX", "LODHA", "MAHLIFE",
        "KOLTEPATIL", "SIGNATURE", "ANANTRAJ", "ASHIANA", "ELDEHSG",
    ],
    "Energy & Oil": [
        "RELIANCE", "ONGC", "BPCL", "IOC", "GAIL",
        "PETRONET", "MGL", "IGL", "HINDPETRO", "ADANIGREEN",
        "TATAPOWER", "NTPC", "POWERGRID", "ATGL", "TORNTPOWER",
        "CESC", "JPPOWER", "NHPC", "SJVN", "RPOWER",
    ],
    "Finance & NBFC": [
        "BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "SBILIFE", "ICICIGI",
        "ICICIPRULI", "CHOLAFIN", "MUTHOOTFIN", "LICHSGFIN", "PFC",
        "IRFC", "JIOFIN", "ABCAPITAL", "MANAPPURAM", "SHRIRAMFIN",
        "IIFL", "RECLTD", "APTUS", "EQUITASBNK", "ANGELONE",
    ],
    "Infrastructure & Capital Goods": [
        "LT", "ADANIPORTS", "ADANIENT", "GMRAIRPORT", "INDUSTOWER",
        "IRCTC", "SIEMENS", "ABB", "CUMMINSIND", "KEC",
        "KALPATPOWR", "THERMAX", "BHEL", "BEL", "HAL",
        "BEML", "COCHINSHIP", "GRINDWELL", "TIINDIA", "ENGINERSIN",
    ],
    "Cement": [
        "ULTRACEMCO", "SHREECEM", "AMBUJACEM", "ACC", "DALMIACEM",
        "JKCEMENT", "RAMCOCEM", "HEIDELBERG", "BIRLACORP", "INDIACEM",
        "PRISMJOHNS", "NUVOCO", "SAGAR", "HIL", "STARCEMENT",
    ],
    "Consumer & Retail": [
        "TITAN", "TRENT", "PAGEIND", "BATAINDIA", "DMART",
        "INDIAMART", "NYKAA", "VMART", "CAMPUS", "METRO",
        "INDIGOPNTS", "ASTRAL", "HAVELLS", "VOLTAS", "WHIRLPOOL",
    ],
    "Telecom & Media": [
        "BHARTIARTL", "IDEA", "TATACOMM", "INDUSTOWER",
        "HFCL", "STLTECH", "TEJASNET", "ITI", "ONMOBILE",
    ],
    "PSU / Govt": [
        "NTPC", "POWERGRID", "COALINDIA", "ONGC", "BPCL",
        "IOC", "BEL", "HAL", "BHEL", "IRCTC",
        "IRFC", "RECLTD", "PFC", "NMDC", "SAIL",
        "BEML", "COCHINSHIP", "NHPC", "SJVN", "NBCC",
    ],
}

# ── ETF Symbol Lists ──────────────────────────────────────────────────────────
ETF_CATEGORIES = {
    "Broad Market Index": [
        "NIFTYBEES", "JUNIORBEES", "MON100", "SETFNIF50",
        "ICICINIFTY", "HDFCNIFTY", "KOTAKNIFTY", "SBIETF",
    ],
    "Banking": [
        "BANKBEES", "PSUBNKBEES", "ICICIB22",
    ],
    "Gold & Silver": [
        "GOLDBEES", "HDFCGOLD", "AXISGOLD", "KOTAKGOLD",
        "SBIETFGOLD", "NIPPONSILV",
    ],
    "Sectoral": [
        "ITBEES", "PHARMABEES", "INFRABEES", "AUTOBEES",
        "MAFANG", "CONSUMBEES",
    ],
    "International": [
        "N100", "HNGSNGBEES", "NASDAQ100",
    ],
    "Debt & Liquid": [
        "LIQUIDBEES", "CPSEETF", "BHARAT22ETF",
    ],
}

# ── Top Mutual Fund Scheme Codes (AMFI) ──────────────────────────────────────
# Format: {category: [{code, name, amc}]}
MF_SCHEMES = {
    "Large Cap": [
        {"code": "120716", "name": "Axis Bluechip Fund - Growth", "amc": "Axis"},
        {"code": "118989", "name": "Mirae Asset Large Cap Fund - Growth", "amc": "Mirae"},
        {"code": "119270", "name": "SBI Bluechip Fund - Growth", "amc": "SBI"},
        {"code": "119062", "name": "HDFC Top 100 Fund - Growth", "amc": "HDFC"},
        {"code": "120465", "name": "UTI Mastershare - Growth", "amc": "UTI"},
        {"code": "100119", "name": "Nippon India Large Cap - Growth", "amc": "Nippon"},
    ],
    "Flexi Cap": [
        {"code": "120503", "name": "Parag Parikh Flexi Cap - Growth", "amc": "PPFAS"},
        {"code": "119551", "name": "HDFC Flexi Cap Fund - Growth", "amc": "HDFC"},
        {"code": "120465", "name": "UTI Flexi Cap Fund - Growth", "amc": "UTI"},
        {"code": "119267", "name": "ICICI Pru Flexi Cap - Growth", "amc": "ICICI"},
        {"code": "145552", "name": "Motilal Oswal Flexi Cap - Growth", "amc": "Motilal"},
    ],
    "Mid Cap": [
        {"code": "119319", "name": "SBI Magnum Midcap - Growth", "amc": "SBI"},
        {"code": "118778", "name": "Nippon India Growth Fund - Growth", "amc": "Nippon"},
        {"code": "119554", "name": "HDFC Mid-Cap Opportunities - Growth", "amc": "HDFC"},
        {"code": "120503", "name": "Kotak Emerging Equity - Growth", "amc": "Kotak"},
        {"code": "118989", "name": "Axis Midcap Fund - Growth", "amc": "Axis"},
    ],
    "Small Cap": [
        {"code": "125354", "name": "SBI Small Cap Fund - Growth", "amc": "SBI"},
        {"code": "118778", "name": "Nippon India Small Cap - Growth", "amc": "Nippon"},
        {"code": "120716", "name": "Axis Small Cap Fund - Growth", "amc": "Axis"},
        {"code": "119062", "name": "HDFC Small Cap Fund - Growth", "amc": "HDFC"},
    ],
    "ELSS (Tax Saving)": [
        {"code": "119755", "name": "Axis Long Term Equity - Growth", "amc": "Axis"},
        {"code": "118989", "name": "Mirae Asset Tax Saver - Growth", "amc": "Mirae"},
        {"code": "119319", "name": "SBI Long Term Equity - Growth", "amc": "SBI"},
        {"code": "119062", "name": "Canara Robeco ELSS Tax Saver - Growth", "amc": "Canara"},
        {"code": "120841", "name": "Quant ELSS Tax Saver - Growth", "amc": "Quant"},
    ],
    "Index Funds": [
        {"code": "120594", "name": "UTI Nifty 50 Index Fund - Growth", "amc": "UTI"},
        {"code": "119267", "name": "HDFC Index Fund Nifty 50 - Growth", "amc": "HDFC"},
        {"code": "120503", "name": "SBI Nifty Index Fund - Growth", "amc": "SBI"},
        {"code": "118778", "name": "Nippon India Index Fund Nifty 50 - Growth", "amc": "Nippon"},
        {"code": "119554", "name": "ICICI Pru Nifty 50 Index - Growth", "amc": "ICICI"},
        {"code": "120716", "name": "Axis Nifty 100 Index Fund - Growth", "amc": "Axis"},
    ],
    "Hybrid / Balanced": [
        {"code": "119319", "name": "HDFC Balanced Advantage - Growth", "amc": "HDFC"},
        {"code": "118989", "name": "ICICI Pru Balanced Advantage - Growth", "amc": "ICICI"},
        {"code": "120503", "name": "Kotak Balanced Advantage - Growth", "amc": "Kotak"},
        {"code": "119270", "name": "SBI Equity Hybrid Fund - Growth", "amc": "SBI"},
        {"code": "119062", "name": "Mirae Asset Hybrid Equity - Growth", "amc": "Mirae"},
    ],
    "Liquid / Debt": [
        {"code": "119551", "name": "SBI Liquid Fund - Growth", "amc": "SBI"},
        {"code": "119267", "name": "HDFC Liquid Fund - Growth", "amc": "HDFC"},
        {"code": "119270", "name": "Axis Liquid Fund - Growth", "amc": "Axis"},
        {"code": "118778", "name": "Nippon India Liquid Fund - Growth", "amc": "Nippon"},
    ],
}

# ── All indices map (for frontend selector) ──────────────────────────────────
INDEX_MAP = {
    "nifty50":    NIFTY50,
    "next50":     NIFTY_NEXT50,
    "midcap":     NIFTY_MIDCAP,
    "smallcap":   NIFTY_SMALLCAP,
}


# ── Batch screener fetch ─────────────────────────────────────────────────────
async def fetch_screener_stocks(index: str = "nifty50", sector: str = "all") -> dict:
    """Fetch live quotes for an index or sector. Returns enriched stock list."""
    if sector != "all" and sector in SECTOR_SYMBOLS:
        symbols = SECTOR_SYMBOLS[sector]
        display = sector
    else:
        symbols = INDEX_MAP.get(index, NIFTY50)
        display = index

    # Batch in groups of 25 to avoid rate limits
    all_quotes = {}
    for i in range(0, len(symbols), 25):
        batch = symbols[i:i+25]
        chunk = await fetch_quotes_batch(batch)
        all_quotes.update(chunk)

    stocks = []
    for sym in symbols:
        q = all_quotes.get(sym)
        if q:
            stocks.append({
                "symbol":      sym,
                "name":        q.get("name", sym),
                "price":       q.get("price", 0),
                "change":      q.get("change", 0),
                "change_pct":  q.get("change_pct", 0),
                "day_high":    q.get("day_high", 0),
                "day_low":     q.get("day_low", 0),
                "prev_close":  q.get("prev_close", 0),
                "volume":      q.get("volume", 0),
                "market_state": q.get("market_state", "CLOSED"),
            })

    # Sort by absolute change_pct descending by default
    stocks.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    return {
        "index": index,
        "sector": sector,
        "display": display,
        "count": len(stocks),
        "stocks": stocks,
        "sectors": list(SECTOR_SYMBOLS.keys()),
    }


async def fetch_etf_board() -> dict:
    """Fetch all ETF quotes grouped by category."""
    all_symbols = [s for syms in ETF_CATEGORIES.values() for s in syms]
    # dedupe
    unique = list(dict.fromkeys(all_symbols))

    all_quotes = {}
    for i in range(0, len(unique), 25):
        chunk = await fetch_quotes_batch(unique[i:i+25])
        all_quotes.update(chunk)

    result = {}
    for cat, symbols in ETF_CATEGORIES.items():
        etfs = []
        for sym in symbols:
            q = all_quotes.get(sym)
            if q:
                etfs.append({
                    "symbol":     sym,
                    "name":       q.get("name", sym),
                    "price":      q.get("price", 0),
                    "change_pct": q.get("change_pct", 0),
                    "change":     q.get("change", 0),
                    "volume":     q.get("volume", 0),
                    "day_high":   q.get("day_high", 0),
                    "day_low":    q.get("day_low", 0),
                })
        result[cat] = etfs

    return result


async def fetch_mf_category(category: str) -> list:
    """Fetch NAV for all funds in a category. Returns with 1M/3M/1Y returns."""
    import httpx
    schemes = MF_SCHEMES.get(category, [])
    result  = []

    async def get_fund_data(scheme: dict) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"https://api.mfapi.in/mf/{scheme['code']}")
            data = r.json()
            navs = data.get("data", [])
            if not navs:
                return None
            meta = data.get("meta", {})
            latest_nav = float(navs[0]["nav"])

            def ret(n):
                if len(navs) > n:
                    old = float(navs[n]["nav"])
                    return round((latest_nav - old) / old * 100, 2) if old else None
                return None

            return {
                "code":     scheme["code"],
                "name":     meta.get("scheme_name", scheme["name"]),
                "amc":      meta.get("fund_house", scheme["amc"]),
                "category": meta.get("scheme_category", category),
                "nav":      latest_nav,
                "nav_date": navs[0]["date"],
                "ret_1m":   ret(21),
                "ret_3m":   ret(63),
                "ret_6m":   ret(126),
                "ret_1y":   ret(252),
            }
        except Exception:
            return None

    tasks = [get_fund_data(s) for s in schemes]
    fetched = await asyncio.gather(*tasks)
    return [f for f in fetched if f]
