"""
news.py
Market news from RSS feeds + stock-specific news filtering.
"""

import asyncio
import re
import time
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from utils import strip_html

FEEDS = [
    ("ET Markets",    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("MoneyControl",  "https://www.moneycontrol.com/rss/MCtopnews.xml"),
    ("LiveMint",      "https://www.livemint.com/rss/markets"),
    ("ET Stocks",     "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    ("ET MF",         "https://economictimes.indiatimes.com/mf/rssfeeds/13881834.cms"),
    ("Business Std",  "https://www.business-standard.com/rss/markets-106.rss"),
]

_NEWS_CACHE = {"expires": 0.0, "data": None}
_NEWS_TTL = 180   # seconds

# words in company names that would match far too much
_STOP = {"limited", "india", "indian", "ltd", "corp", "corporation", "company", "industries",
         "the", "and", "of", "bank", "finance", "financial", "services", "international", "group",
         "enterprises", "holdings", "power", "energy", "motors", "pharma", "life", "fund",
         "exchange", "traded", "growth", "direct", "plan", "market", "markets", "stock", "stocks",
         "share", "shares", "capital", "global", "national", "digital", "technologies", "technology",
         "solutions", "systems", "products", "general", "insurance", "steel", "auto", "cement",
         "chemicals", "petroleum", "electronics", "healthcare", "hospitals", "laboratories"}


async def fetch_feed(source: str, url: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.text)
        channel = root.find("channel")
        if channel is None:
            return []
        items = []
        for item in channel.findall("item")[:15]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            items.append({
                "source": source,
                "title": title,
                "link": (item.findtext("link") or "").strip(),
                "description": strip_html(item.findtext("description") or "")[:350],
                "published": (item.findtext("pubDate") or "").strip(),
            })
        return items
    except Exception as e:
        print(f"[news] {source}: {e}")
        return []


def _parse_date(s: str) -> float:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return 0.0


async def fetch_all_news(limit: int = 40) -> dict:
    now = time.time()
    if _NEWS_CACHE["data"] and _NEWS_CACHE["expires"] > now:
        data = _NEWS_CACHE["data"]
        return {"articles": data["articles"][:limit], "fetched_at": data["fetched_at"], "cached": True}
    results = await asyncio.gather(*[fetch_feed(s, u) for s, u in FEEDS])
    articles = [item for feed in results for item in feed]
    # newest first, de-duplicated by title
    seen, unique = set(), []
    for a in sorted(articles, key=lambda a: _parse_date(a["published"]), reverse=True):
        k = a["title"].lower()[:80]
        if k in seen:
            continue
        seen.add(k)
        unique.append(a)
    data = {"articles": unique, "fetched_at": datetime.now().isoformat()}
    _NEWS_CACHE.update({"expires": now + _NEWS_TTL, "data": data})
    return {"articles": unique[:limit], "fetched_at": data["fetched_at"], "cached": False}


def filter_news_for_symbol(articles: list, symbol: str, company_name: str = "") -> list:
    """Return articles mentioning this stock (whole-word match, so IOC ≠ SOCIAL)."""
    keywords = {symbol.upper()}
    if company_name:
        for word in re.split(r"[\s\-&()]+", company_name):
            w = word.strip().lower()
            if len(w) > 3 and w not in _STOP:
                keywords.add(w.upper())
    pats = [re.compile(r"\b" + re.escape(k) + r"\b", re.I) for k in keywords]
    matching = []
    for a in articles:
        text = a["title"] + " " + a["description"]
        if any(p.search(text) for p in pats):
            matching.append(a)
    return matching


async def fetch_stock_news(symbol: str, company_name: str = "") -> list:
    """Fetch all news then filter for the specific stock."""
    data = await fetch_all_news(limit=120)
    return filter_news_for_symbol(data["articles"], symbol, company_name)
