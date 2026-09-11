"""SQLite persistence for AI Calendar. SQLite is the source of truth."""
from __future__ import annotations
import json, os, shutil, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

class CalendarDB:
    def __init__(self, path: str = "data/calendar.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_code TEXT UNIQUE NOT NULL, title TEXT NOT NULL, event_type TEXT NOT NULL,
          start_datetime TEXT, end_datetime TEXT, weekday INTEGER, recurring_time TEXT,
          recurring_end_time TEXT, all_day INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recurring_exceptions (id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_code TEXT NOT NULL, target_date TEXT NOT NULL, exception_type TEXT NOT NULL,
          override_start TEXT, override_end TEXT, created_at TEXT NOT NULL,
          UNIQUE(event_code, target_date));
        CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
          action TEXT NOT NULL, event_code TEXT, before_data TEXT, after_data TEXT,
          discord_user_id INTEGER, created_at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.conn.commit()

    def close(self): self.conn.close()
    def now(self): return datetime.now(ZoneInfo('Asia/Taipei')).isoformat()
    def setting(self, key):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    def set_setting(self, key, value):
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.conn.commit()
    def next_code(self, prefix):
        row = self.conn.execute("SELECT event_code FROM events WHERE event_code LIKE ? ORDER BY id DESC LIMIT 1", (prefix + "%",)).fetchone()
        return f"{prefix}{(int(row[0][1:]) + 1) if row else 1:03d}"
    def event(self, code): return self.conn.execute("SELECT * FROM events WHERE event_code=?", (code.upper(),)).fetchone()
    def active_events(self): return self.conn.execute("SELECT * FROM events WHERE status='active' ORDER BY id").fetchall()
    def add_audit(self, action, code, before, after, user):
        self.conn.execute("INSERT INTO audit_log(action,event_code,before_data,after_data,discord_user_id,created_at) VALUES(?,?,?,?,?,?)", (action, code, json.dumps(before, ensure_ascii=False) if before else None, json.dumps(after, ensure_ascii=False) if after else None, user, self.now()))
    def snapshot(self, row): return dict(row) if row else None
    def prune_deleted(self, once_limit=20, weekly_limit=10):
        """Keep only the newest deleted records of each event type."""
        for event_type, limit in (("once", once_limit), ("weekly", weekly_limit)):
            old = self.conn.execute(
                "SELECT event_code FROM events WHERE status='deleted' AND event_type=? "
                "ORDER BY updated_at DESC, id DESC LIMIT -1 OFFSET ?",
                (event_type, limit),
            ).fetchall()
            for row in old:
                self.conn.execute("DELETE FROM recurring_exceptions WHERE event_code=?", (row[0],))
                self.conn.execute("DELETE FROM events WHERE event_code=?", (row[0],))
        self.conn.commit()
    def backup(self, keep=30):
        folder = self.path.parent / "backups"; folder.mkdir(exist_ok=True)
        target = folder / f"calendar-{datetime.now().strftime('%Y-%m-%d')}.db"
        dst = sqlite3.connect(target)
        with dst: self.conn.backup(dst)
        dst.close()
        files = sorted(folder.glob("calendar-*.db"), reverse=True)
        for old in files[keep:]: old.unlink(missing_ok=True)
