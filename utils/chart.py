import asyncio
import io
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import mplfinance as mpf

import pandas as pd

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

_executor = ThreadPoolExecutor(max_workers=2)

_KR_FONT = next(
    (f.name for f in fm.fontManager.ttflist if f.name in ("Nanum Gothic", "Apple SD Gothic Neo", "AppleGothic")),
    None,
)

_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up="#ef5350",
        down="#26a69a",
        edge="inherit",
        wick="inherit",
        volume="in",
    ),
    facecolor="#1e2329",
    figcolor="#1e2329",
    gridcolor="#2a2f35",
    rc={"font.family": _KR_FONT} if _KR_FONT else {},
)


def _business_days(n: int) -> list[datetime]:
    dates: list[datetime] = []
    d = datetime.today()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    return list(reversed(dates))


def _gen_ohlcv(base_price: int, days: int = 90) -> pd.DataFrame:
    dates = _business_days(days)
    price = float(base_price)
    records = []
    index = []
    for date in dates:
        price *= 1 + random.gauss(0, 0.015)
        open_p = price * (1 + random.gauss(0, 0.005))
        high = max(open_p, price) * (1 + abs(random.gauss(0, 0.007)))
        low = min(open_p, price) * (1 - abs(random.gauss(0, 0.007)))
        records.append({
            "Open": round(open_p),
            "High": round(high),
            "Low": round(low),
            "Close": round(price),
            "Volume": max(int(random.gauss(10_000_000, 2_500_000)), 100_000),
        })
        index.append(date.date())
    return pd.DataFrame(records, index=pd.DatetimeIndex(index))


def _render_chart(name: str, code: str, df: pd.DataFrame) -> io.BytesIO:
    fig, _ = mpf.plot(
        df,
        type="candle",
        style=_STYLE,
        title=f"\n{name} ({code})  최근 90 영업일",
        ylabel="가격 (원)",
        volume=True,
        figsize=(10, 6),
        returnfig=True,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf


async def fetch_chart(code: str) -> tuple[io.BytesIO, dict] | None:
    info = DUMMY_STOCKS.get(code)
    if not info:
        return None

    df = _gen_ohlcv(info["base_price"])
    loop = asyncio.get_event_loop()
    buf = await loop.run_in_executor(_executor, _render_chart, info["name"], code, df)

    last, prev = df.iloc[-1], df.iloc[-2]
    change = int(last["Close"]) - int(prev["Close"])

    return buf, {
        "name": info["name"],
        "code": code,
        "close": int(last["Close"]),
        "open": int(last["Open"]),
        "high": int(last["High"]),
        "low": int(last["Low"]),
        "volume": int(last["Volume"]),
        "change": change,
        "change_pct": change / int(prev["Close"]) * 100,
    }


def supported_codes() -> str:
    return ", ".join(f"`{k}` {v['name']}" for k, v in DUMMY_STOCKS.items())
