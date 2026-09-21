import asyncio
import logging
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)


@dataclass
class Track:
  title: str
  webpage_url: str
  stream_url: str
  duration: Optional[int] = None


class GuildMusicPlayer:
  """A small per-guild music queue and voice-player manager."""

  def __init__(self, bot):
    self.bot = bot
    self.queues = defaultdict(deque)
    self.current: dict[int, Optional[Track]] = defaultdict(lambda: None)
    self.text_channels: dict[int, discord.abc.Messageable] = {}
    self.volumes = defaultdict(lambda: 0.5)
    self.locks = defaultdict(asyncio.Lock)

  def queue_for(self, guild_id: int):
    return self.queues[guild_id]

  async def extract_track(self, query: str) -> Track:
    """Resolve a URL or a search term without blocking the bot event loop."""
    try:
      import yt_dlp
    except ImportError as error:
      raise RuntimeError("尚未安裝 yt-dlp，請先安裝專案依賴。") from error

    options = {
      "format": "bestaudio/best",
      "noplaylist": True,
      "quiet": True,
      "no_warnings": True,
      "default_search": "ytsearch1",
      "source_address": "0.0.0.0",
    }

    def _extract():
      with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
          info = next((entry for entry in info["entries"] if entry), None)
        if not info or not info.get("url"):
          raise RuntimeError("找不到可以播放的音訊。")
        return Track(
          title=info.get("title", "未知歌曲"),
          webpage_url=info.get("webpage_url", query),
          stream_url=info["url"],
          duration=info.get("duration"),
        )

    return await asyncio.to_thread(_extract)

  async def play_next(self, guild: discord.Guild):
    voice = guild.voice_client
    if voice is None or not voice.is_connected() or voice.is_playing():
      return

    queue = self.queue_for(guild.id)
    if not queue:
      self.current[guild.id] = None
      return

    track = queue.popleft()
    self.current[guild.id] = track
    ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
    before_options = (
      "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    )
    audio = discord.FFmpegPCMAudio(
      track.stream_url,
      executable=ffmpeg_path,
      before_options=before_options,
      options="-vn",
    )
    source = discord.PCMVolumeTransformer(
      audio,
      volume=self.volumes[guild.id],
    )

    loop = asyncio.get_running_loop()

    def after_play(error):
      if error:
        print(f"音樂播放錯誤（{guild.id}）：{error}")
      asyncio.run_coroutine_threadsafe(self.play_next(guild), loop)

    voice.play(source, after=after_play)
    channel = self.text_channels.get(guild.id)
    if channel:
      await channel.send(f"🎵 正在播放：**{track.title}**")

  async def disconnect(self, guild: discord.Guild):
    voice = guild.voice_client
    self.queue_for(guild.id).clear()
    self.current[guild.id] = None
    if voice and voice.is_connected():
      if voice.is_playing() or voice.is_paused():
        voice.stop()
      await voice.disconnect()


class Music(commands.Cog):
  def __init__(self, bot):
    self.bot = bot
    self.player = GuildMusicPlayer(bot)

  async def get_voice(self, ctx):
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用。")
      return None
    if not ctx.author.voice or not ctx.author.voice.channel:
      await ctx.send("請先加入一個語音頻道。")
      return None

    voice = ctx.guild.voice_client
    channel = ctx.author.voice.channel
    if voice is None:
      try:
        voice = await channel.connect()
      except discord.Forbidden:
        await ctx.send(
          "Discord 拒絕了語音連線。請確認我在這個語音頻道有「連接」和「說話」權限。"
        )
        return None
      except discord.ClientException as error:
        logger.exception("連線到語音頻道時發生 Discord ClientException")
        await ctx.send(f"無法加入語音頻道：{error}")
        return None
      except discord.HTTPException as error:
        logger.exception("連線到語音頻道時發生 Discord HTTPException")
        await ctx.send(f"Discord 語音連線失敗（HTTP {error.status}），請稍後再試。")
        return None
      except Exception as error:
        logger.exception("連線到語音頻道時發生未預期錯誤")
        await ctx.send(f"加入語音頻道失敗：{error}")
        return None
    elif voice.channel != channel:
      await ctx.send("我目前正在另一個語音頻道播放音樂。")
      return None
    return voice

  @commands.command(name="join", aliases=["connect"])
  async def join(self, ctx):
    """加入使用者所在的語音頻道。"""
    if await self.get_voice(ctx):
      await ctx.send("已加入語音頻道。")

  @commands.command(name="play", aliases=["p"])
  async def play(self, ctx, *, query: str):
    """播放網址或搜尋關鍵字。"""
    voice = await self.get_voice(ctx)
    if voice is None:
      return

    await ctx.send("🔎 正在尋找音樂，請稍候……")
    try:
      track = await self.player.extract_track(query)
    except Exception as error:
      await ctx.send(f"找不到或無法播放這首歌：{error}")
      return

    guild_id = ctx.guild.id
    self.player.text_channels[guild_id] = ctx.channel
    queue = self.player.queue_for(guild_id)
    queue.append(track)
    if voice.is_playing() or voice.is_paused():
      await ctx.send(f"已加入佇列：**{track.title}**（目前 {len(queue)} 首待播放）")
      return

    await self.player.play_next(ctx.guild)

  @commands.command(name="pause")
  async def pause(self, ctx):
    voice = ctx.guild.voice_client if ctx.guild else None
    if voice and voice.is_playing():
      voice.pause()
      await ctx.send("⏸️ 已暫停播放。")
    else:
      await ctx.send("目前沒有正在播放的音樂。")

  @commands.command(name="resume", aliases=["continue"])
  async def resume(self, ctx):
    voice = ctx.guild.voice_client if ctx.guild else None
    if voice and voice.is_paused():
      voice.resume()
      await ctx.send("▶️ 已繼續播放。")
    else:
      await ctx.send("目前沒有暫停中的音樂。")

  @commands.command(name="skip", aliases=["next"])
  async def skip(self, ctx):
    voice = ctx.guild.voice_client if ctx.guild else None
    if voice and (voice.is_playing() or voice.is_paused()):
      voice.stop()
      await ctx.send("⏭️ 已跳過目前歌曲。")
    else:
      await ctx.send("目前沒有正在播放的音樂。")

  @commands.command(name="queue", aliases=["q"])
  async def queue(self, ctx):
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用。")
      return
    current = self.player.current[ctx.guild.id]
    tracks = list(self.player.queue_for(ctx.guild.id))
    lines = [f"現在播放：**{current.title}**"] if current else ["目前沒有播放歌曲。"]
    if tracks:
      lines.append("待播放：")
      lines.extend(f"{index}. {track.title}" for index, track in enumerate(tracks, 1))
    await ctx.send("\n".join(lines)[:2000])

  @commands.command(name="stop")
  async def stop(self, ctx):
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用。")
      return
    voice = ctx.guild.voice_client
    self.player.queue_for(ctx.guild.id).clear()
    if voice and (voice.is_playing() or voice.is_paused()):
      voice.stop()
    self.player.current[ctx.guild.id] = None
    await ctx.send("⏹️ 已停止播放並清空佇列。")

  @commands.command(name="leave", aliases=["disconnect"])
  async def leave(self, ctx):
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用。")
      return
    await self.player.disconnect(ctx.guild)
    await ctx.send("已離開語音頻道。")


async def setup(bot):
  await bot.add_cog(Music(bot))
