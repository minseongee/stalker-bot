from pydantic import BaseModel


class TokenRequest(BaseModel):
    user_id: str
    stock_code: str


class TokenResponse(BaseModel):
    token: str
    editor_url: str
    expires_in: int  # seconds


class ChannelSaveRequest(BaseModel):
    token: str
    stock_code: str
    p1_ts: float    # 추세선 1번 점 — Unix timestamp (초)
    p1_price: float
    p2_ts: float    # 추세선 2번 점
    p2_price: float
    offset_y: float  # 하단 평행선 오프셋 (원)


class ChannelResponse(BaseModel):
    id: int
    user_id: str
    stock_code: str
    p1_ts: float
    p1_price: float
    p2_ts: float
    p2_price: float
    offset_y: float
    created_at: int


class ChannelUpdateRequest(BaseModel):
    token: str
    p1_ts: float
    p1_price: float
    p2_ts: float
    p2_price: float
    offset_y: float


class ChannelDeleteRequest(BaseModel):
    token: str
    channel_id: int
