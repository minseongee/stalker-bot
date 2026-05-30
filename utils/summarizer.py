"""
기존 GPT 웹검색 방식 → RSS/DART 파이프라인으로 교체.
get_cached_news() / get_cache_time_kst() 시그니처는 유지해 기존 호출부 호환.
summarize_news()는 삭제하고 cogs/general.py에서 직접 DB 조회로 대체.
"""
import json
from datetime import datetime, timezone, timedelta

from server.database import get_hot_news, get_latest_hot_news_time

_KST = timezone(timedelta(hours=9))


def get_cached_news() -> str | None:
    """최신 핫뉴스 헤드라인 목록을 텍스트로 반환 (브리핑 embed 초기 로드용)."""
    rows = get_hot_news(limit=10)
    if not rows:
        return None
    lines: list[str] = []
    for r in rows:
        direction = r.get("direction", "neutral")
        emoji = "📈" if direction == "positive" else ("📉" if direction == "negative" else "📌")
        headline = r.get("headline") or r.get("title") or ""
        lines.append(f"{emoji} {headline}")
    return "\n".join(lines)


def get_cache_time_kst() -> str | None:
    ts = get_latest_hot_news_time()
    if ts is None:
        return None
    dt = datetime.fromtimestamp(ts, tz=_KST)
    return dt.strftime("%Y-%m-%d %H:%M KST")


def _build_hot_news_embeds() -> list[dict]:
    """핫뉴스 목록을 embed 데이터 리스트로 반환 (cogs/general.py에서 사용)."""
    rows = get_hot_news(limit=10)
    results: list[dict] = []
    for r in rows:
        sources = json.loads(r["sources_json"]) if r.get("sources_json") else []
        tags    = json.loads(r["stock_tags"])    if r.get("stock_tags")    else []
        results.append({
            "headline":  r.get("headline", ""),
            "summary":   r.get("summary", ""),
            "direction": r.get("direction", "neutral"),
            "tags":      tags,
            "sources":   sources,
            "fetched_at": r.get("fetched_at", 0),
        })
    return results
