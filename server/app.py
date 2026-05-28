import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .database import (
    consume_token,
    create_token,
    delete_channel,
    get_channels,
    init_db,
    save_channel,
)
from .models import (
    ChannelDeleteRequest,
    ChannelResponse,
    ChannelSaveRequest,
    TokenRequest,
    TokenResponse,
)

BASE_URL = os.getenv("EDITOR_BASE_URL", "http://localhost:8000")
TOKEN_TTL = 600  # 10분


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Stalker Bot API", lifespan=lifespan)


@app.post("/token", response_model=TokenResponse)
def issue_token(req: TokenRequest):
    """Discord 봇이 호출 — 사용자별 10분짜리 편집 토큰 발급."""
    token = secrets.token_urlsafe(32)
    create_token(token, req.user_id, ttl_seconds=TOKEN_TTL)
    return TokenResponse(
        token=token,
        editor_url=f"{BASE_URL}/editor?token={token}&code={req.user_id}",
        expires_in=TOKEN_TTL,
    )


@app.post("/channels", response_model=ChannelResponse)
def save_channel_endpoint(req: ChannelSaveRequest):
    """웹 에디터가 호출 — 채널 좌표를 저장."""
    user_id = consume_token(req.token)
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
    )
    rows = get_channels(user_id)
    row = next(r for r in rows if r["id"] == channel_id)
    return ChannelResponse(**row)


@app.get("/channels/{user_id}", response_model=list[ChannelResponse])
def list_channels(user_id: str):
    """알림 워커가 호출 — 사용자의 전체 채널 목록 조회."""
    return [ChannelResponse(**r) for r in get_channels(user_id)]


@app.delete("/channels")
def delete_channel_endpoint(req: ChannelDeleteRequest):
    """웹 에디터가 호출 — 채널 삭제."""
    user_id = consume_token(req.token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않거나 만료됐습니다.")
    if not delete_channel(req.channel_id, user_id):
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")
    return {"ok": True}
