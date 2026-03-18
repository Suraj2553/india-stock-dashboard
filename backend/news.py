"""
news.py
Market news from RSS feeds + stock-specific news filtering.
"""

import asyncio
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
]


async def fetch_feed(source: str, url: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        root    = ET.fromstring(r.text)
        channel = root.find("channel")
        if channel is None:
            return []
        items = []
        for item in channel.findall("item")[:12]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            items.append({
                "source":      source,
                "title":       title,
                "link":        (item.findtext("link") or "").strip(),
                "description": strip_html(item.findtext("description") or "")[:350],
                "published":   (item.findtext("pubDate") or "").strip(),
            })
        return items
    except Exception as e:
        print(f"[news] {source}: {e}")
        return []


async def fetch_all_news(limit: int = 40) -> dict:
    results  = await asyncio.gather(*[fetch_feed(s, u) for s, u in FEEDS])
    articles = [item for feed in results for item in feed]
    return {
        "articles":   articles[:limit],
        "fetched_at": datetime.now().isoformat(),
    }


def filter_news_for_symbol(articles: list, symbol: str, company_name: str = "") -> list:
    """Return articles mentioning this stock."""
    keywords = {symbol.upper()}
    if company_name:
        # Add significant words from company name
        for word in company_name.split():
            if len(word) > 4 and word.lower() not in ("limited", "india", "ltd", "corp"):
                keywords.add(word.upper())

    matching = []
    for a in articles:
        text = (a["title"] + " " + a["description"]).upper()
        if any(kw in text for kw in keywords):
            matching.append(a)
    return matching


async def fetch_stock_news(symbol: str, company_name: str = "") -> list:
    """Fetch all news then filter for the specific stock."""
    data = await fetch_all_news(limit=60)
    return filter_news_for_symbol(data["articles"], symbol, company_name)
