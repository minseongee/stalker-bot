import os
import traceback

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from utils.summarizer import summarize_news, get_cached_news, get_cache_time_kst
from utils.chart import fetch_chart, supported_codes
from server.database import get_watchlist, add_to_watchlist, remove_from_watchlist
from server.ohlcv import DUMMY_STOCKS, gen_ohlcv

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")

_news_embed: discord.Embed | None = None
_news_loading: bool = False


# ── 임베드 빌더 ──────────────────────────────────────────────────────────────

def _build_dashboard_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📊 Stalker Bot",
        description="아래 버튼을 눌러 원하는 기능을 선택하세요.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="🔍 주식 검색", value="종목 코드를 입력하면 캔들스틱 차트와 시세를 조회합니다.", inline=False)
    embed.add_field(name="⭐ 관심 종목", value="나만의 관심 종목 목록을 관리합니다.", inline=False)
    embed.add_field(name="📰 시장 뉴스", value="최신 주식 시장 뉴스를 확인합니다.", inline=False)
    news_ts = get_cache_time_kst()
    if _news_loading:
        footer = "뉴스 갱신 중..."
    elif news_ts:
        footer = f"뉴스 마지막 갱신: {news_ts}"
    else:
        footer = "뉴스 아직 로드되지 않음"
    embed.set_footer(text=footer)
    return embed


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


async def _build_news_embed() -> discord.Embed | None:
    summary = await summarize_news()
    if not summary:
        return None
    embed = discord.Embed(
        title="📰 오늘의 시장 브리핑",
        description=summary,
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"마지막 업데이트: {get_cache_time_kst()}")
    return embed


# ── 주식 검색 Modal ───────────────────────────────────────────────────────────
# 차트 이미지(파일 첨부)가 필요해서 edit_message 불가 → 별도 ephemeral로 유지

class StockSearchModal(discord.ui.Modal, title="주식 차트 조회"):
    code = discord.ui.TextInput(
        label="종목 코드 (6자리)",
        placeholder="예: 005930",
        min_length=6,
        max_length=6,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        raw = self.code.value.strip()
        try:
            result = await fetch_chart(raw)
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ 차트 이미지를 가져오는 데 실패했습니다.\n```{type(e).__name__}: {e}```",
                ephemeral=True,
            )
            return
        if result is None:
            await interaction.followup.send(
                f"❌ `{raw}` 종목을 찾을 수 없습니다.\n\n**지원 종목**\n{supported_codes()}",
                ephemeral=True,
            )
            return

        buf, info = result
        sign  = "▲" if info["change"] >= 0 else "▼"
        color = discord.Color.red() if info["change"] >= 0 else discord.Color.blue()
        embed = discord.Embed(title=f"📊 {info['name']} ({info['code']})", color=color)
        embed.add_field(
            name="현재가",
            value=f"**{info['close']:,}원** {sign} {info['change']:+,}원 ({info['change_pct']:+.2f}%)",
            inline=False,
        )
        embed.add_field(name="시가",   value=f"{info['open']:,}원",   inline=True)
        embed.add_field(name="고가",   value=f"{info['high']:,}원",   inline=True)
        embed.add_field(name="저가",   value=f"{info['low']:,}원",    inline=True)
        embed.add_field(name="거래량", value=f"{info['volume']:,}",   inline=True)
        embed.set_image(url="attachment://chart.png")
        embed.set_footer(text="⚠️ 목업 데이터 — 한국투자증권 API 연동 예정")

        await interaction.followup.send(
            embed=embed,
            file=discord.File(buf, filename="chart.png"),
            view=ChartResultView(raw),
            ephemeral=True,
        )


# ── 차트 결과 View ────────────────────────────────────────────────────────────

class ChartResultView(discord.ui.View):
    def __init__(self, stock_code: str):
        super().__init__(timeout=300)
        self.stock_code = stock_code

    @discord.ui.button(label="✏️ 차트수정", style=discord.ButtonStyle.secondary)
    async def edit_chart(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{SERVER_URL}/token",
                    json={"user_id": str(interaction.user.id), "stock_code": self.stock_code},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"서버 오류 {resp.status}")
                    data = await resp.json()
        except Exception as e:
            await interaction.followup.send(f"⚠️ 에디터 링크 생성 실패\n```{e}```", ephemeral=True)
            return

        expires_min = data["expires_in"] // 60
        try:
            await interaction.user.send(
                f"✏️ **{self.stock_code} 채널 에디터**\n\n"
                f"아래 링크에서 추세 채널을 그리고 저장하세요.\n"
                f"링크는 **{expires_min}분** 후 만료됩니다.\n\n"
                f"{data['editor_url']}"
            )
            await interaction.followup.send("✅ DM으로 에디터 링크를 보냈습니다!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                f"✏️ **{self.stock_code} 채널 에디터** (DM 차단)\n\n"
                f"{data['editor_url']}\n⏱️ {expires_min}분 후 만료",
                ephemeral=True,
            )


# ── 관심 종목 추가 View (Select Menu) ─────────────────────────────────────────

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


# ── 관심 종목 삭제 View (Select Menu) ─────────────────────────────────────────

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


# ── 관심 종목 메인 View ───────────────────────────────────────────────────────

class WatchlistView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=None)
        self.user_id = user_id

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

    @discord.ui.button(label="🔄 새로고침", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = _build_watchlist_embed(str(interaction.user.id))
        await interaction.response.edit_message(embed=embed, view=self)


# ── 메인 대시보드 View ────────────────────────────────────────────────────────

class StockView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="주식 검색", style=discord.ButtonStyle.primary, emoji="🔍", custom_id="stock:search")
    async def search_stock(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(StockSearchModal())

    @discord.ui.button(label="관심 종목", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="stock:watchlist")
    async def watchlist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed = _build_watchlist_embed(str(interaction.user.id))
        await interaction.response.send_message(
            embed=embed, view=WatchlistView(str(interaction.user.id)), ephemeral=True
        )

    @discord.ui.button(label="시장 뉴스", style=discord.ButtonStyle.secondary, emoji="📰", custom_id="stock:news")
    async def news(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if _news_loading or _news_embed is None:
            await interaction.response.send_message(
                "⏳ 뉴스를 갱신하는 중입니다. 잠시 후 다시 시도해주세요.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=_news_embed, ephemeral=True)


# ── Cog ──────────────────────────────────────────────────────────────────────

class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        global _news_embed
        cached = get_cached_news()
        if cached:
            embed = discord.Embed(
                title="📰 오늘의 시장 브리핑",
                description=cached,
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"마지막 업데이트: {get_cache_time_kst()}")
            _news_embed = embed
            print("[뉴스] 캐시에서 _news_embed 사전 로드 완료")
        self.refresh_news.start()

    def cog_unload(self):
        self.refresh_news.cancel()

    @tasks.loop(hours=1)
    async def refresh_news(self):
        global _news_embed, _news_loading
        _news_loading = True
        try:
            embed = await _build_news_embed()
            if embed:
                _news_embed = embed
                print("[뉴스] _news_embed 설정 완료")
            else:
                print("[뉴스] embed 생성 실패 — summary가 비어있음")
        except Exception:
            print("[뉴스] refresh_news 오류:")
            traceback.print_exc()
        finally:
            _news_loading = False

    @refresh_news.error
    async def on_refresh_error(self, error: Exception):
        print(f"[뉴스] refresh_news 태스크 오류: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)

    @refresh_news.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="주식", description="주식 어시스턴트 대시보드를 채널에 고정합니다. (관리자 전용)")
    @app_commands.checks.has_permissions(administrator=True)
    async def stock_menu(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=_build_dashboard_embed(), view=StockView()
        )

    @stock_menu.error
    async def stock_menu_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True
            )

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"[General] Cog 로드 완료")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
