"""공유 AsyncOpenAI 클라이언트 싱글톤 — 여러 모듈에서 재사용."""
from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client
