# 토스증권 Open API 연동 설계

## 배경

Stalker Bot의 시세 데이터는 현재 `server/ohlcv.py`, `utils/chart.py`의 `DUMMY_STOCKS` 8종목 고정 딕셔너리와 랜덤 워크 생성기로 만들어진 목업이다. 이를 [토스증권 Open API](https://developers.tossinvest.com/docs) (`https://openapi.tossinvest.com`)의 실제 시세로 교체한다.

## 범위

- **포함**: 시세 조회(현재가, 캔들/OHLCV, 종목 기본정보)만 연동. 임의의 KRX 종목코드를 조회/등록할 수 있도록 확장.
- **제외**: 계좌·보유주식·주문(매수/매도) 기능은 이번 범위에서 제외. `X-Tossinvest-Account` 헤더가 필요한 엔드포인트는 다루지 않는다.
- **제외**: "전체 종목 리스트" 브라우징 UX. 토스 API에 종목 전수 조회 엔드포인트가 없으므로, 종목명 기반 전체 검색은 지원하지 않고 6자리 종목코드 입력 방식으로 통일한다.

## API 요약 (토스증권 Open API)

- Base URL: `https://openapi.tossinvest.com`
- 인증: OAuth2 Client Credentials Grant
  - `POST /oauth2/token` (form-urlencoded, `grant_type=client_credentials`, `client_id`, `client_secret`) → `{access_token, token_type, expires_in}` (기본 86400초, refresh token 없음, client당 유효 토큰 1개 — 재발급 시 이전 토큰 즉시 무효화)
  - 이후 요청은 `Authorization: Bearer {access_token}` 헤더 사용
- 주요 엔드포인트 (Rate Limit Group):
  - `GET /api/v1/prices?symbols=a,b,c` (최대 200개, `MARKET_DATA` 10회/초) — 현재가만 반환 (전일종가 없음)
  - `GET /api/v1/candles?symbol=&interval=1d|1m&count=&before=&adjusted=` (`MARKET_DATA_CHART` 5회/초) — 캔들 1종목/회, 최대 200개, `before`(ISO8601)로 과거 페이지네이션
  - `GET /api/stocks?symbols=a,b,c` (최대 200개, `STOCK` 5회/초) — 종목명/시장/상장일 등 기본정보
  - `GET /api/v1/orderbook`, `/api/v1/trades`, `/api/v1/price-limits` — 이번 범위에서는 미사용
- 종목코드: 국내주식은 6자리 숫자(예: `005930`), 그대로 `symbol` 파라미터로 사용 (기존 봇 내부 코드 형식과 동일 — 별도 변환 불필요)
- 에러: `{"error": {"requestId","code","message","data"}}`, 429 시 `Retry-After` 헤더 존중

## 아키텍처

### 1. 신규 모듈 `utils/toss_api.py`

토스 API를 감싸는 비동기 클라이언트. 이 모듈 하나만 실제 HTTP 호출을 알고, 나머지 코드는 이 모듈이 반환하는 정규화된 dict만 다룬다.

```
class TossAPIError(Exception): ...

async def get_stock_info(codes: list[str]) -> dict[str, dict]
    # {"005930": {"name": "삼성전자", "market": "KOSPI", ...}}
    # 프로세스 메모리에 무기한 캐싱 (이름/시장은 사실상 불변)

async def get_prices(codes: list[str]) -> dict[str, dict]
    # {"005930": {"price": 73200.0, "timestamp": "..."}}

async def get_candles(code: str, interval="1d", count=90, before=None) -> list[dict] | None
    # [{"time": "2026-07-08", "open":.., "high":.., "low":.., "close":.., "volume":..}]
    # None이면 종목 없음

async def get_candles_range(code: str, days: int) -> list[dict] | None
    # count=200씩 `before` 커서로 페이지네이션 반복 호출, days개 모일 때까지 또는 nextBefore=None까지
```

- **토큰 관리**: 모듈 전역에 `_token`, `_expires_at`을 캐싱. 만료 60초 전이면 `asyncio.Lock`으로 동시 재발급 경합 방지 후 재발급. 재발급 시 이전 토큰이 즉시 무효화되므로 락 없이 여러 코루틴이 동시에 재발급하면 서로의 토큰을 무효화시키는 문제를 막는다.
- **재시도**: 429 응답 시 `Retry-After` 헤더만큼 대기 후 최대 2회 재시도, 지수 백오프. 그 외 4xx/5xx는 `TossAPIError(status, code, message)`로 즉시 전파.
- **미조회 처리**: 응답 배열에 해당 심볼이 없으면(존재하지 않는 종목코드) 해당 함수는 `None`(단건) 또는 dict에서 키 누락(배치)으로 표현 — 예외를 던지지 않는다.

### 2. 데이터 계층 (`server/ohlcv.py`, `utils/chart.py`)

- `DUMMY_STOCKS`, `_business_days`, `_gen_ohlcv`(랜덤 워크) 전부 삭제.
- `server/ohlcv.py::gen_ohlcv(code, days=90) -> list[dict] | None`: `async def`로 변경, 내부적으로 `days <= 200`이면 `get_candles`, 초과하면 `get_candles_range` 호출.
- `utils/chart.py::fetch_chart(code) -> tuple[BytesIO, dict] | None`: `async def` 유지(이미 async), 내부에서 `get_candles(code, count=90)`로 DataFrame 구성 후 기존 mplfinance 렌더링 로직은 그대로 사용. `last, prev = df.iloc[-1], df.iloc[-2]`로 전일 대비 등락 계산 로직도 그대로 재사용 가능(캔들이 실제 거래일 기준으로 오므로).
- `supported_codes()` 삭제. 호출부(`general.py`의 종목 미존재 에러 메시지)는 "❌ `{code}` 종목을 찾을 수 없습니다. 종목코드를 다시 확인해주세요."로 단순화.
- `/ohlcv/{code}?days=3000` (에디터용, `server/app.py`)은 `get_candles_range`를 타면서 최대 15회 순차 페이지 호출이 발생할 수 있음 — 응답 지연(수 초) 가능. 초기 구현에서는 `(code, days)` 키의 60초 인메모리 TTL 캐시만 추가해 짧은 시간 내 반복 요청을 방지한다. SQLite 영구 캐싱은 이번 범위에서 제외(향후 필요 시 확장).

### 3. 디스코드 봇

**`cogs/general.py`**
- 모든 `DUMMY_STOCKS.get(code, {}).get("name", code)` 조회를 `toss_api.get_stock_info` 기반의 캐시된 이름 조회 헬퍼로 교체.
- `_build_watchlist_embed`: 종목별로 `get_candles(code, count=2)`를 호출해 현재가·전일대비 등락을 계산 (관심종목 수가 적어 온디맨드 호출로 충분, 배치 API가 전일종가를 안 주므로 캔들 2개로 계산).
- `WatchlistAddView` (Select Menu 기반 "추가할 종목 선택")를 삭제하고, `StockSearchModal`과 동일한 패턴의 "종목코드 입력 모달"로 교체. 제출 시 `get_stock_info([code])`로 검증 → 없으면 에러 메시지, 있으면 `add_to_watchlist` 후 `_build_watchlist_embed` 갱신.
- 임베드 footer의 "⚠️ 목업 데이터 — 한국투자증권 API 연동 예정" 문구 제거.

**`cogs/worker.py`**
- `poll()`을 재구성: 매 사이클마다 `get_all_channels()`에서 고유 종목코드를 모아 `get_prices(codes)` 한 번(배치)으로 현재가를 조회한 뒤, 그 결과 dict를 `_check`/`_check_normal`/`_check_fib`에 전달. 채널마다 개별 호출하던 기존 구조(`_current_price`)를 제거해 API 호출 수를 채널 수와 무관하게 유지한다.
- 알림 메시지의 이름 조회도 캐시된 헬퍼로 교체, "⚠️ 목업 데이터" 문구 제거.

### 4. 웹 대시보드 (`server/app.py` + `watchlist.html` / `chart-editor.html`)

- `/api/me/stocks`, `/api/me/stocks-all`: `DUMMY_STOCKS` 순회로 "전체 종목" 채우던 로직 제거. 사용자의 관심종목 ∪ 채널 종목코드만 모아 `get_stock_info`로 배치 이름 조회 후 반환하도록 단순화 (두 엔드포인트가 사실상 같은 모양이 되므로, 프론트 마이그레이션 후 `stocks-all`은 제거하고 `stocks` 하나로 통합).
- `watchlist.html`, `chart-editor.html`: "전체 종목" 섹션(비관심종목 브라우징)을 제거. 대신 "종목코드 추가" 입력창 + 버튼을 추가 — 제출 시 `POST /api/me/watchlist/{code}`를 호출하되, 서버 쪽에서 `get_stock_info`로 코드 유효성 검증 후 없으면 404 반환, 프론트는 에러를 인라인 표시.
- 기존 검색창(`#search`)은 "내 목록 안에서" 필터링 용도로 남는다.
- `/ohlcv/{code}` 라우트 핸들러를 `async def`로 변경.

### 5. 환경설정

`.env`에 추가:
```
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
```

## 데이터 흐름 예시

**차트 조회 (`/주식` → 종목코드 입력)**
1. `StockSearchModal.on_submit` → `_send_chart(interaction, code)`
2. `fetch_chart(code)` → `toss_api.get_candles(code, interval="1d", count=90)`
3. 없으면 `None` 반환 → "종목을 찾을 수 없습니다" 에러 표시
4. 있으면 DataFrame 구성 → mplfinance 렌더 → 임베드 전송

**알림 워커 폴링 (5분 주기)**
1. `get_all_channels()`로 전체 채널 목록 조회
2. 고유 종목코드 집합 추출 → `toss_api.get_prices(codes)` 배치 호출 1회
3. 채널별로 캐시된 가격 dict에서 조회 → 채널 상/하단 판정 → 필요 시 DM 발송

## 에러 처리

- 종목코드 오류(존재하지 않음): 각 진입점에서 이미 존재하는 "종목을 찾을 수 없습니다" 류 ephemeral 에러로 통일 처리 (기존 패턴 유지).
- API 일시 장애/429: `toss_api` 내부에서 `Retry-After` 기반 재시도(최대 2회) 후에도 실패하면 `TossAPIError` 전파 → 기존 `_send_chart`의 `except Exception as e` 블록이 이미 잡아서 에러 메시지를 보여주므로 추가 처리 불필요. `worker.poll()`에는 사이클 전체를 감싸는 try/except를 추가해 API 장애 시 다음 5분 주기에 자연 복구되도록 한다.
- 토큰 발급 실패(자격증명 오류): 봇 시작 시점이 아니라 최초 API 호출 시점에 발생 — 발생 시 로그에 명확히 남기고 사용자에게는 일반적인 "일시적 오류" 메시지 노출.

## 테스트 계획

- `utils/toss_api.py`: 토큰 캐싱/만료 재발급 로직, `get_candles_range`의 페이지네이션 종료 조건(200개 단위 누적, `nextBefore=None` 처리), 429 재시도 로직을 HTTP 응답을 모킹한 단위 테스트로 검증.
- `server/ohlcv.py`, `utils/chart.py`: `toss_api` 함수를 모킹해 정규화된 필드 매핑(open/high/low/close/volume/time)이 올바른지 검증.
- 통합 확인(수동): 실제 `TOSS_CLIENT_ID/SECRET`로 `/주식` 차트 조회, 관심종목 추가/삭제, 워커 폴링 1사이클, 웹 대시보드 관심종목 추가를 로컬에서 직접 실행해 확인.
