import asyncio
import logging
import os
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)


@dataclass
class Track:
  title: str
  webpage_url: str
  stream_url: str
  duration: Optional[int] = None


@dataclass
class RandomPlaylistSession:
  title: str
  source_url: str
  entries: tuple[str, ...]
  remaining: list[str] = field(default_factory=list)
  last_url: Optional[str] = None
  prefetch_task: Optional[asyncio.Task] = None
  prefetch_started: bool = False

  def take_random_url(self) -> str:
    """Return one entry, avoiding repeats until every entry was used."""
    if not self.remaining:
      self.remaining = list(self.entries)
      random.shuffle(self.remaining)
      if (
        len(self.remaining) > 1
        and self.last_url is not None
        and self.remaining[-1] == self.last_url
      ):
        self.remaining[0], self.remaining[-1] = (
          self.remaining[-1],
          self.remaining[0],
        )

    selected = self.remaining.pop()
    self.last_url = selected
    return selected


class GuildMusicPlayer:
  """A small per-guild music queue and voice-player manager."""

  def __init__(self, bot):
    self.bot = bot
    self.queues = defaultdict(deque)
    self.current: dict[int, Optional[Track]] = defaultdict(lambda: None)
    self.text_channels: dict[int, discord.abc.Messageable] = {}
    self.volumes = defaultdict(lambda: 0.5)
    self.locks = defaultdict(asyncio.Lock)
    self.advance_locks = defaultdict(asyncio.Lock)
    self.disconnecting: set[int] = set()
    self.stop_requested: set[int] = set()
    self.pending_play_requests: set[int] = set()
    self.play_request_versions = defaultdict(int)
    self.random_playlists: dict[int, RandomPlaylistSession] = {}

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

  async def extract_playlist(
    self,
    playlist_url: str,
  ) -> Optional[RandomPlaylistSession]:
    """Read only a playlist's lightweight entries, not each track."""
    try:
      import yt_dlp
    except ImportError as error:
      raise RuntimeError("尚未安裝 yt-dlp，請先安裝專案依賴。") from error

    options = {
      "extract_flat": "in_playlist",
      "skip_download": True,
      "quiet": True,
      "no_warnings": True,
      "noplaylist": False,
      "ignoreerrors": True,
      "source_address": "0.0.0.0",
    }

    def _extract():
      with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        if not info or "entries" not in info:
          return None

        urls = []
        seen = set()
        for entry in info.get("entries") or ():
          if not entry:
            continue
          entry_url = entry.get("webpage_url") or entry.get("url")
          entry_id = entry.get("id")
          extractor = str(
            entry.get("ie_key") or entry.get("extractor_key") or ""
          ).lower()
          if (
            "youtube" in extractor
            and entry_id
            and (
              not entry_url
              or not urlparse(str(entry_url)).scheme
            )
          ):
            entry_url = f"https://www.youtube.com/watch?v={entry_id}"
          if entry_url and entry_url not in seen:
            seen.add(entry_url)
            urls.append(entry_url)

        if not urls:
          raise RuntimeError("播放清單內沒有可播放的歌曲。")

        return RandomPlaylistSession(
          title=info.get("title") or "未命名播放清單",
          source_url=playlist_url,
          entries=tuple(urls),
        )

    return await asyncio.to_thread(_extract)

  async def stop_random_playlist(self, guild_id: int):
    session = self.random_playlists.pop(guild_id, None)
    task = session.prefetch_task if session else None
    if (
      task
      and task is not asyncio.current_task()
      and not task.done()
    ):
      task.cancel()
      try:
        await task
      except asyncio.CancelledError:
        pass

  async def _resolve_random_track(
    self,
    guild: discord.Guild,
    session: RandomPlaylistSession,
  ) -> Optional[Track]:
    """Resolve one randomly selected entry, skipping a few broken ones."""
    attempts = min(len(session.entries), 10)
    last_error = None
    for _ in range(attempts):
      if self.random_playlists.get(guild.id) is not session:
        return None

      entry_url = session.take_random_url()
      try:
        track = await self.extract_track(entry_url)
      except Exception as error:
        last_error = error
        logger.warning(
          "無法解析隨機播放清單歌曲（%s）：%s",
          guild.id,
          error,
        )
        continue

      if self.random_playlists.get(guild.id) is session:
        return track
      return None

    await self.stop_random_playlist(guild.id)
    channel = self.text_channels.get(guild.id)
    if channel:
      await channel.send(
        f"⚠️ 連續找不到可播放的歌曲，已停止隨機播放：{last_error}"
      )
    return None

  async def _prefetch_random_track(
    self,
    guild: discord.Guild,
    session: RandomPlaylistSession,
    track: Track,
  ) -> Optional[Track]:
    """Resolve exactly one next track shortly before the current one ends."""
    if not track.duration:
      return None

    remaining = max(float(track.duration) - 10.0, 0.0)
    while remaining > 0:
      if (
        self.random_playlists.get(guild.id) is not session
        or self.current[guild.id] is not track
      ):
        return None

      voice = guild.voice_client
      if voice is None or not voice.is_connected():
        return None
      interval = min(remaining, 2.0)
      await asyncio.sleep(interval)
      if voice.is_playing():
        remaining -= interval

    if (
      self.random_playlists.get(guild.id) is not session
      or self.current[guild.id] is not track
    ):
      return None

    session.prefetch_started = True
    return await self._resolve_random_track(guild, session)

  async def _next_random_track(
    self,
    guild: discord.Guild,
    session: RandomPlaylistSession,
  ) -> Optional[Track]:
    task = session.prefetch_task
    session.prefetch_task = None

    if task is None:
      return await self._resolve_random_track(guild, session)

    if not task.done() and not session.prefetch_started:
      task.cancel()

    try:
      prefetched = await task
    except asyncio.CancelledError:
      prefetched = None
    finally:
      session.prefetch_started = False

    if prefetched is not None:
      return prefetched
    if self.random_playlists.get(guild.id) is session:
      return await self._resolve_random_track(guild, session)
    return None

  async def play_next(self, guild: discord.Guild):
    async with self.advance_locks[guild.id]:
      if guild.id in self.disconnecting:
        return

      voice = guild.voice_client
      if (
        voice is None
        or not voice.is_connected()
        or voice.is_playing()
        or voice.is_paused()
      ):
        return

      queue = self.queue_for(guild.id)
      random_session = None
      if queue:
        track = queue.popleft()
      else:
        random_session = self.random_playlists.get(guild.id)
        if random_session:
          track = await self._next_random_track(guild, random_session)
          if self.random_playlists.get(guild.id) is not random_session:
            return
          if track is None:
            return
        elif guild.id in self.pending_play_requests:
          return
        else:
          self.current[guild.id] = None
          await self.disconnect(guild, announce=True)
          return

      voice = guild.voice_client
      if (
        voice is None
        or not voice.is_connected()
        or voice.is_playing()
        or voice.is_paused()
      ):
        return

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
        asyncio.run_coroutine_threadsafe(
          self.handle_track_finished(guild),
          loop,
        )

      voice.play(source, after=after_play)
      if random_session:
        random_session.prefetch_started = False
        random_session.prefetch_task = asyncio.create_task(
          self._prefetch_random_track(guild, random_session, track)
        )

      channel = self.text_channels.get(guild.id)
      if channel:
        await channel.send(f"🎵 正在播放：**{track.title}**")

  async def handle_track_finished(self, guild: discord.Guild):
    """Wait for discord.py's audio thread to fully stop before continuing."""
    if guild.id in self.stop_requested:
      self.stop_requested.discard(guild.id)
      return

    voice = guild.voice_client
    for _ in range(20):
      if voice is None or not voice.is_playing():
        break
      await asyncio.sleep(0.1)

    # AudioPlayer.stop() marks the source finished before its worker thread
    # exits. Force-clear that short-lived state so the next track or the
    # automatic disconnect is not skipped.
    if voice and voice.is_playing():
      voice.stop()
      await asyncio.sleep(0.1)

    await self.play_next(guild)

  async def disconnect(self, guild: discord.Guild, announce=False):
    if guild.id in self.disconnecting:
      return

    self.disconnecting.add(guild.id)
    self.play_request_versions[guild.id] += 1
    voice = guild.voice_client
    await self.stop_random_playlist(guild.id)
    self.queue_for(guild.id).clear()
    self.current[guild.id] = None
    try:
      if voice:
        if voice.is_playing() or voice.is_paused():
          voice.stop()
        if voice.is_connected():
          await voice.disconnect(force=True)
        else:
          # Bot 被手動踢出時，Discord 可能仍暫時保留舊的 voice_client。
          voice.cleanup()
    except (discord.ClientException, discord.HTTPException):
      logger.exception("清理語音連線時發生錯誤（%s）", guild.id)
      if voice:
        voice.cleanup()
    finally:
      self.disconnecting.discard(guild.id)

    if announce:
      channel = self.text_channels.get(guild.id)
      if channel:
        await channel.send("🎵 播放完畢，高歌離席~。")


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
    bot_voice = ctx.guild.me.voice if ctx.guild.me else None

    # Bot 被手動踢出後，voice_client 可能會短暫殘留，導致下一次加入
    # 被誤判成「已在另一個語音頻道」。
    if voice and (
      not voice.is_connected()
      or bot_voice is None
      or bot_voice.channel != voice.channel
    ):
      await self.player.disconnect(ctx.guild)
      voice = None

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
    """播放單曲；播放清單網址會啟動持續隨機播放。"""
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用。")
      return

    guild_id = ctx.guild.id
    async with self.player.locks[guild_id]:
      if guild_id in self.player.random_playlists:
        await ctx.send(
          "🎲 隨機播放清單正在運作，請先使用 `ai-stop` 停止。"
        )
        return

      voice = await self.get_voice(ctx)
      if voice is None:
        return

      request_version = self.player.play_request_versions[guild_id]
      self.player.text_channels[guild_id] = ctx.channel
      self.player.pending_play_requests.add(guild_id)
      session = None
      track = None

      await ctx.send("🔎 正在尋找音樂，請稍候……")
      try:
        parsed = urlparse(query.strip())
        if parsed.scheme in {"http", "https"} and parsed.netloc:
          session = await self.player.extract_playlist(query)

        if self.player.play_request_versions[guild_id] != request_version:
          return

        if session is not None:
          self.player.random_playlists[guild_id] = session
        else:
          track = await self.player.extract_track(query)

        if self.player.play_request_versions[guild_id] != request_version:
          if session is not None:
            await self.player.stop_random_playlist(guild_id)
          return
      except Exception as error:
        await ctx.send(f"找不到或無法播放：{error}")
        return
      finally:
        self.player.pending_play_requests.discard(guild_id)

      if session is not None:
        await ctx.send(
          f"🎲 已啟動隨機播放：**{session.title}**"
          f"（{len(session.entries)} 首）\n"
          "使用 `ai-stop` 停止。"
        )
        if self.player.random_playlists.get(guild_id) is not session:
          return
        await self.player.play_next(ctx.guild)
        return

      queue = self.player.queue_for(guild_id)
      queue.append(track)
      if voice.is_playing() or voice.is_paused():
        await ctx.send(
          f"已加入佇列：**{track.title}**（目前 {len(queue)} 首待播放）"
        )
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
    random_session = self.player.random_playlists.get(ctx.guild.id)
    if random_session:
      lines.append(f"🎲 隨機播放：**{random_session.title}**（直到 `ai-stop`）")
    if tracks:
      lines.append("待播放：")
      lines.extend(f"{index}. {track.title}" for index, track in enumerate(tracks, 1))
    await ctx.send("\n".join(lines)[:2000])

  @commands.command(name="stop")
  async def stop(self, ctx):
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用。")
      return
    await self.player.disconnect(ctx.guild)
    await ctx.send("⏹️ 已停止播放、清空佇列並離開語音頻道。")

  @commands.command(name="leave", aliases=["disconnect"])
  async def leave(self, ctx):
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用。")
      return
    await self.player.disconnect(ctx.guild)
    await ctx.send("已離開語音頻道。")


async def setup(bot):
  await bot.add_cog(Music(bot))
