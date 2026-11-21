"""Audit trail in SQLite: every agent step, routing decision, approval and verification."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path


class AuditLog:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, action TEXT,
            tier TEXT, model TEXT, mode TEXT, reason TEXT, tokens_in INTEGER, tokens_out INTEGER,
            cost_usd REAL, detail TEXT)""")
        if "session" not in {r[1] for r in self.db.execute("PRAGMA table_info(events)")}:   # rows say which browser session made them
            self.db.execute("ALTER TABLE events ADD COLUMN session TEXT DEFAULT ''")
        self.db.commit()
        self.session = ""

    def log(self, run_id: str, agent: str, action: str, tier: str = "T0", model: str | None = None,
            mode: str = "code", reason: str = "", tokens_in: int = 0, tokens_out: int = 0,
            cost_usd: float = 0.0, detail: dict | str | None = None) -> dict:
        row = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "run_id": run_id, "agent": agent,
               "action": action, "tier": tier, "model": model or "", "mode": mode, "reason": reason,
               "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": round(cost_usd, 6),
               "detail": detail if isinstance(detail, str) else json.dumps(detail or {}, default=str), "session": self.session}
        self.db.execute("INSERT INTO events (ts, run_id, agent, action, tier, model, mode, reason, tokens_in, "
                        "tokens_out, cost_usd, detail, session) VALUES (:ts, :run_id, :agent, :action, :tier, :model, :mode, "
                        ":reason, :tokens_in, :tokens_out, :cost_usd, :detail, :session)", row)
        self.db.commit()
        return row

    def rows(self, run_id: str | None = None, session: str | None = None) -> list[dict]:
        where, args = [], []
        if run_id:
            where.append("run_id = ?"), args.append(run_id)
        if session:
            where.append("session = ?"), args.append(session)
        cur = self.db.execute("SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id", tuple(args))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
