import aiohttp
import xml.etree.ElementTree as ET

FEEDS = [
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
]


def _parse_items(xml_text: str, source: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link:
            guid = item.find("guid")
            if guid is not None and guid.get("isPermaLink", "true") != "false":
                link = (guid.text or "").strip()
        if title and link:
            items.append({"title": title, "link": link, "source": source})
    return items


async def fetch_market_news(max_items: int = 5) -> list[dict]:
    news = []
    headers = {"User-Agent": "Mozilla/5.0"}
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(headers=headers) as session:
        for source, url in FEEDS:
            try:
                async with session.get(url, timeout=timeout) as resp:
                    text = await resp.text()
                news.extend(_parse_items(text, source))
            except Exception:
                continue
    return news[:max_items]
