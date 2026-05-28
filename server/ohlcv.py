import random
from datetime import date, datetime, timedelta

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


def _business_days(n: int) -> list[date]:
    result: list[date] = []
    d = datetime.today().date()
    while len(result) < n:
        if d.weekday() < 5:
            result.append(d)
        d -= timedelta(days=1)
    return list(reversed(result))


def gen_ohlcv(code: str, days: int = 90) -> list[dict] | None:
    info = DUMMY_STOCKS.get(code)
    if not info:
        return None

    dates = _business_days(days)
    price = float(info["base_price"])
    records = []
    for d in dates:
        price *= 1 + random.gauss(0, 0.015)
        open_p = price * (1 + random.gauss(0, 0.005))
        high = max(open_p, price) * (1 + abs(random.gauss(0, 0.007)))
        low = min(open_p, price) * (1 - abs(random.gauss(0, 0.007)))
        records.append({
            "time": d.strftime("%Y-%m-%d"),
            "open": round(open_p),
            "high": round(high),
            "low": round(low),
            "close": round(price),
            "volume": max(int(random.gauss(10_000_000, 2_500_000)), 100_000),
        })
    return records
