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


@pytest.mark.asyncio
async def test_get_stock_info_returns_known_codes_only(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbols"] == "005930,999999"
        return httpx.Response(200, json={"result": [{"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}]})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api.get_stock_info(["005930", "999999"])
    assert result == {"005930": {"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}}


@pytest.mark.asyncio
async def test_get_stock_info_uses_cache_on_second_call(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"result": [{"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}]})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    await toss_api.get_stock_info(["005930"])
    await toss_api.get_stock_info(["005930"])

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_stock_name_returns_none_for_unknown_code(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": []})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    name = await toss_api.get_stock_name("999999")
    assert name is None


@pytest.mark.asyncio
async def test_get_prices_batches_symbols(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": [
            {"symbol": "005930", "timestamp": "2026-07-08T09:00:00Z", "lastPrice": "73200", "currency": "KRW"},
        ]})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api.get_prices(["005930", "999999"])
    assert result == {"005930": {"price": 73200.0, "timestamp": "2026-07-08T09:00:00Z"}}


@pytest.mark.asyncio
async def test_get_prices_empty_input_returns_empty_dict():
    assert await toss_api.get_prices([]) == {}


@pytest.mark.asyncio
async def test_get_candles_maps_and_sorts_ascending(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"candles": [
            {"timestamp": "2026-07-08T00:00:00Z", "openPrice": "110", "highPrice": "112",
             "lowPrice": "108", "closePrice": "111", "volume": "1200", "currency": "KRW"},
            {"timestamp": "2026-07-07T00:00:00Z", "openPrice": "100", "highPrice": "105",
             "lowPrice": "95", "closePrice": "100", "volume": "1000", "currency": "KRW"},
        ], "nextBefore": None}})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api.get_candles("005930", interval="1d", count=2)
    assert [c["time"] for c in result] == ["2026-07-07", "2026-07-08"]
    assert result[0]["close"] == 100
    assert result[1]["close"] == 111
    assert result[1]["volume"] == 1200


@pytest.mark.asyncio
async def test_get_candles_returns_none_when_empty(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"candles": [], "nextBefore": None}})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api.get_candles("999999", count=90)
    assert result is None


@pytest.mark.asyncio
async def test_get_candles_range_paginates_until_enough_days(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def _candle(day: str, price: float) -> dict:
        return {
            "timestamp": f"{day}T00:00:00Z", "openPrice": str(price), "highPrice": str(price),
            "lowPrice": str(price), "closePrice": str(price), "volume": "100", "currency": "KRW",
        }

    # 1페이지: 최근 200개(정렬 전 내림차순으로 응답한다고 가정), 2페이지: 그 이전 50개(200개 미만 → 마지막 페이지)
    page1 = [_candle(f"2026-{(200 - i) // 28 + 1:02d}-{(200 - i) % 28 + 1:02d}", 200 - i) for i in range(200)]
    page2 = [_candle(f"2025-{(50 - i) // 28 + 1:02d}-{(50 - i) % 28 + 1:02d}", 1000 + i) for i in range(50)]
    pages = iter([page1, page2])

    def handler(request: httpx.Request) -> httpx.Response:
        candles = next(pages)
        return httpx.Response(200, json={"result": {"candles": candles, "nextBefore": None}})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api.get_candles_range("005930", days=250)
    assert len(result) == 250
    # 오름차순(과거 → 최근)으로 정렬되어 있어야 함
    assert result[0]["time"] < result[-1]["time"]


@pytest.mark.asyncio
async def test_get_candles_range_dedupes_inclusive_cursor_boundary(monkeypatch):
    """
    Test deduplication of boundary candles when API cursor is inclusive.
    If the "before" cursor parameter is inclusive, the next page might include
    a candle with the same timestamp as the previous page's oldest candle.
    This test verifies that duplicate timestamps are removed.
    """
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def _candle(day: str, price: float) -> dict:
        return {
            "timestamp": f"{day}T00:00:00Z", "openPrice": str(price), "highPrice": str(price),
            "lowPrice": str(price), "closePrice": str(price), "volume": "100", "currency": "KRW",
        }

    # Page 1 (most recent): 200 candles (full page, triggers pagination)
    page1 = [_candle(f"2026-{(200 - i) // 28 + 1:02d}-{(200 - i) % 28 + 1:02d}", 100 + i) for i in range(200)]

    # Page 2 (older): 40 candles (less than 200, stops pagination)
    page2_base = [_candle(f"2025-{(40 - i) // 28 + 1:02d}-{(40 - i) % 28 + 1:02d}", 300 + i) for i in range(40)]

    # Simulate inclusive cursor: add a duplicate of page1[0] at the end of page2
    # (page1[0] is the oldest candle in page1, so it's the boundary for the next fetch)
    boundary_timestamp = page1[0]["timestamp"]
    page2_dup = _candle(boundary_timestamp[:-10], 100)  # Same timestamp as page1[0]
    page2 = page2_base + [page2_dup]

    pages = iter([page1, page2])

    def handler(request: httpx.Request) -> httpx.Response:
        candles = next(pages)
        return httpx.Response(200, json={"result": {"candles": candles, "nextBefore": None}})

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api.get_candles_range("005930", days=200)

    # Total before dedup: 200 (page1) + 41 (page2_base + dup) = 241
    # After dedup: 240 unique candles (1 duplicate removed)
    # Request 200 days: should return last 200
    assert len(result) == 200

    # Verify no duplicate timestamps
    times = [c["time"] for c in result]
    assert len(times) == len(set(times)), "Found duplicate timestamps in result"

    # Verify ascending order (oldest first)
    assert times == sorted(times)
