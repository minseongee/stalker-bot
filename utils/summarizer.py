import json
import time
from datetime import datetime, timezone, timedelta

from server.database import get_hot_news, get_latest_hot_news_time, get_recent_news_items
from utils.openai_client import get_openai_client

_KST = timezone(timedelta(hours=9))

_BRIEFING_PROMPT = """당신은 한국 주식 시장 전문 기자입니다.
아래 기사 목록을 바탕으로 오늘의 시장 동향을 신문 브리핑 형식으로 작성해주세요.

작성 규칙:
- 전체 길이는 1500자 이내
- 주요 이슈 3~5개를 각각 2~3문장으로 설명
- 각 이슈 앞에 이모지 불렛(예: 📌 📉 📈 💹 🏦) 사용
- 투자자 관점에서 핵심만 간결하게, 원문을 그대로 복제하지 말 것
- 한국어로 작성
- 마지막 줄에 오늘 검색해볼 만한 키워드 3~5개를 아래 형식으로 추가
  형식: 🔍 오늘의 키워드: #키워드1 #키워드2 #키워드3"""


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


async def summarize_market_briefing(window_hours: int = 6) -> str | None:
    """최근 수집된 기사들을 GPT로 요약해 시장 브리핑 텍스트 반환."""
    since = int(time.time()) - 3600 * window_hours
    rows = get_recent_news_items(limit=60, since=since)
    if not rows:
        return None

    articles = "\n".join(
        f"- [{r['source']}] {r['title']}"
        for r in rows
    )
    user_msg = f"최근 {window_hours}시간 기사 목록:\n{articles}"

    print(f"[브리핑] GPT 요청 중 (기사 {len(rows)}건, 최근 {window_hours}시간)")
    try:
        resp = await get_openai_client().responses.create(
            model="gpt-5.4-mini",
            input=[
                {"role": "system", "content": _BRIEFING_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            store=False,
        )
        result = resp.output_text.strip()
        print(f"[브리핑] GPT 응답 완료")
        return result
    except Exception as e:
        print(f"[브리핑] GPT 호출 실패: {e}")
        return None


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
