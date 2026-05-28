import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")


class Editor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="차트수정", description="종목 채널을 그릴 수 있는 웹 에디터 링크를 DM으로 받습니다.")
    @app_commands.describe(종목코드="6자리 종목 코드 (예: 005930)")
    async def open_editor(self, interaction: discord.Interaction, 종목코드: str):
        code = 종목코드.strip()
        if not code.isdigit() or len(code) != 6:
            await interaction.response.send_message(
                "❌ 종목 코드는 숫자 6자리여야 합니다. (예: `005930`)", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{SERVER_URL}/token",
                    json={"user_id": str(interaction.user.id), "stock_code": code},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"서버 응답 오류: {resp.status}")
                    data = await resp.json()
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ 에디터 링크 생성에 실패했습니다.\n```{e}```", ephemeral=True
            )
            return

        editor_url = data["editor_url"]
        expires_in = data["expires_in"] // 60  # 분 단위

        try:
            await interaction.user.send(
                f"📊 **{code} 채널 에디터**\n\n"
                f"아래 링크에서 추세 채널을 그리고 저장하세요.\n"
                f"링크는 **{expires_in}분** 후 만료됩니다.\n\n"
                f"{editor_url}"
            )
            await interaction.followup.send(
                f"✅ DM으로 에디터 링크를 보냈습니다!", ephemeral=True
            )
        except discord.Forbidden:
            # DM이 막혀있는 경우 채널에 ephemeral로 URL 직접 전송
            await interaction.followup.send(
                f"📊 **{code} 채널 에디터** (DM이 막혀있어 여기에 표시합니다)\n\n"
                f"{editor_url}\n\n"
                f"⏱️ {expires_in}분 후 만료",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Editor(bot))
