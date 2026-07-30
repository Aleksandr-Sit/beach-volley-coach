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


def backfill_generated_weeks(db: DB) -> None:
    """Разовая миграция: помечает уже развёрнутые недели, чтобы не дублировать их
    после перехода на явный реестр generated_weeks."""
    for d in db.template_session_dates():
        try:
            db.mark_week_generated(monday_of(date.fromisoformat(d)).isoformat())
        except ValueError:
            continue


def generate_week(db: DB, any_day: date) -> None:
    """Создаёт сессии недели из шаблона волейбола + дефолтный зал.

    Идемпотентно по реестру generated_weeks. Раньше признаком было «есть хоть одна
    сессия в неделе» — из-за этого перенос сессии на будущую неделю блокировал
    генерацию её плана целиком.
    """
    monday = monday_of(any_day)
    if db.is_week_generated(monday.isoformat()):
        return
    db.mark_week_generated(monday.isoformat())  # до вставок — защита от гонки

    template = db.get_template()
    vb_days = set()
    for t in template:
        d = (monday + timedelta(days=int(t["weekday"]))).isoformat()
        vb_days.add(int(t["weekday"]))
        load = "heavy" if t["kind"] == "game" else "moderate"
        db.add_session(
            date=d,
            start_time=None,  # плавающее время, уточняется
            category="vb",
            title=t["title"],
            kind=t["kind"],
            duration_min=t["duration_min"],
            load=load,
            base_load=load,
            time_hint=t["time_hint"],
            status="planned",
            origin="template",
        )

    # Зал/восстановление — только на дни без волейбола (правило коллизии).
    for weekday, category, title, kind, dur, load in GYM_DEFAULTS:
        if weekday in vb_days:
            continue
        d = (monday + timedelta(days=weekday)).isoformat()
        db.add_session(
            date=d, start_time=None, category=category, title=title,
            kind=kind, duration_min=dur, load=load, base_load=load,
            status="planned", origin="template",
        )

    autoregulate_week(db, monday)


_LOAD_ORDER = ["light", "moderate", "heavy"]


def _lower(load: str, steps: int = 1) -> str:
    """Понизить нагрузку на N ступеней (не ниже light)."""
    i = _LOAD_ORDER.index(load) if load in _LOAD_ORDER else 1
    return _LOAD_ORDER[max(0, i - steps)]


def autoregulate_week(db: DB, monday: date) -> list[str]:
    """Пересчитывает нагрузку зала ОТ ИСХОДНОЙ (base_load) по правилам.

    Полный пересчёт, а не одностороннее понижение: если причина ушла (отдохнул,
    игру отменили) — нагрузка ВОЗВРАЩАЕТСЯ к исходной. Раньше был «храповик»:
    один раз понизив, обратно уже не поднимали.

    Правила:
      - тяжёлые ноги не в день игры и не накануне -> на ступень ниже;
      - check-in дня 'tired' -> зал/восстановление этого дня в light.
    """
    sunday = monday + timedelta(days=6)
    sessions = db.sessions_between(monday.isoformat(), sunday.isoformat())
    notes: list[str] = []

    game_dates = {
        date.fromisoformat(s["date"]) for s in sessions
        if s["category"] == "vb" and s["kind"] == "game" and s["status"] != "cancelled"
    }

    for s in sessions:
        if s["category"] not in ("gym", "recovery"):
            continue
        sd = date.fromisoformat(s["date"])
        base = s["base_load"] or s["load"] or "moderate"
        target = base
        reason = ""

        # 1) тяжёлые ноги близко к игре
        if (s["category"] == "gym" and s["kind"] == "lower" and base == "heavy"
                and any(0 <= (g - sd).days <= 1 for g in game_dates)):
            target = _lower(target)
            reason = "близко к игре"

        # 2) самочувствие «устал» в этот день
        ci = db.latest_checkin(s["date"])
        if ci and ci["readiness"] == "tired":
            target = "light"
            reason = "устал (check-in)"

        if target != s["load"]:
            db.update_session(s["id"], load=target)
            day = WEEKDAY_RU[sd.weekday()]
            if _LOAD_ORDER.index(target) < _LOAD_ORDER.index(s["load"] or "moderate"):
                notes.append(f"{day}: {s['title']} → облегчил ({reason}).")
            else:
                notes.append(f"{day}: {s['title']} → вернул исходную нагрузку.")
    return notes
