"""알림 워커 — 주기적으로 채널 상/하단 돌파를 감지해 Discord DM을 보냅니다."""
import time

import discord
from discord.ext import commands, tasks

from server.database import (
    already_alerted,
    get_all_channels,
    init_db,
    record_alert,
)
from server.ohlcv import DUMMY_STOCKS, gen_ohlcv

POLL_MINUTES = 5

FIB_LEVELS = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5]
FIB_LEVEL_COLORS = {
    0: 0x9e9e9e, 0.5: 0xef5350, 1: 0xff9800, 1.5: 0xffeb3b,
    2: 0x4caf50, 2.5: 0x29b6f6, 3: 0x3f51b5, 3.5: 0x9c27b0,
}


def _current_price(stock_code: str) -> float | None:
    """현재 가격 반환. 실제 API 연동 전까지는 더미 데이터 마지막 종가 사용."""
    data = gen_ohlcv(stock_code, days=2)
    if not data:
        return None
    return float(data[-1]["close"])


def _normal_bounds_now(ch: dict) -> tuple[float, float] | None:
    """현재 시각 기준 채널 상단/하단 가격 계산."""
    p1_ts: float = ch["p1_ts"]
    p2_ts: float = ch["p2_ts"]
    if p1_ts == p2_ts:
        return None
    slope = (ch["p2_price"] - ch["p1_price"]) / (p2_ts - p1_ts)
    now = time.time()
    upper = ch["p1_price"] + slope * (now - p1_ts)
    lower = upper + ch["offset_y"]
    return upper, lower


def _fib_level_prices_now(ch: dict) -> list[tuple[float, float]] | None:
    """현재 시각 기준 피보나치 채널 각 레벨의 가격 반환 — [(level, price), ...]."""
    p1_ts: float = ch["p1_ts"]
    p2_ts: float = ch["p2_ts"]
    if p1_ts == p2_ts:
        return None
    slope = (ch["p2_price"] - ch["p1_price"]) / (p2_ts - p1_ts)
    now = time.time()
    base = ch["p1_price"] + slope * (now - p1_ts)  # level 0 가격
    offset = ch["offset_y"]  # 레벨 1당 가격 간격
    return [(level, base + offset * level) for level in FIB_LEVELS]


class Worker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

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

    async def _get_user(self, user_id: str) -> discord.User | None:
        try:
            return await self.bot.fetch_user(int(user_id))
        except Exception:
            return None

    async def _send_normal_alert(
        self,
        user: discord.User,
        name: str,
        code: str,
        price: float,
        line_price: float,
        side: str,  # "upper" | "lower"
    ):
        if side == "upper":
            title    = "📈 채널 상단선 상향 돌파"
            desc     = "가격이 채널 **상단선을 위로 돌파**했습니다."
            color    = discord.Color.red()
            diff_str = f"+{price - line_price:,.0f}원 위"
        else:
            title    = "📉 채널 하단선 하향 이탈"
            desc     = "가격이 채널 **하단선을 아래로 이탈**했습니다."
            color    = discord.Color.blue()
            diff_str = f"{line_price - price:,.0f}원 아래"

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name="종목",        value=f"**{name}** ({code})",  inline=True)
        embed.add_field(name="현재가",      value=f"**{price:,.0f}원**",   inline=True)
        embed.add_field(name="채널선 가격", value=f"{line_price:,.0f}원",  inline=True)
        embed.add_field(name="이탈 폭",     value=diff_str,                inline=True)
        embed.set_footer(text="⚠️ 목업 데이터 | 쿨타임 1시간")
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _send_fib_alert(
        self,
        user: discord.User,
        name: str,
        code: str,
        price: float,
        level_price: float,
        level: float,
    ):
        color_int = FIB_LEVEL_COLORS.get(level, 0xffffff)
        diff      = price - level_price

        embed = discord.Embed(
            title=f"📊 피보 채널 레벨 {level} 상향 돌파",
            description=f"가격이 피보나치 채널 **레벨 {level}을 위로 돌파**했습니다.",
            color=discord.Color(color_int),
        )
        embed.add_field(name="종목",        value=f"**{name}** ({code})",  inline=True)
        embed.add_field(name="현재가",      value=f"**{price:,.0f}원**",   inline=True)
        embed.add_field(name="레벨 가격",   value=f"{level_price:,.0f}원", inline=True)
        embed.add_field(name="돌파 폭",     value=f"+{diff:,.0f}원",       inline=True)
        embed.set_footer(text="⚠️ 목업 데이터 | 쿨타임 1시간")
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Worker(bot))
