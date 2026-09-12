"""Rainbow Six Siege player profile command."""

import asyncio
import os
from typing import Any

import discord
import requests
from discord.ext import commands


PROFILE_URL = "https://public-api.arenyze.com/r6/api/v2/profile"
PLATFORM_TYPE = "uplay"  # PC / Ubisoft Connect

RANK_NAMES = {
    0: "Unranked",
    1: "Copper V", 2: "Copper IV", 3: "Copper III", 4: "Copper II", 5: "Copper I",
    6: "Bronze V", 7: "Bronze IV", 8: "Bronze III", 9: "Bronze II", 10: "Bronze I",
    11: "Silver V", 12: "Silver IV", 13: "Silver III", 14: "Silver II", 15: "Silver I",
    16: "Gold V", 17: "Gold IV", 18: "Gold III", 19: "Gold II", 20: "Gold I",
    21: "Platinum V", 22: "Platinum IV", 23: "Platinum III", 24: "Platinum II", 25: "Platinum I",
    26: "Emerald V", 27: "Emerald IV", 28: "Emerald III", 29: "Emerald II", 30: "Emerald I",
    31: "Diamond V", 32: "Diamond IV", 33: "Diamond III", 34: "Diamond II", 35: "Diamond I",
    36: "Champion",
}


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _latest_ranked_profile(data: dict[str, Any]) -> dict[str, Any] | None:
    profiles = []
    for family in data.get("stats", {}).get("platform_families_full_profiles", []):
        for board in family.get("board_ids_full_profiles", []):
            if board.get("board_id") == "ranked":
                profiles.extend(board.get("full_profiles", []))
    return max(profiles, key=lambda item: _number(item.get("season_id"))) if profiles else None


def _format_profile(data: dict[str, Any]) -> tuple[str, dict[str, str]]:
    player = data.get("player") or {}
    account = data.get("account") or {}
    ranked = _latest_ranked_profile(data)
    profile = (ranked or {}).get("profile") or {}
    season_stats = (ranked or {}).get("season_statistics") or {}

    kills = _number(season_stats.get("kills", profile.get("kills")))
    deaths = _number(season_stats.get("deaths", profile.get("deaths")))
    outcomes = season_stats.get("match_outcomes") or {}
    wins = _number(outcomes.get("wins", profile.get("wins")))
    losses = _number(outcomes.get("losses", profile.get("losses")))
    abandons = _number(outcomes.get("abandons", profile.get("abandon")))
    matches = wins + losses
    kd = kills / deaths if deaths else float(kills)
    win_rate = wins / matches * 100 if matches else 0

    rank_id = _number(profile.get("rank"))
    peak_rank_id = _number(profile.get("max_rank"))
    username = str(player.get("nameOnPlatform") or "Unknown")
    fields = {
        "等級": str(account.get("level", "N/A")),
        "目前牌位": f"{RANK_NAMES.get(rank_id, f'Unknown ({rank_id})')}\n{_number(profile.get('rank_points')):,} RP",
        "最高牌位": f"{RANK_NAMES.get(peak_rank_id, f'Unknown ({peak_rank_id})')}\n{_number(profile.get('max_rank_points')):,} RP",
        "戰績": f"{wins} 勝 / {losses} 敗\n勝率 **{win_rate:.2f}%**",
        "戰鬥數據": f"擊殺 **{kills:,}**\n死亡 **{deaths:,}**\nK/D **{kd:.2f}**",
        "放棄場次": str(abandons),
    }
    if ranked:
        fields["賽季"] = str(ranked.get("season_id", "N/A"))
    return username, fields


def _request_profile(username: str, api_key: str) -> requests.Response:
    return requests.get(
        PROFILE_URL,
        params={"nameOnPlatform": username, "platformType": PLATFORM_TYPE},
        headers={"api-key": api_key},
        timeout=(10, 90),
    )


class R6(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="r6")
    async def r6(self, ctx: commands.Context, player_id: str | None = None):
        """查詢 PC Rainbow Six Siege 玩家資料：ai-r6 <玩家 ID>"""
        if not player_id:
            await ctx.send("用法：`ai-r6 <玩家 ID>`\n平台已固定為 PC / Ubisoft Connect。")
            return

        api_key = os.getenv("R6_API_KEY", "").strip()
        if not api_key:
            await ctx.send("尚未設定 `R6_API_KEY`，請在 `.env` 補上 Arenyze API key。")
            return

        try:
            response = await asyncio.to_thread(_request_profile, player_id, api_key)
        except requests.ConnectTimeout:
            await ctx.send("連線 Arenyze API 逾時，請稍後再試。")
            return
        except requests.ReadTimeout:
            await ctx.send("Arenyze API 回應逾時，請稍後再試。")
            return
        except requests.ConnectionError:
            await ctx.send("無法連線到 Arenyze API，請稍後再試。")
            return
        except requests.RequestException:
            await ctx.send("查詢 R6 資料時發生網路錯誤。")
            return

        if response.status_code == 401:
            await ctx.send("R6 API key 無效，請檢查 `.env` 的 `R6_API_KEY`。")
            return
        if response.status_code == 404:
            await ctx.send(f'找不到 PC 玩家：`{player_id}`')
            return
        if response.status_code == 429:
            await ctx.send("R6 API 請求太頻繁，請稍後再試。")
            return
        if response.status_code != 200:
            await ctx.send(f"R6 API 暫時無法使用（HTTP {response.status_code}）。")
            return

        try:
            username, fields = _format_profile(response.json())
        except (ValueError, TypeError, AttributeError):
            await ctx.send("R6 API 回傳的資料格式無法辨識。")
            return

        embed = discord.Embed(
            title=f"{username} 的 R6 戰績",
            description="🖥️ 平台：**PC / Ubisoft Connect**",
            color=0x6E56CF,
        )
        for name, value in fields.items():
            embed.add_field(name=name, value=value, inline=True)
        embed.set_footer(text="資料來源：Arenyze R6 API")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(R6(bot))
