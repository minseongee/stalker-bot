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
    channel_type: str = 'normal'  # 'normal' | 'fib'


class ChannelResponse(BaseModel):
    id: int
    user_id: str
    stock_code: str
    p1_ts: float
    p1_price: float
    p2_ts: float
    p2_price: float
    offset_y: float
    channel_type: str = 'normal'
    alert_enabled: bool = True
    created_at: int


class ChannelUpdateRequest(BaseModel):
    token: str
    p1_ts: float
    p1_price: float
    p2_ts: float
    p2_price: float
    offset_y: float


class ChannelAlertToggleRequest(BaseModel):
    token: str
    enabled: bool


class ChannelDeleteRequest(BaseModel):
    token: str
    channel_id: int
