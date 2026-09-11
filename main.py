import os, time
from pathlib import Path
import discord
from discord.ext import commands
import json, asyncio, random
from keep_alive import keep_alive
import datetime
import urllib.request as req
from dotenv import load_dotenv
from typing import Optional 

PROJECT_ROOT = Path(__file__).resolve().parent
JSON_DIR = PROJECT_ROOT / "data" / "json"
load_dotenv(PROJECT_ROOT / ".env")


async def get(n):
  if n == 0:
    with open(JSON_DIR / "st.json", "r", encoding="utf-8") as f:
        users = json.load(f)
  if n == 1:
    with open(JSON_DIR / "log.json", "r", encoding="utf-8") as f:
        users = json.load(f)
  return users

intents = discord.Intents().all()
bot = commands.Bot(command_prefix="ai-", intents=intents)
#bot.remove_command('help')#内建的help去除程式

with open(JSON_DIR / "data.json", "r", encoding="utf8") as file:
    dict_data = json.loads(file.read())

async def isEnd(str):
    if str != '' and str[-1] in "。？！?!":
        return True
    return False

async def generate(param_topic, param_min_length):
    min_length = 0
    param_min_length = int(param_min_length)

    list_famous = dict_data['famous']
    random.shuffle(list_famous)
    list_bullshit = dict_data['bullshit']
    random.shuffle(list_bullshit)     

    # 生成文字資料
    str_gen = ""
    while min_length < param_min_length:
        int_rand = random.randint(0, 99)

        if int_rand < 5 and await isEnd(str_gen):

            str_gen += "\n\n"

        elif int_rand < 27:
            if len(list_famous) == 0:
                break
            sentence_famous = list_famous.pop(0)
            str_before = dict_data['before'][ random.randint(0, len(dict_data['before']) - 1 ) ]

            str_after = dict_data['after'][ random.randint(0, len(dict_data['after']) - 1) ]

            sentence_famous = sentence_famous.replace("a", str_before)
            sentence_famous = sentence_famous.replace("b", str_after)

            str_gen += sentence_famous

        else:
            if len(list_bullshit) == 0:
                break

            sentence_bullshit = list_bullshit.pop(0)

            sentence_bullshit = sentence_bullshit.replace("x", param_topic)

            str_gen += sentence_bullshit

        min_length = len(str_gen)

    if param_topic not in str_gen:
        return await generate(param_topic, param_min_length)
    
    # 回傳結果
    return str_gen

async def load_snipe_data():
  try:
    with open(JSON_DIR / "snipe.json", "r", encoding="utf-8") as file:
      data = json.load(file)
  except (FileNotFoundError, json.JSONDecodeError):
    data = {}

  # 相容舊格式：舊格式直接用伺服器 ID 當作最外層 key。
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


async def save_snipe_data(data):
  with open(JSON_DIR / "snipe.json", "w", encoding="utf-8") as file:
    json.dump(data, file, ensure_ascii=False, indent=2)


async def awa(message, mysg):
  data = await load_snipe_data()
  guild_id = str(message.guild.id)
  records = data["delete"].setdefault(guild_id, [])
  records.append([
    f"{message.author.name}#{message.author.discriminator}",
    mysg,
    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
  ])
  data["delete"][guild_id] = records[-15:]
  await save_snipe_data(data)


async def awa_edit(message_before, message_after):
  data = await load_snipe_data()
  guild_id = str(message_before.guild.id)
  records = data["edit"].setdefault(guild_id, [])
  records.append([
    f"{message_before.author.name}#{message_before.author.discriminator}",
    message_before.content,
    message_after.content,
    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
  ])
  data["edit"][guild_id] = records[-15:]
  await save_snipe_data(data)


def is_snipe_blacklisted(message):
  return (
    message.author.id == 760492945022779432
    or message.content.startswith("ai-")
  )

@bot.event
async def on_message_delete(message):
  if (
    message.guild is None
    or message.author.bot
    or not message.content
    or is_snipe_blacklisted(message)
  ):
    return
  await awa(message, message.content)


@bot.event
async def on_message_edit(message_before, message_after):
  if (
    message_before.guild is None
    or message_before.author.bot
    or not message_before.content
    or message_before.content == message_after.content
    or is_snipe_blacklisted(message_before)
    or is_snipe_blacklisted(message_after)
  ):
    return
  await awa_edit(message_before, message_after)


@bot.event
async def on_ready():            
    print('《{0.user}》出倉'.format(bot))
    # while True:
    #  await bot.change_presence(activity=discord.Game(name="3!help"))
    #  await asyncio.sleep(5)
    #  await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} 個伺服器"))
    #  await asyncio.sleep(5)
    #  await bot.change_presence(activity=discord.Game(name="ヾ(≧▽≦*)o"))
    #  await asyncio.sleep(5)
  
# for Filename in os.listdir('./commands'):
#     if Filename.endswith('.py'):
#         bot.load_extension(F'commands.{Filename[:-3]}')

# @bot.command()
# async def test(ctx,*, ext):
#   await ctx.send(ext)

@bot.command()
async def test(ctx, channel: Optional[discord.TextChannel], *, msg: commands.clean_content):
    channel = channel or ctx.channel
    await ctx.message.delete()
    await channel.send(msg, allowed_mentions = discord.AllowedMentions.none())
    channelog = bot.get_channel(926014780962111509)
    await channelog.send(f"say:{ctx.message.author.name}-{msg}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.channel.id == 1228204874429235290:
        mentioned = bot.user.mentioned_in(message)
        is_reply_to_me = False
        if message.reference and message.reference.resolved:
            if message.reference.resolved.author == bot.user:
                is_reply_to_me = True
        if mentioned or is_reply_to_me:
            AA=await get(0)
            A=random.choice(AA["topic"])
            B=random.randint(10, 100)
            data = await generate(A,B)
            await message.reply(data)
            return  
    await bot.process_commands(message)

async def load_extensions():
    helper_modules = {'calendar_parser', 'calendar_database', 'calendar_service'}
    for file in os.listdir('./ccpt/cmd'):
        if file.endswith('.py') and file != '__init__.py' and file[:-3] not in helper_modules:
            await bot.load_extension(f'ccpt.cmd.{file[:-3]}')

async def main():
    async with bot:
        await load_extensions() 
        token = os.getenv('DISCORD_TOKEN')
        if not token or token.strip() in {'your_token_here', 'YOUR_DISCORD_BOT_TOKEN'}:
            raise RuntimeError(
                f"找不到 DISCORD_TOKEN，請在 {PROJECT_ROOT / '.env'} 設定 Discord Bot Token"
            )
        await bot.start(token.strip())

if __name__ == '__main__':
    keep_alive() 
    asyncio.run(main())
