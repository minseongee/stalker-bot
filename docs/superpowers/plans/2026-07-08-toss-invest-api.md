# 토스증권 Open API 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stalker Bot의 더미(랜덤) 시세 데이터를 토스증권 Open API(`https://openapi.tossinvest.com`)의 실제 시세로 교체한다.

**Architecture:** `utils/toss_api.py`라는 단일 비동기 HTTP 클라이언트 모듈이 OAuth2 인증·시세·캔들·종목정보 조회를 전담한다. 나머지 모든 코드(`server/ohlcv.py`, `utils/chart.py`, 디스코드 cogs, FastAPI 라우트, 정적 HTML)는 이 모듈이 반환하는 정규화된 dict만 사용하며, 더 이상 `DUMMY_STOCKS` 고정 목록을 참조하지 않는다.

**Tech Stack:** Python 3.14, `httpx`(신규 의존성), `discord.py`, `FastAPI`, `pytest` + `pytest-asyncio`(신규 테스트 의존성).

**설계 문서:** `docs/superpowers/specs/2026-07-08-toss-invest-api-design.md`

---

## 사전 참고사항

- 이 저장소에는 테스트가 전혀 없다. Task 1에서 pytest 인프라를 처음 구성한다.
- `poetry` CLI가 로컬에 설치되어 있지 않다(`poetry.lock`만 존재). 따라서 이 계획에서는 `pip3 install`로 패키지를 직접 설치하고, `pyproject.toml`은 의존성 명세 갱신 목적으로만 수정한다. `poetry.lock` 재생성은 범위 밖이다.
- 프로젝트 Python 인터프리터는 `/opt/homebrew/opt/python@3.14/bin/python3.14` (PATH상 `python3`)이다. 모든 명령은 이 인터프리터 기준이다.
- `.env`는 실제 비밀값을 담고 있으므로 Read 하지 않는다. Task 14에서 `cat >> .env`로 플레이스홀더 두 줄만 append한다.

---

### Task 1: pytest 테스트 인프라 구성

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: `pyproject.toml`에 `httpx` 런타임 의존성과 pytest 관련 설정 추가**

`dependencies` 배열 마지막 항목을 수정:

```toml
# before
    "aiohttp (>=3.9.0)"
]

# after
    "aiohttp (>=3.9.0)",
    "httpx (>=0.28.0)"
]
```

파일 맨 끝에 추가:

```toml

[tool.poetry.group.dev.dependencies]
pytest = "^9.1.1"
pytest-asyncio = "^1.4.0"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: 패키지 설치**

Run: `python3 -m pip install pytest pytest-asyncio httpx`
Expected: `Successfully installed ...` (httpx는 이미 설치되어 있을 수 있음 — 그 경우 `Requirement already satisfied`)

- [ ] **Step 3: pytest 동작 확인**

Run: `python3 -m pytest --version`
Expected: `pytest 9.1.1` 류의 버전 출력 (에러 없음)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: pytest 테스트 인프라 및 httpx 의존성 추가"
```

---

### Task 2: `utils/toss_api.py` — OAuth2 토큰 관리 + 인증된 GET 헬퍼

**Files:**
- Create: `utils/toss_api.py`
- Test: `tests/test_toss_api.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_toss_api.py` 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.toss_api'`

- [ ] **Step 3: `utils/toss_api.py` 최소 구현 작성**

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add utils/toss_api.py tests/test_toss_api.py
git commit -m "feat: 토스증권 API OAuth2 토큰 관리 및 인증 GET 헬퍼 추가"
```

---

### Task 3: `utils/toss_api.py` — `get_stock_info` / `get_stock_name`

**Files:**
- Modify: `utils/toss_api.py`
- Test: `tests/test_toss_api.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_toss_api.py` 끝에 추가:

```python
@pytest.mark.asyncio
async def test_get_stock_info_returns_known_codes_only(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbols"] == "005930,999999"
        return httpx.Response(200, json=[{"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}])

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
        return httpx.Response(200, json=[{"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}])

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    await toss_api.get_stock_info(["005930"])
    await toss_api.get_stock_info(["005930"])

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_stock_name_returns_none_for_unknown_code(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    name = await toss_api.get_stock_name("999999")
    assert name is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v -k stock_info or stock_name`
Expected: FAIL — `AttributeError: module 'utils.toss_api' has no attribute 'get_stock_info'`

- [ ] **Step 3: `utils/toss_api.py`에 함수 추가**

파일 끝에 추가:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add utils/toss_api.py tests/test_toss_api.py
git commit -m "feat: 토스증권 API 종목정보 조회(get_stock_info/get_stock_name) 추가"
```

---

### Task 4: `utils/toss_api.py` — `get_prices`

**Files:**
- Modify: `utils/toss_api.py`
- Test: `tests/test_toss_api.py`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
@pytest.mark.asyncio
async def test_get_prices_batches_symbols(monkeypatch):
    monkeypatch.setattr(toss_api, "_token", "tok")
    monkeypatch.setattr(toss_api, "_token_expires_at", time.monotonic() + 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"symbol": "005930", "timestamp": "2026-07-08T09:00:00Z", "lastPrice": "73200", "currency": "KRW"},
        ])

    monkeypatch.setattr(toss_api, "_make_client", _mock_client_factory(handler))

    result = await toss_api.get_prices(["005930", "999999"])
    assert result == {"005930": {"price": 73200.0, "timestamp": "2026-07-08T09:00:00Z"}}


@pytest.mark.asyncio
async def test_get_prices_empty_input_returns_empty_dict():
    assert await toss_api.get_prices([]) == {}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v -k get_prices`
Expected: FAIL — `AttributeError: module 'utils.toss_api' has no attribute 'get_prices'`

- [ ] **Step 3: 구현 추가**

파일 끝에 추가:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add utils/toss_api.py tests/test_toss_api.py
git commit -m "feat: 토스증권 API 배치 현재가 조회(get_prices) 추가"
```

---

### Task 5: `utils/toss_api.py` — `get_candles` / `get_candles_range`

**Files:**
- Modify: `utils/toss_api.py`
- Test: `tests/test_toss_api.py`

**설계 메모:** 캔들 응답은 `{"result": {"candles": [...], "nextBefore": ...}}` 형태다. 정렬 순서가 문서상 명확하지 않으므로, 원시 캔들을 항상 `timestamp` 기준 오름차순(과거→최근)으로 정렬한 뒤 매핑한다. 페이지네이션은 `nextBefore` 필드 대신, 반환된 캔들 수가 요청한 `count`보다 적으면 "더 이상 과거 데이터 없음"으로 판단하는 방식을 쓴다(문서에 명시되지 않은 필드에 의존하지 않기 위함).

- [ ] **Step 1: 실패하는 테스트 추가**

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v -k candles`
Expected: FAIL — `AttributeError: module 'utils.toss_api' has no attribute 'get_candles'`

- [ ] **Step 3: 구현 추가**

파일 끝에 추가:

```python
def _map_candle(raw: dict) -> dict:
    return {
        "time": raw["timestamp"][:10],
        "open": round(float(raw["openPrice"])),
        "high": round(float(raw["highPrice"])),
        "low": round(float(raw["lowPrice"])),
        "close": round(float(raw["closePrice"])),
        "volume": int(float(raw["volume"])),
    }


async def _fetch_raw_candles(
    code: str, interval: str, count: int, before: str | None = None,
) -> list[dict] | None:
    params: dict = {"symbol": code, "interval": interval, "count": count}
    if before:
        params["before"] = before
    try:
        data = await _authed_get("/api/v1/candles", params)
    except TossAPIError as e:
        if e.status_code == 404:
            return None
        raise
    candles = data.get("result", {}).get("candles", [])
    if not candles:
        return None
    return sorted(candles, key=lambda c: c["timestamp"])


async def get_candles(
    code: str, interval: str = "1d", count: int = 90, before: str | None = None,
) -> list[dict] | None:
    raw = await _fetch_raw_candles(code, interval, count, before)
    if raw is None:
        return None
    return [_map_candle(c) for c in raw]


async def get_candles_range(code: str, days: int) -> list[dict] | None:
    pages: list[list[dict]] = []
    before: str | None = None
    total = 0
    while total < days:
        raw = await _fetch_raw_candles(code, "1d", 200, before)
        if raw is None:
            break
        pages.insert(0, raw)
        total += len(raw)
        if len(raw) < 200:
            break
        before = raw[0]["timestamp"]
    if not pages:
        return None
    merged = [c for page in pages for c in page]
    mapped = [_map_candle(c) for c in merged]
    return mapped[-days:] if len(mapped) > days else mapped
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_toss_api.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add utils/toss_api.py tests/test_toss_api.py
git commit -m "feat: 토스증권 API 캔들 조회(get_candles/get_candles_range) 추가"
```

---

### Task 6: `server/ohlcv.py` — 더미 제거하고 실제 API 연동

**Files:**
- Modify: `server/ohlcv.py` (전체 교체)
- Test: `tests/test_ohlcv.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_ohlcv.py` 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_ohlcv.py -v`
Expected: FAIL — 기존 `gen_ohlcv`는 동기 함수이며 `DUMMY_STOCKS` 기반이라 위 테스트들과 시그니처가 맞지 않음 (`TypeError` 혹은 `AttributeError`)

- [ ] **Step 3: `server/ohlcv.py` 전체 교체**

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_ohlcv.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server/ohlcv.py tests/test_ohlcv.py
git commit -m "feat: server/ohlcv.py를 실제 토스증권 API 연동으로 교체"
```

---

### Task 7: `utils/chart.py` — 더미 제거하고 실제 API 연동

**Files:**
- Modify: `utils/chart.py` (전체 교체)
- Test: `tests/test_chart.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_chart.py` 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_chart.py -v`
Expected: FAIL — 기존 `fetch_chart`는 `DUMMY_STOCKS` 기반이라 `chart.get_candles`, `chart.get_stock_name` 속성이 없음

- [ ] **Step 3: `utils/chart.py` 전체 교체**

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_chart.py -v`
Expected: 2 passed (mplfinance 렌더링을 실제로 수행하므로 몇 초 걸릴 수 있음)

- [ ] **Step 5: 전체 테스트 스위트 확인**

Run: `python3 -m pytest -v`
Expected: 이 시점까지의 모든 테스트 통과 (18 passed 전후)

- [ ] **Step 6: Commit**

```bash
git add utils/chart.py tests/test_chart.py
git commit -m "feat: utils/chart.py를 실제 토스증권 API 연동으로 교체"
```

---

### Task 8: `cogs/general.py` — DUMMY_STOCKS 제거 및 비동기 전환

**Files:**
- Modify: `cogs/general.py`

이 태스크는 discord.py UI 컴포넌트를 다루므로 자동화 테스트 대신, 이 태스크 완료 후 Task 14의 수동 통합 체크리스트로 검증한다.

- [ ] **Step 1: import 교체**

```python
# before
from utils.chart import fetch_chart, supported_codes
...
from server.ohlcv import DUMMY_STOCKS, gen_ohlcv

# after
from utils.chart import fetch_chart
from utils.toss_api import get_stock_info, get_stock_name
...
from server.ohlcv import gen_ohlcv
```

- [ ] **Step 2: `_build_watchlist_embed`를 async로 전환하고 DUMMY_STOCKS 제거**

```python
# before
def _build_watchlist_embed(user_id: str) -> discord.Embed:
    codes = get_watchlist(user_id)
    embed = discord.Embed(title="⭐ 내 관심 종목", color=discord.Color.gold())
    if not codes:
        embed.description = "관심 종목이 없습니다.\n**[➕ 추가]** 버튼으로 종목을 추가해보세요!"
        return embed
    for code in codes:
        info = DUMMY_STOCKS.get(code)
        if not info:
            continue
        data = gen_ohlcv(code, days=2)
        if len(data) < 2:
            continue
        last, prev = data[-1], data[-2]
        change     = last["close"] - prev["close"]
        change_pct = change / prev["close"] * 100
        sign  = "▲" if change >= 0 else "▼"
        arrow = "📈" if change >= 0 else "📉"
        embed.add_field(
            name=f"{arrow} {info['name']} ({code})",
            value=f"**{last['close']:,}원** {sign} {change:+,}원 ({change_pct:+.2f}%)",
            inline=False,
        )
    embed.set_footer(text="⚠️ 목업 데이터 — 한국투자증권 API 연동 예정")
    return embed

# after
async def _build_watchlist_embed(user_id: str) -> discord.Embed:
    codes = get_watchlist(user_id)
    embed = discord.Embed(title="⭐ 내 관심 종목", color=discord.Color.gold())
    if not codes:
        embed.description = "관심 종목이 없습니다.\n**[➕ 추가]** 버튼으로 종목을 추가해보세요!"
        return embed
    for code in codes:
        name = await get_stock_name(code)
        if not name:
            continue
        data = await gen_ohlcv(code, days=2)
        if not data or len(data) < 2:
            continue
        last, prev = data[-1], data[-2]
        change     = last["close"] - prev["close"]
        change_pct = change / prev["close"] * 100
        sign  = "▲" if change >= 0 else "▼"
        arrow = "📈" if change >= 0 else "📉"
        embed.add_field(
            name=f"{arrow} {name} ({code})",
            value=f"**{last['close']:,}원** {sign} {change:+,}원 ({change_pct:+.2f}%)",
            inline=False,
        )
    return embed


async def _make_watchlist_view(user_id: str) -> "WatchlistView":
    codes = get_watchlist(user_id)
    names = await get_stock_info(codes) if codes else {}
    return WatchlistView(user_id, names)
```

- [ ] **Step 3: `_send_chart`의 미존재 종목 에러 메시지 및 footer 수정**

```python
# before
    if result is None:
        await interaction.followup.send(
            f"❌ `{code}` 종목을 찾을 수 없습니다.\n\n**지원 종목**\n{supported_codes()}",
            ephemeral=True,
        )
        return
    buf, info = result

# after
    if result is None:
        await interaction.followup.send(
            f"❌ `{code}` 종목을 찾을 수 없습니다. 종목코드를 다시 확인해주세요.",
            ephemeral=True,
        )
        return
    buf, info = result
```

```python
# before
    embed.add_field(name="거래량", value=f"{info['volume']:,}",   inline=True)
    embed.set_image(url="attachment://chart.png")
    embed.set_footer(text="⚠️ 목업 데이터 — 한국투자증권 API 연동 예정")
    await interaction.followup.send(

# after
    embed.add_field(name="거래량", value=f"{info['volume']:,}",   inline=True)
    embed.set_image(url="attachment://chart.png")
    await interaction.followup.send(
```

- [ ] **Step 4: `ChartResultView._toggle_watchlist`의 이름 조회 교체**

```python
# before
    async def _toggle_watchlist(self, interaction: discord.Interaction):
        uid  = str(interaction.user.id)
        name = DUMMY_STOCKS.get(self.stock_code, {}).get("name", self.stock_code)

# after
    async def _toggle_watchlist(self, interaction: discord.Interaction):
        uid  = str(interaction.user.id)
        name = await get_stock_name(self.stock_code) or self.stock_code
```

- [ ] **Step 5: `WatchlistView`가 이름 맵을 받도록 수정**

```python
# before
class WatchlistView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=None)
        self.user_id = user_id

        codes = get_watchlist(user_id)
        if codes:
            options = [
                discord.SelectOption(
                    label=f"{DUMMY_STOCKS.get(c, {}).get('name', c)} ({c})",
                    value=c,
                    emoji="📊",
                )
                for c in codes
            ]

# after
class WatchlistView(discord.ui.View):
    def __init__(self, user_id: str, names: dict[str, dict] | None = None):
        super().__init__(timeout=None)
        self.user_id = user_id

        codes = get_watchlist(user_id)
        if codes:
            names = names or {}
            options = [
                discord.SelectOption(
                    label=f"{names.get(c, {}).get('name', c)} ({c})",
                    value=c,
                    emoji="📊",
                )
                for c in codes
            ]
```

- [ ] **Step 6: `WatchlistRemoveView`가 이름 맵을 받도록 수정**

```python
# before
class WatchlistRemoveView(discord.ui.View):
    def __init__(self, user_id: str, codes: list[str]):
        super().__init__(timeout=120)
        self.user_id = user_id
        options = [
            discord.SelectOption(
                label=f"{DUMMY_STOCKS.get(c, {}).get('name', c)} ({c})",
                value=c,
            )
            for c in codes
        ]
        select = discord.ui.Select(placeholder="삭제할 종목을 선택하세요", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        code = interaction.data["values"][0]
        remove_from_watchlist(self.user_id, code)
        embed = _build_watchlist_embed(self.user_id)
        await interaction.response.edit_message(embed=embed, view=WatchlistView(self.user_id))

    @discord.ui.button(label="← 취소", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = _build_watchlist_embed(self.user_id)
        await interaction.response.edit_message(embed=embed, view=WatchlistView(self.user_id))

# after
class WatchlistRemoveView(discord.ui.View):
    def __init__(self, user_id: str, codes: list[str], names: dict[str, dict] | None = None):
        super().__init__(timeout=120)
        self.user_id = user_id
        names = names or {}
        options = [
            discord.SelectOption(
                label=f"{names.get(c, {}).get('name', c)} ({c})",
                value=c,
            )
            for c in codes
        ]
        select = discord.ui.Select(placeholder="삭제할 종목을 선택하세요", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        code = interaction.data["values"][0]
        remove_from_watchlist(self.user_id, code)
        embed = await _build_watchlist_embed(self.user_id)
        await interaction.response.edit_message(embed=embed, view=await _make_watchlist_view(self.user_id))

    @discord.ui.button(label="← 취소", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = await _build_watchlist_embed(self.user_id)
        await interaction.response.edit_message(embed=embed, view=await _make_watchlist_view(self.user_id))
```

- [ ] **Step 7: `WatchlistView`의 나머지 핸들러에서 이름 맵 생성 후 전달**

```python
# before (remove 버튼)
    @discord.ui.button(label="➖ 삭제", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button):
        codes = get_watchlist(str(interaction.user.id))
        if not codes:
            await interaction.response.edit_message(
                embed=_build_watchlist_embed(str(interaction.user.id)),
                view=self,
            )
            return
        embed = discord.Embed(
            title="➖ 관심 종목 삭제",
            description="삭제할 종목을 선택하세요.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(
            embed=embed, view=WatchlistRemoveView(str(interaction.user.id), codes)
        )

# after
    @discord.ui.button(label="➖ 삭제", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button):
        codes = get_watchlist(str(interaction.user.id))
        if not codes:
            await interaction.response.edit_message(
                embed=await _build_watchlist_embed(str(interaction.user.id)),
                view=self,
            )
            return
        names = await get_stock_info(codes)
        embed = discord.Embed(
            title="➖ 관심 종목 삭제",
            description="삭제할 종목을 선택하세요.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(
            embed=embed, view=WatchlistRemoveView(str(interaction.user.id), codes, names)
        )
```

```python
# before (새로고침 버튼)
    @discord.ui.button(label="🔄 새로고침", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = _build_watchlist_embed(str(interaction.user.id))
        await interaction.response.edit_message(embed=embed, view=self)

# after
    @discord.ui.button(label="🔄 새로고침", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = await _build_watchlist_embed(str(interaction.user.id))
        await interaction.response.edit_message(embed=embed, view=self)
```

- [ ] **Step 8: `StockView.watchlist` 핸들러 수정**

```python
# before
    @discord.ui.button(label="관심 종목", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="stock:watchlist")
    async def watchlist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = _build_watchlist_embed(str(interaction.user.id))
        await interaction.response.send_message(
            embed=embed, view=WatchlistView(str(interaction.user.id)), ephemeral=True
        )

# after
    @discord.ui.button(label="관심 종목", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="stock:watchlist")
    async def watchlist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        uid = str(interaction.user.id)
        embed = await _build_watchlist_embed(uid)
        await interaction.response.send_message(
            embed=embed, view=await _make_watchlist_view(uid), ephemeral=True
        )
```

- [ ] **Step 9: `_notify_watchlist`의 이름 조회 교체**

```python
# before
        for user_id, watching_codes in matched.items():
            try:
                user = await self.bot.fetch_user(int(user_id))
            except Exception:
                continue

            from server.ohlcv import DUMMY_STOCKS
            names = [DUMMY_STOCKS.get(c, {}).get("name", c) for c in watching_codes]
            stock_str = ", ".join(f"**{n}**({c})" for n, c in zip(names, watching_codes))

# after
        for user_id, watching_codes in matched.items():
            try:
                user = await self.bot.fetch_user(int(user_id))
            except Exception:
                continue

            info_map = await get_stock_info(watching_codes)
            names = [info_map.get(c, {}).get("name", c) for c in watching_codes]
            stock_str = ", ".join(f"**{n}**({c})" for n, c in zip(names, watching_codes))
```

- [ ] **Step 10: 문법 검사**

Run: `python3 -m py_compile cogs/general.py`
Expected: 에러 없음 (조용히 종료)

- [ ] **Step 11: Commit**

```bash
git add cogs/general.py
git commit -m "feat: general.py를 실제 토스증권 API 연동으로 교체 (DUMMY_STOCKS 제거)"
```

---

### Task 9: `cogs/general.py` — 관심종목 추가 UX를 모달 방식으로 교체

**Files:**
- Modify: `cogs/general.py`

**배경:** `WatchlistAddView`는 `DUMMY_STOCKS` 8종목 중 아직 담지 않은 것을 Select Menu로 보여주는 방식이었다. 이제 임의의 종목코드를 추가할 수 있어야 하므로, `StockSearchModal`과 동일한 패턴의 코드 입력 모달로 교체한다.

- [ ] **Step 1: `WatchlistAddView` 클래스를 `WatchlistAddModal`로 교체**

```python
# before — 이 클래스 전체를 삭제
class WatchlistAddView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=120)
        self.user_id = user_id
        existing = set(get_watchlist(user_id))
        available = [c for c in DUMMY_STOCKS if c not in existing]
        if available:
            options = [
                discord.SelectOption(
                    label=f"{DUMMY_STOCKS[c]['name']} ({c})",
                    value=c,
                )
                for c in available
            ]
            select = discord.ui.Select(placeholder="추가할 종목을 선택하세요", options=options)
            select.callback = self._on_select
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        code = interaction.data["values"][0]
        add_to_watchlist(self.user_id, code)
        embed = _build_watchlist_embed(self.user_id)
        await interaction.response.edit_message(embed=embed, view=WatchlistView(self.user_id))

    @discord.ui.button(label="← 취소", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = _build_watchlist_embed(self.user_id)
        await interaction.response.edit_message(embed=embed, view=WatchlistView(self.user_id))

# after
class WatchlistAddModal(discord.ui.Modal, title="관심 종목 추가"):
    code = discord.ui.TextInput(
        label="종목 코드 (6자리)",
        placeholder="예: 005930",
        min_length=6,
        max_length=6,
    )

    def __init__(self, user_id: str):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        code = self.code.value.strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        name = await get_stock_name(code)
        if not name:
            await interaction.followup.send(f"❌ `{code}` 종목을 찾을 수 없습니다.", ephemeral=True)
            return
        add_to_watchlist(self.user_id, code)
        await interaction.followup.send(
            f"⭐ **{name}** ({code})을 관심 종목에 추가했습니다!\n"
            "관심 종목 메뉴에서 🔄 새로고침을 눌러 확인하세요.",
            ephemeral=True,
        )
```

- [ ] **Step 2: `WatchlistView.add` 버튼이 모달을 열도록 수정**

```python
# before
    @discord.ui.button(label="➕ 추가", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button):
        existing = set(get_watchlist(str(interaction.user.id)))
        if len(existing) >= len(DUMMY_STOCKS):
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="⭐ 내 관심 종목",
                    description="지원하는 모든 종목이 이미 관심 목록에 있습니다.",
                    color=discord.Color.gold(),
                ),
                view=WatchlistView(str(interaction.user.id)),
            )
            return
        embed = discord.Embed(
            title="➕ 관심 종목 추가",
            description="추가할 종목을 선택하세요.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(
            embed=embed, view=WatchlistAddView(str(interaction.user.id))
        )

# after
    @discord.ui.button(label="➕ 추가", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(WatchlistAddModal(str(interaction.user.id)))
```

- [ ] **Step 3: 문법 검사**

Run: `python3 -m py_compile cogs/general.py`
Expected: 에러 없음

- [ ] **Step 4: Commit**

```bash
git add cogs/general.py
git commit -m "feat: 관심종목 추가를 종목코드 입력 모달 방식으로 변경"
```

---

### Task 10: `cogs/worker.py` — 배치 시세 조회로 폴링 재구성

**Files:**
- Modify: `cogs/worker.py`

- [ ] **Step 1: import 교체**

```python
# before
from server.ohlcv import DUMMY_STOCKS, gen_ohlcv

# after
from utils.toss_api import get_prices, get_stock_info, TossAPIError
```

- [ ] **Step 2: `_current_price` 함수 삭제**

```python
# before — 이 함수 전체를 삭제
def _current_price(stock_code: str) -> float | None:
    """현재 가격 반환. 실제 API 연동 전까지는 더미 데이터 마지막 종가 사용."""
    data = gen_ohlcv(stock_code, days=2)
    if not data:
        return None
    return float(data[-1]["close"])
```

- [ ] **Step 3: `poll`/`_check`/`_check_normal`/`_check_fib`를 배치 조회 기반으로 재구성**

```python
# before
    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        for ch in get_all_channels():
            await self._check(ch)

    async def _check(self, ch: dict):
        if not ch.get("alert_enabled", 1):
            return
        if ch.get("channel_type") == "fib":
            await self._check_fib(ch)
        else:
            await self._check_normal(ch)

    async def _check_normal(self, ch: dict):
        code  = ch["stock_code"]
        price = _current_price(code)
        if price is None:
            return

        bounds = _normal_bounds_now(ch)
        if bounds is None:
            return
        upper, lower = bounds

        user = await self._get_user(ch["user_id"])
        if user is None:
            return

        ch_id = ch["id"]
        name  = DUMMY_STOCKS.get(code, {}).get("name", code)

        # 상단선 상향 돌파
        if price >= upper:
            if not already_alerted(ch_id, "upper"):
                record_alert(ch_id, "upper")
                await self._send_normal_alert(user, name, code, price, upper, "upper")

        # 하단선 하향 이탈
        if price <= lower:
            if not already_alerted(ch_id, "lower"):
                record_alert(ch_id, "lower")
                await self._send_normal_alert(user, name, code, price, lower, "lower")

    async def _check_fib(self, ch: dict):
        code  = ch["stock_code"]
        price = _current_price(code)
        if price is None:
            return

        levels = _fib_level_prices_now(ch)
        if levels is None:
            return

        user = await self._get_user(ch["user_id"])
        if user is None:
            return

        ch_id = ch["id"]
        name  = DUMMY_STOCKS.get(code, {}).get("name", code)

        for level, level_price in levels:
            side = f"fib_{level}"
            if price >= level_price and not already_alerted(ch_id, side):
                record_alert(ch_id, side)
                await self._send_fib_alert(user, name, code, price, level_price, level)

# after
    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        channels = get_all_channels()
        if not channels:
            return
        codes = list({ch["stock_code"] for ch in channels})
        try:
            prices = await get_prices(codes)
            names = await get_stock_info(codes)
        except TossAPIError as e:
            print(f"[워커] 시세 조회 실패, 이번 주기는 건너뜁니다: {e}")
            return
        for ch in channels:
            price_info = prices.get(ch["stock_code"])
            if price_info is None:
                continue
            name = names.get(ch["stock_code"], {}).get("name", ch["stock_code"])
            await self._check(ch, price_info["price"], name)

    async def _check(self, ch: dict, price: float, name: str):
        if not ch.get("alert_enabled", 1):
            return
        if ch.get("channel_type") == "fib":
            await self._check_fib(ch, price, name)
        else:
            await self._check_normal(ch, price, name)

    async def _check_normal(self, ch: dict, price: float, name: str):
        code = ch["stock_code"]
        bounds = _normal_bounds_now(ch)
        if bounds is None:
            return
        upper, lower = bounds

        user = await self._get_user(ch["user_id"])
        if user is None:
            return

        ch_id = ch["id"]

        # 상단선 상향 돌파
        if price >= upper:
            if not already_alerted(ch_id, "upper"):
                record_alert(ch_id, "upper")
                await self._send_normal_alert(user, name, code, price, upper, "upper")

        # 하단선 하향 이탈
        if price <= lower:
            if not already_alerted(ch_id, "lower"):
                record_alert(ch_id, "lower")
                await self._send_normal_alert(user, name, code, price, lower, "lower")

    async def _check_fib(self, ch: dict, price: float, name: str):
        code = ch["stock_code"]
        levels = _fib_level_prices_now(ch)
        if levels is None:
            return

        user = await self._get_user(ch["user_id"])
        if user is None:
            return

        ch_id = ch["id"]
        for level, level_price in levels:
            side = f"fib_{level}"
            if price >= level_price and not already_alerted(ch_id, side):
                record_alert(ch_id, side)
                await self._send_fib_alert(user, name, code, price, level_price, level)
```

- [ ] **Step 4: 목업 데이터 안내 문구 제거**

```python
# before (두 곳)
        embed.set_footer(text="⚠️ 목업 데이터 | 쿨타임 1시간")

# after (두 곳 모두)
        embed.set_footer(text="쿨타임 1시간")
```

- [ ] **Step 5: 문법 검사**

Run: `python3 -m py_compile cogs/worker.py`
Expected: 에러 없음

- [ ] **Step 6: Commit**

```bash
git add cogs/worker.py
git commit -m "feat: worker.py 폴링을 배치 시세 조회 기반으로 재구성"
```

---

### Task 11: `server/app.py` — 실제 API 연동

**Files:**
- Modify: `server/app.py`

- [ ] **Step 1: import 교체**

```python
# before
from .ohlcv import gen_ohlcv, DUMMY_STOCKS

# after
from .ohlcv import gen_ohlcv
from utils.toss_api import get_stock_info
```

- [ ] **Step 2: `/ohlcv/{code}`를 async로 전환**

```python
# before
@app.get("/ohlcv/{code}")
def ohlcv(code: str, days: int = 90):
    data = gen_ohlcv(code.upper(), days)
    if data is None:
        raise HTTPException(status_code=404, detail=f"{code} 종목을 찾을 수 없습니다.")
    return data

# after
@app.get("/ohlcv/{code}")
async def ohlcv(code: str, days: int = 90):
    data = await gen_ohlcv(code.upper(), days)
    if data is None:
        raise HTTPException(status_code=404, detail=f"{code} 종목을 찾을 수 없습니다.")
    return data
```

- [ ] **Step 3: `api_me_stocks_all`을 실제 API 기반으로 재작성하고, 미사용 `api_me_stocks`는 삭제**

`grep -rn "api/me/stocks\b" server/static/*.html`로 확인한 결과 `/api/me/stocks`(전체목록 아닌 쪽)는 어떤 정적 페이지에서도 호출되지 않는다. 죽은 코드이므로 삭제한다.

```python
# before — 두 함수를 아래로 통째 교체
@app.get("/api/me/stocks-all")
def api_me_stocks_all(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user_id = user["id"]
    watchlist = get_watchlist(user_id)
    channels  = get_channels(user_id)
    wl_set = set(watchlist)
    ch_set = set(c["stock_code"] for c in channels)
    result = []
    # 관심종목 먼저
    for code in watchlist:
        info = DUMMY_STOCKS.get(code, {})
        result.append({"code": code, "name": info.get("name", code), "in_watchlist": True, "has_channel": code in ch_set})
    # 전체 종목 (관심종목 제외)
    for code, info in DUMMY_STOCKS.items():
        if code not in wl_set:
            result.append({"code": code, "name": info.get("name", code), "in_watchlist": False, "has_channel": code in ch_set})
    return result


@app.get("/api/me/stocks")
def api_me_stocks(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user_id = user["id"]
    watchlist = get_watchlist(user_id)
    channels  = get_channels(user_id)
    channel_codes = list(dict.fromkeys(c["stock_code"] for c in channels))
    wl_set = set(watchlist)
    result = []
    for code in watchlist:
        info = DUMMY_STOCKS.get(code, {})
        result.append({"code": code, "name": info.get("name", code), "in_watchlist": True})
    for code in channel_codes:
        if code not in wl_set:
            info = DUMMY_STOCKS.get(code, {})
            result.append({"code": code, "name": info.get("name", code), "in_watchlist": False})
    return result

# after
@app.get("/api/me/stocks-all")
async def api_me_stocks_all(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user_id = user["id"]
    watchlist = get_watchlist(user_id)
    channels  = get_channels(user_id)
    channel_codes = list(dict.fromkeys(c["stock_code"] for c in channels))
    wl_set = set(watchlist)
    ch_set = set(channel_codes)

    all_codes = list(dict.fromkeys(watchlist + channel_codes))
    names = await get_stock_info(all_codes) if all_codes else {}

    result = []
    for code in watchlist:
        name = names.get(code, {}).get("name", code)
        result.append({"code": code, "name": name, "in_watchlist": True, "has_channel": code in ch_set})
    for code in channel_codes:
        if code not in wl_set:
            name = names.get(code, {}).get("name", code)
            result.append({"code": code, "name": name, "in_watchlist": False, "has_channel": True})
    return result
```

- [ ] **Step 4: 관심종목 추가 시 종목코드 검증**

```python
# before
@app.post("/api/me/watchlist/{code}")
def api_watchlist_add(code: str, request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    add_to_watchlist(user["id"], code.upper())
    return {"ok": True}

# after
@app.post("/api/me/watchlist/{code}")
async def api_watchlist_add(code: str, request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    code = code.upper()
    info = await get_stock_info([code])
    if code not in info:
        raise HTTPException(status_code=404, detail=f"{code} 종목을 찾을 수 없습니다.")
    add_to_watchlist(user["id"], code)
    return {"ok": True, "name": info[code].get("name", code)}
```

- [ ] **Step 5: 문법 검사**

Run: `python3 -m py_compile server/app.py`
Expected: 에러 없음

- [ ] **Step 6: Commit**

```bash
git add server/app.py
git commit -m "feat: app.py 웹 API를 실제 토스증권 API 연동으로 교체, 미사용 stocks 엔드포인트 제거"
```

---

### Task 12: `server/static/watchlist.html` — 전체종목 브라우징 제거, 코드 입력 추가

**Files:**
- Modify: `server/static/watchlist.html`

- [ ] **Step 1: 스타일 추가**

```html
<!-- before -->
    .search-input:focus { border-color:#7dd3fc; }
    .list { background:#16213e; border:1px solid #1e3a5f; border-radius:12px; overflow:hidden; }

<!-- after -->
    .search-input:focus { border-color:#7dd3fc; }
    .add-wrap { display:flex; gap:8px; margin-bottom:8px; }
    .add-wrap .search-input { flex:1; }
    .add-error { color:#ef5350; font-size:0.8rem; margin-bottom:14px; min-height:1em; }
    .list { background:#16213e; border:1px solid #1e3a5f; border-radius:12px; overflow:hidden; }
```

- [ ] **Step 2: 검색창 placeholder 수정 및 코드 입력 UI 추가**

```html
<!-- before -->
  <div class="search-wrap">
    <input class="search-input" id="search" placeholder="종목명 또는 코드 검색…" oninput="render()">
  </div>
  <div id="list-wrap"><div class="loading">불러오는 중…</div></div>

<!-- after -->
  <div class="search-wrap">
    <input class="search-input" id="search" placeholder="내 목록에서 검색…" oninput="render()">
  </div>
  <div class="add-wrap">
    <input class="search-input" id="add-code" placeholder="종목코드 입력 후 추가 (예: 005930)" maxlength="6">
    <button class="toggle-btn off" id="add-btn" onclick="addByCode()">⭐ 추가</button>
  </div>
  <div id="add-error" class="add-error"></div>
  <div id="list-wrap"><div class="loading">불러오는 중…</div></div>
```

- [ ] **Step 3: "전체 종목" 섹션 헤더 이름 수정**

```javascript
// before
  if (!q && wl.length && rest.length) html += `<div class="section-header">전체 종목</div>`;

// after
  if (!q && wl.length && rest.length) html += `<div class="section-header">채널 등록 종목</div>`;
```

- [ ] **Step 4: `addByCode` 함수 추가**

```javascript
// before
async function toggle(code, btn) {

// after
async function addByCode() {
  const input = document.getElementById('add-code');
  const errBox = document.getElementById('add-error');
  const btn = document.getElementById('add-btn');
  const code = input.value.trim();
  errBox.textContent = '';
  if (!/^\d{6}$/.test(code)) {
    errBox.textContent = '6자리 종목코드를 입력해주세요.';
    return;
  }
  btn.disabled = true;
  try {
    const res = await fetch(`/api/me/watchlist/${code}`, { method: 'POST' });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      errBox.textContent = data.detail || '종목을 찾을 수 없습니다.';
      return;
    }
    const data = await res.json();
    input.value = '';
    const existing = stocks.find(s => s.code === code);
    if (existing) {
      existing.in_watchlist = true;
    } else {
      stocks.push({ code, name: data.name || code, in_watchlist: true, has_channel: false });
    }
    render();
  } finally {
    btn.disabled = false;
  }
}

async function toggle(code, btn) {
```

- [ ] **Step 5: Commit**

```bash
git add server/static/watchlist.html
git commit -m "feat: watchlist.html에 종목코드 추가 입력 UI 추가, 전체종목 브라우징 문구 정리"
```

---

### Task 13: `server/static/chart-editor.html` — 전체종목 브라우징 제거, 코드 직접 열기 추가

**Files:**
- Modify: `server/static/chart-editor.html`

- [ ] **Step 1: 스타일 추가**

```html
<!-- before -->
    .search-input:focus { border-color:#7dd3fc; }

    .list { background:#16213e; border:1px solid #1e3a5f; border-radius:12px; overflow:hidden; }

<!-- after -->
    .search-input:focus { border-color:#7dd3fc; }
    .add-wrap { display:flex; gap:8px; margin-bottom:16px; }
    .add-wrap .search-input { flex:1; }

    .list { background:#16213e; border:1px solid #1e3a5f; border-radius:12px; overflow:hidden; }
```

- [ ] **Step 2: 검색창 placeholder 수정 및 코드 직접 열기 UI 추가**

```html
<!-- before -->
  <div class="search-wrap">
    <input class="search-input" id="search" placeholder="종목명 또는 코드 검색…" oninput="render()">
  </div>
  <div id="list-wrap"><div class="loading">불러오는 중…</div></div>

<!-- after -->
  <div class="search-wrap">
    <input class="search-input" id="search" placeholder="내 목록에서 검색…" oninput="render()">
  </div>
  <div class="add-wrap">
    <input class="search-input" id="direct-code" placeholder="종목코드로 바로 열기 (예: 005930)" maxlength="6">
    <button class="edit-btn" onclick="openDirect()">✏️ 열기</button>
  </div>
  <div id="list-wrap"><div class="loading">불러오는 중…</div></div>
```

- [ ] **Step 3: "전체 종목" 섹션 헤더 이름 수정**

```javascript
// before
  if (wl.length && !q && rest.length) html += `<div class="section-header">전체 종목</div>`;

// after
  if (wl.length && !q && rest.length) html += `<div class="section-header">채널 등록 종목</div>`;
```

- [ ] **Step 4: `openEditor`가 버튼 없이도 호출 가능하도록 수정하고 `openDirect` 추가**

```javascript
// before
async function openEditor(code, name, btn) {
  btn.disabled = true;
  btn.textContent = '로딩 중…';
  try {
    const res = await fetch(`/api/me/editor-token?code=${code}`);
    const data = await res.json();
    window.open(data.editor_url, '_blank');
  } finally {
    btn.disabled = false;
    btn.textContent = '✏️ 차트수정';
  }
}

// after
async function openEditor(code, name, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '로딩 중…'; }
  try {
    const res = await fetch(`/api/me/editor-token?code=${code}`);
    const data = await res.json();
    window.open(data.editor_url, '_blank');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✏️ 차트수정'; }
  }
}

function openDirect() {
  const input = document.getElementById('direct-code');
  const code = input.value.trim();
  if (!/^\d{6}$/.test(code)) {
    alert('6자리 종목코드를 입력해주세요.');
    return;
  }
  openEditor(code, code, null);
}
```

- [ ] **Step 5: Commit**

```bash
git add server/static/chart-editor.html
git commit -m "feat: chart-editor.html에 종목코드 직접 열기 추가, 전체종목 브라우징 문구 정리"
```

---

### Task 14: 환경설정 마무리 및 수동 통합 검증

**Files:**
- Modify: `.env` (secrets — append only, read하지 않음)

- [ ] **Step 1: `.env`에 플레이스홀더 추가 (기존 내용은 건드리지 않음)**

Run:
```bash
cat >> .env << 'EOF'

# 토스증권 Open API (developers.tossinvest.com)
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
EOF
```

- [ ] **Step 2: 사용자가 직접 `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET` 값 채워넣기**

이 단계는 자동화할 수 없음 — 토스증권 WTS의 Open API 메뉴에서 발급받은 실제 client_id/client_secret을 `.env`에 직접 입력해달라고 사용자에게 안내한다.

- [ ] **Step 3: 전체 자동화 테스트 스위트 실행**

Run: `python3 -m pytest -v`
Expected: 이 계획에서 작성한 모든 테스트 통과 (Task 2~7 합산, 약 20개)

- [ ] **Step 4: 서버 기동 확인**

Run: `./start.sh` (백그라운드 확인 후 Ctrl+C로 종료)
Expected: `[서버] PID ... — 준비 완료`, `[봇] Discord 봇 시작 중...` 순서로 출력, 크래시 없음

- [ ] **Step 5: 수동 통합 체크리스트 (실제 `TOSS_CLIENT_ID`/`SECRET` 필요)**

- [ ] 디스코드에서 `/주식` → 🔍 주식 검색 → 실제 존재하는 종목코드(예: `005930`) 입력 → 실제 캔들스틱 차트와 시세가 표시되는지 확인
- [ ] 존재하지 않는 코드(예: `999999`) 입력 → "종목을 찾을 수 없습니다" 에러가 표시되는지 확인
- [ ] ⭐ 관심 종목 → ➕ 추가 → 모달에서 종목코드 입력 → 정상 추가 및 확인 메시지 확인
- [ ] ⭐ 관심 종목 임베드에서 실제 현재가·등락률이 표시되는지 확인 (더 이상 "목업 데이터" 문구 없음)
- [ ] 웹 대시보드 `/watchlist` 페이지에서 종목코드 입력 후 추가 → 목록에 반영되는지 확인, 잘못된 코드 입력 시 에러 문구 확인
- [ ] 웹 대시보드 `/chart-editor` 페이지에서 종목코드 직접 열기로 에디터가 열리는지 확인, 에디터에서 실제 캔들 데이터(최근 150봉 확대)가 표시되는지 확인
- [ ] 알림 워커 로그에서 5분 주기 폴링이 배치 조회(`get_prices` 1회)로 동작하며 에러 없이 도는지 콘솔 출력으로 확인

- [ ] **Step 6: Commit**

```bash
git add .env
git commit -m "chore: 토스증권 API 자격증명 환경변수 플레이스홀더 추가"
```

**주의:** `.env`는 실제 비밀값을 포함하므로, 커밋 전 `git status`로 `.gitignore`에 의해 실제로 제외되는지 반드시 확인한다. 이미 추적 중이 아니라면(`.gitignore`에 `.env`가 등록되어 있다면) 이 커밋은 비어 있거나 실패할 수 있으며, 그 경우 이 Step은 건너뛴다.

---

## Self-Review 요약

- **스펙 커버리지:** 스펙의 1~5절(신규 모듈, 데이터 계층, 봇, 웹 대시보드, 환경설정)이 각각 Task 2~5 / 6~7 / 8~10 / 11~13 / 14에 매핑됨. 에러 처리(429/재시도/TossAPIError)는 Task 2, 워커 장애 복원력은 Task 10에 포함.
- **플레이스홀더 스캔:** 없음 — 모든 스텝에 실행 가능한 완전한 코드/명령이 포함됨.
- **타입/시그니처 일관성:** `gen_ohlcv`, `get_candles`, `get_candles_range`, `get_stock_info`, `get_stock_name`, `get_prices`의 시그니처가 Task 2~7과 이후 호출부(Task 8~11)에서 동일하게 사용됨을 확인함. `_build_watchlist_embed`/`_make_watchlist_view`는 Task 8에서 정의되고 Task 8~9 나머지 호출부에서 일관되게 `await`로 사용됨.
