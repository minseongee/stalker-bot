import discord
from discord.ext import commands
from discord import app_commands
from utils.news import fetch_market_news


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
        await interaction.response.defer(ephemeral=True)
        articles = await fetch_market_news(max_items=5)
        if not articles:
            await interaction.followup.send("뉴스를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
            return
        lines = [f"{i}. [{a['title'][:100]}]({a['link']}) — {a['source']}" for i, a in enumerate(articles, 1)]
        embed = discord.Embed(
            title="📰 최신 시장 뉴스",
            description="\n\n".join(lines),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
