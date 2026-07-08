import time

from utils.toss_api import get_candles, get_candles_range

_RANGE_CACHE_TTL = 60.0
_range_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}


async def gen_ohlcv(code: str, days: int = 90) -> list[dict] | None:
    cache_key = (code, days)
    cached = _range_cache.get(cache_key)
    if cached is not None:
        cached_at, data = cached
        if time.monotonic() - cached_at < _RANGE_CACHE_TTL:
            return data

    if days <= 200:
        data = await get_candles(code, interval="1d", count=days)
    else:
        data = await get_candles_range(code, days)

    if data is not None:
        _range_cache[cache_key] = (time.monotonic(), data)
    return data
