"""Сборка экранов: (текст, клавиатура). Общее для команд, кнопок и пушей.

Слой между БД и обработчиками: обработчики только отправляют то, что здесь
собрано, — благодаря этому один и тот же экран одинаков в /today, в панели
снизу и в утреннем пуше.
"""
from __future__ import annotations

from datetime import timedelta

from .clock import today
from .db import DB
from .modules.schedule_sync import generate_week, monday_of
from .nutrition.targets import compute_targets
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
