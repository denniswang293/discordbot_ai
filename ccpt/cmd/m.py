import datetime
import json
import asyncio
import logging
import re
from pathlib import Path

import discord
from discord.ext import commands
from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests


JSON_DIR = Path(__file__).resolve().parents[2] / "data" / "json"
SNIPE_FILE = JSON_DIR / "snipe.json"
logger = logging.getLogger(__name__)
translation_lock = asyncio.Lock()


def translate_with_google(text):
  """Translate text through deep-translator's GoogleTranslator."""
  return GoogleTranslator(source="auto", target="zh-TW").translate(text)


def load_snipe_data():
  try:
    with open(SNIPE_FILE, "r", encoding="utf-8") as file:
      data = json.load(file)
  except (FileNotFoundError, json.JSONDecodeError):
    data = {}

  # 相容舊格式：舊格式直接把伺服器 ID 放在最外層。
  if "delete" not in data:
    data["delete"] = {
      key: value for key, value in data.items()
      if key not in {"delete", "edit"}
    }
  if not isinstance(data.get("delete"), dict):
    data["delete"] = {}
  if not isinstance(data.get("edit"), dict):
    data["edit"] = {}
  return data


class Snipe(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

  async def delete_command_message(self, ctx):
    try:
      await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
      # 沒有管理訊息權限，或訊息已經被刪除時，不影響指令正常執行。
      pass

  async def send_snipe(self, ctx, category, number, is_edit=False):
    if ctx.guild is None:
      await ctx.send("這個指令不能在私訊使用")
      return
    if number > 15:
      await ctx.send("不能超過15 awa")
      return
    if number <= 0:
      await ctx.send("<:humm:899868504528937000>")
      return

    data = load_snipe_data()
    records = data[category].get(str(ctx.guild.id), [])
    if len(records) < number:
      await ctx.send("還沒有信息")
      return

    record = records[-number]
    embed = discord.Embed(
      title=f"UTC+0 {record[-1]}",
      color=0x00FFE1,
      timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    if is_edit:
      content = f"編輯前：{record[1]}\n編輯後：{record[2]}"
    else:
      content = record[1]

    # Discord Embed field value 最多 1024 個字元。
    embed.add_field(
      name=record[0],
      value=content[:1024],
      inline=False,
    )
    await ctx.send(embed=embed)

  async def get_message_to_translate(self, ctx, message_link=None):
    if message_link:
      match = re.fullmatch(
        r"https?://(?:www\.)?discord(?:app)?\.com/channels/"
        r"(?:\d+|@me)/(\d+)/(\d+)",
        message_link,
      )
      if not match:
        return None, "請提供有效的 Discord 訊息連結。"

      channel_id, message_id = map(int, match.groups())
      channel = self.bot.get_channel(channel_id)
      if channel is None:
        try:
          channel = await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
          return None, "找不到這個頻道，或機器人沒有查看權限。"
      try:
        return await channel.fetch_message(message_id), None
      except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return None, "找不到這則訊息，或機器人沒有查看權限。"

    reference = ctx.message.reference
    if reference is None:
      return None, "請回覆一則訊息，或提供 Discord 訊息連結。"
    if isinstance(reference.resolved, discord.Message):
      return reference.resolved, None

    try:
      channel = self.bot.get_channel(reference.channel_id) or ctx.channel
      return await channel.fetch_message(reference.message_id), None
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
      return None, "找不到被回覆的訊息，或機器人沒有查看權限。"

  @commands.command(name="translate", aliases=["tran"])
  async def translate(self, ctx, message_link: str = None):
    await self.delete_command_message(ctx)
    message, error = await self.get_message_to_translate(ctx, message_link)
    if error:
      await ctx.send(error)
      return
    if not message.content.strip():
      await ctx.send("這則訊息沒有可以翻譯的文字。")
      return

    try:
      # 避免 Discord 同時收到多個翻譯指令時，瞬間打爆 API 限流。
      async with translation_lock:
        translated = await asyncio.to_thread(
          translate_with_google,
          message.content,
        )
    except Exception as error:
      logger.exception("翻譯失敗")
      if isinstance(error, TooManyRequests):
        await ctx.send("Google 翻譯目前請求過多，請稍等幾分鐘後再試。")
      else:
        await ctx.send("翻譯失敗，請稍後再試。")
      return

    await ctx.send(translated[:2000])

  @commands.command(aliases=["Snipe", "SNIPE"])
  async def snipe(self, ctx, number: int = 1):
    await self.delete_command_message(ctx)
    await self.send_snipe(ctx, "delete", number)

  @commands.command(
    name="snipe_edit",
    aliases=["SnipeEdit", "SNIPE_EDIT"],
  )
  async def snipe_edit(self, ctx, number: int = 1):
    await self.delete_command_message(ctx)
    await self.send_snipe(ctx, "edit", number, is_edit=True)


async def setup(bot):
  await bot.add_cog(Snipe(bot))
