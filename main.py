import discord
from discord.ext import commands
import os
import asyncio
import dotenv

dotenv.load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=";", intents=intents)


@bot.event
async def on_ready():
    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"[Bot] 길드 {guild_id} 슬래시 커맨드 즉시 동기화 완료")
    else:
        await bot.tree.sync()
        print("[Bot] 전역 슬래시 커맨드 동기화 완료 (최대 1시간 소요)")
    print(f"[Bot] {bot.user} 로그인 완료")
    print(f"[Bot] 서버 수: {len(bot.guilds)}")


async def main():
    async with bot:
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                await bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"[Cog] {filename} 로드 완료")
        from cogs.general import StockView
        bot.add_view(StockView())

        from news.pipeline import run_loop
        from server.app import push_hot_news
        from news.pipeline import register_hot_callback
        register_hot_callback(push_hot_news)  # SSE 구독자에게도 전달
        asyncio.create_task(run_loop())
        print("[Pipeline] 뉴스 수집 태스크 시작")
        await bot.start(os.getenv("DISCORD_TOKEN"))


asyncio.run(main())
