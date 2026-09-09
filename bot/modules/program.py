"""Программа недели: сезон, правка шаблона, пересборка плана, мезоциклы.

Два вопроса, на которые отвечает модуль:

1. ЧТО в неделе — шаблон в program_template. Меняется сменой сезона целиком
   или правкой одного дня из Telegram. Раньше это был код, и потому не менялось.

2. КАК ДАВНО мы это делаем — мезоцикл. Программа разбита на блоки по 4 недели:
   три рабочих и разгрузочная. Номер блока задаёт вариант подсобки, поэтому
   упражнения меняются сами каждые 4 недели, а не «когда надоест».

Якорь блока (program_anchor) — понедельник, с которого началась текущая
программа. Смена сезона сдвигает якорь: новый сезон = новый мезоцикл с первой
недели.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..content.seasons import BLOCK_WEEKS, DEFAULT_SEASON, SEASONS, season, slots_for
from ..db import DB
from .schedule_sync import generate_week, monday_of

S_SEASON = "season"
S_ANCHOR = "program_anchor"
S_EDITED = "season_edited"


# ---------------------------------------------------------------- сезон
def current_season(db: DB) -> str:
    return db.get_setting(S_SEASON, DEFAULT_SEASON) or DEFAULT_SEASON


def is_edited(db: DB) -> bool:
    return (db.get_setting(S_EDITED, "0") or "0") == "1"


def season_title(db: DB) -> str:
    key = current_season(db)
    s = season(key)
    suffix = " <i>(изменён вручную)</i>" if is_edited(db) else ""
    return f"{s['label']}{suffix}"


def mark_edited(db: DB) -> None:
    """Ручная правка дня: сезон больше не «чистый пресет»."""
    db.set_setting(S_EDITED, "1")


# ------------------------------------------------------------- мезоцикл
def anchor(db: DB) -> date:
    """Понедельник старта текущей программы. При первом обращении — эта неделя."""
    raw = db.get_setting(S_ANCHOR)
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    from ..clock import today
    mon = monday_of(today())
    db.set_setting(S_ANCHOR, mon.isoformat())
    return mon


def set_anchor(db: DB, d: date) -> None:
    db.set_setting(S_ANCHOR, monday_of(d).isoformat())


def week_index(db: DB, d: date) -> int:
    """Сколько полных недель прошло с начала программы (0 = первая неделя)."""
    return max(0, (monday_of(d) - anchor(db)).days // 7)


def block_index(db: DB, d: date) -> int:
    """Номер мезоцикла с нуля. Задаёт вариант подсобки."""
    return week_index(db, d) // BLOCK_WEEKS


def week_in_block(db: DB, d: date) -> int:
    """Позиция недели в блоке, 1..BLOCK_WEEKS. Последняя — разгрузочная."""
    return week_index(db, d) % BLOCK_WEEKS + 1


def is_deload(db: DB, d: date) -> bool:
    """Разгрузочная ли неделя.

    Считается по КАЛЕНДАРЮ, а не по числу применённых прогрессий. Раньше
    разгрузка зависела от того, сколько недель атлет логировал тренировки:
    логируешь редко — разгрузка не наступает никогда, хотя усталость копится.
    """
    return week_in_block(db, d) == BLOCK_WEEKS


def cycle_badge(db: DB, d: date) -> str:
    """«Блок 2 · неделя 3 из 4» — чтобы ротация была видна, а не угадывалась."""
    w = week_in_block(db, d)
    tail = " · разгрузка" if w == BLOCK_WEEKS else ""
    return f"Блок {block_index(db, d) + 1} · неделя {w} из {BLOCK_WEEKS}{tail}"


# --------------------------------------------------- пересборка расписания
HORIZON_WEEKS = 2  # насколько вперёд гарантированно собран план


def regenerate_from(db: DB, start: date, horizon: int = HORIZON_WEEKS) -> int:
    """Пересобирает план из текущего шаблона, начиная с даты start.

    Прошлое не трогает вообще. Из будущего сносит только то, что поставил
    шаблон и что осталось нетронутым (см. delete_template_sessions_from):
    выполненное, подтверждённое, добавленное вручную и всё с логом — остаётся.

    Горизонт: недели собираются на horizon вперёд, даже если раньше их ещё не
    было. Без этого смена сезона перестраивала только текущую неделю, а
    «неделя вперёд» до своего наступления оставалась пустой — то есть увидеть
    новую программу целиком было нельзя.
    """
    mon = monday_of(start)
    weeks = set(db.generated_weeks_from(mon.isoformat()))
    weeks.update((mon + timedelta(days=7 * i)).isoformat() for i in range(horizon))

    db.delete_template_sessions_from(start.isoformat())
    for w in sorted(weeks):
        db.unmark_week_generated(w)
    for w in sorted(weeks):
        wd = date.fromisoformat(w)
        # В текущей неделе не переписываем уже прошедшие дни.
        generate_week(db, wd, not_before=start if wd == mon else None)
    return len(weeks)


def apply_season(db: DB, key: str, start: date) -> tuple[str, int]:
    """Ставит сезонный пресет и пересобирает план с даты start.

    Возвращает (объяснение «почему такая неделя», сколько недель пересобрано).
    """
    if key not in SEASONS:
        raise ValueError(f"неизвестный сезон: {key}")
    db.replace_program(slots_for(key))
    db.set_setting(S_SEASON, key)
    db.set_setting(S_EDITED, "0")
    set_anchor(db, start)  # новый сезон — новый мезоцикл с первой недели
    weeks = regenerate_from(db, start)
    db.log_event("season_changed", {"season": key, "from": start.isoformat(),
                                    "weeks": weeks})
    return SEASONS[key]["why"], weeks


# ------------------------------------------------------------ правка дня
def day_slots(db: DB, weekday: int) -> list:
    return [s for s in db.get_program() if int(s["weekday"]) == weekday]


def remove_slot(db: DB, slot_id: int, start: date) -> tuple[bool, int]:
    slot = db.get_program_slot(slot_id)
    if not slot:
        return False, 0
    db.delete_program_slot(slot_id)
    mark_edited(db)
    db.log_event("program_slot_removed", {"title": slot["title"],
                                          "weekday": slot["weekday"]})
    return True, regenerate_from(db, start)


def add_slot(db: DB, weekday: int, category: str, title: str, kind: str,
             duration: int, time_hint: str | None, load: str,
             start: date) -> int:
    db.add_program_slot(weekday, category, title, kind, duration, time_hint, load)
    mark_edited(db)
    db.log_event("program_slot_added", {"title": title, "weekday": weekday})
    return regenerate_from(db, start)


def next_monday(d: date) -> date:
    return monday_of(d) + timedelta(days=7)
