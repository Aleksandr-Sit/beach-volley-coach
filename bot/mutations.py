"""Мутации плана + пересчёт недели. Общие для кнопок и свободного текста.

Каждая мутация: пишет событие (план vs факт) и запускает автопрегуляцию недели.
"""
from __future__ import annotations

from datetime import date, timedelta

from .db import DB
from .modules.schedule_sync import WEEKDAY_RU, autoregulate_week, monday_of


def _recompute(db: DB, ref_date: str) -> list[str]:
    return autoregulate_week(db, monday_of(date.fromisoformat(ref_date)))


def cancel_session(db: DB, sid: int) -> tuple[str, list[str]]:
    s = db.get_session(sid)
    if not s:
        return "Сессия не найдена.", []
    # Добавленную вручную сессию при отмене удаляем (не засоряем «Вернуть»);
    # штатную из расписания помечаем cancelled — её можно вернуть.
    if s["origin"] == "manual":
        db.delete_session(sid)
        db.log_event("session_deleted", {"sid": sid, "title": s["title"], "date": s["date"]})
        return f"🗑 Убрал: {s['title']} (добавляй заново при необходимости).", _recompute(db, s["date"])
    db.update_session(sid, status="cancelled")
    db.log_event("session_cancelled", {"sid": sid, "title": s["title"], "date": s["date"]})
    notes = _recompute(db, s["date"])
    msg = f"🌧 Отменил: {s['title']}."
    if s["category"] == "vb":
        msg += " Освободилось окно — если хочешь, добавлю лёгкий зал/мобильность."
    return msg, notes


def retime_session(db: DB, sid: int, new_time: str) -> tuple[str, list[str]]:
    s = db.get_session(sid)
    if not s:
        return "Сессия не найдена.", []
    db.update_session(sid, start_time=new_time, status="confirmed")
    db.log_event("session_retimed", {"sid": sid, "date": s["date"], "time": new_time})
    return f"🕐 Ок: {s['title']} теперь в {new_time}.", _recompute(db, s["date"])


def date_for_weekday(target_weekday: int, ref: date | None = None) -> str:
    """Дата указанного дня недели. Если он уже прошёл ИЛИ это сегодня — берём
    следующую неделю (перенос «на день» подразумевает другой день, не сегодня)."""
    ref = ref or date.today()
    target = monday_of(ref) + timedelta(days=target_weekday)
    if target <= ref:
        target += timedelta(days=7)
    return target.isoformat()


def move_session(db: DB, sid: int, target_date_iso: str,
                 new_time: str | None = None) -> tuple[str, list[str]]:
    s = db.get_session(sid)
    if not s:
        return "Сессия не найдена.", []
    db.update_session(sid, date=target_date_iso, start_time=new_time, status="planned")
    db.log_event("session_moved", {
        "sid": sid, "from": s["date"], "to": target_date_iso, "time": new_time,
    })
    notes = _recompute(db, target_date_iso)
    d = date.fromisoformat(target_date_iso)
    when = f" в {new_time}" if new_time else " (время уточним)"
    return f"📅 Перенёс: {s['title']} → {WEEKDAY_RU[d.weekday()]} {d.strftime('%d.%m')}{when}.", notes


def add_session(db: DB, target_date: str, title: str, category: str = "vb",
                kind: str | None = None, duration: int = 90,
                new_time: str | None = None) -> tuple[str, list[str]]:
    sid = db.add_session(
        date=target_date, start_time=new_time, category=category, title=title,
        kind=kind or ("game" if category == "vb" else "session"),
        duration_min=duration, load="moderate", status="planned", origin="manual",
    )
    db.log_event("session_added", {"sid": sid, "date": target_date, "title": title})
    d = date.fromisoformat(target_date)
    return f"➕ Добавил: {title} → {WEEKDAY_RU[d.weekday()]}.", _recompute(db, target_date)


def restore_session(db: DB, sid: int) -> tuple[str, list[str]]:
    s = db.get_session(sid)
    if not s:
        return "Сессия не найдена.", []
    db.update_session(sid, status="planned")
    db.log_event("session_restored", {"sid": sid, "title": s["title"], "date": s["date"]})
    return f"↩️ Вернул: {s['title']}.", _recompute(db, s["date"])


def confirm_all(db: DB, day: str) -> str:
    for s in db.sessions_for(day):
        db.update_session(s["id"], status="confirmed")
    db.log_event("day_confirmed", {"date": day})
    return "✅ Принято, день по плану. Хорошей тренировки!"


def format_result(msg: str, notes: list[str]) -> str:
    if notes:
        msg += "\n\nПересчёт недели:\n" + "\n".join(f"• {n}" for n in notes)
    return msg
