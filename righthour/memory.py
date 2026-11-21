"""Plant sign-in and the plant's long-term memory.

Each plant signs in with a plant code and a passphrase (at least 8 characters). Everything the agent learns for that plant is saved under its code:
settings, rules learned from the manager, the forecast correction (with its history), the tariff library, the agent
settings and the guardrail floors. A new tab, a page refresh or a new day loads the same memory.

The passphrase is never stored: only a salted PBKDF2 hash. A sign-in gives a random session token; only its SHA-256 hash is
stored, and it expires. Five wrong tries lock the plant; each lock lasts twice as long. Many wrong tries across all plants slow down further wrong tries.

Storage is one SQLite file. On Streamlit Community Cloud that file lives on the app's disk, which a redeploy or a
reboot clears; "Download my memory" keeps a copy.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "righthour_memory.sqlite"
DEMO = "demo"
KEYS = ("cfg", "bias", "bias_history", "library", "proc", "price_rules")
SESSION_DAYS = 1
MAX_FAILS, LOCK_MIN = 5, 5          # 5 wrong tries lock the plant; each new lock lasts twice as long, at most 40 minutes
GLOBAL_FAILS_PER_MIN = 30            # wrong tries across all plants in one minute before each wrong try is slowed down


def _now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def clean_code(code: str) -> str:
    """A plant code is 3 to 32 letters, digits or dashes, lower case."""
    c = re.sub(r"[^a-z0-9-]+", "-", (code or "").strip().lower()).strip("-")
    if not 3 <= len(c) <= 32:
        raise ValueError("A plant code needs 3 to 32 letters, digits or dashes.")
    if c == DEMO:
        raise ValueError("“demo” is the shared demo plant. Choose another code.")
    return c


def _hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000).hex()


class MemoryStore:
    def __init__(self, path: str | Path = DB_PATH):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS plants (code TEXT PRIMARY KEY, name TEXT, salt TEXT, pin_hash TEXT, created TEXT,
                                                   fails INTEGER DEFAULT 0, locked_until TEXT, locks INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS fails (ts TEXT);
                CREATE TABLE IF NOT EXISTS memory (plant TEXT, key TEXT, value TEXT, updated TEXT, PRIMARY KEY (plant, key));
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, plant TEXT, expires TEXT);""")
            if "locks" not in {r[1] for r in self.db.execute("PRAGMA table_info(plants)")}:   # a file made by the first version
                self.db.execute("ALTER TABLE plants ADD COLUMN locks INTEGER DEFAULT 0")
            self.db.commit()

    # -------------------------------------------------------------- sign-in
    def sign_in(self, code: str, pin: str, name: str = "") -> tuple[str, bool]:
        """Signs in, or creates the plant on first use. Returns (session token, created)."""
        code = clean_code(code)
        if not 8 <= len(pin or "") <= 64:
            raise ValueError("The passphrase needs at least 8 characters.")
        with self.lock:
            since = (_now() - dt.timedelta(minutes=1)).isoformat()
            busy = self.db.execute("SELECT COUNT(*) FROM fails WHERE ts > ?", (since,)).fetchone()[0] >= GLOBAL_FAILS_PER_MIN
            row = self.db.execute("SELECT salt, pin_hash, fails, locked_until, locks FROM plants WHERE code = ?", (code,)).fetchone()
            created = row is None
            if created:
                wrong = False
                salt = secrets.token_bytes(16)
                self.db.execute("INSERT INTO plants (code, name, salt, pin_hash, created) VALUES (?, ?, ?, ?, ?)",
                                (code, name.strip()[:60] or code, salt.hex(), _hash_pin(pin, salt), _now().isoformat()))
            else:
                salt, ph, fails, locked, locks = bytes.fromhex(row[0]), row[1], row[2] or 0, row[3], row[4] or 0
                if locked and dt.datetime.fromisoformat(locked) > _now():
                    raise PermissionError(f"Too many wrong tries. Try again after {dt.datetime.fromisoformat(locked):%H:%M}.")
                if not hmac.compare_digest(_hash_pin(pin, salt), ph):
                    fails += 1
                    self.db.execute("INSERT INTO fails VALUES (?)", (_now().isoformat(),))
                    self.db.execute("DELETE FROM fails WHERE ts < ?", ((_now() - dt.timedelta(hours=1)).isoformat(),))
                    if fails >= MAX_FAILS:     # lock, and each lock lasts twice as long as the one before
                        until = (_now() + dt.timedelta(minutes=LOCK_MIN * 2 ** min(locks, 3))).isoformat()
                        self.db.execute("UPDATE plants SET fails = 0, locked_until = ?, locks = ? WHERE code = ?", (until, locks + 1, code))
                    else:
                        self.db.execute("UPDATE plants SET fails = ? WHERE code = ?", (fails, code))
                    self.db.commit()
                    wrong = True
                else:
                    wrong = False
                    self.db.execute("UPDATE plants SET fails = 0, locked_until = NULL, locks = 0 WHERE code = ?", (code,))
            self.db.commit()
        if not created and wrong:                # outside the lock: a slow wrong try never holds up anyone else
            if busy:                             # many wrong tries app-wide: each wrong try waits; a right one gets in at once
                time.sleep(2)
            raise PermissionError("Wrong passphrase for this plant code.")
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), code,
                                                                       (_now() + dt.timedelta(days=SESSION_DAYS)).isoformat()))
            self.db.commit()
        return token, created

    def resume(self, token: str | None) -> str | None:
        """The plant code for a valid session token, else None."""
        if not token:
            return None
        th = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT plant, expires FROM sessions WHERE token_hash = ?", (th,)).fetchone()
        if not row or dt.datetime.fromisoformat(row[1]) < _now():
            return None
        return row[0]

    def sign_out(self, token: str | None) -> None:
        if token:
            with self.lock:
                self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
                self.db.commit()

    def unlock(self, code: str) -> None:
        """For the app's owner (run from a shell): clears a plant's lock and its lock count."""
        with self.lock:
            self.db.execute("UPDATE plants SET fails = 0, locked_until = NULL, locks = 0 WHERE code = ?", (clean_code(code),))
            self.db.commit()

    def plant_name(self, code: str) -> str:
        with self.lock:
            row = self.db.execute("SELECT name FROM plants WHERE code = ?", (code,)).fetchone()
        return row[0] if row else code

    # -------------------------------------------------------------- memory
    def load(self, plant: str) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT key, value, updated FROM memory WHERE plant = ?", (plant,)).fetchall()
        return {k: json.loads(v) for k, v, _ in rows}

    def updated(self, plant: str) -> dict[str, str]:
        with self.lock:
            return dict(self.db.execute("SELECT key, updated FROM memory WHERE plant = ?", (plant,)).fetchall())

    def save(self, plant: str, data: dict) -> list[str]:
        """Writes only the keys whose value changed. Returns the keys written."""
        old = self.load(plant)
        changed = [k for k in KEYS if k in data and old.get(k) != json.loads(json.dumps(data[k], default=str))]
        if changed:
            with self.lock:
                for k in changed:
                    self.db.execute("INSERT OR REPLACE INTO memory VALUES (?, ?, ?, ?)",
                                    (plant, k, json.dumps(data[k], default=str), _now().isoformat()))
                self.db.commit()
        return changed

    def forget(self, plant: str, keys: tuple[str, ...] = KEYS) -> None:
        with self.lock:
            self.db.executemany("DELETE FROM memory WHERE plant = ? AND key = ?", [(plant, k) for k in keys])
            self.db.commit()
