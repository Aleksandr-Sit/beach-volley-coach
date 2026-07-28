"""schedule-sync: генерация недельного плана и пересчёт после правок.

Инвариант: волейбол первичен. При коллизии зал уступает волейболу.
Тяжёлые ноги не ставим <48ч до игровой (game).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..db import DB

WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Дефолтные слоты зала/восстановления на дни БЕЗ волейбола.
# Верх — плечо-дружественный (без классического жима штанги), + prehab.
GYM_DEFAULTS = [
    # (weekday, category, title, kind, duration_min, load)
    (0, "gym", "Зал: ноги/мощность", "lower", 60, "heavy"),      # Пн — свежий
    (5, "gym", "Зал: верх (плечо-safe) + prehab", "upper", 60, "moderate"),  # Сб
    (6, "recovery", "Мобильность/восстановление", "mobility", 30, "light"),  # Вс
]


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def generate_week(db: DB, any_day: date) -> None:
    """Создаёт сессии недели из шаблона волейбола + дефолтный зал.

    Идемпотентно: если на неделю уже есть сессии — не дублирует.
    """
    monday = monday_of(any_day)
    sunday = monday + timedelta(days=6)
    if db.week_has_sessions(monday.isoformat(), sunday.isoformat()):
        return

    template = db.get_template()
    vb_days = set()
    for t in template:
        d = (monday + timedelta(days=int(t["weekday"]))).isoformat()
        vb_days.add(int(t["weekday"]))
        db.add_session(
            date=d,
            start_time=None,  # плавающее время, уточняется
            category="vb",
            title=t["title"],
            kind=t["kind"],
            duration_min=t["duration_min"],
            load="heavy" if t["kind"] == "game" else "moderate",
            status="planned",
            origin="template",
            notes=f"подсказка времени: {t['time_hint']}",
        )

    # Зал/восстановление — только на дни без волейбола (правило коллизии).
    for weekday, category, title, kind, dur, load in GYM_DEFAULTS:
        if weekday in vb_days:
            continue
        d = (monday + timedelta(days=weekday)).isoformat()
        db.add_session(
            date=d, start_time=None, category=category, title=title,
            kind=kind, duration_min=dur, load=load, status="planned",
            origin="template", notes="автопрегуляция по check-in",
        )

    autoregulate_week(db, monday)


def autoregulate_week(db: DB, monday: date) -> list[str]:
    """Проверяет инварианты и мягко чинит зал. Возвращает список пояснений.

    MVP-логика:
      - тяжёлые ноги (gym/lower/heavy) не ближе 48ч ДО игровой -> понижаем нагрузку;
      - если check-in дня 'tired' -> зал этого дня понижается.
    Более глубокую периодизацию подключим в gym_plan/weekly-adapt.
    """
    sunday = monday + timedelta(days=6)
    sessions = db.sessions_between(monday.isoformat(), sunday.isoformat())
    notes: list[str] = []

    game_dates = {
        date.fromisoformat(s["date"]) for s in sessions
        if s["category"] == "vb" and s["kind"] == "game"
    }

    for s in sessions:
        if s["category"] != "gym" or s["kind"] != "lower":
            continue
        sd = date.fromisoformat(s["date"])
        # Тяжёлые ноги нельзя в день игры или накануне (<=1 дня до игровой).
        too_close = any(0 <= (g - sd).days <= 1 for g in game_dates)
        if too_close and s["load"] == "heavy":
            db.update_session(s["id"], load="moderate",
                              notes="понижено: менее 48ч до игры")
            notes.append(
                f"{WEEKDAY_RU[sd.weekday()]}: тяжёлые ноги близко к игре → облегчил."
            )

    # Учёт самочувствия: tired -> лёгкий день.
    for s in sessions:
        if s["category"] not in ("gym", "recovery"):
            continue
        ci = db.latest_checkin(s["date"])
        if ci and ci["readiness"] == "tired" and s["load"] != "light":
            db.update_session(s["id"], load="light", notes="понижено: устал (check-in)")
            notes.append(
                f"{WEEKDAY_RU[date.fromisoformat(s['date']).weekday()]}: "
                f"устал → зал облегчил."
            )
    return notes
