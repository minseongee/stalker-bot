import pytest

from utils import chart


@pytest.mark.asyncio
async def test_fetch_chart_returns_none_for_unknown_code(monkeypatch):
    async def fake_get_candles(code, interval, count):
        return None

    monkeypatch.setattr(chart, "get_candles", fake_get_candles)

    result = await chart.fetch_chart("999999")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_chart_computes_change_and_name(monkeypatch):
    candles = [
        {"time": "2026-07-07", "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
        {"time": "2026-07-08", "open": 100, "high": 112, "low": 99, "close": 110, "volume": 1200},
    ]

    async def fake_get_candles(code, interval, count):
        return candles

    async def fake_get_stock_name(code):
        return "삼성전자"

    monkeypatch.setattr(chart, "get_candles", fake_get_candles)
    monkeypatch.setattr(chart, "get_stock_name", fake_get_stock_name)

    buf, info = await chart.fetch_chart("005930")

    assert info["name"] == "삼성전자"
    assert info["close"] == 110
    assert info["change"] == 10
    assert round(info["change_pct"], 2) == 10.0
    assert buf.getbuffer().nbytes > 0
