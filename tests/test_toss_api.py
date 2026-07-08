import time

import httpx
import pytest

from utils import toss_api


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", None)
    monkeypatch.setattr(toss_api, "_token_expires_at", 0.0)
    toss_api._stock_info_cache.clear()
    monkeypatch.setattr(toss_api, "_client_id", "test-id")
    monkeypatch.setattr(toss_api, "_client_secret", "test-secret")


def _mock_client_factory(handler):
    def factory():
        return httpx.AsyncClient(
            base_url=toss_api.BASE_URL,
            transport=httpx.MockTransport(handler),
        )
    return factory


@pytest.mark.asyncio
async def test_get_access_token_fetches_and_caches(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"access_token": "tok-1", "token_type": "Bearer", "expires_in": 86400},
        )

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    token1 = await toss_api._get_access_token()
    token2 = await toss_api._get_access_token()

    assert token1 == "tok-1"
    assert token2 == "tok-1"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_access_token_refetches_after_expiry(monkeypatch):
    tokens = iter(["tok-1", "tok-2"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": next(tokens), "token_type": "Bearer", "expires_in": 86400},
        )

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    token1 = await toss_api._get_access_token()
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() - 1)
    token2 = await toss_api._get_access_token()

    assert token1 == "tok-1"
    assert token2 == "tok-2"


@pytest.mark.asyncio
async def test_authed_get_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    responses = iter([
        httpx.Response(
            429,
            headers={"Retry-After": "0"},
            json={"error": {"code": "rate-limited", "message": "too many requests"}},
        ),
        httpx.Response(200, json={"ok": True}),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api._authed_get("/api/v1/prices", {"symbols": "005930"})
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_authed_get_raises_tossapierror_on_4xx(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "invalid-request", "message": "잘못된 요청"}})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    with pytest.raises(toss_api.TossAPIError) as exc_info:
        await toss_api._authed_get("/api/v1/prices", {"symbols": "BAD"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid-request"
