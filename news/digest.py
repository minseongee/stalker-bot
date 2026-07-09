"""관심종목별 종합 다이제스트 — 파편화된 개별 핫뉴스를 종목 단위로 집계·종합한다.

건별 direction(호재/악재/중립)은 유지하되, 같은 종목에 대해 쌓인 여러 뉴스를
한 번에 GPT로 종합해 '순(net) 판단'을 만들어 낸다. 종목당 GPT 호출은 1회만 하고
전 유저가 이 카드를 공유해 본다 (cogs/general.py의 다이제스트 DM, server/app.py의 웹 API).
"""
from server.database import (
    get_all_watchlists,
    get_hot_news_for_codes_since,
    upsert_stock_digest,
)
from utils.summarizer import summarize_stock_digest
from utils.toss_api import get_stock_info


def _extract_url(item: dict) -> str:
    return item.get("url") or ""


async def build_stock_digest_cards(since_ts: int, window_key: str) -> list[dict]:
    """전 유저 관심종목을 모아 종목별 다이제스트 카드를 생성 (뉴스 없는 종목은 제외).

    since_ts: 이 시각 이후 정제된 핫뉴스만 집계 (직전 다이제스트 발송 시각).
    window_key: (code, window_key) idempotency 키 — 같은 주기 재실행 시 GPT 재호출 없이 덮어씀.
    """
    watchlists = get_all_watchlists()
    all_codes = sorted({c for codes in watchlists.values() for c in codes})
    if not all_codes:
        return []

    names = await get_stock_info(all_codes)

    cards: list[dict] = []
    for code in all_codes:
        items = get_hot_news_for_codes_since([code], since_ts)
        if not items:
            continue

        counts = {"positive": 0, "negative": 0, "neutral": 0}
        for it in items:
            d = it.get("direction") or "neutral"
            counts[d] = counts.get(d, 0) + 1

        name = names.get(code, {}).get("name", code)
        digest = await summarize_stock_digest(name, code, items, counts)
        if digest is None:
            continue

        sources = [
            {"headline": it.get("headline") or it.get("title"), "url": _extract_url(it)}
            for it in items[-6:]
        ]

        card = {
            "code":       code,
            "name":       name,
            "counts":     counts,
            "net_stance": digest["net_stance"],
            "net_reason": digest["net_reason"],
            "key_issues": digest["key_issues"],
            "sources":    sources,
        }
        upsert_stock_digest(window_key, card)
        cards.append(card)

    return cards
