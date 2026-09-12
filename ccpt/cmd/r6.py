"""Rainbow Six Siege Tracker Network profile command."""

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import discord
from discord.ext import commands


PROFILE_URL = "https://api.tracker.gg/api/v2/r6siege/standard/profile/ubi/{}"
TRACKER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://r6.tracker.network",
    "Priority": "u=1, i=0",
    "Referer": "https://r6.tracker.network/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
        "Safari/537.36 Edg/153.0.0.0"
    ),
}


class TrackerError(Exception):
  def __init__(self, status=None):
    self.status = status


def fetch_profile(username):
  url = PROFILE_URL.format(quote(username, safe=""))
  request = Request(url, headers=TRACKER_HEADERS, method="GET")
  try:
    with urlopen(request, timeout=15) as response:
      return json.loads(response.read().decode("utf-8"))
  except HTTPError as error:
    # Consume the response body so the underlying connection can be closed.
    error.read()
    raise TrackerError(error.code) from error
  except URLError as error:
    raise TrackerError() from error
  except json.JSONDecodeError as error:
    raise TrackerError() from error


def get_r6_highlights(payload, limit=5):
  """依演算法.txt 從 Tracker 回傳資料挑選最值得展示的數據。"""
  segments = payload.get("data", {}).get("segments", [])
  season_segments = [s for s in segments if s.get("type") == "season"]
  candidates = []

  def stat(seg, key):
    return seg.get("stats", {}).get(key, {}) or {}

  def value(seg, key, default=None):
    return stat(seg, key).get("value", default)

  def display(seg, key, default="-"):
    return stat(seg, key).get("displayValue") or default

  def percentile(seg, key):
    return stat(seg, key).get("percentile")

  def sample_weight(matches):
    if matches >= 100:
      return 1.00
    if matches >= 50:
      return 0.95
    if matches >= 20:
      return 0.85
    if matches >= 10:
      return 0.72
    if matches >= 5:
      return 0.50
    return 0.25

  def add(score, category, title, main, detail):
    candidates.append({
      "score": round(score, 2),
      "category": category,
      "title": title,
      "main": main,
      "detail": detail,
    })

  percentile_metrics = [
    ("matchesPlayed", "場次", "volume", 1.00),
    ("matchesWon", "勝場", "volume", 1.02),
    ("kills", "擊殺", "combat", 1.03),
    ("winPercentage", "勝率", "winrate", 1.00),
    ("kdRatio", "K/D", "kd", 1.00),
    ("rankPoints", "RP", "rank", 0.90),
    ("maxRankPoints", "最高 RP", "rank", 0.95),
  ]

  for seg in season_segments:
    metadata = seg.get("metadata", {})
    season = metadata.get("shortName", "?")
    mode = metadata.get("gamemodeName", "?")
    matches = value(seg, "matchesPlayed", 0) or 0
    sample = sample_weight(matches)

    for key, label, category, multiplier in percentile_metrics:
      p = percentile(seg, key)
      v = value(seg, key)
      if p is None or v is None or p < 75:
        continue
      score = p * multiplier
      if key in ("winPercentage", "kdRatio"):
        score *= sample
      add(score, category, f"{season} {mode} {label}", display(seg, key), f"P{p:g}｜{matches} 場")

    hs = value(seg, "headshotPct")
    kills = value(seg, "kills", 0) or 0
    if hs is not None and hs >= 45 and kills >= 20:
      add(
        min(99, 45 + hs * 0.75) * sample,
        "headshot",
        f"{season} {mode} 爆頭率",
        display(seg, "headshotPct"),
        f"{kills} kills｜{matches} 場",
      )

    clutches = value(seg, "clutches", 0) or 0
    if clutches > 0:
      clutch_counts = [
        (value(seg, "clutches1v5", 0) or 0, "1v5"),
        (value(seg, "clutches1v4", 0) or 0, "1v4"),
        (value(seg, "clutches1v3", 0) or 0, "1v3"),
        (value(seg, "clutches1v2", 0) or 0, "1v2"),
        (value(seg, "clutches1v1", 0) or 0, "1v1"),
      ]
      score = 60 + min(clutches, 30) * 1.1
      score += (value(seg, "clutches1v3", 0) or 0) * 8
      score += (value(seg, "clutches1v4", 0) or 0) * 15
      score += (value(seg, "clutches1v5", 0) or 0) * 22
      details = [f"{amount}×{name}" for amount, name in clutch_counts if amount]
      add(score, "clutch", f"{season} {mode} Clutch", str(clutches), " / ".join(details))

    ace = value(seg, "kills5K", 0) or 0
    four_k = value(seg, "kills4K", 0) or 0
    three_k = value(seg, "kills3K", 0) or 0
    if ace > 0:
      add(100 + ace * 5 + four_k * 1.2, "ace", f"{season} {mode} ACE", f"{ace} 次", f"{four_k} 次 4K｜{three_k} 次 3K")
    elif four_k > 0:
      add(72 + four_k * 3, "multikill", f"{season} {mode} 4K", f"{four_k} 次", f"{three_k} 次 3K")

  overview = next((s for s in segments if s.get("type") == "overview"), None)
  if overview:
    for key, label, category, score in [
      ("matchesPlayed", "生涯場次", "career_matches", 65),
      ("timePlayed", "遊玩時間", "career_time", 64),
      ("kills", "生涯擊殺", "career_kills", 67),
      ("headshots", "生涯爆頭", "career_headshots", 68),
      ("wallbangs", "穿牆擊殺", "career_wallbangs", 62),
    ]:
      if value(overview, key) is not None:
        add(score, category, label, display(overview, key), "Career")

  candidates.sort(key=lambda item: item["score"], reverse=True)
  selected = []
  used_categories = set()
  for item in candidates:
    if item["category"] in used_categories:
      continue
    selected.append(item)
    used_categories.add(item["category"])
    if len(selected) >= limit:
      break
  return selected


def get_avatar_url(payload):
  """取得 Tracker 回傳的玩家頭像網址。"""
  platform_info = payload.get("data", {}).get("platformInfo", {})
  for key in ("avatarUrl", "avatar_url", "avatar"):
    avatar_url = platform_info.get(key)
    if isinstance(avatar_url, str) and avatar_url.startswith(("http://", "https://")):
      return avatar_url
  return None


class R6(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

  @commands.command(name="r6")
  @commands.cooldown(1, 300, commands.BucketType.default)
  async def r6(self, ctx, *, player_id=None):
    """查詢 Rainbow Six Siege 玩家亮點：ai-r6 <Ubisoft ID>"""
    player_id = (player_id or "").strip()
    if not player_id:
      await ctx.send("用法：`ai-r6 <Ubisoft ID>`")
      return

    try:
      payload = await asyncio.to_thread(fetch_profile, player_id)
    except TrackerError as error:
      if error.status in (403, 404):
        await ctx.send(f"找不到玩家 `{player_id}`，或 Tracker 不允許查詢這個玩家。")
      elif error.status == 429:
        await ctx.send("Tracker 請求太頻繁，請稍後再試。")
      elif error.status:
        await ctx.send(f"Tracker API 暫時無法使用（HTTP {error.status}）。")
      else:
        await ctx.send("無法連線到 Tracker API，請稍後再試。")
      return

    highlights = get_r6_highlights(payload, limit=5)
    profile_info = payload.get("data", {}).get("platformInfo", {})
    display_name = profile_info.get("platformUserHandle") or player_id
    embed = discord.Embed(
      title=f"{display_name} 的亮眼表現🔥🔥🔥",
      description="資料來源：依舊你媽",
      color=0x6E56CF,
    )
    avatar_url = get_avatar_url(payload)
    if avatar_url:
      embed.set_thumbnail(url=avatar_url)
    if not highlights:
      embed.description = "沒有找到符合條件的突出數據。"
    else:
      for item in highlights:
        embed.add_field(
          name=f"{item['title']}  ·  {item['main']}",
          value=f"{item['detail']}｜分數 {item['score']}",
          inline=False,
        )
    await ctx.send(embed=embed)

  @r6.error
  async def r6_error(self, ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
      minutes, seconds = divmod(int(error.retry_after), 60)
      if minutes:
        message = f"這個指令還在冷卻中，請 {minutes} 分 {seconds} 秒後再使用。"
      else:
        message = f"這個指令還在冷卻中，請 {seconds} 秒後再使用。"
      await ctx.send(message)
    elif isinstance(error, commands.MissingRequiredArgument):
      await ctx.send("用法：`ai-r6 <Ubisoft ID>`")


async def setup(bot):
  await bot.add_cog(R6(bot))
