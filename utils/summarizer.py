from openai import AsyncOpenAI

client = AsyncOpenAI()

_PROMPT = """당신은 한국 주식 시장 전문 기자입니다.
웹 검색을 통해 오늘의 한국 및 글로벌 시장 동향을 파악하고, 신문 브리핑 형식으로 작성해주세요.

작성 규칙:
- 전체 길이는 800자 이내
- 주요 이슈 3~5개를 각각 2~3문장으로 설명
- 각 이슈 앞에 이모지 불렛(예: 📌 📉 📈 💹 🏦) 사용
- 투자자 관점에서 핵심만 간결하게
- 한국어로 작성"""


async def summarize_news() -> str:
    response = await client.responses.create(
        model="gpt-5.4-mini",
        input=[{"role": "user", "content": _PROMPT}],
        tools=[{"type": "web_search_preview"}],
        include=[
            "reasoning.encrypted_content",
            "web_search_call.action.sources",
        ],
        reasoning={"effort": "medium", "summary": "auto"},
        store=True,
    )
    return response.output_text.strip()
