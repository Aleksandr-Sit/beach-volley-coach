"""Сборка экранов: (текст, клавиатура). Общее для команд, кнопок и пушей.

Слой между БД и обработчиками: обработчики только отправляют то, что здесь
собрано, — благодаря этому один и тот же экран одинаков в /today, в панели
снизу и в утреннем пуше.
"""
from __future__ import annotations

from datetime import date as date_st
from datetime import timedelta
from html import escape as escape_st

from .clock import today
from .db import DB
from .modules.schedule_sync import generate_week, monday_of
from .nutrition.targets import compute_targets
from .ui import DIVIDER as DIVIDER_ST
from .ui import (
    day_keyboard,
    nutrition_kb,
    render_day,
    render_nutrition,
    render_week,
    week_keyboard,
)


def day_view(db: DB):
    """План на сегодня + клавиатура (генерирует неделю, если её ещё нет)."""
    d = today()
    generate_week(db, d)
    sessions = db.sessions_for(d.isoformat())
    cancelled = db.cancelled_for(d.isoformat())
    return render_day(d, sessions, cancelled), day_keyboard(d, sessions, cancelled)


def week_view(db: DB):
    """Вся неделя + кнопки правки каждой сессии."""
    d = today()
    generate_week(db, d)
    mon = monday_of(d)
    sessions = [s for s in db.sessions_between(
        mon.isoformat(), (mon + timedelta(days=6)).isoformat())
        if s["status"] != "cancelled"]
    return render_week(d, sessions), week_keyboard(sessions)


def nutrition_view(db: DB):
    """Питание: цель от фактического веса, съеденное, добавки по дозам."""
    profile = db.get_profile() or {}
    weight = db.latest_weight()
    targets = compute_targets(profile, weight[1] if weight else None)
    d = today()
    supps = profile.get("supplements") or []
    counts = db.supplement_counts(d.isoformat())
    text = render_nutrition(d, targets, db.food_for(d.isoformat()), supps,
                            profile.get("supplement_gap"), counts,
                            weight=weight, trend=db.weight_trend())
    return text, nutrition_kb(supps, counts)


def weight_view(db: DB) -> str:
    """История замеров веса и тренд."""
    rows = db.weights_since((today() - timedelta(days=60)).isoformat())
    lines = ["⚖️ <b>Вес тела</b>", ""]
    if rows:
        for r in rows[-10:]:
            lines.append(f"   {r['date']} — <b>{r['kg']:g} кг</b>")
        trend = db.weight_trend()
        if trend is not None:
            arrow = "↗️" if trend > 0 else ("↘️" if trend < 0 else "→")
            lines.append(f"\n{arrow} <b>{trend:+g} кг</b> за месяц")
    else:
        lines.append("<i>Замеров пока нет.</i>")
    lines.append("\n<i>Записать: «вес 91.4» или кнопка ⚖️ в «Питание». "
                 "Взвешивайся утром натощак.</i>")
    return "\n".join(lines)


def program_view(db: DB):
    """Шаблон недели: сезон, цикл, что стоит на каждый день."""
    from .content.seasons import season
    from .modules.program import current_season, cycle_badge, season_title
    from .ui import program_kb, render_program

    s = season(current_season(db))
    return (render_program(season_title(db), s["period"],
                           cycle_badge(db, today()), db.get_program()),
            program_kb())


def status_view(db: DB, llm_ok: bool, llm_detail: str) -> str:
    """Самодиагностика: жив ли мозг, свежи ли данные, есть ли бэкап.

    Появилась после ревизии 09.09.2026: модель была мертва полтора месяца, и
    узнать об этом изнутри бота было нельзя.
    """
    import os

    from .activity import days_silent
    from .modules.program import cycle_badge, season_title

    d = today()
    lines = ["🩺 <b>Состояние</b>", DIVIDER_ST, ""]
    lines.append(f"{'🟢' if llm_ok else '🔴'} <b>Мозг:</b> {escape_st(llm_detail)}")
    if not llm_ok:
        lines.append("   <i>Расписание и тренировки работают без него.</i>")
    lines.append(f"🗓 <b>Программа:</b> {season_title(db)}")
    lines.append(f"   <i>{escape_st(cycle_badge(db, d))}</i>")

    silent = days_silent(db)
    lines.append(f"👋 <b>Последний заход:</b> "
                 f"{'сегодня' if silent == 0 else f'{silent} дн. назад'}")
    lines.append("")
    lines.append("📊 <b>Свежесть данных</b>")

    def _age(iso: str | None) -> str:
        if not iso:
            return "нет записей"
        try:
            n = (d - date_st.fromisoformat(iso[:10])).days
        except ValueError:
            return "нет записей"
        return "сегодня" if n == 0 else f"{n} дн. назад"

    w = db.latest_weight()
    last_ci = db.conn.execute(
        "SELECT MAX(date) m FROM checkins").fetchone()["m"]
    last_food = db.conn.execute(
        "SELECT MAX(date) m FROM food_log").fetchone()["m"]
    last_wo = db.conn.execute(
        "SELECT MAX(date) m FROM workout_log").fetchone()["m"]
    lines.append(f"   ⚖️ Вес — {_age(w[0]) if w else 'нет записей'}")
    lines.append(f"   💬 Чек-ин — {_age(last_ci)}")
    lines.append(f"   🍽 Еда — {_age(last_food)}")
    lines.append(f"   🏋️ Тренировка — {_age(last_wo)}")

    bdir = os.path.join(os.path.dirname(db.conn.execute(
        "PRAGMA database_list").fetchone()[2] or "data/coach.db"), "backups")
    try:
        files = sorted(f for f in os.listdir(bdir) if f.endswith(".db"))
    except OSError:
        files = []
    if files:  # пустой каталог не должен ронять экран IndexError-ом
        lines.append(f"\n💾 <b>Бэкап:</b> {len(files)} копий, свежая "
                     f"<code>{escape_st(files[-1])}</code>")
    else:
        lines.append("\n💾 <b>Бэкап:</b> <i>копий нет</i>")
    lines.append("\n<i>Программа не та? /program</i>")
    return "\n".join(lines)


def products_view(db: DB) -> str:
    """Список своих продуктов + подсказка формата."""
    foods = db.get_custom_foods()
    lines = ["🧾 <b>Свои продукты</b>", ""]
    if foods:
        for name, _keys, _bg, kcal, prot, ftype in foods:
            unit = "шт" if ftype == "count" else "100 г"
            lines.append(f"• {name} — {kcal:g} ккал, {prot:g} г белка / {unit}")
    else:
        lines.append("<i>Пока нет.</i>")
    lines.append("\n<b>Добавить:</b> напиши «добавь продукт: рис бурый 130 3» "
                 "(ккал и белок на 100 г; для штучного добавь «шт»).")
    return "\n".join(lines)
