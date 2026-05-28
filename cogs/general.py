import traceback
import discord
from discord.ext import commands, tasks
from discord import app_commands
from utils.summarizer import summarize_news, get_cached_news, get_cache_time_kst
from utils.chart import fetch_chart, supported_codes

_news_embed: discord.Embed | None = None
_news_loading: bool = False
_dashboard_message: discord.Message | None = None


def _build_dashboard_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Stalker Bot",
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
        result = await fetch_chart(raw)
        if result is None:
            await interaction.followup.send(
                f"❌ `{raw}` 종목을 찾을 수 없습니다.\n\n**지원 종목**\n{supported_codes()}",
                ephemeral=True,
            )
            return

        buf, info = result
        sign = "▲" if info["change"] >= 0 else "▼"
        color = discord.Color.red() if info["change"] >= 0 else discord.Color.blue()

        embed = discord.Embed(title=f"📊 {info['name']} ({info['code']})", color=color)
        embed.add_field(
            name="현재가",
            value=f"**{info['close']:,}원** {sign} {info['change']:+,}원 ({info['change_pct']:+.2f}%)",
            inline=False,
        )
        embed.add_field(name="시가", value=f"{info['open']:,}원", inline=True)
        embed.add_field(name="고가", value=f"{info['high']:,}원", inline=True)
        embed.add_field(name="저가", value=f"{info['low']:,}원", inline=True)
        embed.add_field(name="거래량", value=f"{info['volume']:,}", inline=True)
        embed.set_image(url="attachment://chart.png")
        embed.set_footer(text="⚠️ 목업 데이터 — 한국투자증권 API 연동 예정")

        await interaction.followup.send(
            embed=embed,
            file=discord.File(buf, filename="chart.png"),
            ephemeral=True,
        )


class StockView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="주식 검색", style=discord.ButtonStyle.primary, emoji="🔍", custom_id="stock:search")
    async def search_stock(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(StockSearchModal())

    @discord.ui.button(label="관심 종목", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="stock:watchlist")
    async def watchlist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(
            "관심 종목 기능은 준비 중입니다.", ephemeral=True
        )

    @discord.ui.button(label="시장 뉴스", style=discord.ButtonStyle.secondary, emoji="📰", custom_id="stock:news")
    async def news(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if _news_loading or _news_embed is None:
            await interaction.response.send_message(
                "⏳ 뉴스를 갱신하는 중입니다. 잠시 후 다시 시도해주세요.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=_news_embed, ephemeral=True)


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
        global _news_embed, _news_loading, _dashboard_message
        _news_loading = True
        if _dashboard_message:
            try:
                await _dashboard_message.edit(embed=_build_dashboard_embed())
            except Exception:
                pass
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
        if _dashboard_message:
            try:
                await _dashboard_message.edit(embed=_build_dashboard_embed())
            except Exception:
                pass

    @refresh_news.error
    async def on_refresh_error(self, error: Exception):
        print(f"[뉴스] refresh_news 태스크 오류: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)

    @refresh_news.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="주식", description="주식 어시스턴트 대시보드를 생성합니다. (관리자 전용)")
    @app_commands.checks.has_permissions(administrator=True)
    async def stock_menu(self, interaction: discord.Interaction):
        global _dashboard_message
        await interaction.response.send_message(embed=_build_dashboard_embed(), view=StockView())
        _dashboard_message = await interaction.original_response()

    @stock_menu.error
    async def stock_menu_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"[General] Cog 로드 완료")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
