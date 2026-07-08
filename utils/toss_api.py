"""토스증권 Open API 클라이언트 — 시세 조회 전용."""
import asyncio
import os
import time

import httpx

BASE_URL = "https://openapi.tossinvest.com"

_client_id = os.getenv("TOSS_CLIENT_ID", "")
_client_secret = os.getenv("TOSS_CLIENT_SECRET", "")

_token: str | None = None
_token_expires_at: float = 0.0
_token_lock = asyncio.Lock()

_stock_info_cache: dict[str, dict] = {}


class TossAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(f"[{status_code}] {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)


async def _get_access_token() -> str:
    global _token, _token_expires_at
    if _token and time.monotonic() < _token_expires_at - 60:
        return _token
    async with _token_lock:
        if _token and time.monotonic() < _token_expires_at - 60:
            return _token
        async with _make_client() as client:
            resp = await client.post(
                "/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": _client_id,
                    "client_secret": _client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise TossAPIError(resp.status_code, "auth-failed", "토큰 발급 실패")
        data = resp.json()
        _token = data["access_token"]
        _token_expires_at = time.monotonic() + data["expires_in"]
        return _token


async def _authed_get(path: str, params: dict, retries: int = 2):
    token = await _get_access_token()
    async with _make_client() as client:
        for attempt in range(retries + 1):
            resp = await client.get(
                path, params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 429 and attempt < retries:
                wait = float(resp.headers.get("Retry-After", "1"))
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                try:
                    err = resp.json().get("error", {})
                except ValueError:
                    err = {}
                raise TossAPIError(
                    resp.status_code,
                    err.get("code", "unknown"),
                    err.get("message", resp.text),
                )
            return resp.json()
    raise TossAPIError(429, "rate-limited", "재시도 초과")


def _chunk(items: list[str], size: int = 200) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def get_stock_info(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    to_fetch = [c for c in dict.fromkeys(codes) if c not in _stock_info_cache]
    for chunk in _chunk(to_fetch):
        data = await _authed_get("/api/stocks", {"symbols": ",".join(chunk)})
        for item in data:
            _stock_info_cache[item["symbol"]] = item
    return {code: _stock_info_cache[code] for code in codes if code in _stock_info_cache}


async def get_stock_name(code: str) -> str | None:
    info = await get_stock_info([code])
    return info.get(code, {}).get("name")


async def get_prices(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    result: dict[str, dict] = {}
    for chunk in _chunk(list(dict.fromkeys(codes))):
        data = await _authed_get("/api/v1/prices", {"symbols": ",".join(chunk)})
        for item in data:
            result[item["symbol"]] = {
                "price": float(item["lastPrice"]),
                "timestamp": item["timestamp"],
            }
    return result
