import datetime
import json
from pathlib import Path

import discord
from discord.ext import commands


JSON_DIR = Path(__file__).resolve().parents[2] / "data" / "json"
SNIPE_FILE = JSON_DIR / "snipe.json"


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
