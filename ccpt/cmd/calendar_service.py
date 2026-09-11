from __future__ import annotations
from datetime import date, datetime, timedelta
from .calendar_parser import TZ, TimeSpec, occurrence_datetimes, parse_date, parse_time_spec

class CalendarService:
    def __init__(self, db): self.db = db
    def _save(self, action, code, before, after, user):
        self.db.add_audit(action, code, before, after, user); self.db.conn.commit()
    def add(self, title, day, spec, user, weekly_day=None):
        if not title.strip() or len(title) > 100: raise ValueError('事件名稱不可為空且最多 100 字')
        now = self.db.now(); typ = 'weekly' if weekly_day is not None else 'once'; code = self.db.next_code('W' if typ == 'weekly' else 'A')
        start, end = occurrence_datetimes(day, spec) if typ == 'once' else (None, None)
        if typ == 'once' and start and start <= datetime.now(TZ): raise ValueError('不能新增已經過去的事件')
        vals = (code, title, typ, start.isoformat() if start else None, end.isoformat() if end else None, weekly_day, spec.start.strftime('%H:%M') if spec.start else None, spec.end.strftime('%H:%M') if spec.end else None, int(spec.all_day), now, now)
        self.db.conn.execute("INSERT INTO events(event_code,title,event_type,start_datetime,end_datetime,weekday,recurring_time,recurring_end_time,all_day,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", vals)
        row = self.db.event(code); self._save('add', code, None, self.db.snapshot(row), user); return row
    def edit(self, code, field, value, user):
        row = self.db.event(code); 
        if not row or row['status'] != 'active': raise ValueError('找不到有效的事件 ID')
        before = self.db.snapshot(row); updates = {}
        if field == 'title': updates['title'] = value
        elif field == 'date' and row['event_type'] == 'once':
            day = parse_date(value)
            if row['all_day']:
                spec = TimeSpec(None, None, True)
            else:
                spec = TimeSpec(datetime.fromisoformat(row['start_datetime']).time(), datetime.fromisoformat(row['end_datetime']).time() if row['end_datetime'] else None)
            s,e=occurrence_datetimes(day,spec); updates.update(start_datetime=s.isoformat() if s else None,end_datetime=e.isoformat() if e else None)
        elif field in ('time','datetime'):
            if row['event_type'] == 'weekly':
                spec = parse_time_spec(value.split()[0]); updates.update(recurring_time=spec.start.strftime('%H:%M') if spec.start else None, recurring_end_time=spec.end.strftime('%H:%M') if spec.end else None, all_day=int(spec.all_day))
            else:
                parts=value.split(); day=parse_date(parts[0]) if field=='datetime' else datetime.fromisoformat(row['start_datetime']).date(); spec=parse_time_spec(parts[-1]); s,e=occurrence_datetimes(day,spec); updates.update(start_datetime=s.isoformat() if s else None,end_datetime=e.isoformat() if e else None,all_day=int(spec.all_day))
        elif field == 'weekday' and row['event_type'] == 'weekly':
            from .calendar_parser import weekday; updates['weekday'] = weekday(value)
        else: raise ValueError('這個事件不支援該修改方式')
        updates['updated_at'] = self.db.now(); sql=', '.join(f'{k}=?' for k in updates); self.db.conn.execute(f'UPDATE events SET {sql} WHERE event_code=?', (*updates.values(), code.upper())); after=self.db.snapshot(self.db.event(code)); self._save('edit', code.upper(), before, after, user); return after
    def delete(self, code, user):
        row=self.db.event(code); 
        if not row or row['status']!='active': raise ValueError('找不到有效的事件 ID')
        before=self.db.snapshot(row); self.db.conn.execute("UPDATE events SET status='deleted',updated_at=? WHERE event_code=?",(self.db.now(),code.upper())); after=self.db.snapshot(self.db.event(code)); self._save('delete',code.upper(),before,after,user)
        self.db.prune_deleted()
    def exception(self, action, code, day, spec, user):
        row=self.db.event(code); 
        if not row or row['event_type']!='weekly' or row['status']!='active': raise ValueError('找不到有效的 Weekly ID')
        old=self.db.conn.execute('SELECT * FROM recurring_exceptions WHERE event_code=? AND target_date=?',(code.upper(),day.isoformat())).fetchone(); before=dict(old) if old else None
        s,e=occurrence_datetimes(day,spec) if spec else (None,None); self.db.conn.execute("INSERT INTO recurring_exceptions(event_code,target_date,exception_type,override_start,override_end,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(event_code,target_date) DO UPDATE SET exception_type=excluded.exception_type,override_start=excluded.override_start,override_end=excluded.override_end",(code.upper(),day.isoformat(),action,s.isoformat() if s else None,e.isoformat() if e else None,self.db.now())); after=dict(self.db.conn.execute('SELECT * FROM recurring_exceptions WHERE event_code=? AND target_date=?',(code.upper(),day.isoformat())).fetchone()); self._save(action,code.upper(),before,after,user)
    def undo(self, user):
        import json
        audit=self.db.conn.execute("SELECT * FROM audit_log WHERE undone=0 AND action IN ('add','edit','delete','skip','move') ORDER BY id DESC LIMIT 1").fetchone()
        if not audit: raise ValueError('沒有可復原的操作')
        before=json.loads(audit['before_data']) if audit['before_data'] else None
        after=json.loads(audit['after_data']) if audit['after_data'] else None
        code=audit['event_code']
        if audit['action']=='add': self.db.conn.execute("UPDATE events SET status='deleted',updated_at=? WHERE event_code=?",(self.db.now(),code))
        elif audit['action']=='delete': self.db.conn.execute("UPDATE events SET status='active',updated_at=? WHERE event_code=?",(self.db.now(),code))
        elif audit['action']=='edit' and before:
            fields={k:v for k,v in before.items() if k not in ('id','event_code')}; fields['updated_at']=self.db.now(); sql=', '.join(f'{k}=?' for k in fields); self.db.conn.execute(f'UPDATE events SET {sql} WHERE event_code=?',(*fields.values(),code))
        elif before:
            self.db.conn.execute("UPDATE recurring_exceptions SET exception_type=?,override_start=?,override_end=? WHERE event_code=? AND target_date=?",(before['exception_type'],before['override_start'],before['override_end'],before['event_code'],before['target_date']))
        elif after: self.db.conn.execute("DELETE FROM recurring_exceptions WHERE event_code=? AND target_date=?",(code,after['target_date']))
        self.db.conn.execute('UPDATE audit_log SET undone=1 WHERE id=?',(audit['id'],)); self.db.add_audit('undo',code,after,before,user); self.db.conn.commit()
    def occurrences(self, start, end):
        result=[]
        for row in self.db.active_events():
            if row['event_type']=='once':
                s=datetime.fromisoformat(row['start_datetime']) if row['start_datetime'] else datetime.combine(start,datetime.min.time(),TZ)
                if start <= s.date() <= end: result.append((s.date(),s,row))
            else:
                day=start+timedelta(days=(row['weekday']-start.weekday())%7)
                while day<=end:
                    ex=self.db.conn.execute('SELECT * FROM recurring_exceptions WHERE event_code=? AND target_date=?',(row['event_code'],day.isoformat())).fetchone()
                    if not ex or ex['exception_type']!='skip':
                        spec=TimeSpec(datetime.strptime(row['recurring_time'],'%H:%M').time() if row['recurring_time'] else None, datetime.strptime(row['recurring_end_time'],'%H:%M').time() if row['recurring_end_time'] else None, bool(row['all_day'])); s,e=occurrence_datetimes(day,spec)
                        if ex and ex['override_start']: s=datetime.fromisoformat(ex['override_start']); e=datetime.fromisoformat(ex['override_end']) if ex['override_end'] else None
                        result.append((day,s,row))
                    day+=timedelta(days=7)
        return sorted(result,key=lambda x:(x[0], x[1] or datetime.min.replace(tzinfo=TZ), x[2]['created_at']))
