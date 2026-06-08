import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from urllib.parse import quote
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .ohlcv import gen_ohlcv, DUMMY_STOCKS
from .database import (
    get_watchlist,
    upsert_user_profile,
    get_user_alerts,
    add_to_watchlist,
    remove_from_watchlist,
    create_token,
    delete_channel,
    get_admin_stats,
    get_all_settings,
    get_channels,
    get_hot_news_by_score,
    get_live_setting,
    get_recent_news_items,
    get_user_detail,
    get_users_summary,
    init_db,
    save_channel,
    set_setting,
    update_channel_alert,
    update_channel_coords,
    validate_token,
    get_announcements,
    add_announcement,
    delete_announcement,
    save_hot_news_alert,
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

DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", f"{BASE_URL}/auth/callback").rstrip("/")
ADMIN_USER_IDS        = set(filter(None, os.getenv("ADMIN_USER_IDS", "").split(",")))
SESSION_SECRET        = os.getenv("SESSION_SECRET", "")

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

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False)

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")] \
    if os.getenv("CORS_ORIGINS") else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC, html=False), name="static")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# ── Discord OAuth2 ─────────────────────────────────────────────────────────────

@app.get("/auth/login")
def auth_login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    # 요청이 들어온 호스트 기반으로 콜백 URI 동적 결정
    redirect_uri = f"{request.url.scheme}://{request.url.netloc}/auth/callback"
    request.session["redirect_uri"] = redirect_uri
    url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        "&response_type=code"
        "&scope=identify"
        f"&state={state}"
    )
    return RedirectResponse(url)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str):
    if state != request.session.pop("oauth_state", None):
        raise HTTPException(status_code=400, detail="잘못된 state 값입니다.")

    redirect_uri = request.session.pop("redirect_uri", DISCORD_REDIRECT_URI)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id":     DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = token_resp.json()
        if "access_token" not in token_data:
            raise HTTPException(status_code=400, detail="Discord 토큰 발급 실패")

        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        user = user_resp.json()

    profile = {
        "id":          user["id"],
        "username":    user["username"],
        "global_name": user.get("global_name") or user["username"],
        "avatar":      user.get("avatar"),
    }
    request.session["user"] = profile
    upsert_user_profile(profile["id"], profile["username"], profile["global_name"], profile["avatar"])

    if user["id"] in ADMIN_USER_IDS:
        return RedirectResponse("/administrator", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login", status_code=303)


# ── 페이지 라우트 ───────────────────────────────────────────────────────────────

@app.get("/login")
def login_page():
    return FileResponse(_STATIC / "login.html")


@app.get("/editor")
def editor_page():
    return FileResponse(_STATIC / "editor.html")


@app.get("/administrator")
def dashboard_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["id"] not in ADMIN_USER_IDS:
        return HTMLResponse("<h1>403 — 관리자만 접근할 수 있습니다.</h1>", status_code=403)
    return FileResponse(_STATIC / "administrator.html")


@app.get("/news-list")
def news_list_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["id"] not in ADMIN_USER_IDS:
        return HTMLResponse("<h1>403 — 관리자만 접근할 수 있습니다.</h1>", status_code=403)
    return FileResponse(_STATIC / "news-list.html")


@app.get("/api/me")
def api_me(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return {**user, "is_admin": user["id"] in ADMIN_USER_IDS}


# ── 관리자 페이지 ──────────────────────────────────────────────────────────────

@app.get("/user-management")
def user_management_page(request: Request):
    user = request.session.get("user")
    if not user or user["id"] not in ADMIN_USER_IDS:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(_STATIC / "user-management.html")


@app.get("/bot-settings")
def bot_settings_page(request: Request):
    user = request.session.get("user")
    if not user or user["id"] not in ADMIN_USER_IDS:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(_STATIC / "bot-settings.html")


@app.get("/stats")
def stats_page(request: Request):
    user = request.session.get("user")
    if not user or user["id"] not in ADMIN_USER_IDS:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(_STATIC / "stats.html")


# ── 관리자 API ──────────────────────────────────────────────────────────────────

def _admin_check(request: Request):
    user = request.session.get("user")
    if not user or user["id"] not in ADMIN_USER_IDS:
        raise HTTPException(status_code=401, detail="관리자만 접근할 수 있습니다.")
    return user


@app.get("/api/admin/discord-lookup/{user_id}")
async def api_discord_lookup(user_id: str, request: Request):
    _admin_check(request)
    import httpx as _httpx
    token = os.getenv("DISCORD_TOKEN", "")
    async with _httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://discord.com/api/v10/users/{user_id}",
            headers={"Authorization": f"Bot {token}"},
        )
    if resp.status_code != 200:
        return {"user_id": user_id, "global_name": user_id, "avatar": None}
    u = resp.json()
    gn = u.get("global_name") or u.get("username", user_id)
    upsert_user_profile(user_id, u.get("username", user_id), gn, u.get("avatar"))
    return {"user_id": user_id, "global_name": gn, "avatar": u.get("avatar")}


@app.get("/api/admin/users")
def api_admin_users(request: Request):
    _admin_check(request)
    return get_users_summary()


@app.get("/api/admin/users/{user_id}")
def api_admin_user_detail(user_id: str, request: Request):
    _admin_check(request)
    return get_user_detail(user_id)


@app.get("/api/admin/stats")
def api_admin_stats(request: Request):
    _admin_check(request)
    return get_admin_stats()


@app.get("/api/admin/settings")
def api_admin_settings_get(request: Request):
    _admin_check(request)
    return get_all_settings()


@app.post("/api/admin/settings")
async def api_admin_settings_post(request: Request):
    _admin_check(request)
    body = await request.json()
    for key, value in body.items():
        set_setting(key, str(value))
    return {"ok": True}


@app.post("/api/admin/trigger-briefing")
def api_trigger_briefing(request: Request):
    _admin_check(request)
    import time as _t
    set_setting("FORCE_BRIEFING", str(int(_t.time())))
    return {"ok": True}


@app.get("/api/announcements")
def api_get_announcements(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return get_announcements()


@app.post("/api/admin/announcements")
async def api_post_announcement(request: Request):
    user = _admin_check(request)
    body = await request.json()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="제목과 내용을 입력해주세요.")
    announcement_id = add_announcement(title, content, user["id"], user.get("global_name", user["id"]))
    return {"ok": True, "id": announcement_id}


@app.delete("/api/admin/announcements/{announcement_id}")
def api_delete_announcement(announcement_id: int, request: Request):
    _admin_check(request)
    delete_announcement(announcement_id)
    return {"ok": True}


@app.get("/dashboard")
def user_dashboard_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(_STATIC / "dashboard.html")


@app.get("/watchlist")
def watchlist_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(_STATIC / "watchlist.html")


@app.get("/alert-history")
def alert_history_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(_STATIC / "alert-history.html")


@app.post("/api/me/watchlist/{code}")
def api_watchlist_add(code: str, request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    add_to_watchlist(user["id"], code.upper())
    return {"ok": True}


@app.delete("/api/me/watchlist/{code}")
def api_watchlist_remove(code: str, request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    remove_from_watchlist(user["id"], code.upper())
    return {"ok": True}


@app.get("/api/me/alerts")
def api_me_alerts(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return get_user_alerts(user["id"])


@app.get("/chart-editor")
def chart_editor_list_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(_STATIC / "chart-editor.html")


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


@app.get("/api/me/editor-token")
def api_me_editor_token(request: Request, code: str):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    token = secrets.token_urlsafe(32)
    create_token(token, user["id"], ttl_seconds=86400)
    editor_url = f"{BASE_URL}/editor?token={token}&code={code.upper()}"
    return {"editor_url": editor_url}


# ── API ────────────────────────────────────────────────────────────────────────

@app.get("/api/news")
def news_dashboard(request: Request, limit: int = Query(default=200, le=1000), hot_only: bool = False):
    user = request.session.get("user")
    if not user or user["id"] not in ADMIN_USER_IDS:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    if hot_only:
        threshold = float(get_live_setting("HOT_SCORE_THRESHOLD"))
        rows = get_hot_news_by_score(threshold, limit=limit)
    else:
        rows = get_recent_news_items(limit=limit)
    result = []
    for r in rows:
        result.append({
            "id":           r["id"],
            "title":        r["title"],
            "url":          r["url"],
            "source":       r["source"],
            "published_at": r["published_at"],
            "fetched_at":   r["fetched_at"],
            "hot_score":    round(r["hot_score"] or 0, 2),
            "is_hot":       bool(r["is_hot"]),
            "direction":    r["direction"] or "",
            "headline":     r["headline"] or "",
            "stock_tags":   json.loads(r["stock_tags"]) if r.get("stock_tags") else [],
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
    threshold = float(get_live_setting("HOT_SCORE_THRESHOLD"))
    rows = get_hot_news_by_score(threshold, limit=limit)
    result = []
    for r in rows:
        sources = json.loads(r["sources_json"]) if r.get("sources_json") else []
        tags    = json.loads(r["stock_tags"])    if r.get("stock_tags")    else []
        result.append({
            "headline":   r["headline"],
            "summary":    r["summary"],
            "direction":  r["direction"],
            "tags":       tags,
            "sources":    sources,
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
