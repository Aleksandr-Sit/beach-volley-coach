"""SQLite — единый источник правды. Схема + тонкие хелперы.

Все модули ЧИТАЮТ состояние отсюда; обработчик Телеграма его МУТИРУЕТ.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

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

-- Прогрессия применяется РОВНО ОДИН РАЗ за неделю (иначе повторный /review
-- накручивал веса: 4 тапа = +10 кг).
CREATE TABLE IF NOT EXISTS progression_applied (
    week_start  TEXT PRIMARY KEY,   -- понедельник недели, ISO
    applied_at  TEXT NOT NULL
);

-- Какие недели уже сгенерированы из шаблона. Раньше признаком было «есть хоть
-- одна сессия», из-за чего перенос сессии на будущую неделю блокировал её план.
CREATE TABLE IF NOT EXISTS generated_weeks (
    week_start   TEXT PRIMARY KEY,  -- понедельник недели, ISO
    generated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """ALTER TABLE ADD COLUMN, если колонки ещё нет (идемпотентно)."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_1(conn: sqlite3.Connection) -> None:
    """v1: base_load (храповик нагрузки) и time_hint (перестал теряться в notes)."""
    _add_column(conn, "sessions", "base_load", "TEXT")
    _add_column(conn, "sessions", "time_hint", "TEXT")
    # backfill: исходная нагрузка = текущая; подсказку времени достаём из notes
    conn.execute("UPDATE sessions SET base_load=load WHERE base_load IS NULL")
    conn.execute(
        "UPDATE sessions SET time_hint='утро' "
        "WHERE time_hint IS NULL AND notes LIKE '%утро%'")
    conn.execute(
        "UPDATE sessions SET time_hint='вечер' "
        "WHERE time_hint IS NULL AND notes LIKE '%вечер%'")


def _migrate_2(conn: sqlite3.Connection) -> None:
    """v2: один чек-ин на день — схлопываем историю дублей, оставляя последний."""
    conn.execute("""
        DELETE FROM checkins WHERE id NOT IN (
            SELECT MAX(id) FROM checkins GROUP BY date
        )""")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date)")


def _migrate_3(conn: sqlite3.Connection) -> None:
    """v3: замеры веса тела — без них цель по калориям не пересчитывалась."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS body_weight (
            date TEXT PRIMARY KEY,
            kg   REAL NOT NULL
        )""")


MIGRATIONS = [_migrate_1, _migrate_2, _migrate_3]


class DB:
    def __init__(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Версионные миграции через PRAGMA user_version (идемпотентны)."""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i, migrate in enumerate(MIGRATIONS, start=1):
            if version < i:
                migrate(self.conn)
                self.conn.execute(f"PRAGMA user_version = {i}")
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

    # ---- checkins ------------------------------------------------------
    def add_checkin(self, date: str, **kw: Any) -> None:
        """Один чек-ин на день: повторный тап ОБНОВЛЯЕТ запись, а не плодит дубли
        (раньше «устал×2 → передумал на свежий» давал разбору tired=2)."""
        kw = {"date": date, "created_at": _now(), **kw}
        cols = ", ".join(kw)
        ph = ", ".join("?" for _ in kw)
        updates = ", ".join(f"{k}=excluded.{k}" for k in kw if k != "date")
        self.conn.execute(
            f"INSERT INTO checkins({cols}) VALUES({ph}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}",
            tuple(kw.values()),
        )
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

    def is_progression_applied(self, week_start: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM progression_applied WHERE week_start=?", (week_start,)
        ).fetchone()
        return row is not None

    def mark_progression_applied(self, week_start: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO progression_applied(week_start, applied_at) "
            "VALUES(?,?)", (week_start, _now()),
        )
        self.conn.commit()

    # ---- generated_weeks (какие недели развёрнуты из шаблона) -----------
    def is_week_generated(self, week_start: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM generated_weeks WHERE week_start=?", (week_start,)
        ).fetchone()
        return row is not None

    def mark_week_generated(self, week_start: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO generated_weeks(week_start, generated_at) "
            "VALUES(?,?)", (week_start, _now()),
        )
        self.conn.commit()

    def template_session_dates(self) -> list[str]:
        """Даты сессий из шаблона — для разовой backfill-миграции generated_weeks."""
        rows = self.conn.execute(
            "SELECT DISTINCT date FROM sessions WHERE origin='template'"
        ).fetchall()
        return [r["date"] for r in rows]

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

    # ---- body_weight (замеры веса тела) --------------------------------
    def add_weight(self, date: str, kg: float) -> None:
        self.conn.execute(
            "INSERT INTO body_weight(date, kg) VALUES(?,?) "
            "ON CONFLICT(date) DO UPDATE SET kg=excluded.kg", (date, kg))
        self.conn.commit()

    def latest_weight(self) -> tuple[str, float] | None:
        row = self.conn.execute(
            "SELECT date, kg FROM body_weight ORDER BY date DESC LIMIT 1").fetchone()
        return (row["date"], row["kg"]) if row else None

    def weights_since(self, start: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT date, kg FROM body_weight WHERE date>=? ORDER BY date", (start,)
        ).fetchall()

    def weight_trend(self, days: int = 28) -> float | None:
        """Изменение веса (кг) за период: среднее последней недели минус первой."""
        from datetime import date as _d
        from datetime import timedelta as _td
        start = (_d.today() - _td(days=days)).isoformat()
        rows = self.weights_since(start)
        if len(rows) < 2:
            return None
        half = max(1, len(rows) // 3)
        first = sum(r["kg"] for r in rows[:half]) / half
        last = sum(r["kg"] for r in rows[-half:]) / half
        return round(last - first, 1)

    # ---- events (audit) ------------------------------------------------
    def log_event(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(ts, kind, payload) VALUES(?,?,?)",
            (_now(), kind, json.dumps(payload or {}, ensure_ascii=False)),
        )
        self.conn.commit()
