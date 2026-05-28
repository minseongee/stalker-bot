"""알림 워커 — 주기적으로 채널 상/하단 돌파를 감지해 Discord DM을 보냅니다."""
import time

import discord
from discord.ext import commands, tasks

from server.database import (
    already_alerted,
    clear_alert,
    get_all_channels,
    init_db,
    record_alert,
)
from server.ohlcv import DUMMY_STOCKS, gen_ohlcv

POLL_MINUTES = 5


def _current_price(stock_code: str) -> float | None:
    """현재 가격 반환. 실제 API 연동 전까지는 더미 데이터 마지막 종가 사용."""
    data = gen_ohlcv(stock_code, days=2)
    if not data:
        return None
    return float(data[-1]["close"])


def _channel_bounds_now(ch: dict) -> tuple[float, float] | None:
    """현재 시각 기준 채널 상단/하단 가격 계산."""
    p1_ts: float = ch["p1_ts"]
    p2_ts: float = ch["p2_ts"]
    if p1_ts == p2_ts:
        return None
    slope = (ch["p2_price"] - ch["p1_price"]) / (p2_ts - p1_ts)
    now = time.time()
    upper = ch["p1_price"] + slope * (now - p1_ts)
    lower = upper + ch["offset_y"]  # offset_y는 보통 음수
    return upper, lower


class Worker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        channels = get_all_channels()
        for ch in channels:
            await self._check(ch)

    async def _check(self, ch: dict):
        code = ch["stock_code"]
        price = _current_price(code)
        if price is None:
            return

        bounds = _channel_bounds_now(ch)
        if bounds is None:
            return
        upper, lower = bounds

        user = await self._get_user(ch["user_id"])
        if user is None:
            return

        ch_id = ch["id"]
        name = DUMMY_STOCKS.get(code, {}).get("name", code)

        if price >= upper:
            if not already_alerted(ch_id, "upper"):
                record_alert(ch_id, "upper")
                await self._send_alert(user, name, code, price, upper, "상단")
        else:
            clear_alert(ch_id, "upper")

        if price <= lower:
            if not already_alerted(ch_id, "lower"):
                record_alert(ch_id, "lower")
                await self._send_alert(user, name, code, price, lower, "하단")
        else:
            clear_alert(ch_id, "lower")

    async def _get_user(self, user_id: str) -> discord.User | None:
        try:
            return await self.bot.fetch_user(int(user_id))
        except Exception:
            return None

    async def _send_alert(
        self,
        user: discord.User,
        name: str,
        code: str,
        price: float,
        line_price: float,
        side: str,
    ):
        arrow = "📈" if side == "상단" else "📉"
        embed = discord.Embed(
            title=f"{arrow} 채널 {side}선 돌파 알림",
            color=discord.Color.red() if side == "상단" else discord.Color.blue(),
        )
        embed.add_field(name="종목", value=f"**{name}** ({code})", inline=True)
        embed.add_field(name="현재가", value=f"**{price:,.0f}원**", inline=True)
        embed.add_field(name=f"채널 {side}선", value=f"{line_price:,.0f}원", inline=True)
        embed.set_footer(text="⚠️ 목업 데이터 — 한국투자증권 API 연동 예정")
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Worker(bot))
