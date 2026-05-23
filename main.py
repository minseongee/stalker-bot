import discord
from discord.ext import commands
from cogs.general import StockView
import os
import asyncio
import dotenv

dotenv.load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=";", intents=intents)


@bot.event
async def on_ready():
    bot.add_view(StockView())
    await bot.tree.sync()
    print(f"[Bot] {bot.user} 로그인 완료")
    print(f"[Bot] 슬래시 커맨드 동기화 완료")
    print(f"[Bot] 서버 수: {len(bot.guilds)}")


async def main():
    async with bot:
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                await bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"[Cog] {filename} 로드 완료")

        await bot.start(os.getenv("DISCORD_TOKEN"))


asyncio.run(main())
