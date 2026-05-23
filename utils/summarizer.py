from openai import AsyncOpenAI

client = AsyncOpenAI()

_PROMPT = """당신은 한국 주식 시장 전문 기자입니다.
아래 뉴스 기사 제목 목록을 바탕으로 오늘의 시장 동향을 신문 브리핑 형식으로 작성해주세요.

작성 규칙:
- 전체 길이는 800자 이내
- 주요 이슈 3~5개를 각각 2~3문장으로 설명
- 각 이슈 앞에 이모지 불렛(예: 📌 📉 📈 💹 🏦) 사용
- 투자자 관점에서 핵심만 간결하게
- 한국어로 작성

뉴스 기사 제목:
{headlines}"""


async def summarize_news(articles: list[dict]) -> str:
    headlines = "\n".join(f"- [{a['source']}] {a['title']}" for a in articles)
    response = await client.responses.create(
        model="gpt-5.4-mini",
        input=[{"role": "user", "content": _PROMPT.format(headlines=headlines)}],
        text={
            "format": {"type": "text"},
            "verbosity": "medium",
        },
        reasoning={"effort": "medium", "summary": "auto"},
        tools=[],
        store=True,
    )
    return response.output_text.strip()
