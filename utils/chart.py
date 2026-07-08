import asyncio
import io
from concurrent.futures import ThreadPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import mplfinance as mpf

import pandas as pd

from utils.toss_api import get_candles, get_stock_name

_executor = ThreadPoolExecutor(max_workers=4)

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


def _to_dataframe(candles: list[dict]) -> pd.DataFrame:
    records = [
        {"Open": c["open"], "High": c["high"], "Low": c["low"], "Close": c["close"], "Volume": c["volume"]}
        for c in candles
    ]
    index = pd.DatetimeIndex([c["time"] for c in candles])
    return pd.DataFrame(records, index=index)


def _render_chart(name: str, code: str, df: pd.DataFrame) -> io.BytesIO:
    fig, _ = mpf.plot(
        df,
        type="candle",
        style=_STYLE,
        title=f"\n{name} ({code})  최근 {len(df)} 영업일",
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
    candles = await get_candles(code, interval="1d", count=90)
    if not candles or len(candles) < 2:
        return None

    name = await get_stock_name(code) or code
    df = _to_dataframe(candles)
    loop = asyncio.get_event_loop()
    buf = await loop.run_in_executor(_executor, _render_chart, name, code, df)

    last, prev = candles[-1], candles[-2]
    change = last["close"] - prev["close"]

    return buf, {
        "name": name,
        "code": code,
        "close": last["close"],
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "volume": last["volume"],
        "change": change,
        "change_pct": change / prev["close"] * 100,
    }
