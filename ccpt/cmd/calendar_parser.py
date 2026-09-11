"""Strict parser and validators for the AI Calendar text command."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
WEEKDAYS = {
    "mon": 0, "monday": 0, "一": 0, "週一": 0, "星期一": 0,
    "tue": 1, "tuesday": 1, "二": 1, "週二": 1, "星期二": 1,
    "wed": 2, "wednesday": 2, "三": 2, "週三": 2, "星期三": 2,
    "thu": 3, "thursday": 3, "四": 3, "週四": 3, "星期四": 3,
    "fri": 4, "friday": 4, "五": 4, "週五": 4, "星期五": 4,
    "sat": 5, "saturday": 5, "六": 5, "週六": 5, "星期六": 5,
    "sun": 6, "sunday": 6, "日": 6, "天": 6, "週日": 6, "星期日": 6, "星期天": 6,
}

@dataclass(frozen=True)
class TimeSpec:
    start: time | None
    end: time | None
    all_day: bool = False

def parse_date(value: str, now: datetime | None = None) -> date:
    now = now or datetime.now(TZ)
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", value)
    if m:
        result = date(int(m[1]), int(m[2]), int(m[3]))
    else:
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", value)
        if not m:
            raise ValueError("日期格式錯誤，請使用 YYYY/MM/DD 或 M/D")
        result = date(now.year, int(m[1]), int(m[2]))
        if result < now.date():
            result = date(now.year + 1, int(m[1]), int(m[2]))
    return result

def parse_month(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})", value)
    if not m or not 1 <= int(m[2]) <= 12:
        raise ValueError("月份格式錯誤，請使用 YYYY/MM")
    return int(m[1]), int(m[2])

def parse_time_spec(value: str) -> TimeSpec:
    if value.lower() in ("all-day", "全天"):
        return TimeSpec(None, None, True)
    m = re.fullmatch(r"(\d{2}):(\d{2})(?:-(\d{2}):(\d{2}))?", value)
    if not m:
        raise ValueError("時間格式錯誤，請使用 HH:MM 或 HH:MM-HH:MM")
    h1, n1 = int(m[1]), int(m[2])
    h2, n2 = (int(m[3]), int(m[4])) if m[3] else (None, None)
    if h1 > 23 or n1 > 59 or (h2 is not None and (h2 > 23 or n2 > 59)):
        raise ValueError("時間必須介於 00:00 至 23:59")
    return TimeSpec(time(h1, n1), time(h2, n2) if h2 is not None else None)

def weekday(value: str) -> int:
    try:
        return WEEKDAYS[value.lower()]
    except KeyError as exc:
        raise ValueError("星期格式錯誤") from exc

def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat()

def occurrence_datetimes(day: date, spec: TimeSpec) -> tuple[datetime, datetime | None]:
    # Keep the actual date for all-day events.  The time is only a storage
    # anchor; renderers use all_day to display "全天" instead of 00:00.
    if spec.all_day:
        return datetime.combine(day, time.min, TZ), None
    start = datetime.combine(day, spec.start, TZ) if spec.start else None
    end = datetime.combine(day, spec.end, TZ) if spec.end else None
    if start and end and end <= start:
        end += timedelta(days=1)
    return start, end
