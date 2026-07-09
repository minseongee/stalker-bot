import os
import json
import time
from datetime import datetime

from server.database import get_hot_news, get_latest_hot_news_time, get_recent_news_items
from utils.openai_client import get_openai_client
from utils import KST as _KST

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
            model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
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


_STOCK_DIGEST_PROMPT = """당신은 장기 보유 주주 관점의 한국 주식 애널리스트입니다.
아래는 한 종목에 대해 최근 발생한 개별 뉴스들이며, 각 뉴스는 이미 호재/악재/중립으로 분류되어 있습니다.
이 뉴스들을 종합해, 주주 입장에서 이 종목을 지금 어떻게 봐야 하는지 하나의 일관된 관점으로 정리하세요.
개별 뉴스의 상충되는 관점을 그대로 나열하지 말고, 전체적으로 종합했을 때의 순(net) 판단을 내리세요.

아래 JSON 형식으로만 응답하세요:
{
  "net_stance": "positive | negative | mixed | neutral",
  "net_reason": "순 판단의 근거 (1~2문장)",
  "key_issues": ["핵심 이슈 1", "핵심 이슈 2", ...]
}

net_stance 기준:
- positive: 종합적으로 호재가 우세
- negative: 종합적으로 악재가 우세
- mixed: 호재와 악재가 팽팽하거나 서로 다른 성격의 이슈가 혼재
- neutral: 대부분 중립적 뉴스뿐이거나 주가에 미치는 영향이 불분명
key_issues는 최대 4개, 각 10~25자 내외로 간결하게 작성하세요."""


async def summarize_stock_digest(name: str, code: str, items: list[dict], counts: dict) -> dict | None:
    """종목 하나에 대해 쌓인 여러 핫뉴스를 GPT로 종합해 주주 관점의 순(net) 판단 반환."""
    if not items:
        return None

    def _dir_label(d: str) -> str:
        return "호재" if d == "positive" else ("악재" if d == "negative" else "중립")

    lines = [
        f"- [{_dir_label(it.get('direction') or 'neutral')}] "
        f"{it.get('headline') or it.get('title') or ''}: {it.get('summary') or ''}"
        for it in items
    ]
    articles = "\n".join(lines)
    user_msg = (
        f"종목: {name}({code})\n"
        f"집계: 호재 {counts.get('positive', 0)}건 · 악재 {counts.get('negative', 0)}건 · "
        f"중립 {counts.get('neutral', 0)}건\n\n"
        f"개별 뉴스 목록:\n{articles}"
    )

    print(f"[다이제스트] {name}({code}) GPT 종합 요청 중… (뉴스 {len(items)}건)")
    try:
        resp = await get_openai_client().responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
            input=[
                {"role": "system", "content": _STOCK_DIGEST_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            store=False,
        )
        raw = resp.output_text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
    except Exception as e:
        print(f"[다이제스트] {name}({code}) GPT 호출 실패: {e}")
        return None

    return {
        "net_stance": result.get("net_stance", "neutral"),
        "net_reason": result.get("net_reason", ""),
        "key_issues": result.get("key_issues", []),
    }


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
