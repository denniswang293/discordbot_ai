from __future__ import annotations
import asyncio, logging, os, shlex
from datetime import date, datetime, timedelta
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from .calendar_database import CalendarDB
from .calendar_parser import TZ, parse_date, parse_month, parse_time_spec, weekday
from .calendar_service import CalendarService

load_dotenv(); log=logging.getLogger('ai_calendar')

HELP='''📅 AI Calendar 使用說明
ai-calendar add <日期> <時間> <事件名稱>
ai-calendar weekly <星期> <時間> <事件名稱>
ai-calendar edit <ID> title|date|time|datetime|weekday <值>
ai-calendar delete <ID>
ai-calendar show event|date|weekday|month <值>
ai-calendar search <關鍵字>
ai-calendar skip <Weekly ID> <日期>
ai-calendar move <Weekly ID> <日期> <新時間>
ai-calendar undo

日期：2026/09/12、2026-09-12、9/12（無年份使用下一個尚未經過的日期）
時間：HH:MM、HH:MM-HH:MM、all-day／全天
星期：mon/tue/wed/thu/fri/sat/sun 或 星期一～星期日'''

class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot=bot; self.db=CalendarDB(os.getenv('CALENDAR_DB_PATH','data/calendar.db')); self.service=CalendarService(self.db); self.lock=asyncio.Lock(); self.channel_id=int(os.getenv('CALENDAR_CHANNEL_ID','0') or 0); self.owner_id=int(os.getenv('CALENDAR_OWNER_ID','0') or 0); self.message_id=int(self.db.setting('calendar_message_id') or 0); self.days=int(os.getenv('CALENDAR_DAYS','14') or 14); self.refresh_loop.start()
    def cog_unload(self): self.refresh_loop.cancel(); self.db.close()
    def allowed_channel(self,m): return self.channel_id and m.channel.id==self.channel_id
    def allowed_owner(self,m): return not self.owner_id or m.author.id==self.owner_id
    async def temporary(self, channel, text, seconds=20):
        try:
            msg=await channel.send(text); await asyncio.sleep(seconds); await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException): pass
    async def delete_command(self,m):
        try: await m.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException): pass
    @commands.Cog.listener()
    async def on_ready(self):
        await self.refresh()
    @tasks.loop(minutes=1)
    async def refresh_loop(self):
        now=datetime.now(TZ)
        if now.hour==0 and now.minute==1:
            try: self.db.backup()
            except Exception: log.exception('Calendar backup failed')
            await self.refresh()
    @refresh_loop.before_loop
    async def before_refresh(self): await self.bot.wait_until_ready()
    async def refresh(self):
        if not self.channel_id: return
        channel=self.bot.get_channel(self.channel_id)
        if channel is None:
            log.error('Calendar channel %s not found; refresh disabled',self.channel_id); return
        today=datetime.now(TZ).date(); until=today+timedelta(days=self.days-1); lines=[f'📅 接下來 {self.days} 天\n{today:%Y/%m/%d} ～ {until:%Y/%m/%d}']
        names=['一','二','三','四','五','六','日']; current=None
        for day,s,row in self.service.occurrences(today,until):
            if day!=current: current=day; lines.append(f'\n📍 {day.month}/{day.day}（{names[day.weekday()]}）')
            when='全天' if row['all_day'] else (s.strftime('%H:%M') if s else '')
            if row['event_type']=='weekly': when='↻ '+when
            lines.append(f'[{row["event_code"]}] {when} {row["title"]}')
        lines.append(f'\n────────────\n最後更新：{datetime.now(TZ):%Y/%m/%d %H:%M}')
        embed=discord.Embed(description='\n'.join(lines), colour=discord.Colour.blurple())
        try:
            msg=await channel.fetch_message(self.message_id) if self.message_id else None
            if msg: await msg.edit(embed=embed, content=None)
            else:
                msg=await channel.send(embed=embed); self.message_id=msg.id; self.db.set_setting('calendar_message_id',msg.id)
        except discord.NotFound:
            try:
                msg=await channel.send(embed=embed); self.message_id=msg.id; self.db.set_setting('calendar_message_id',msg.id); log.warning('Calendar message recreated')
            except discord.DiscordException as exc: log.error('Calendar send failed: %s',exc)
        except discord.DiscordException as exc: log.error('Calendar refresh failed: %s',exc)
    @commands.Cog.listener()
    async def on_message(self,m):
        if m.author.bot or not self.allowed_channel(m) or not m.content.lower().startswith('ai-calendar'): return
        try: args=shlex.split(m.content)[1:]
        except ValueError: args=[]
        await self.delete_command(m)
        try:
            async with self.lock: response, wait = await self.execute(args,m.author.id)
            if response: await self.temporary(m.channel,response,wait)
            elif args and args[0] not in ('show','search','help'): await self.refresh()
        except Exception as exc:
            log.exception('Calendar command failed'); await self.temporary(m.channel,f'❌ {exc}',15)
    async def execute(self,a,user):
        if not a or a[0]=='help': return HELP,60
        cmd=a[0].lower(); now=datetime.now(TZ)
        if cmd in ('add','weekly','edit','delete','del','skip','move','undo') and not self.allowed_owner(type('M',(),{'author':type('A',(),{'id':user})()})()): raise ValueError('你沒有行事曆操作權限')
        if cmd=='add' and len(a)>=4:
            row=self.service.add(' '.join(a[3:]),parse_date(a[1],now),parse_time_spec(a[2]),user); return None,0
        if cmd=='weekly' and len(a)>=4:
            row=self.service.add(' '.join(a[3:]),now.date(),parse_time_spec(a[2]),user,weekday(a[1])); return None,0
        if cmd in ('delete','del') and len(a)==2: self.service.delete(a[1],user); return None,0
        if cmd=='edit' and len(a)>=4: self.service.edit(a[1],a[2].lower(),' '.join(a[3:]),user); return None,0
        if cmd=='skip' and len(a)==3: self.service.exception('skip',a[1],parse_date(a[2],now),None,user); return None,0
        if cmd=='move' and len(a)==4: self.service.exception('move',a[1],parse_date(a[2],now),parse_time_spec(a[3]),user); return None,0
        if cmd=='undo' and len(a)==1: self.service.undo(user); return None,0
        if cmd=='search' and len(a)>=2:
            q=' '.join(a[1:]); rows=[r for r in self.db.active_events() if q.casefold() in r['title'].casefold()]; return ('搜尋：'+q+'\n'+'\n'.join(f'[{r["event_code"]}] '+(f'每週 {r["weekday"]} ' if r['event_type']=='weekly' else (r['start_datetime'] or '')[:10]+' ')+r['title'] for r in rows) or '找不到事件'),20
        if cmd=='show' and len(a)>=3:
            kind=a[1].lower()
            if kind=='event':
                r=self.db.event(a[2]); return (f'事件 ID：{a[2].upper()}\n名稱：{r["title"]}\n類型：{"每週事件" if r["event_type"]=="weekly" else "一次性事件"}\n建立時間：{r["created_at"]}' if r else '找不到事件'),15
            if kind=='weekday':
                w=weekday(a[2]); rows=[r for r in self.db.active_events() if r['event_type']=='weekly' and r['weekday']==w]; return ('📅 每週固定事件\n'+'\n'.join(f'[{r["event_code"]}] {r["recurring_time"] or "全天"} {r["title"]}' for r in rows) or '沒有事件'),20
            if kind in ('date','month'):
                start=parse_date(a[2],now) if kind=='date' else date(*parse_month(a[2]),1)
                end=start if kind=='date' else (date(start.year+1,1,1)-timedelta(days=1) if start.month==12 else date(start.year,start.month+1,1)-timedelta(days=1))
                occ=self.service.occurrences(start,end)
                lines=['📅 '+start.strftime('%Y/%m/%d') if kind=='date' else f'📅 {start.year} 年 {start.month} 月']
                shown_day=None
                for day,s,r in occ:
                    if kind=='month' and day != shown_day:
                        shown_day=day; lines.append(f'\n{day.month}/{day.day}')
                    lines.append(f'[{r["event_code"]}] {s.strftime("%H:%M") if s else "全天"} {r["title"]}')
                return ('\n'.join(lines) if occ else '沒有事件'),30
        raise ValueError('指令格式錯誤，請使用 ai-calendar help')

async def setup(bot): await bot.add_cog(CalendarCog(bot))
