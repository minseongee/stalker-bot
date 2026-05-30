"""GPT 요약 정제 — 기존 AsyncOpenAI 클라이언트·gpt-5.4-mini 재사용."""
import json

from utils.openai_client import get_openai_client


# 프롬프트는 여기서 수정
_SYSTEM_PROMPT = """당신은 한국 주식 시장 전문 애널리스트입니다.
제공된 기사 목록을 바탕으로 아래 JSON 형식으로만 응답하세요. 원문을 그대로 복제하지 말고 자체 표현으로 재작성하세요.

{
  "headline": "투자자용 한 줄 헤드라인 (30자 이내)",
  "summary": "핵심 내용 2~3문장 요약",
  "direction": "positive | negative | neutral",
  "stock_tags": ["관련종목코드또는섹터명", ...]
}"""


async def refine_cluster(cluster_id: str, items: list[dict]) -> dict | None:
    """
    items: cluster_id에 속한 news_items 행 리스트.
    반환: {headline, summary, direction, stock_tags, sources}
    """
    if not items:
        return None

    articles_text = "\n\n".join(
        f"[{i+1}] 매체: {it['source']}\n제목: {it['title']}\n링크: {it['url']}"
        for i, it in enumerate(items)
    )
    user_msg = f"다음 기사들을 분석하세요:\n\n{articles_text}"

    print(f"[Refiner] GPT 호출 중… (기사 {len(items)}건)")
    try:
        resp = await get_openai_client().responses.create(
            model="gpt-5.4-mini",
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            store=False,
        )
        raw = resp.output_text.strip()
        # JSON 블록 추출 (마크다운 코드펜스 처리)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
    except Exception as e:
        print(f"[Refiner] GPT 호출 실패: {e}")
        return None
    print(f"[Refiner] 완료 → {result.get('headline', '')}")

    sources = [
        {"source": it["source"], "url": it["url"]}
        for it in items
    ]

    return {
        "headline":   result.get("headline", ""),
        "summary":    result.get("summary", ""),
        "direction":  result.get("direction", "neutral"),
        "stock_tags": result.get("stock_tags", []),
        "sources":    sources,
    }
