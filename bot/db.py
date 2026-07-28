"""SQLite — единый источник правды. Схема + тонкие хелперы.

Все модули ЧИТАЮТ состояние отсюда; обработчик Телеграма его МУТИРУЕТ.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    data    TEXT NOT NULL            -- JSON: рост/вес/цели/формат/травмы и т.д.
);

CREATE TABLE IF NOT EXISTS vb_template (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday      INTEGER NOT NULL,    -- 0=Пн .. 6=Вс
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL,       -- technique | game | personal
    duration_min INTEGER NOT NULL,
    time_hint    TEXT,                -- 'утро' | 'вечер' | 'HH:MM'
    flexible     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,       -- ISO YYYY-MM-DD (локальная дата)
    start_time   TEXT,                -- 'HH:MM' или NULL (уточняется)
    category     TEXT NOT NULL,       -- vb | gym | recovery
    title        TEXT NOT NULL,
    kind         TEXT,                -- technique/game/lower/upper/mobility...
    duration_min INTEGER,
    load         TEXT DEFAULT 'moderate',   -- light | moderate | heavy
    status       TEXT NOT NULL DEFAULT 'planned',  -- planned|confirmed|done|moved|cancelled
    origin       TEXT NOT NULL DEFAULT 'template', -- template | manual
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

CREATE TABLE IF NOT EXISTS checkins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    readiness     TEXT,              -- fresh | ok | tired
    rpe_yesterday INTEGER,
    sleep_hours   REAL,
    sleep_quality TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,        -- session_cancelled|moved|retimed|added|checkin...
    payload    TEXT                  -- JSON план vs факт
);

CREATE TABLE IF NOT EXISTS nutrition_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    meal      TEXT,
    text      TEXT,
    photo_path TEXT,
    protein_g REAL,
    kcal      REAL
);

CREATE TABLE IF NOT EXISTS supplements (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    dose    TEXT,
    timing  TEXT,
    active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workout_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER,
    date        TEXT,
    category    TEXT,
    kind        TEXT,
    text        TEXT,           -- что реально сделал: веса/повторы/заметка
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wlog_kind ON workout_log(category, kind);

CREATE TABLE IF NOT EXISTS progression (
    lift_key    TEXT PRIMARY KEY,   -- squat | trapbar
    weight      REAL NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS food_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    text        TEXT,               -- что съел (как написал)
    kcal        REAL,
    protein     REAL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_food_date ON food_log(date);

CREATE TABLE IF NOT EXISTS custom_foods (
    name    TEXT PRIMARY KEY,       -- название (оно же ключ поиска)
    base_g  INTEGER NOT NULL,       -- 100 для weight, 1 для count
    kcal    REAL NOT NULL,
    protein REAL NOT NULL,
    ftype   TEXT NOT NULL           -- weight | count
);

CREATE TABLE IF NOT EXISTS supplement_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    date    TEXT NOT NULL,
    name    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_supp_date ON supplement_log(date);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- profile -------------------------------------------------------
    def get_profile(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM profile WHERE id=1").fetchone()
        return json.loads(row["data"]) if row else None

    def set_profile(self, data: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO profile(id, data) VALUES(1, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (json.dumps(data, ensure_ascii=False),),
        )
        self.conn.commit()

    # ---- vb_template ---------------------------------------------------
    def get_template(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM vb_template ORDER BY weekday, time_hint"
        ).fetchall()

    def seed_template(self, rows: Iterable[tuple]) -> None:
        cur = self.conn.execute("SELECT COUNT(*) c FROM vb_template").fetchone()
        if cur["c"]:
            return
        self.conn.executemany(
            "INSERT INTO vb_template(weekday, title, kind, duration_min, time_hint, flexible) "
            "VALUES(?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    # ---- sessions ------------------------------------------------------
    def sessions_for(self, date: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sessions WHERE date=? AND status!='cancelled' "
            "ORDER BY COALESCE(start_time,'99:99')",
            (date,),
        ).fetchall()

    def sessions_between(self, start: str, end: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sessions WHERE date BETWEEN ? AND ? ORDER BY date, "
            "COALESCE(start_time,'99:99')",
            (start, end),
        ).fetchall()

    def get_session(self, sid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

    def cancelled_for(self, date: str) -> list[sqlite3.Row]:
        # Только штатные (template) — их можно вернуть. Добавленные вручную при
        # отмене удаляются, чтобы не засорять меню.
        return self.conn.execute(
            "SELECT * FROM sessions WHERE date=? AND status='cancelled' "
            "AND origin='template' ORDER BY id",
            (date,),
        ).fetchall()

    def delete_session(self, sid: int) -> None:
        self.conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        self.conn.commit()

    def add_session(self, **kw: Any) -> int:
        cols = ", ".join(kw)
        ph = ", ".join("?" for _ in kw)
        cur = self.conn.execute(
            f"INSERT INTO sessions({cols}) VALUES({ph})", tuple(kw.values())
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_session(self, sid: int, **kw: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(
            f"UPDATE sessions SET {sets} WHERE id=?", (*kw.values(), sid)
        )
        self.conn.commit()

    def week_has_sessions(self, start: str, end: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE date BETWEEN ? AND ?", (start, end)
        ).fetchone()
        return bool(row["c"])

    # ---- checkins ------------------------------------------------------
    def add_checkin(self, date: str, **kw: Any) -> None:
        kw = {"date": date, "created_at": _now(), **kw}
        cols = ", ".join(kw)
        ph = ", ".join("?" for _ in kw)
        self.conn.execute(f"INSERT INTO checkins({cols}) VALUES({ph})", tuple(kw.values()))
        self.conn.commit()

    def latest_checkin(self, date: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM checkins WHERE date=? ORDER BY id DESC LIMIT 1", (date,)
        ).fetchone()

    # ---- workout_log (веса/повторы по факту) ---------------------------
    def add_workout_log(self, session_id: int, category: str, kind: str,
                        date: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO workout_log(session_id, category, kind, date, text, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (session_id, category, kind, date, text, _now()),
        )
        self.conn.commit()

    def last_workout_log(self, category: str, kind: str,
                         before_session: int | None = None) -> sqlite3.Row | None:
        q = "SELECT * FROM workout_log WHERE category=? AND kind=?"
        args: list[Any] = [category, kind]
        if before_session is not None:
            q += " AND session_id != ?"
            args.append(before_session)
        q += " ORDER BY date DESC, id DESC LIMIT 1"
        return self.conn.execute(q, tuple(args)).fetchone()

    # ---- progression (рабочие веса) ------------------------------------
    def seed_weights(self, defaults: dict[str, float]) -> None:
        for key, w in defaults.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO progression(lift_key, weight, updated_at) "
                "VALUES(?,?,?)", (key, w, _now()),
            )
        self.conn.commit()

    def get_weights(self) -> dict[str, float]:
        rows = self.conn.execute("SELECT lift_key, weight FROM progression").fetchall()
        return {r["lift_key"]: r["weight"] for r in rows}

    def set_weight(self, key: str, weight: float) -> None:
        self.conn.execute(
            "INSERT INTO progression(lift_key, weight, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(lift_key) DO UPDATE SET weight=excluded.weight, "
            "updated_at=excluded.updated_at", (key, weight, _now()),
        )
        self.conn.commit()

    def logs_between(self, start: str, end: str, category: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM workout_log WHERE date BETWEEN ? AND ?"
        args: list[Any] = [start, end]
        if category:
            q += " AND category=?"
            args.append(category)
        return self.conn.execute(q + " ORDER BY date", tuple(args)).fetchall()

    # ---- food_log (питание) --------------------------------------------
    def add_food(self, date: str, text: str, kcal: float, protein: float) -> None:
        self.conn.execute(
            "INSERT INTO food_log(date, text, kcal, protein, created_at) VALUES(?,?,?,?,?)",
            (date, text, kcal, protein, _now()),
        )
        self.conn.commit()

    def food_for(self, date: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM food_log WHERE date=? ORDER BY id", (date,)
        ).fetchall()

    # ---- custom_foods (свои продукты) ----------------------------------
    def add_custom_food(self, name: str, base_g: int, kcal: float,
                        protein: float, ftype: str) -> None:
        self.conn.execute(
            "INSERT INTO custom_foods(name, base_g, kcal, protein, ftype) "
            "VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET base_g=excluded.base_g, "
            "kcal=excluded.kcal, protein=excluded.protein, ftype=excluded.ftype",
            (name.lower(), base_g, kcal, protein, ftype),
        )
        self.conn.commit()

    def get_custom_foods(self) -> list[tuple]:
        """Возвращает в формате FOODS: (name, [ключи], base_g, kcal, protein, ftype)."""
        rows = self.conn.execute("SELECT * FROM custom_foods").fetchall()
        out = []
        for r in rows:
            name = r["name"]
            keys = [name]
            words = name.split()
            if len(words) == 2:  # «рис бурый» ↔ «бурый рис»
                keys.append(f"{words[1]} {words[0]}")
            out.append((name, keys, r["base_g"], r["kcal"], r["protein"], r["ftype"]))
        return out

    def delete_custom_food(self, name: str) -> bool:
        cur = self.conn.execute("DELETE FROM custom_foods WHERE name=?", (name.lower(),))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- supplement_log (приём добавок по дозам) -----------------------
    def take_supplement(self, date: str, name: str) -> None:
        self.conn.execute(
            "INSERT INTO supplement_log(date, name) VALUES(?,?)", (date, name))
        self.conn.commit()

    def clear_supplement(self, date: str, name: str) -> None:
        self.conn.execute(
            "DELETE FROM supplement_log WHERE date=? AND name=?", (date, name))
        self.conn.commit()

    def supplement_counts(self, date: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT name, COUNT(*) c FROM supplement_log WHERE date=? GROUP BY name",
            (date,),
        ).fetchall()
        return {r["name"]: r["c"] for r in rows}

    def delete_last_food(self, date: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM food_log WHERE date=? ORDER BY id DESC LIMIT 1", (date,)
        ).fetchone()
        if not row:
            return False
        self.conn.execute("DELETE FROM food_log WHERE id=?", (row["id"],))
        self.conn.commit()
        return True

    def checkins_between(self, start: str, end: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM checkins WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ).fetchall()

    # ---- events (audit) ------------------------------------------------
    def log_event(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(ts, kind, payload) VALUES(?,?,?)",
            (_now(), kind, json.dumps(payload or {}, ensure_ascii=False)),
        )
        self.conn.commit()
