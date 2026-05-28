import io
import random
from datetime import datetime, timedelta

import aiohttp

DUMMY_STOCKS: dict[str, dict] = {
    "005930": {"name": "삼성전자", "base_price": 73200},
    "000660": {"name": "SK하이닉스", "base_price": 198000},
    "035720": {"name": "카카오", "base_price": 41500},
    "035420": {"name": "NAVER", "base_price": 172000},
    "005380": {"name": "현대차", "base_price": 212000},
    "000270": {"name": "기아", "base_price": 98000},
    "051910": {"name": "LG화학", "base_price": 295000},
    "006400": {"name": "삼성SDI", "base_price": 156000},
}


def _business_days(n: int) -> list[datetime]:
    dates: list[datetime] = []
    d = datetime.today()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    return list(reversed(dates))


def _gen_ohlcv(base_price: int, days: int = 20) -> list[dict]:
    """한투 API 일봉 응답 포맷을 모방한 더미 OHLCV 시뮬레이션."""
    dates = _business_days(days)
    price = float(base_price)
    records = []
    for date in dates:
        price *= 1 + random.gauss(0, 0.015)
        open_p = price * (1 + random.gauss(0, 0.005))
        high = max(open_p, price) * (1 + abs(random.gauss(0, 0.007)))
        low = min(open_p, price) * (1 - abs(random.gauss(0, 0.007)))
        records.append({
            "x": date.strftime("%Y-%m-%d"),
            "o": round(open_p),
            "h": round(high),
            "l": round(low),
            "c": round(price),
            "v": max(int(random.gauss(10_000_000, 2_500_000)), 100_000),
            "_dt": date,
        })
    return records


def _build_chart_config(name: str, code: str, candles: list[dict]) -> dict:
    data = [{"x": c["x"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]} for c in candles]
    return {
        "type": "candlestick",
        "data": {
            "datasets": [{
                "label": f"{name} ({code})",
                "data": data,
            }]
        },
    }


class ChartAPIError(Exception):
    """quickchart.io 호출 실패 시 발생."""


async def fetch_chart(code: str) -> tuple[io.BytesIO, dict] | None:
    """더미 OHLCV로 캔들스틱 차트 이미지와 종목 요약을 반환합니다.

    Returns None if code is unknown; raises ChartAPIError on API failure.
    """
    info = DUMMY_STOCKS.get(code)
    if not info:
        return None

    candles = _gen_ohlcv(info["base_price"])
    chart_cfg = _build_chart_config(info["name"], code, candles)

    payload = {
        "version": "4",
        "width": 700,
        "height": 420,
        "backgroundColor": "#1e2329",
        "chart": chart_cfg,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://quickchart.io/chart",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.read()
                body_text = body.decode("utf-8", errors="replace")[:200]
                raise ChartAPIError(f"quickchart.io {resp.status}: {body_text}")
            data = await resp.read()

    buf = io.BytesIO(data)
    buf.seek(0)

    last, prev = candles[-1], candles[-2]
    change = last["c"] - prev["c"]

    return buf, {
        "name": info["name"],
        "code": code,
        "close": last["c"],
        "open": last["o"],
        "high": last["h"],
        "low": last["l"],
        "volume": last["v"],
        "change": change,
        "change_pct": change / prev["c"] * 100,
    }


def supported_codes() -> str:
    return ", ".join(f"`{k}` {v['name']}" for k, v in DUMMY_STOCKS.items())
