import discord,random,json,asyncio,datetime,os
from pathlib import Path
from discord.ext import commands
import aiohttp

JSON_DIR = Path(__file__).resolve().parents[2] / "data" / "json"

async def get(n):
  if n == 0:
    with open(JSON_DIR / "anonymous.json", "r", encoding="utf-8") as f:
      users = json.load(f)
    return users
  elif n == 1:
    with open(JSON_DIR / "mute.json", "r", encoding="utf-8") as f:
      users = json.load(f)
    return users 
  elif n == 2:
    with open(JSON_DIR / "st.json", "r", encoding="utf-8") as f:
      users = json.load(f)
    return users  

async def cl():
  a=await get(0)
  #for x in a:
  #print(a[f"{x}"])
  a.clear()
  with open(JSON_DIR / "anonymous.json", "w", encoding="utf-8") as f:
    json.dump(a,f,ensure_ascii=False)

class another(commands.Cog):
  def __init__(self,bot):
    self.bot=bot

  async def cog_load(self):
    # discord.py 2.7 不允許在 Cog 建構子直接存取 bot.loop。
    # cog_load 會在 bot 已經處於非同步上下文時被呼叫。
    self.bg_task = asyncio.create_task(self.timew())

  async def cog_unload(self):
    self.bg_task.cancel()

  async def timew(self):
    await self.bot.wait_until_ready()
    while not self.bot.is_closed():
      now=datetime.datetime.utcnow().strftime("%H%M%S")
      if int(now[2:])>4800 and int(now[2:])<5500:
        ch=self.bot.get_channel(1228204874429235290)
        message=await ch.fetch_message(ch.last_message_id)
        # if message.content == "編號已更換" and message.author.id == 1078261984862154793:
        #   await cl()
        # else:
        await cl()
          #await ch.send("編號已更換")
        await asyncio.sleep(60*10)
      else:
        await asyncio.sleep(60*5)
        pass

  @commands.command() 
  @commands.dm_only()
  async def anonymous (self,ctx):
    """此指令只能在DMChannel用"""
    while True:
      await ctx.send("在以下輸入你/妳想要匿名聊天的内容 限時：30秒")
      def check(m):
        return m.channel == ctx.message.channel and not(m.author.bot) and m.author == ctx.message.author
      try:
        msg = await self.bot.wait_for('message', check=check, timeout=30) 
      except asyncio.TimeoutError:
        await ctx.send("30秒已到")
        self.bot.get_command("anonymous").reset_cooldown(ctx)
        break
      if msg.attachments:
        try:
          TYPE,mes=0,msg.content
        except:
          TYPE=2
      else:
        TYPE,mes=1,msg.content
      async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(os.getenv('Webhook'), session=session)
        js=await get(0)
        set=await get(2)
        if str(msg.author.id) in js:
          z=js[f"{msg.author.id}"][0]
          Q=js[f"{msg.author.id}"][1]
        else:
          AA=range(10)
          QQ=range(10)
          WW=range(10)
          SS=range(10)
          z=f"{random.choice(AA)}{random.choice(QQ)}{random.choice(WW)}{random.choice(SS)}"
          Q=random.choice(range(17))
          js[f"{msg.author.id}"]=[z,Q]
          with open(JSON_DIR / "anonymous.json", "w", encoding="utf-8") as f:
            json.dump(js,f,ensure_ascii=False)
        if TYPE==0:
          file = await msg.attachments[0].to_file()
          t=msg.attachments[0].content_type
          a=t.find("/")+1
          t=t[a:]
          file.filename = f'image.{t}'
          await webhook.send(mes,file=file,username=f"Anonymous#{z}",avatar_url=set["pfp"][Q-1])
        elif TYPE==1:
          await webhook.send(mes,username=f"Anonymous#{z}",avatar_url=set["pfp"][Q-1])
        elif TYPE==2:
          file = await msg.attachments[0].to_file()
          t=msg.attachments[0].content_type
          a=t.find("/")+1
          t=t[a:]
          file.filename = f'image.{t}'
          await webhook.send(file=file,username=f"Anonymous#{z}",avatar_url=set["pfp"][Q-1])
      await ctx.send("已匿名傳送信息成功")
      if msg.content in ["m-anonymous"]:
        break
      if ctx.author.id in [760492945022779432,905036907296206918]:
        pass
      else:
        ch=self.bot.get_channel(926014780962111509)
        await ch.send(f"{ctx.author.name}《Anonymous#{z}》：{mes}")     
        
async def setup(bot):
  await bot.add_cog(another(bot))
