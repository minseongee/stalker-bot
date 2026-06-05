import asyncio
import datetime
from utils import KST as _KST
import os
import traceback

import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands

from utils.summarizer import (
    get_cache_time_kst,
    _build_hot_news_embeds,
    summarize_market_briefing,
)
from utils.chart import fetch_chart, supported_codes
from server.database import (
    get_watchlist, add_to_watchlist, remove_from_watchlist,
    set_news_channel, get_news_channels_by_type,
    save_message_id, get_message_id, get_broadcast_cluster_ids,
    get_live_setting, set_setting, get_users_watching,
)
from server.ohlcv import DUMMY_STOCKS, gen_ohlcv


SERVER_URL    = os.getenv("SERVER_URL", "http://localhost:8000")
DASHBOARD_URL = os.getenv("EDITOR_BASE_URL", SERVER_URL)

_NEWS_TIMES = [
    datetime.time(hour=8,  tzinfo=_KST),
    datetime.time(hour=12, tzinfo=_KST),
    datetime.time(hour=16, tzinfo=_KST),
    datetime.time(hour=21, tzinfo=_KST),
]

_last_broadcast_cluster_ids: set[str] = set()  # 중복 브로드캐스트 방지
_sent_messages: dict[str, dict[str, int]] = {}  # cluster_id → {channel_id: message_id}
_BROADCAST_ID_LIMIT = 1000  # 메모리 누수 방지 상한


# ── 임베드 빌더 ──────────────────────────────────────────────────────────────

def _build_dashboard_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📊 Stalker Bot",
        description="아래 버튼을 눌러 원하는 기능을 선택하세요.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="🔍 주식 검색", value="종목 코드를 입력하면 캔들스틱 차트와 시세를 조회합니다.", inline=False)
    embed.add_field(name="⭐ 관심 종목", value="나만의 관심 종목 목록을 관리합니다.", inline=False)
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


def _build_hot_embed(news: dict) -> discord.Embed:
    direction  = news.get("direction", "neutral")
    hot_score  = float(news.get("hot_score", 0))
    emphasis_threshold = float(os.getenv("HOT_EMPHASIS_THRESHOLD", "60"))
    emphasis   = hot_score >= emphasis_threshold

    if emphasis:
        color = discord.Color.orange()
    elif direction == "positive":
        color = discord.Color.red()
    elif direction == "negative":
        color = discord.Color.blue()
    else:
        color = discord.Color.greyple()

    dir_emoji = "📈" if direction == "positive" else ("📉" if direction == "negative" else "📌")
    title_emoji = f"🔥{dir_emoji}" if emphasis else dir_emoji

    embed = discord.Embed(
        title=f"{title_emoji} {news['headline']}",
        description=news["summary"],
        color=color,
    )
    dir_label = "📈 호재" if direction == "positive" else ("📉 악재" if direction == "negative" else "📌 중립")
    embed.add_field(name="시장 영향", value=dir_label, inline=True)

    tags = news.get("stock_tags", [])
    if tags:
        def _fmt_tag(t: str) -> str:
            if ":" in t:
                code, name = t.split(":", 1)
                return f"`{code}` {name}"
            return f"`{t}`"
        embed.add_field(name="관련주", value="\n".join(_fmt_tag(t) for t in tags), inline=True)

    sources = news.get("sources", [])
    if sources:
        dart_srcs  = [s for s in sources if s.get("source") == "DART"]
        other_srcs = [s for s in sources if s.get("source") != "DART"]
        lines: list[str] = [f"[{s['source']}]({s['url']})" for s in other_srcs[:4]]
        if dart_srcs:
            first = dart_srcs[0]
            raw   = first.get("title", "")
            corp  = raw[1:raw.index("]")] if raw.startswith("[") and "]" in raw else "DART"
            label = f"DART·{corp}"
            if len(dart_srcs) == 1:
                lines.append(f"[{label}]({first['url']})")
            else:
                lines.append(f"[{label}]({first['url']}) 외 {len(dart_srcs)-1}건")
        embed.add_field(name="출처", value="\n".join(lines), inline=False)

    ft = news.get('fetched_at')
    if ft:
        dt = datetime.datetime.fromtimestamp(ft, tz=_KST)
        ts = dt.strftime('%Y-%m-%d %H:%M KST')
    else:
        ts = get_cache_time_kst() or '-'
    embed.set_footer(text=f'수집 시각: {ts}')
    return embed


# ── 차트 전송 헬퍼 ────────────────────────────────────────────────────────────
# defer() 후 호출. 차트 embed + ChartResultView를 ephemeral followup으로 전송.

async def _send_chart(interaction: discord.Interaction, code: str) -> None:
    try:
        result = await fetch_chart(code)
    except Exception as e:
        await interaction.followup.send(
            f"⚠️ 차트 이미지를 가져오는 데 실패했습니다.\n```{type(e).__name__}: {e}```",
            ephemeral=True,
        )
        return
    if result is None:
        await interaction.followup.send(
            f"❌ `{code}` 종목을 찾을 수 없습니다.\n\n**지원 종목**\n{supported_codes()}",
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
        view=ChartResultView(code, str(interaction.user.id)),
        ephemeral=True,
    )


# ── 주식 검색 Modal ───────────────────────────────────────────────────────────

class StockSearchModal(discord.ui.Modal, title="주식 차트 조회"):
    code = discord.ui.TextInput(
        label="종목 코드 (6자리)",
        placeholder="예: 005930",
        min_length=6,
        max_length=6,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _send_chart(interaction, self.code.value.strip())


# ── 차트 결과 View ────────────────────────────────────────────────────────────

class ChartResultView(discord.ui.View):
    def __init__(self, stock_code: str, user_id: str):
        super().__init__(timeout=300)
        self.stock_code = stock_code
        self.user_id    = user_id

        # 관심 종목 버튼 — 현재 상태에 따라 동적으로 생성
        in_wl = stock_code in get_watchlist(user_id)
        wl_btn = discord.ui.Button(
            label="★ 관심 종목 제거" if in_wl else "⭐ 관심 종목 추가",
            style=discord.ButtonStyle.danger if in_wl else discord.ButtonStyle.secondary,
        )
        wl_btn.callback = self._toggle_watchlist
        self.add_item(wl_btn)

        # 차트수정 버튼
        edit_btn = discord.ui.Button(label="✏️ 차트수정", style=discord.ButtonStyle.secondary)
        edit_btn.callback = self._edit_chart
        self.add_item(edit_btn)

    async def _toggle_watchlist(self, interaction: discord.Interaction):
        uid  = str(interaction.user.id)
        name = DUMMY_STOCKS.get(self.stock_code, {}).get("name", self.stock_code)
        if self.stock_code in get_watchlist(uid):
            remove_from_watchlist(uid, self.stock_code)
            msg = f"★ **{name}** ({self.stock_code})을 관심 종목에서 제거했습니다."
        else:
            add_to_watchlist(uid, self.stock_code)
            msg = f"⭐ **{name}** ({self.stock_code})을 관심 종목에 추가했습니다!"
        # 버튼 상태 갱신 (embed·첨부 파일은 유지)
        await interaction.response.edit_message(view=ChartResultView(self.stock_code, uid))
        await interaction.followup.send(msg, ephemeral=True)

    async def _edit_chart(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"✏️ **{self.stock_code} 차트 수정**\n\n"
            f"아래 대시보드에서 로그인 후 차트를 수정하세요.\n"
            f"{DASHBOARD_URL}/dashboard",
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

        codes = get_watchlist(user_id)
        if codes:
            options = [
                discord.SelectOption(
                    label=f"{DUMMY_STOCKS.get(c, {}).get('name', c)} ({c})",
                    value=c,
                    emoji="📊",
                )
                for c in codes
            ]
            select = discord.ui.Select(
                placeholder="🔍 종목 차트 바로 조회…",
                options=options,
                row=1,
            )
            select.callback = self._on_search
            self.add_item(select)

    async def _on_search(self, interaction: discord.Interaction):
        code = interaction.data["values"][0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _send_chart(interaction, code)

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



# ── Cog ──────────────────────────────────────────────────────────────────────

class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        global _last_broadcast_cluster_ids
        _last_broadcast_cluster_ids |= get_broadcast_cluster_ids()
        from news.pipeline import register_hot_callback
        register_hot_callback(self._on_hot_news)
        print(f"[뉴스] hot news 콜백 등록 완료 (기존 broadcast {len(_last_broadcast_cluster_ids)}건 로드)")
        self.daily_briefing.start()
        self.force_briefing_check.start()

    def cog_unload(self):
        self.daily_briefing.cancel()
        self.force_briefing_check.cancel()

    @tasks.loop(time=_NEWS_TIMES)
    async def daily_briefing(self):
        now_kst = datetime.datetime.now(tz=_KST).strftime('%Y-%m-%d %H:%M KST')
        print(f"[브리핑] 스케줄 실행 시작 ({now_kst})")
        try:
            summary = await summarize_market_briefing()
            if not summary:
                print("[브리핑] 수집된 기사 없음, 건너뜀")
                return
            embed = discord.Embed(
                title="📰 시장 브리핑",
                description=summary,
                color=discord.Color.green(),
            )
            now_kst = datetime.datetime.now(tz=_KST).strftime('%Y-%m-%d %H:%M KST')
            embed.set_footer(text=f'브리핑 생성: {now_kst}')
            briefing_channels = get_news_channels_by_type("briefing")
            if not briefing_channels:
                print("[브리핑] 채널 미배정 — /브리핑채널 명령어로 채널을 지정해주세요.")
                return
            for row in briefing_channels:
                ch = self.bot.get_channel(int(row["channel_id"]))
                if ch is None:
                    continue
                try:
                    await ch.send(embed=embed)
                    print(f"[브리핑] 채널 {row['channel_id']} 전송 완료")
                except Exception as e:
                    print(f"[브리핑] 채널 {row['channel_id']} 전송 실패: {e}")
        except Exception:
            print("[브리핑] daily_briefing 오류:")
            traceback.print_exc()

    @daily_briefing.before_loop
    async def before_daily_briefing(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=30)
    async def force_briefing_check(self):
        if not self.daily_briefing.is_running():
            print("[브리핑] daily_briefing 태스크 중단 감지 — 재시작")
            self.daily_briefing.start()

        import time as _t
        val = get_live_setting("FORCE_BRIEFING")
        if not val:
            return
        triggered_at = int(val)
        if _t.time() - triggered_at > 300:  # 5분 지나면 무시
            set_setting("FORCE_BRIEFING", "")
            return
        set_setting("FORCE_BRIEFING", "")
        print("[브리핑] 웹 대시보드 강제실행 요청 감지")
        try:
            summary = await summarize_market_briefing()
            if not summary:
                print("[브리핑] 수집된 기사 없음, 건너뜀")
                return
            embed = discord.Embed(
                title="📰 시장 브리핑 (강제실행)",
                description=summary,
                color=discord.Color.orange(),
            )
            now_kst = datetime.datetime.now(tz=_KST).strftime('%Y-%m-%d %H:%M KST')
            embed.set_footer(text=f'브리핑 생성: {now_kst}')
            briefing_channels = get_news_channels_by_type("briefing")
            for row in briefing_channels:
                ch = self.bot.get_channel(int(row["channel_id"]))
                if ch:
                    await ch.send(embed=embed)
            print(f"[브리핑] 강제실행 완료 ({len(briefing_channels)}개 채널)")
        except Exception:
            print("[브리핑] 강제실행 오류:")
            traceback.print_exc()

    @force_briefing_check.before_loop
    async def before_force_briefing_check(self):
        await self.bot.wait_until_ready()

    async def _notify_watchlist(self, news: dict) -> None:
        """핫뉴스의 stock_tags와 관심 종목 교집합이 있는 유저에게 DM 전송."""
        tags = news.get("stock_tags", [])
        # "005930:삼성전자" → "005930", 코드 없는 섹터명은 제외
        codes = [t.split(":")[0] for t in tags if ":" in t]
        if not codes:
            return

        matched = get_users_watching(codes)
        if not matched:
            return

        direction = news.get("direction", "neutral")
        dir_label = "📈 호재" if direction == "positive" else ("📉 악재" if direction == "negative" else "📌 중립")

        for user_id, watching_codes in matched.items():
            try:
                user = await self.bot.fetch_user(int(user_id))
            except Exception:
                continue

            from server.ohlcv import DUMMY_STOCKS
            names = [DUMMY_STOCKS.get(c, {}).get("name", c) for c in watching_codes]
            stock_str = ", ".join(f"**{n}**({c})" for n, c in zip(names, watching_codes))

            notify_embed = discord.Embed(
                title=f"🔔 관심 종목 핫뉴스 — {dir_label}",
                description=f"{stock_str} 관련 뉴스가 감지됐습니다.\n\n**{news['headline']}**\n{news['summary']}",
                color=discord.Color.gold(),
            )
            notify_embed.set_footer(text="Stalker Bot · 관심 종목 알림")
            try:
                await user.send(embed=notify_embed)
                print(f"[관심종목알림] {user_id} → {watching_codes}")
            except discord.Forbidden:
                pass  # DM 차단한 유저
            except Exception as e:
                print(f"[관심종목알림] {user_id} 전송 실패: {e}")

    def _on_hot_news(self, refined_list: list[dict]) -> None:
        """pipeline.py에서 새 핫뉴스 정제 완료 시 호출 (동기 콜백)."""
        asyncio.create_task(self._broadcast_hot_news(refined_list))

    async def _broadcast_hot_news(self, refined_list: list[dict]) -> None:
        global _last_broadcast_cluster_ids, _sent_messages
        for news in refined_list:
            cid       = news.get("cluster_id", "")
            is_update = news.get("is_update", False)

            is_dart  = any(s.get("source") == "DART" for s in news.get("sources", []))
            channels = get_news_channels_by_type("dart") if is_dart else []
            if not channels:
                if is_dart:
                    print("[DART] dart채널 미배정 — 핫뉴스채널로 대신 전송합니다.")
                channels = get_news_channels_by_type("hot")
            if not channels:
                print("[HOT] 채널 미배정 — /핫뉴스채널 명령어로 채널을 지정해주세요.")
                continue
            ch_type = "dart" if is_dart else "hot"

            embed = _build_hot_embed(news)

            if is_update:
                # 편입 기사 — 기존 메시지 수정
                for row in channels:
                    ch = self.bot.get_channel(int(row["channel_id"]))
                    if ch is None:
                        continue
                    msg_id = (_sent_messages.get(cid, {}).get(str(row["channel_id"]))
                              or get_message_id(cid, str(row["channel_id"])))
                    if msg_id is None:
                        continue  # 저장된 메시지 ID 없으면 skip
                    try:
                        msg = await ch.fetch_message(msg_id)
                        await msg.edit(embed=embed)
                        print(f"[{ch_type.upper()}] 채널 {row['channel_id']} 편입 업데이트: {news['headline']}")
                    except Exception as e:
                        print(f"[{ch_type.upper()}] 채널 {row['channel_id']} 편입 업데이트 실패: {e}")
                continue

            # 신규 핫뉴스
            if cid in _last_broadcast_cluster_ids:
                continue
            _last_broadcast_cluster_ids.add(cid)
            if len(_last_broadcast_cluster_ids) > _BROADCAST_ID_LIMIT:
                oldest = next(iter(_last_broadcast_cluster_ids))
                _last_broadcast_cluster_ids.discard(oldest)

            for row in channels:
                ch = self.bot.get_channel(int(row["channel_id"]))
                if ch is None:
                    continue
                try:
                    msg = await ch.send(embed=embed)
                    _sent_messages.setdefault(cid, {})[str(row["channel_id"])] = msg.id
                    save_message_id(cid, str(row["channel_id"]), msg.id)
                    print(f"[{ch_type.upper()}] 채널 {row['channel_id']} 전송: {news['headline']}")
                except Exception as e:
                    print(f"[{ch_type.upper()}] 채널 {row['channel_id']} 전송 실패: {e}")

            await self._notify_watchlist(news)

    @app_commands.command(name="핫뉴스채널", description="이 채널을 실시간 핫뉴스 채널로 지정합니다. (관리자 전용)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_hot_ch(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("서버 채널에서만 사용할 수 있습니다.", ephemeral=True)
            return
        set_news_channel(str(interaction.guild_id), str(interaction.channel_id), "hot")
        await interaction.response.send_message(
            f"✅ <#{interaction.channel_id}>을 **실시간 핫뉴스** 채널로 지정했습니다.\n"
            "hot_score 기준을 넘는 뉴스가 발생하면 즉시 전송됩니다.",
            ephemeral=True,
        )
        # 지정 즉시 최근 핫뉴스 전송
        hot_list = _build_hot_news_embeds()
        if hot_list:
            for news in hot_list[:5]:
                try:
                    await interaction.channel.send(embed=_build_hot_embed(news))
                except Exception as e:
                    print(f"[핫뉴스] 즉시 전송 실패: {e}")

    @set_hot_ch.error
    async def set_hot_ch_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    @app_commands.command(name="dart채널", description="이 채널을 DART 공시 전용 채널로 지정합니다. (관리자 전용)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_dart_ch(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("서버 채널에서만 사용할 수 있습니다.", ephemeral=True)
            return
        set_news_channel(str(interaction.guild_id), str(interaction.channel_id), "dart")
        await interaction.response.send_message(
            f"✅ <#{interaction.channel_id}>을 **DART 공시** 채널로 지정했습니다.\n"
            "전자공시(유상증자·합병·실적 등)가 접수되면 즉시 전송됩니다.",
            ephemeral=True,
        )

    @set_dart_ch.error
    async def set_dart_ch_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    @app_commands.command(name="브리핑채널", description="이 채널을 하루 4회 시장 브리핑 채널로 지정합니다. (관리자 전용)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_briefing_ch(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("서버 채널에서만 사용할 수 있습니다.", ephemeral=True)
            return
        set_news_channel(str(interaction.guild_id), str(interaction.channel_id), "briefing")
        await interaction.response.send_message(
            f"✅ <#{interaction.channel_id}>을 **시장 브리핑** 채널로 지정했습니다.\n"
            "08:00 / 12:00 / 16:00 / 21:00 KST에 시장 요약이 전송됩니다.",
            ephemeral=True,
        )

    @set_briefing_ch.error
    async def set_briefing_ch_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

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

    @app_commands.command(name="브리핑강제실행", description="브리핑을 즉시 실행합니다. (봇 소유자 전용)")
    async def force_briefing(self, interaction: discord.Interaction):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("봇 소유자만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            summary = await summarize_market_briefing()
            if not summary:
                await interaction.followup.send("⚠️ 수집된 기사가 없어 브리핑을 생성할 수 없습니다.", ephemeral=True)
                return
            embed = discord.Embed(
                title="📰 시장 브리핑 (강제실행)",
                description=summary,
                color=discord.Color.orange(),
            )
            now_kst = datetime.datetime.now(tz=_KST).strftime('%Y-%m-%d %H:%M KST')
            embed.set_footer(text=f'브리핑 생성: {now_kst}')
            briefing_channels = get_news_channels_by_type("briefing")
            if not briefing_channels:
                await interaction.followup.send("⚠️ 브리핑 채널이 지정되지 않았습니다.", ephemeral=True)
                return
            count = 0
            for row in briefing_channels:
                ch = self.bot.get_channel(int(row["channel_id"]))
                if ch is None:
                    continue
                try:
                    await ch.send(embed=embed)
                    count += 1
                except Exception as e:
                    print(f"[브리핑] 강제실행 채널 {row['channel_id']} 전송 실패: {e}")
            await interaction.followup.send(f"✅ 브리핑을 {count}개 채널에 전송했습니다.", ephemeral=True)
            print(f"[브리핑] 강제실행 완료 ({count}개 채널)")
        except Exception as e:
            await interaction.followup.send(f"⚠️ 오류 발생: {e}", ephemeral=True)
            print(f"[브리핑] 강제실행 오류: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"[General] Cog 로드 완료")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
