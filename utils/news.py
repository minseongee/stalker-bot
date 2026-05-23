import re
import aiohttp

# pyexpat 의존성 없이 RSS를 직접 파싱하기 위해 정규식 사용
FEEDS = [
    ("한국경제", "https://www.hankyung.com/feed/finance"),
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("이데일리", "https://rss.edaily.co.kr/edaily/section/economy.xml"),
]


def _extract_text(block: str, tag: str) -> str:
    m = re.search(rf'<{tag}[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{tag}>', block, re.DOTALL)
    return m.group(1).strip() if m else ""


def _parse_items(xml_text: str, source: str) -> list[dict]:
    results = []
    for item in re.finditer(r'<item[^>]*>(.*?)</item>', xml_text, re.DOTALL):
        block = item.group(1)
        title = _extract_text(block, "title")
        link = _extract_text(block, "link")
        if not link:
            guid_m = re.search(r'<guid[^>]*isPermaLink="true"[^>]*>(.*?)</guid>', block, re.DOTALL)
            if guid_m:
                link = guid_m.group(1).strip()
        if title and link:
            results.append({"title": title, "link": link, "source": source})
    return results


async def fetch_market_news(max_items: int = 5) -> list[dict]:
    news = []
    headers = {"User-Agent": "Mozilla/5.0"}
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(headers=headers) as session:
        for source, url in FEEDS:
            try:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        continue
                    text = await resp.text()
                news.extend(_parse_items(text, source))
            except Exception:
                continue
    return news[:max_items]
