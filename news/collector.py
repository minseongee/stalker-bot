"""RSS/DART 기사 수집 — 각 소스 독립 실패, 성공한 소스만 반환."""
import asyncio
import hashlib
import time
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import aiohttp

from .config import DART_API_KEY, DART_ENDPOINT, DART_EXCLUDE_REPORTS, RSS_FEEDS

_HEADERS = {"User-Agent": "StalkerBot/1.0 (news collector)"}
_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _make_guid(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def _parse_rss_time(raw: str | None) -> int:
    if not raw:
        return int(time.time())
    try:
        return int(parsedate_to_datetime(raw).timestamp())
    except Exception:
        return int(time.time())


async def _fetch_rss(session: aiohttp.ClientSession, feed: dict) -> list[dict]:
    source = feed["source"]
    try:
        async with session.get(feed["url"], headers=_HEADERS, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            text = await resp.text(errors="replace")
    except Exception as e:
        print(f"[Collector] {source} RSS 실패: {e}")
        return []

    items: list[dict] = []
    try:
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        # RSS 2.0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            pub   = item.findtext("pubDate")
            if not title or not link:
                continue
            items.append({
                "guid":         _make_guid(link),
                "title":        title,
                "url":          link,
                "source":       source,
                "published_at": _parse_rss_time(pub),
                "fetched_at":   int(time.time()),
            })
        # Atom
        if not items:
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                link = (link_el.get("href") or "") if link_el is not None else ""
                pub  = entry.findtext("{http://www.w3.org/2005/Atom}published")
                if not title or not link:
                    continue
                items.append({
                    "guid":         _make_guid(link),
                    "title":        title,
                    "url":          link,
                    "source":       source,
                    "published_at": _parse_rss_time(pub) if pub else int(time.time()),
                    "fetched_at":   int(time.time()),
                })
    except Exception as e:
        print(f"[Collector] {source} XML 파싱 실패: {e}")

    return items


async def _fetch_dart(session: aiohttp.ClientSession) -> list[dict]:
    if not DART_API_KEY:
        return []
    today = time.strftime("%Y%m%d")
    params = {
        "crtfc_key":  DART_API_KEY,
        "bgn_de":     today,
        "page_no":    "1",
        "page_count": "20",
        "sort":       "date",
        "sort_mth":   "desc",
    }
    try:
        async with session.get(DART_ENDPOINT, params=params, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    except Exception as e:
        print(f"[Collector] DART API 실패: {e}")
        return []

    if data.get("status") != "000":
        return []

    items: list[dict] = []
    for d in data.get("list", []):
        corp  = d.get("corp_name", "")
        title = d.get("report_nm", "")
        rcpno = d.get("rcept_no", "")
        date  = d.get("rcept_dt", "")
        if not rcpno:
            continue
        if any(kw in title for kw in DART_EXCLUDE_REPORTS):
            continue
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcpno}"
        pub = int(time.mktime(time.strptime(date, "%Y%m%d"))) if date else int(time.time())
        items.append({
            "guid":         _make_guid(rcpno),
            "title":        f"[{corp}] {title}",
            "url":          url,
            "source":       "DART",
            "published_at": pub,
            "fetched_at":   int(time.time()),
        })
    return items


async def collect_all() -> list[dict]:
    """모든 소스에서 기사 수집. 실패한 소스는 건너뜀."""
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_rss(session, feed) for feed in RSS_FEEDS]
        tasks.append(_fetch_dart(session))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[dict] = []
    for r in results:
        if isinstance(r, list):
            items.extend(r)
        elif isinstance(r, Exception):
            print(f"[Collector] 소스 수집 오류: {r}")
    return items
