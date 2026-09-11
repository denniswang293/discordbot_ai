import discord, json, asyncio, aiohttp
from pathlib import Path
from discord.ext import commands
from discord import Webhook

JSON_DIR = Path(__file__).resolve().parents[2] / "data" / "json"

async def wait(ctx, self, message):
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"]

    try:
        reaction, user = await self.bot.wait_for("reaction_add", timeout=6, check=check)
        if str(reaction.emoji) == "✅":
            return 1
        elif str(reaction.emoji) == "❌":
            return 2
    except asyncio.TimeoutError:
        return 3


async def get(n):
    if n == 0:
        with open(JSON_DIR / "webhook.json", "r", encoding="utf-8") as f:
            users = json.load(f)
        return users
    elif n == 1:
        with open(JSON_DIR / "edit.json", "r", encoding="utf-8") as f:
            users = json.load(f)
        return users
    # elif n == 2:
    #   with open("setting.json","r") as f:
    #       users = json.load(f)
    #   return users
    elif n == 3:
        with open(JSON_DIR / "log.json", "r", encoding="utf-8") as f:
            users = json.load(f)
        return users
    # elif n == 4:
    #   with open("mute.json","r") as f:
    #       users = json.load(f)
    #   return users


nlog = 0


class Web(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # fq=await get(4)
        js = await get(0)
        if (
            str(message.channel.id) in js
            and not (message.author.bot)
            and not any(word in message.content for word in ["ai-"])
        ):
            # if str(message.author.id) in fq:
            #   await message.add_reaction("🤡")
            #   await message.channel.send(f"你已經被禁言 原因:{fq[str(message.author.id)]} \n您的信息不會傳輸道其他任何頻道")
            #   return
            if message.reference is not None:
                mysg = message.reference.cached_message.clean_content
                if len(mysg) > 44 and not "> **" in mysg:
                    mysg = f"{mysg[:45]}..."
                if not "> **" in mysg:
                    mysg = mysg.replace("\n", " ")
                    if message.attachments:
                        file = await message.attachments[0].to_file()
                        t = message.attachments[0].content_type
                        a = t.find("/") + 1
                        t = t[a:]
                        file.filename = f"image.{t}"
                        if not message.reference.cached_message.attachments:
                            TYPE, mes = (
                                0,
                                f"> **{message.reference.cached_message.author.name}**:{mysg}\n{message.clean_content}",
                            )
                        else:
                            TYPE, mes = (
                                0,
                                f"> **{message.reference.cached_message.author.name}**:{mysg} <:img:916546386995077202>\n{message.clean_content}",
                            )
                    else:
                        if not message.reference.cached_message.attachments:
                            TYPE, mes = (
                                1,
                                f"> **{message.reference.cached_message.author.name}**:{mysg}\n{message.clean_content}",
                            )
                        else:
                            TYPE, mes = (
                                1,
                                f"> **{message.reference.cached_message.author.name}**:{mysg} <:img:916546386995077202>\n{message.clean_content}",
                            )
                else:
                    if not message.attachments:
                        mysg = mysg.split("\n")[1]
                        mysg = mysg.replace("\n", " ")
                        if not message.reference.cached_message.attachments:
                            TYPE, mes = (
                                1,
                                f"> **{message.reference.cached_message.author.name}**:{mysg}\n{message.clean_content}",
                            )
                        else:
                            TYPE, mes = (
                                1,
                                f"> **{message.reference.cached_message.author.name}**:{mysg} <:img:916546386995077202>\n{message.clean_content}",
                            )
                    else:
                        try:
                            mysg = mysg.split("\n")[1]
                            mysg = mysg.replace("\n", " ")
                        except IndexError:
                            mysg = " "
                        if not message.reference.cached_message.attachments:
                            TYPE, mes = (
                                0,
                                f"> **{message.reference.cached_message.author.name}**:{mysg}\n{message.clean_content}",
                            )
                        else:
                            TYPE, mes = (
                                0,
                                f"> **{message.reference.cached_message.author.name}**:{mysg} <:img:916546386995077202>\n{message.clean_content}",
                            )
            else:
                if message.attachments:
                    try:
                        TYPE, mes = 0, message.clean_content
                    except:
                        TYPE = 2
                else:
                    TYPE, mes = 1, message.clean_content

            async with aiohttp.ClientSession() as session:
                jss = await get(1)
                jss[f"{message.id}"] = []
                log = []
                for webhooks in js:
                    if webhooks == str(message.channel.id):
                        continue
                    try:
                        ch = self.bot.get_channel(int(webhooks))
                        awa = await ch.webhooks()
                    except:
                        continue
                    z = []
                    for x in awa:
                        z.append(x.url)
                    if js[webhooks] in z:
                        webhook = Webhook.from_url(js[webhooks], session=session)
                    else:
                        lol = await ch.create_webhook(
                            name="刪屁哦>:c", avatar=None, reason="刪屁哦"
                        )
                        js[f"{ch.id}"] = lol.url
                        with open(JSON_DIR / "webhook.json", "w", encoding="utf-8") as f:
                            json.dump(js, f, ensure_ascii=False)
                        webhook = Webhook.from_url(lol.url, session=session)

                    if TYPE == 0:
                        file = await message.attachments[0].to_file()
                        t = message.attachments[0].content_type
                        a = t.find("/") + 1
                        t = t[a:]
                        file.filename = f"image.{t}"
                        await webhook.send(
                            mes,
                            file=file,
                            username=message.author.name,
                            avatar_url=message.author.avatar.url,
                        )
                    elif TYPE == 1:
                        await webhook.send(
                            mes,
                            username=message.author.name,
                            avatar_url=message.author.avatar.url,
                        )
                    elif TYPE == 2:
                        file = await message.attachments[0].to_file()
                        t = message.attachments[0].content_type
                        a = t.find("/") + 1
                        t = t[a:]
                        file.filename = f"image.{t}"
                        await webhook.send(
                            file=file,
                            username=message.author.name,
                            avatar_url=message.author.avatar.url,
                        )

                    if len(jss) > 20:
                        for x in jss:
                            jss.pop(x)
                            break
                    global nlog
                    nlog += 1
                    while True:
                        if nlog == 1:
                            meslog = ch.last_message_id
                            break
                        else:
                            await asyncio.sleep(0.5)
                    nlog -= 1
                    jss[f"{message.id}"].append(f"{ch.id}-{meslog}")
                    with open(JSON_DIR / "edit.json", "w", encoding="utf-8") as f:
                        json.dump(jss, f, ensure_ascii=False)
                    if ch.id == 1010923058648846458:
                        lmid = str(meslog)
                    else:
                        log.append(f"{ch.id}-{meslog}")
                channelog = self.bot.get_channel(926014780962111509)
                await channelog.send(f"跨群:{message.author.name}-{mes}")

                # awa=await message.channel.create_invite(max_age = 300)
                # await channel.send(awa)

    @commands.Cog.listener()
    async def on_message_edit(self, message_before, message_after):
        jss = await get(1)
        if str(message_before.id) in jss:
            js = await get(0)
            async with aiohttp.ClientSession() as session:
                for x in jss[f"{message_before.id}"]:
                    a = x.find("-")
                    ci = x[:a]
                    webhook = Webhook.from_url(js[str(ci)], session=session)
                    try:
                        await webhook.edit_message(
                            x[a + 1 :], content=message_after.clean_content
                        )
                    except:
                        pass

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        js = await get(3)
        jss = await get(1)
        if message.channel.id == 1010923058648846458:
            if str(message.id) in js:
                for x in js[f"{message.id}"]:
                    a = x.find("-")
                    ci = x[:a]
                    mi = x[a + 1 :]
                    channel = self.bot.get_channel(int(ci))
                    msg = await channel.fetch_message(int(mi))
                    await msg.delete()
        if str(message.id) in jss:
            js = await get(0)
            async with aiohttp.ClientSession() as session:
                for x in jss[f"{message.id}"]:
                    a = x.find("-")
                    ci = x[:a]
                    if ci == "1010923058648846458":
                        continue
                    try:
                        webhook = Webhook.from_url(js[str(ci)], session=session)
                        await webhook.delete_message(x[a + 1 :])
                    except:
                        pass

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def connect_cloud(self, ctx, channel: discord.TextChannel = None):
        print("awa")
        if channel == None:
            channel = ctx.channel
        js = await get(0)
        if not str(channel.id) in js:
            mes = await ctx.send(f"把`{channel.name}`頻道 連接至雲端?")
            await mes.add_reaction("✅")
            await mes.add_reaction("❌")
            awa = await wait(ctx, self, mes)
            if awa == 1:
                lol = await channel.create_webhook(
                    name="看屁哦", avatar=None, reason="關你屁事"
                )
                js[f"{channel.id}"] = lol.url
                with open(JSON_DIR / "webhook.json", "w", encoding="utf-8") as f:
                    json.dump(js, f, ensure_ascii=False)
                await ctx.send(f"已經把`{channel.name}`頻道 連接至雲端了awa")

                async with aiohttp.ClientSession() as session:
                    jss = await get(1)
                    jss[f"{ctx.message.id}"] = []
                    for webhooks in js:
                        try:
                            ch = self.bot.get_channel(int(webhooks))
                            awa = await ch.webhooks()
                            z = []

                            for x in awa:
                                z.append(x.url)
                            if js[webhooks] in z:
                                webhook = Webhook.from_url(
                                    js[webhooks], session=session
                                )
                            else:
                                lol = await ch.create_webhook(
                                    name="刪屁哦>:c", avatar=None, reason="刪屁哦"
                                )
                                js[f"{ch.id}"] = lol.url
                                with open(JSON_DIR / "webhook.json", "w", encoding="utf-8") as f:
                                    json.dump(js, f, ensure_ascii=False)
                        except:
                            pass
            elif awa == 2 or awa == 3:
                await mes.edit(content="已取消")
        else:
            await ctx.send(f"`{channel.name}`頻道 已經是連接中的頻道了")

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def disconnect_cloud(self, ctx, channel: discord.TextChannel = None):
        print("awa--")
        if channel == None:
            channel = ctx.channel
        js = await get(0)
        if str(channel.id) in js:
            mes = await ctx.send(f"把`{channel.name}`變回普通頻道?")
            await mes.add_reaction("✅")
            await mes.add_reaction("❌")
            awa = await wait(ctx, self, mes)
            if awa == 1:
                async with aiohttp.ClientSession() as session:
                    webhook = Webhook.from_url(js[f"{channel.id}"], session=session)
                    await webhook.delete(reason="啊 是你叫我刪的")
                js.pop(f"{channel.id}")
                with open(JSON_DIR / "webhook.json", "w", encoding="utf-8") as f:
                    json.dump(js, f, ensure_ascii=False)
                await ctx.send(f"已經把`{channel.name}`變回 普通頻道了awa")
            elif awa == 2 or awa == 3:
                await mes.edit(content="已取消")
        else:
            await ctx.send(f"`{channel.name}`頻道 不是連接中的頻道>:c")

    @connect_cloud.error
    async def a_error(self, ctx, err):
        if isinstance(err, commands.MissingPermissions):
            await ctx.send("你需要`管理頻道`權限來操作")

    @disconnect_cloud.error
    async def a(self, ctx, err):
        if isinstance(err, commands.MissingPermissions):
            await ctx.send("你需要`管理頻道`權限來操作")

    # @commands.command()
    # async def mute(self, ctx,member,*,reason=None):
    #   if ctx.author.id == 760492945022779432:
    #     fq=await get(4)
    #     fq[member]=reason
    #     with open("mute.json","w") as f:
    #       json.dump(fq,f,ensure_ascii=False)
    #   else:
    #     pass

    # @commands.command()
    # async def unmute(self, ctx,member,reason=None):
    #   if ctx.author.id == 760492945022779432:
    #     fq=await get(4)
    #     fq.pop(member)
    #     with open("mute.json","w") as f:
    #       json.dump(fq,f,ensure_ascii=False)
    #   else:
    #     pass


async def setup(bot):
    print("--- 開始執行 setup ---")
    cog = Web(bot)
    print(f"Cog 物件類型: {type(cog)}")
    result = bot.add_cog(cog)
    print(f"add_cog 回傳類型: {type(result)}")
    await result # 如果這行噴錯，代表 add_cog 回傳了 list
