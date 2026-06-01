import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .ohlcv import gen_ohlcv
from .database import (
    create_token,
    delete_channel,
    get_channels,
    get_hot_news_by_score,
    get_recent_news_items,
    init_db,
    save_channel,
    update_channel_alert,
    update_channel_coords,
    validate_token,
)
from .models import (
    ChannelAlertToggleRequest,
    ChannelDeleteRequest,
    ChannelResponse,
    ChannelSaveRequest,
    ChannelUpdateRequest,
    TokenRequest,
    TokenResponse,
)

BASE_URL = os.getenv("EDITOR_BASE_URL", "http://localhost:8000")
TOKEN_TTL = 600  # 10분

# SSE 구독자: {queue} 셋
_sse_subscribers: set[asyncio.Queue] = set()


def push_hot_news(refined_list: list[dict]) -> None:
    """pipeline.py의 hot callback — 정제된 핫뉴스를 모든 SSE 구독자에게 전송."""
    payload = json.dumps(refined_list, ensure_ascii=False)
    dead = set()
    for q in list(_sse_subscribers):  # copy — 순회 중 set 변경 방지
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.add(q)
    _sse_subscribers.difference_update(dead)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Stalker Bot API", lifespan=lifespan)

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")] \
    if os.getenv("CORS_ORIGINS") else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/editor")
def editor_page():
    return FileResponse(_STATIC / "editor.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(_STATIC / "dashboard.html")


@app.get("/api/news")
def news_dashboard(limit: int = Query(default=200, le=1000), hot_only: bool = False):
    if hot_only:
        threshold = float(os.getenv("HOT_SCORE_THRESHOLD", "70"))
        rows = get_hot_news_by_score(threshold, limit=limit)
    else:
        rows = get_recent_news_items(limit=limit)
    result = []
    for r in rows:
        result.append({
            "id":          r["id"],
            "title":       r["title"],
            "url":         r["url"],
            "source":      r["source"],
            "published_at": r["published_at"],
            "fetched_at":  r["fetched_at"],
            "hot_score":   round(r["hot_score"] or 0, 2),
            "is_hot":      bool(r["is_hot"]),
            "direction":   r["direction"] or "",
            "headline":    r["headline"] or "",
            "stock_tags":  json.loads(r["stock_tags"]) if r.get("stock_tags") else [],
        })
    return result


@app.get("/ohlcv/{code}")
def ohlcv(code: str, days: int = 90):
    data = gen_ohlcv(code.upper(), days)
    if data is None:
        raise HTTPException(status_code=404, detail=f"{code} 종목을 찾을 수 없습니다.")
    return data


@app.post("/token", response_model=TokenResponse)
def issue_token(req: TokenRequest):
    """Discord 봇이 호출 — 사용자별 10분짜리 편집 토큰 발급."""
    token = secrets.token_urlsafe(32)
    create_token(token, req.user_id, ttl_seconds=TOKEN_TTL)
    return TokenResponse(
        token=token,
        editor_url=f"{BASE_URL}/editor?token={token}&code={req.stock_code.upper()}",
        expires_in=TOKEN_TTL,
    )


@app.get("/channels/me", response_model=list[ChannelResponse])
def my_channels(token: str, code: str | None = None):
    """웹 에디터가 호출 — 내 채널 목록 조회 (토큰 소모 없음)."""
    user_id = validate_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않거나 만료됐습니다.")
    rows = get_channels(user_id)
    if code:
        rows = [r for r in rows if r["stock_code"] == code.upper()]
    return [ChannelResponse(**r) for r in rows]


@app.post("/channels", response_model=ChannelResponse)
def save_channel_endpoint(req: ChannelSaveRequest):
    """웹 에디터가 호출 — 채널 좌표를 저장."""
    user_id = validate_token(req.token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않거나 만료됐습니다.")

    channel_id = save_channel(
        user_id=user_id,
        stock_code=req.stock_code.upper(),
        p1_ts=req.p1_ts,
        p1_price=req.p1_price,
        p2_ts=req.p2_ts,
        p2_price=req.p2_price,
        offset_y=req.offset_y,
        channel_type=req.channel_type,
    )
    rows = get_channels(user_id)
    row = next(r for r in rows if r["id"] == channel_id)
    return ChannelResponse(**row)


@app.get("/channels/{user_id}", response_model=list[ChannelResponse])
def list_channels(user_id: str):
    """알림 워커가 호출 — 사용자의 전체 채널 목록 조회."""
    return [ChannelResponse(**r) for r in get_channels(user_id)]


@app.put("/channels/{channel_id}", response_model=ChannelResponse)
def update_channel_endpoint(channel_id: int, req: ChannelUpdateRequest):
    """웹 에디터가 호출 — 채널 좌표 수정."""
    user_id = validate_token(req.token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않거나 만료됐습니다.")
    row = update_channel_coords(
        channel_id, user_id,
        req.p1_ts, req.p1_price,
        req.p2_ts, req.p2_price,
        req.offset_y,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")
    return ChannelResponse(**row)


@app.patch("/channels/{channel_id}/alert", response_model=ChannelResponse)
def toggle_alert(channel_id: int, req: ChannelAlertToggleRequest):
    """웹 에디터가 호출 — 채널 알림 on/off 토글."""
    user_id = validate_token(req.token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않거나 만료됐습니다.")
    row = update_channel_alert(channel_id, user_id, req.enabled)
    if row is None:
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")
    return ChannelResponse(**row)


@app.delete("/channels")
def delete_channel_endpoint(req: ChannelDeleteRequest):
    """웹 에디터가 호출 — 채널 삭제."""
    user_id = validate_token(req.token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않거나 만료됐습니다.")
    if not delete_channel(req.channel_id, user_id):
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")
    return {"ok": True}


# ── 뉴스 API ──────────────────────────────────────────────────────────────────

@app.get("/news/hot")
def hot_news(limit: int = Query(default=20, le=100)):
    """최근 정제된 핫뉴스 목록 조회."""
    threshold = float(os.getenv("HOT_SCORE_THRESHOLD", "70"))
    rows = get_hot_news_by_score(threshold, limit=limit)
    result = []
    for r in rows:
        sources = json.loads(r["sources_json"]) if r.get("sources_json") else []
        tags    = json.loads(r["stock_tags"])    if r.get("stock_tags")    else []
        result.append({
            "headline":  r["headline"],
            "summary":   r["summary"],
            "direction": r["direction"],
            "tags":      tags,
            "sources":   sources,
            "fetched_at": r["fetched_at"],
        })
    return result


@app.get("/news/stream")
async def news_stream(tags: str = Query(default="")):
    """
    SSE 스트림 — 핫뉴스 발생 시 실시간 전달.
    tags: 쉼표 구분 종목/섹터 필터 (비어있으면 전체 전달)
    """
    filter_tags = {t.strip() for t in tags.split(",") if t.strip()} if tags else set()
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_subscribers.add(queue)

    async def event_generator():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    items: list[dict] = json.loads(payload)
                    if filter_tags:
                        items = [
                            it for it in items
                            if filter_tags & set(it.get("stock_tags", []))
                        ]
                    if items:
                        data = json.dumps(items, ensure_ascii=False)
                        yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
