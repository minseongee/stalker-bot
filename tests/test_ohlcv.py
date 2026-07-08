import pytest

from server import ohlcv


@pytest.fixture(autouse=True)
def clear_cache():
    ohlcv._range_cache.clear()


@pytest.mark.asyncio
async def test_gen_ohlcv_uses_get_candles_for_small_range(monkeypatch):
    async def fake_get_candles(code, interval, count):
        assert code == "005930"
        assert count == 90
        return [{"time": "2026-07-08", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000}]

    monkeypatch.setattr(ohlcv, "get_candles", fake_get_candles)

    result = await ohlcv.gen_ohlcv("005930", days=90)
    assert result[0]["close"] == 105


@pytest.mark.asyncio
async def test_gen_ohlcv_uses_range_for_large_range(monkeypatch):
    called = {}

    async def fake_get_candles_range(code, days):
        called["days"] = days
        return [{"time": "2020-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    monkeypatch.setattr(ohlcv, "get_candles_range", fake_get_candles_range)

    result = await ohlcv.gen_ohlcv("005930", days=3000)
    assert called["days"] == 3000
    assert result[0]["close"] == 1


@pytest.mark.asyncio
async def test_gen_ohlcv_caches_within_ttl(monkeypatch):
    call_count = {"n": 0}

    async def fake_get_candles(code, interval, count):
        call_count["n"] += 1
        return [{"time": "2026-07-08", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    monkeypatch.setattr(ohlcv, "get_candles", fake_get_candles)

    await ohlcv.gen_ohlcv("005930", days=90)
    await ohlcv.gen_ohlcv("005930", days=90)

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_gen_ohlcv_returns_none_for_unknown_code(monkeypatch):
    async def fake_get_candles(code, interval, count):
        return None

    monkeypatch.setattr(ohlcv, "get_candles", fake_get_candles)

    result = await ohlcv.gen_ohlcv("999999", days=90)
    assert result is None
