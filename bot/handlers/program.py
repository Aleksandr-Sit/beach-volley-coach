"""Правка программы недели из Telegram: сезон, отдельный день, пересборка плана.

Причина существования модуля: до 09.09.2026 недельный шаблон был константой в
коде. Кончился пляжный сезон — и поправить программу можно было только правкой
файла с последующим деплоем. На практике это значит, что план просто расходится
с жизнью, а ботом перестают пользоваться.

Весь флоу на кнопках: набирать текст ради смены сезона никто не будет.
"""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from ..clock import today
from ..content.seasons import SEASONS, season, suggest_for_month
from ..db import DB
from ..modules.program import (
    add_slot,
    apply_season,
    current_season,
    day_slots,
    next_monday,
    regenerate_from,
    remove_slot,
)
from ..ui import (
    HINT_CODES,
    PROGRAM_TYPES,
    WEEKDAY_FULL,
    Cb,
    program_day_kb,
    program_hint_kb,
    program_type_kb,
    program_weekday_kb,
    regen_scope_kb,
    season_kb,
    season_scope_kb,
)
from ..views import program_view

router = Router(name="program")


def _scope_date(code: str):
    """«t» — с завтрашнего дня, «w» — со следующего понедельника."""
    return today() + timedelta(days=1) if code == "t" else next_monday(today())


def _weeks_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "неделю"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "недели"
    return "недель"


async def _show_program(cq: CallbackQuery, db: DB) -> None:
    text, kb = program_view(db)
    await cq.message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------- экран
@router.callback_query(Cb.filter(F.a == "prog"))
async def on_program(cq: CallbackQuery, db: DB) -> None:
    await cq.answer()
    await _show_program(cq, db)


# ---------------------------------------------------------------- сезон
@router.callback_query(Cb.filter(F.a == "season"))
async def on_season_list(cq: CallbackQuery, db: DB) -> None:
    await cq.answer()
    cur = current_season(db)
    hint = suggest_for_month(today().month)
    text = ["🔄 <b>Сменить сезон</b>", ""]
    for key, s in SEASONS.items():
        mark = " ✓ <i>сейчас</i>" if key == cur else (
            " 💡 <i>подходит по календарю</i>" if key == hint else "")
        text.append(f"{s['label']} · <i>{s['period']}</i>{mark}")
    text.append("")
    text.append("<i>Пресет — стартовая точка. После применения любой день "
                "правится кнопкой «Изменить день».</i>")
    await cq.message.answer("\n".join(text), reply_markup=season_kb(SEASONS, cur, hint))


@router.callback_query(Cb.filter(F.a == "seasonpick"))
async def on_season_pick(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    """Показываем, что именно изменится, и почему — до применения."""
    await cq.answer()
    key = callback_data.v or ""
    if key not in SEASONS:
        await cq.message.answer("Такого сезона нет — пришли /program.")
        return
    s = SEASONS[key]
    lines = [f"{s['label']} · <i>{s['period']}</i>", "", f"<i>{s['why']}</i>", ""]
    lines.append("<b>Неделя будет такой:</b>")
    by_day: dict[int, list[str]] = {}
    for wd, cat, title, _kind, dur, hint, _load in s["slots"]:
        icon = {"vb": "🏐", "gym": "🏋️", "recovery": "🧘"}.get(cat, "•")
        when = f", {hint}" if hint else ""
        by_day.setdefault(wd, []).append(f"{icon} {title} <i>({dur} мин{when})</i>")
    for wd in range(7):
        if wd in by_day:
            lines.append(f"   <b>{WEEKDAY_FULL[wd]}:</b> " + "; ".join(by_day[wd]))
        else:
            lines.append(f"   <b>{WEEKDAY_FULL[wd]}:</b> <i>свободно</i>")
    lines.append("")
    lines.append("<i>Прошедшие дни и записанные тренировки не тронутся. "
                 "Счётчик блока начнётся заново.</i>")
    await cq.message.answer("\n".join(lines), reply_markup=season_scope_kb(key))


@router.callback_query(Cb.filter(F.a == "seasonset"))
async def on_season_set(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer("Применяю")
    raw = (callback_data.v or "").split("|")
    if len(raw) != 2 or raw[0] not in SEASONS:
        await cq.message.answer("Не понял выбор — пришли /program.")
        return
    key, scope = raw
    start = _scope_date(scope)
    why, weeks = apply_season(db, key, start)
    await cq.message.answer(
        f"✅ <b>{season(key)['label']}</b> — с {start.strftime('%d.%m')}.\n"
        f"<i>{why}</i>\n\n"
        f"Собрал {weeks} {_weeks_word(weeks)} вперёд. Дальше недели строятся "
        "по новому сезону сами.\n"
        "<i>Счётчик блока пошёл заново: подсобка сменится через 4 недели.</i>")
    await _show_program(cq, db)


# ------------------------------------------------------------ правка дня
@router.callback_query(Cb.filter(F.a == "pday"))
async def on_pick_day(cq: CallbackQuery) -> None:
    await cq.answer()
    await cq.message.answer("✏️ <b>Какой день правим?</b>",
                            reply_markup=program_weekday_kb())


@router.callback_query(Cb.filter(F.a == "pdayedit"))
async def on_day_edit(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer()
    try:
        wd = int(callback_data.v or "0")
    except ValueError:
        wd = 0
    slots = day_slots(db, wd)
    lines = [f"✏️ <b>{WEEKDAY_FULL[wd]}</b>", ""]
    if slots:
        for s in slots:
            icon = {"vb": "🏐", "gym": "🏋️", "recovery": "🧘"}.get(s["category"], "•")
            lines.append(f"{icon} {s['title']} · <i>{s['duration_min']} мин</i>")
    else:
        lines.append("<i>Пусто — день свободный.</i>")
    lines.append("")
    lines.append("<i>Правка применится к будущим дням и пересоберёт план.</i>")
    await cq.message.answer("\n".join(lines), reply_markup=program_day_kb(wd, slots))


@router.callback_query(Cb.filter(F.a == "pdel"))
async def on_slot_delete(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    slot = db.get_program_slot(callback_data.sid)
    if not slot:
        await cq.answer("Уже убрано", show_alert=True)
        return
    wd = int(slot["weekday"])
    title = slot["title"]
    ok, weeks = remove_slot(db, callback_data.sid, today() + timedelta(days=1))
    await cq.answer("Убрал")
    if ok:
        await cq.message.answer(
            f"🗑 Убрал из программы: <b>{title}</b> ({WEEKDAY_FULL[wd]}).\n"
            f"<i>Пересобрал {weeks} {_weeks_word(weeks)} вперёд.</i>")
    await _show_program(cq, db)


@router.callback_query(Cb.filter(F.a == "padd"))
async def on_slot_add_type(cq: CallbackQuery, callback_data: Cb) -> None:
    await cq.answer()
    try:
        wd = int(callback_data.v or "0")
    except ValueError:
        wd = 0
    await cq.message.answer(
        f"➕ <b>{WEEKDAY_FULL[wd]}</b> — что добавить?",
        reply_markup=program_type_kb(wd))


@router.callback_query(Cb.filter(F.a == "paddtype"))
async def on_slot_add_hint(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer()
    parts = (callback_data.v or "").split("|")
    if len(parts) != 2 or parts[1] not in PROGRAM_TYPES:
        await cq.message.answer("Не понял тип — пришли /program.")
        return
    wd, tkey = int(parts[0]), parts[1]
    _label, cat, *_rest = PROGRAM_TYPES[tkey]

    # Инвариант «зал уступает волейболу» сильнее ручной правки: такой слот
    # просто не развернётся в план. Говорим об этом ДО добавления — иначе бот
    # ответил бы «добавил», а в расписании ничего бы не появилось.
    if cat in ("gym", "recovery"):
        vb_titles = [s["title"] for s in day_slots(db, wd) if s["category"] == "vb"]
        if vb_titles:
            await cq.message.answer(
                f"⛔️ В {WEEKDAY_FULL[wd].lower()} уже стоит волейбол: "
                f"<b>{'; '.join(vb_titles)}</b>.\n\n"
                "<i>Зал в день волейбола не ставится — это правило приоритета, "
                "иначе силовая съедает игру. Выбери свободный день или сначала "
                "убери отсюда волейбол.</i>",
                reply_markup=program_day_kb(wd, day_slots(db, wd)))
            return

    await cq.message.answer(
        f"🕐 Когда обычно? <i>({PROGRAM_TYPES[tkey][0]})</i>",
        reply_markup=program_hint_kb(wd, tkey))


@router.callback_query(Cb.filter(F.a == "paddhint"))
async def on_slot_add_done(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    parts = (callback_data.v or "").split("|")
    if len(parts) != 3 or parts[1] not in PROGRAM_TYPES:
        await cq.answer("Не понял выбор", show_alert=True)
        return
    wd, tkey, hcode = int(parts[0]), parts[1], parts[2]
    _label, cat, kind, title, dur, load = PROGRAM_TYPES[tkey]
    weeks = add_slot(db, wd, cat, title, kind, dur, HINT_CODES.get(hcode),
                     load, today() + timedelta(days=1))
    await cq.answer("Добавил")
    await cq.message.answer(
        f"➕ В программу: <b>{title}</b> — {WEEKDAY_FULL[wd]}.\n"
        f"<i>Пересобрал {weeks} {_weeks_word(weeks)} вперёд.</i>")
    await _show_program(cq, db)


# ------------------------------------------------------------ пересборка
@router.callback_query(Cb.filter(F.a == "regen"))
async def on_regen(cq: CallbackQuery) -> None:
    await cq.answer()
    await cq.message.answer(
        "♻️ <b>Пересобрать план из шаблона</b>\n\n"
        "<i>Вернёт будущие дни к программе: отменённое и передвинутое встанет "
        "на место. Записанные тренировки и добавленное вручную останутся.</i>",
        reply_markup=regen_scope_kb())


@router.callback_query(Cb.filter(F.a == "regenset"))
async def on_regen_set(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer("Пересобираю")
    start = _scope_date(callback_data.v or "t")
    weeks = regenerate_from(db, start)
    await cq.message.answer(
        f"♻️ Готово: {weeks} {_weeks_word(weeks)} с {start.strftime('%d.%m')}.")
    await _show_program(cq, db)
