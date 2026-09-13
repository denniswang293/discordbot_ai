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


def get_r6_highlights_legacy(payload, limit=5):
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


def get_r6_highlights(payload, limit=5):
  """從 Tracker 資料挑出最多五個有話題、而且容易理解的表現。"""
  data = payload.get("data", {}) or {}
  segments = [s for s in data.get("segments", []) if s.get("type") == "season"]
  candidates = []

  def stat(segment, key):
    return segment.get("stats", {}).get(key, {}) or {}

  def value(segment, key, default=0):
    result = stat(segment, key).get("value", default)
    return default if result is None else result

  def shown(segment, key, default="-"):
    return stat(segment, key).get("displayValue") or default

  def add(score, category, title, main, explanation, detail):
    candidates.append({
      "score": round(score, 2),
      "category": category,
      "title": title,
      "main": main,
      "plain": explanation,
      "detail": detail,
    })

  for segment in segments:
    metadata = segment.get("metadata", {}) or {}
    season = metadata.get("shortName", "這個賽季")
    mode = metadata.get("gamemodeName", "對戰")
    matches = int(value(segment, "matchesPlayed") or 0)
    if matches <= 0:
      continue
    confidence = min(matches / 30, 1.0)
    percentile_bonus = lambda key: (stat(segment, key).get("percentile") or 0) * 0.25

    # 大場數本身不是實力證明，但能讓極端數據更可信，也很適合當背景資訊。
    if matches >= 50:
      add(
        35 + min(matches / 20, 25), "experience", f"{season} {mode} 出場量",
        shown(segment, "matchesPlayed"),
        f"這個賽季打了 {matches:,} 場，樣本夠大，其他戰績比較有參考價值。",
        f"{shown(segment, 'timePlayed')} 遊玩時間",
      )

    wins = int(value(segment, "matchesWon") or 0)
    win_rate = float(value(segment, "winPercentage") or 0)
    if matches >= 8 and win_rate >= 55:
      add(
        48 + (win_rate - 50) * 1.4 + percentile_bonus("winPercentage") + confidence * 8,
        "winrate", f"{season} {mode} 勝率", shown(segment, "winPercentage"),
        f"平均每 100 場大約贏 {win_rate:.1f} 場，這季的贏場效率高於一般玩家。",
        f"{wins:,} 勝｜{matches:,} 場",
      )

    kd = float(value(segment, "kdRatio") or 0)
    kills = int(value(segment, "kills") or 0)
    deaths = int(value(segment, "deaths") or 0)
    if matches >= 8 and kd >= 1.15:
      add(
        52 + min((kd - 1) * 24, 35) + percentile_bonus("kdRatio") + confidence * 7,
        "kd", f"{season} {mode} 槍法效率", shown(segment, "kdRatio"),
        f"平均每死亡 1 次可以換來 {kd:.2f} 次擊殺，正面交戰很有優勢。",
        f"{kills:,} 擊殺｜{deaths:,} 死亡",
      )

    hs = float(value(segment, "headshotPct") or 0)
    if kills >= 20 and hs >= 35:
      add(
        50 + (hs - 30) * 1.2 + percentile_bonus("headshotPct") + confidence * 6,
        "headshot", f"{season} {mode} 爆頭率", shown(segment, "headshotPct"),
        f"大約每 {max(1, 100 / hs):.1f} 次擊殺就有 1 次爆頭，瞄準頭部的習慣相當明顯。",
        f"{kills:,} 擊殺｜{matches:,} 場",
      )

    aces = int(value(segment, "kills5K") or 0)
    four_k = int(value(segment, "kills4K") or 0)
    three_k = int(value(segment, "kills3K") or 0)
    if aces:
      add(
        105 + aces * 6 + four_k * 2, "multikill", f"{season} {mode} ACE", f"{aces} 次",
        f"至少 {aces} 場曾在同一回合擊殺對面五名玩家，這是最具話題性的表現。",
        f"{four_k} 次 4K｜{three_k} 次 3K",
      )
    elif four_k:
      add(
        78 + four_k * 4 + three_k, "multikill", f"{season} {mode} 多殺表現", f"{four_k} 次 4K",
        f"有 {four_k} 次在同一回合擊殺四人，代表常常能把殘局打穿。",
        f"{three_k} 次 3K｜{matches:,} 場",
      )

    clutches = int(value(segment, "clutches") or 0)
    if clutches:
      clutch_types = [
        (int(value(segment, key) or 0), label)
        for key, label in (("clutches1v5", "1v5"), ("clutches1v4", "1v4"),
                           ("clutches1v3", "1v3"), ("clutches1v2", "1v2"),
                           ("clutches1v1", "1v1"))
      ]
      best = next(((amount, label) for amount, label in clutch_types if amount), None)
      best_text = f"，其中包含 {best[0]} 次 {best[1]}" if best else ""
      add(
        70 + min(clutches, 25) * 1.5 + sum(amount * (6 if label in ("1v4", "1v5") else 2) for amount, label in clutch_types),
        "clutch", f"{season} {mode} 絕境翻盤", f"{clutches} 次 Clutch",
        f"在少打多的劣勢局面中成功翻盤 {clutches} 次{best_text}，關鍵時刻很能穩住。",
        f"{matches:,} 場｜越逆風越亮眼",
      )

    first_bloods = int(value(segment, "firstBloods") or 0)
    if first_bloods >= 8:
      add(
        53 + min(first_bloods / max(matches, 1) * 100, 25) + percentile_bonus("firstBloods"),
        "entry", f"{season} {mode} 首殺能力", f"{first_bloods} 次首殺",
        f"有 {first_bloods} 次先拿到第一個擊殺，常常是打開回合局面的那個人。",
        f"{matches:,} 場｜進攻節奏偏主動",
      )

    round_win_rate = float(value(segment, "roundWinPct") or 0)
    if matches >= 8 and round_win_rate >= 55:
      add(
        47 + (round_win_rate - 50) * 1.1 + percentile_bonus("roundWinPct") + confidence * 5,
        "rounds", f"{season} {mode} 回合掌控力", shown(segment, "roundWinPct"),
        f"平均每 100 個回合能拿下 {round_win_rate:.1f} 個，單回合決策穩定。",
        f"{shown(segment, 'roundsWon')} 勝回合｜{shown(segment, 'roundsPlayed')} 回合",
      )

    abandoned = int(value(segment, "matchesAbandoned") or 0)
    if abandoned >= 3:
      add(
        40 + abandoned, "abandon", f"{season} {mode} 有趣紀錄", f"{abandoned} 次離場",
        f"這季有 {abandoned} 場中途離場；如果不是斷線，隊友可能對這項紀錄很有感。",
        "這是負面紀錄，僅作為話題補充",
      )

  # 分數優先，但同一類只留一個，避免五個結果都在講 K/D 或勝率。
  candidates.sort(key=lambda item: item["score"], reverse=True)
  selected = []
  used_categories = set()
  for item in candidates:
    if item["category"] in used_categories:
      continue
    selected.append(item)
    used_categories.add(item["category"])
    if len(selected) >= min(limit, 5):
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
          value=f"{item['plain']}\n`{item['detail']}`",
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
