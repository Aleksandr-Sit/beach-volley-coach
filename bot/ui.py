"""Рендер сообщений и inline-клавиатуры для утреннего пуша и edit-флоу."""
from __future__ import annotations

from datetime import date, timedelta
from html import escape
from urllib.parse import quote_plus

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .modules.schedule_sync import WEEKDAY_RU

CAT_ICON = {"vb": "🏐", "gym": "🏋️", "recovery": "🧘"}
LOAD_BADGE = {"heavy": "🔴 тяжёлая", "moderate": "🟡 средняя", "light": "🟢 лёгкая"}
DIVIDER = "➖➖➖➖➖➖➖➖➖➖"

WEEKDAY_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница",
                "Суббота", "Воскресенье"]
MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
             "августа", "сентября", "октября", "ноября", "декабря"]


def _full_date(d: date) -> str:
    return f"{WEEKDAY_FULL[d.weekday()]}, {d.day} {MONTHS_RU[d.month - 1]}"


def _time_sub(sess) -> str:
    if sess["start_time"]:
        return f"🕐 {sess['start_time']}"
    hint = (sess["time_hint"] or "") if "time_hint" in sess else ""
    if "утро" in hint:
        return "🕐 утром"
    if "вечер" in hint:
        return "🕐 вечером"
    return "🕐 время уточнить"


class Cb(CallbackData, prefix="s"):
    """Единая фабрика callback-данных. a=action, sid=session id, v=доп. значение.

    v — Optional: при пустом значении aiogram распаковывает хвост как None,
    поэтому тип должен допускать None (иначе pydantic роняет обработчик).
    """
    a: str
    sid: int = 0
    v: str | None = None


def _session_sub(s) -> str:
    """Подстрока деталей сессии: время · нагрузка · длительность."""
    parts = [_time_sub(s)]
    show_load = s["category"] in ("gym", "recovery") or s["kind"] == "game"
    if show_load and s["load"] in LOAD_BADGE:
        parts.append(LOAD_BADGE[s["load"]])
    if s["duration_min"]:
        parts.append(f"{s['duration_min']} мин")
    return " · ".join(parts)


def render_day(d: date, sessions: list, cancelled: list | None = None) -> str:
    lines = [f"🗓 <b>{_full_date(d)}</b>", ""]
    if sessions:
        for s in sessions:
            icon = CAT_ICON.get(s["category"], "•")
            lines.append(f"{icon} <b>{escape(s['title'])}</b>")
            lines.append(f"   <i>{_session_sub(s)}</i>")
            lines.append("")
        lines.append(DIVIDER)
        lines.append("💬 <b>Как самочувствие?</b>")
    else:
        lines.append("😌 <b>Сегодня свободно</b>")
        lines.append("<i>Отдых или лёгкая мобильность — восстановление тоже работа.</i>")
    if cancelled:
        names = ", ".join(escape(s["title"]) for s in cancelled)
        lines.append(f"\n🌧 <i>Отменено:</i> {names} — можно вернуть кнопкой ниже.")
    return "\n".join(lines).strip()


def day_keyboard(d: date, sessions: list, cancelled: list | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    ds = d.isoformat()
    kb.button(text="😀 Свежий", callback_data=Cb(a="ci", v=f"fresh|{ds}"))
    kb.button(text="😐 Норм", callback_data=Cb(a="ci", v=f"ok|{ds}"))
    kb.button(text="😩 Устал", callback_data=Cb(a="ci", v=f"tired|{ds}"))
    kb.adjust(3)
    sizes = [3]
    if sessions:
        kb.button(text="✅ Всё по плану", callback_data=Cb(a="allok", v=ds))
        kb.button(text="📋 Упражнения сегодня", callback_data=Cb(a="wtoday", v=ds))
        sizes += [1, 1]
        for s in sessions:
            kb.button(text=f"✏️ Изменить: {s['title']}", callback_data=Cb(a="menu", sid=s["id"]))
            sizes.append(1)
    for s in (cancelled or []):
        kb.button(text=f"↩️ Вернуть: {s['title']}", callback_data=Cb(a="restore", sid=s["id"]))
        sizes.append(1)
    kb.button(text="➕ Добавить тренировку", callback_data=Cb(a="add"))
    sizes.append(1)
    kb.adjust(*sizes)
    return kb.as_markup()


def render_week(d: date, sessions: list) -> str:
    from .modules.schedule_sync import monday_of
    mon = monday_of(d)
    sun = mon + timedelta(days=6)
    if mon.month == sun.month:
        rng = f"{mon.day}–{sun.day} {MONTHS_RU[sun.month - 1]}"
    else:
        rng = f"{mon.day} {MONTHS_RU[mon.month - 1]} – {sun.day} {MONTHS_RU[sun.month - 1]}"
    head = f"🗓 <b>Неделя {rng}</b>"
    if not sessions:
        return head + "\n\n<i>Пока пусто.</i>"
    lines = [head, ""]
    cur = None
    for s in sessions:
        sd = date.fromisoformat(s["date"])
        if sd != cur:
            cur = sd
            if lines[-1] != "":
                lines.append("")
            lines.append(f"<b>{WEEKDAY_FULL[sd.weekday()]}</b> · {sd.strftime('%d.%m')}")
        icon = CAT_ICON.get(s["category"], "•")
        tm = s["start_time"] or "—"
        lines.append(f"   {icon} <b>{tm}</b> · {escape(s['title'])}")
    lines.append(f"\n{DIVIDER}")
    lines.append("<i>Нажми «Изменить» под нужной сессией ниже.</i>")
    return "\n".join(lines)


def _bar(cur: float, target: float, width: int = 10) -> str:
    if target <= 0:
        return ""
    filled = max(0, min(width, round(width * cur / target)))
    return "▓" * filled + "░" * (width - filled)


def render_nutrition(d: date, targets: dict, foods: list,
                     supplements: list | None = None, gap: str | None = None,
                     counts: dict | None = None,
                     weight: tuple | None = None, trend: float | None = None) -> str:
    counts = counts or {}
    eaten_k = sum((f["kcal"] or 0) for f in foods)
    eaten_p = sum((f["protein"] or 0) for f in foods)
    lines = [f"🍽 <b>Питание</b> · <i>{_full_date(d)}</i>",
             f"<i>режим: {targets['mode']}</i>", DIVIDER]
    if weight:
        w_date, w_kg = weight
        trend_s = ""
        if trend is not None:
            arrow = "↗️" if trend > 0 else ("↘️" if trend < 0 else "→")
            trend_s = f" · {arrow} {trend:+g} кг за месяц"
        lines.append(f"⚖️ <b>{w_kg:g} кг</b> <i>({w_date}){trend_s}</i>")
        lines.append("")
    lines.append("🎯 <b>Цель на день</b>")
    lines.append(f"   🔥 {targets['kcal']} ккал · 🥩 {targets['protein']} г белка")
    lines.append(f"   🧈 жиры {targets['fat']} г · 🍚 углеводы ~{targets['carbs']} г")
    lines.append("")
    lines.append("📥 <b>Съедено сегодня</b>")
    lines.append(f"   🔥 {round(eaten_k)} / {targets['kcal']} ккал")
    lines.append(f"   <code>{_bar(eaten_k, targets['kcal'])}</code>")
    lines.append(f"   🥩 {round(eaten_p)} / {targets['protein']} г белка")
    lines.append(f"   <code>{_bar(eaten_p, targets['protein'])}</code>")
    if foods:
        lines.append("")
        lines.append("<b>Приёмы</b>")
        for f in foods:
            lines.append(f"   • {escape(f['text'])} — ≈{round(f['kcal'])} ккал, "
                         f"{round(f['protein'])} г белка")
    left_p = targets["protein"] - eaten_p
    if foods and left_p > 25:
        lines.append(f"\n<i>Добери ещё ~{round(left_p)} г белка — это индейка/творог/"
                     "яйца в следующий приём.</i>")
    lines.append(f"\n{DIVIDER}")
    if supplements:
        lines.append("💊 <b>Добавки сегодня</b>")
        for s in supplements:
            name = s.get("name", "")
            target = max(1, int(s.get("doses", 1)))
            c = counts.get(name, 0)
            timing = f" · {escape(s['timing'])}" if s.get("timing") else ""
            if target > 1:
                filled = min(target, c)
                bar = "▓" * filled + "░" * (target - filled)
                lines.append(f"   {bar} {escape(name)} — {c}/{target}{timing}")
            else:
                mark = "✅" if c >= 1 else "⬜"
                lines.append(f"   {mark} {escape(name)} {escape(s.get('dose',''))}{timing}")
    if gap:
        lines.append(f"⚠️ <i>{escape(gap)}</i>")
    lines.append("\n<i>Вес: мясо — сырое (как на упаковке), гарнир — готовый, "
                 "овсянка — сухая. Свои продукты: /product. Оценка ≈.</i>")
    return "\n".join(lines).strip()


def nutrition_kb(supplements: list | None = None,
                 counts: dict | None = None) -> InlineKeyboardMarkup:
    supplements = supplements or []
    counts = counts or {}
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить приём", callback_data=Cb(a="food_add"))
    kb.button(text="↩️ Убрать последний", callback_data=Cb(a="food_undo"))
    kb.button(text="⚖️ Записать вес", callback_data=Cb(a="weight_add"))
    for i, s in enumerate(supplements):
        name = s.get("name", "")
        target = max(1, int(s.get("doses", 1)))
        c = counts.get(name, 0)
        kb.button(text=f"💊 {name} {c}/{target}", callback_data=Cb(a="supp", v=str(i)))
    kb.button(text="◀️ План на сегодня", callback_data=Cb(a="today_btn"))
    kb.adjust(1)
    return kb.as_markup()


def week_keyboard(sessions: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in sessions:
        sd = date.fromisoformat(s["date"])
        kb.button(text=f"✏️ {WEEKDAY_RU[sd.weekday()]}: {s['title']}",
                  callback_data=Cb(a="menu", sid=s["id"]))
    kb.adjust(1)
    return kb.as_markup()


# Типы тренировок для «Добавить»: key -> (label, category, kind, title, duration)
ADD_TYPES = {
    "match": ("🏐 Игровая (с тренером)", "vb", "game", "Игровая с тренером", 105),
    "game": ("🏖 Игра (свободная)", "vb", "game", "Игра", 120),
    "pers": ("👤 Персональная (техника)", "vb", "technique", "Персональная (техника)", 60),
    "group": ("👥 Групповая", "vb", "game", "Групповая", 90),
    "gym": ("🏋️ Зал", "gym", "session", "Зал", 60),
}


def add_type_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, (label, *_rest) in ADD_TYPES.items():
        kb.button(text=label, callback_data=Cb(a="addtype", v=key))
    kb.adjust(1)
    return kb.as_markup()


def add_day_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📍 Сегодня", callback_data=Cb(a="addday", v="t"))
    for i, name in enumerate(WEEKDAY_RU):
        kb.button(text=name, callback_data=Cb(a="addday", v=str(i)))
    kb.adjust(1, 4, 3)
    return kb.as_markup()


def session_menu_kb(sid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Упражнения", callback_data=Cb(a="workout", sid=sid))
    kb.button(text="📝 Записать результат", callback_data=Cb(a="log", sid=sid))
    kb.button(text="✅ Состоится", callback_data=Cb(a="confirm", sid=sid))
    kb.button(text="🌧 Отменить", callback_data=Cb(a="cancel", sid=sid))
    kb.button(text="🕐 Время / длительность", callback_data=Cb(a="retime", sid=sid))
    kb.button(text="📅 Перенести на день", callback_data=Cb(a="moveday", sid=sid))
    kb.adjust(1)
    return kb.as_markup()


def weekday_kb(sid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📍 Сегодня", callback_data=Cb(a="setday", sid=sid, v="t"))
    for i, name in enumerate(WEEKDAY_RU):
        kb.button(text=name, callback_data=Cb(a="setday", sid=sid, v=str(i)))
    kb.adjust(1, 4, 3)
    return kb.as_markup()


def back_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ План на сегодня", callback_data=Cb(a="today_btn"))
    return kb.as_markup()


def glossary_kb(query: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    url = "https://www.youtube.com/results?search_query=" + quote_plus(query + " техника упражнение")
    kb.button(text="▶️ Видео как делать", url=url)
    kb.button(text="◀️ План на сегодня", callback_data=Cb(a="today_btn"))
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Применить", callback_data=Cb(a="apply"))
    kb.button(text="✖️ Отмена", callback_data=Cb(a="drop"))
    kb.adjust(2)
    return kb.as_markup()
