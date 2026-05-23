import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime
from utils.summarizer import summarize_news, get_cache_time_kst

_news_embed: discord.Embed | None = None


async def _build_news_embed() -> discord.Embed | None:
    summary = await summarize_news()
    if not summary:
        return None
    embed = discord.Embed(
        title="📰 오늘의 시장 브리핑",
        description=summary,
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return embed


class StockView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="주식 검색", style=discord.ButtonStyle.primary, emoji="🔍", custom_id="stock:search")
    async def search_stock(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(
            "검색할 종목 티커를 입력해주세요. (예: AAPL, TSLA, 005930)", ephemeral=True
        )

    @discord.ui.button(label="관심 종목", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="stock:watchlist")
    async def watchlist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(
            "관심 종목 기능은 준비 중입니다.", ephemeral=True
        )

    @discord.ui.button(label="시장 뉴스", style=discord.ButtonStyle.secondary, emoji="📰", custom_id="stock:news")
    async def news(self, interaction: discord.Interaction, _button: discord.ui.Button):
        global _news_embed
        if _news_embed is None:
            await interaction.response.defer(ephemeral=True)
            _news_embed = await _build_news_embed()
        if _news_embed is None:
            await interaction.followup.send("뉴스를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=_news_embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=_news_embed, ephemeral=True)


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.refresh_news.start()

    def cog_unload(self):
        self.refresh_news.cancel()

    @tasks.loop(hours=1)
    async def refresh_news(self):
        global _news_embed
        embed = await _build_news_embed()
        if embed:
            _news_embed = embed

    @refresh_news.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="주식", description="주식 어시스턴트 대시보드를 생성합니다. (관리자 전용)")
    @app_commands.checks.has_permissions(administrator=True)
    async def stock_menu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="주식 어시스턴트",
            description="아래 버튼을 눌러 원하는 기능을 선택하세요.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="🔍 주식 검색", value="종목 정보를 조회합니다.", inline=False)
        embed.add_field(name="⭐ 관심 종목", value="나만의 관심 종목 목록을 관리합니다.", inline=False)
        embed.add_field(name="📰 시장 뉴스", value="최신 주식 시장 뉴스를 확인합니다.", inline=False)
        news_ts = get_cache_time_kst()
        embed.set_footer(text=f"뉴스 마지막 갱신: {news_ts}" if news_ts else "뉴스 아직 로드되지 않음")
        await interaction.response.send_message(embed=embed, view=StockView())

    @stock_menu.error
    async def stock_menu_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"[General] Cog 로드 완료")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
