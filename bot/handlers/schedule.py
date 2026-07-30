"""Обработчики расписания: check-in, edit-флоу (кнопки) и свободный текст."""
from __future__ import annotations

import re
from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..clock import today as _today
from ..coach import advise, parse_edit
from ..content.exercises import match_exercise, normalize_log
from ..content.glossary import lookup as glossary_lookup
from ..content.workout import build_workout
from ..db import DB
from ..intent import parse_local, parse_time_and_duration
from ..llm.client import LLMClient
from ..modules.weekly_adapt import is_deload_week
from ..mutations import (
    add_session,
    cancel_session,
    confirm_all,
    date_for_weekday,
    format_result,
    move_session,
    restore_session,
    retime_session,
)
from ..states import Flow
from ..ui import (
    ADD_TYPES,
    Cb,
    add_day_kb,
    add_type_kb,
    back_kb,
    confirm_kb,
    day_keyboard,
    glossary_kb,
    render_day,
    session_menu_kb,
    weekday_kb,
)

router = Router()

_HOWTO_MARK = ("как ", "что такое", "что за", "объясни", "покажи", "техник",
               "упражнен", "делать")
_EDIT_MARK = ("отмен", "перенес", "перенос", "дожд", "добав", "сдвин", "подвин")


_EDIT_HINTS = ("отмен", "перенес", "перенос", "сдвин", "подвин", "дожд", "добав",
               "поставь", "запиш", "не буд", "не пойд", "пропущ", "заболел",
               "болею", "отдыха", "завтра", "сегодня", "послезавтра",
               "понедельник", "вторник", "сред", "четверг", "пятниц", "суббот",
               "воскресен", "тренировк", "игр", "зал")


def _looks_like_edit(text: str) -> bool:
    """Стоит ли тратить вызов LLM на разбор правки расписания."""
    t = text.lower()
    has_hint = any(w in t for w in _EDIT_HINTS)
    has_time = bool(re.search(r"\d{1,2}\s*[:.;]\s*\d{2}|\bв\s*\d{1,2}\b", t))
    return has_hint or has_time


def _is_howto(text: str) -> bool:
    """Похоже ли сообщение на вопрос об упражнении/технике (для видео-фолбэка)."""
    t = text.lower().strip()
    if any(m in t for m in _HOWTO_MARK):
        return True
    # короткая фраза без команд-правок — вероятно название упражнения
    return len(t.split()) <= 4 and not any(w in t for w in _EDIT_MARK)


async def show_day(target: Message, db: DB) -> None:
    """Показать (вернуть) план на сегодня с основной клавиатурой — чтобы не терялся.
    Показывает и отменённые сегодня сессии с кнопкой «Вернуть»."""
    today = _today()
    sessions = db.sessions_for(today.isoformat())
    cancelled = db.cancelled_for(today.isoformat())
    await target.answer(render_day(today, sessions, cancelled),
                        reply_markup=day_keyboard(today, sessions, cancelled))



# ---------- check-in ----------
@router.callback_query(Cb.filter(F.a == "ci"))
async def on_checkin(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    readiness, ds = callback_data.v.split("|", 1)
    db.add_checkin(ds, readiness=readiness)
    notes = []
    if readiness == "tired":
        from ..modules.schedule_sync import autoregulate_week, monday_of
        notes = autoregulate_week(db, monday_of(date.fromisoformat(ds)))
    txt = {"fresh": "Отлично, работаем в полную силу.",
           "ok": "Принято, идём по плану.",
           "tired": "Понял, ты устал — сегодня снижаю нагрузку, не геройствуй."
           }.get(readiness, "Принято.")
    if notes:
        txt += "\n" + "\n".join(f"• {n}" for n in notes)
    await cq.answer("Записал самочувствие")
    await cq.message.answer(txt)


# ---------- «всё по плану» ----------
@router.callback_query(Cb.filter(F.a == "allok"))
async def on_allok(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer()
    await cq.message.answer(confirm_all(db, callback_data.v))


# ---------- меню сессии ----------
@router.callback_query(Cb.filter(F.a == "menu"))
async def on_menu(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer()  # сразу гасим «часики» на кнопке
    s = db.get_session(callback_data.sid)
    if not s:
        await cq.message.answer("Эта сессия уже неактуальна (перегенерён план?). Пришли /today.")
        return
    await cq.message.answer(f"{s['title']} — что делаем?",
                            reply_markup=session_menu_kb(callback_data.sid))


def _workout_text(db: DB, s) -> str:
    last = db.last_workout_log(s["category"], s["kind"] or "")
    return build_workout(s, last_log=last, weights=db.get_weights(),
                         deload=is_deload_week(db))


@router.callback_query(Cb.filter(F.a == "today_btn"))
async def on_today_btn(cq: CallbackQuery, db: DB) -> None:
    await cq.answer()
    await show_day(cq.message, db)


@router.callback_query(Cb.filter(F.a == "workout"))
async def on_workout(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer()
    s = db.get_session(callback_data.sid)
    if not s:
        await cq.message.answer("Сессия не найдена — пришли /today.")
        return
    await cq.message.answer(_workout_text(db, s), reply_markup=back_kb())


@router.callback_query(Cb.filter(F.a == "wtoday"))
async def on_wtoday(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    await cq.answer()
    sessions = db.sessions_for(_today().isoformat())
    if not sessions:
        await cq.message.answer("Сегодня тренировок нет.")
        return
    for i, s in enumerate(sessions):
        kb = back_kb() if i == len(sessions) - 1 else None
        await cq.message.answer(_workout_text(db, s), reply_markup=kb)


@router.callback_query(Cb.filter(F.a == "log"))
async def on_log(cq: CallbackQuery, callback_data: Cb, state: FSMContext, db: DB) -> None:
    s = db.get_session(callback_data.sid)
    if not s:
        await cq.answer("Сессия не найдена", show_alert=True)
        return
    await state.update_data(log_sid=callback_data.sid)
    await state.set_state(Flow.wait_log)
    await cq.answer()
    await cq.message.answer(
        f"Что сделал на «{s['title']}»? Напиши как удобно, например:\n"
        "«присед 100 5×5, трап 100 3×6, плечо ок» или «игра 1.5ч, чувствовал легко»."
    )


@router.message(Flow.wait_log)
async def got_log(msg: Message, state: FSMContext, db: DB) -> None:
    data = await state.get_data()
    await state.clear()
    s = db.get_session(data["log_sid"])
    normalized = normalize_log(msg.text.strip()) or msg.text.strip()
    if s:
        db.add_workout_log(s["id"], s["category"], s["kind"] or "", s["date"], normalized)
        db.update_session(s["id"], status="done")
    await msg.answer(f"📝 <b>Записал:</b> {escape(normalized)}\n"
                     "<i>В следующий раз покажу как ориентир — будешь прогрессировать.</i>")
    await show_day(msg, db)


@router.callback_query(Cb.filter(F.a == "confirm"))
async def on_confirm(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    db.update_session(callback_data.sid, status="confirmed")
    await cq.answer("Ок")
    await cq.message.answer("✅ Оставил по плану.")
    await show_day(cq.message, db)


@router.callback_query(Cb.filter(F.a == "cancel"))
async def on_cancel(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    msg, notes = cancel_session(db, callback_data.sid)
    await cq.answer()
    await cq.message.answer(format_result(msg, notes))
    await show_day(cq.message, db)


@router.callback_query(Cb.filter(F.a == "restore"))
async def on_restore(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    res, notes = restore_session(db, callback_data.sid)
    await cq.answer()
    await cq.message.answer(format_result(res, notes))
    await show_day(cq.message, db)


@router.callback_query(Cb.filter(F.a == "retime"))
async def on_retime(cq: CallbackQuery, callback_data: Cb, state: FSMContext) -> None:
    await state.update_data(sid=callback_data.sid)
    await state.set_state(Flow.wait_retime)
    await cq.answer()
    await cq.message.answer("Во сколько? Можно с длительностью: «18:00 90» "
                            "(или просто «18:00»).")


@router.message(Flow.wait_retime)
async def got_retime(msg: Message, state: FSMContext, db: DB) -> None:
    tm, dur = parse_time_and_duration(msg.text)
    if not tm and not dur:
        await msg.answer("Не понял. Время — 18:30 (или 18, 18 30). "
                         "С длительностью — «18:00 90» / «18:00 2ч». "
                         "Можно только длительность — «90» / «2ч».")
        return  # остаёмся в состоянии — ждём корректный ввод
    data = await state.get_data()
    await state.clear()
    sid = data["sid"]
    if dur:
        db.update_session(sid, duration_min=dur)
    if tm:
        res, notes = retime_session(db, sid, tm)
    else:
        s = db.get_session(sid)
        res, notes = f"🕐 Ок: {s['title']} — длительность обновил.", []
    if dur:
        res += f" Длительность: {dur} мин."
    await msg.answer(format_result(res, notes))
    await show_day(msg, db)


@router.callback_query(Cb.filter(F.a == "moveday"))
async def on_moveday(cq: CallbackQuery, callback_data: Cb) -> None:
    await cq.answer()
    await cq.message.answer("На какой день перенести?",
                            reply_markup=weekday_kb(callback_data.sid))


@router.callback_query(Cb.filter(F.a == "setday"))
async def on_setday(cq: CallbackQuery, callback_data: Cb, state: FSMContext) -> None:
    v = callback_data.v
    target = _today().isoformat() if v == "t" else date_for_weekday(int(v))
    await state.update_data(sid=callback_data.sid, target_date=target)
    await state.set_state(Flow.wait_moveday_time)
    await cq.answer()
    await cq.message.answer("Во сколько? Можно с длительностью «18:00 90» "
                            "(или напиши «уточню позже»).")


@router.message(Flow.wait_moveday_time)
async def got_moveday_time(msg: Message, state: FSMContext, db: DB) -> None:
    txt = msg.text.strip().lower()
    dur = None
    if "позже" in txt or "уточ" in txt:
        new_time = None
    else:
        new_time, dur = parse_time_and_duration(msg.text)
        if not new_time:
            await msg.answer("Не понял время. Напиши как 18:30 (или 18, 18 30), "
                             "с длительностью «18:00 90», либо «уточню позже».")
            return  # остаёмся в состоянии
    data = await state.get_data()
    await state.clear()
    if dur:
        db.update_session(data["sid"], duration_min=dur)
    res, notes = move_session(db, data["sid"], data["target_date"], new_time)
    if dur:
        res += f" Длительность: {dur} мин."
    if new_time is None:
        res += " Время задашь позже: /week → выбери эту сессию."
    await msg.answer(format_result(res, notes))
    await show_day(msg, db)


# ---------- добавить тренировку (выбор типа → день → время) ----------
@router.callback_query(Cb.filter(F.a == "add"))
async def on_add(cq: CallbackQuery) -> None:
    await cq.answer()
    await cq.message.answer("Какую тренировку добавить?", reply_markup=add_type_kb())


@router.callback_query(Cb.filter(F.a == "addtype"))
async def on_addtype(cq: CallbackQuery, callback_data: Cb, state: FSMContext) -> None:
    if callback_data.v not in ADD_TYPES:
        await cq.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(add_type=callback_data.v)
    await cq.answer()
    await cq.message.answer("На какой день?", reply_markup=add_day_kb())


@router.callback_query(Cb.filter(F.a == "addday"))
async def on_addday(cq: CallbackQuery, callback_data: Cb, state: FSMContext) -> None:
    v = callback_data.v
    target = _today().isoformat() if v == "t" else date_for_weekday(int(v))
    await state.update_data(add_date=target)
    await state.set_state(Flow.wait_add_time)
    await cq.answer()
    await cq.message.answer("Во сколько? Можно с длительностью «18:00 90» "
                            "(или «уточню позже»).")


@router.message(Flow.wait_add_time)
async def got_add_time(msg: Message, state: FSMContext, db: DB) -> None:
    txt = msg.text.strip().lower()
    data = await state.get_data()
    label, category, kind, title, default_dur = ADD_TYPES[data["add_type"]]
    if "позже" in txt or "уточ" in txt:
        new_time, dur = None, None
    else:
        new_time, dur = parse_time_and_duration(msg.text)
        if not new_time:
            await msg.answer("Не понял время. Напиши как 18:30, с длительностью "
                             "«18:00 90», либо «уточню позже».")
            return
    await state.clear()
    res, notes = add_session(db, data["add_date"], title, category, kind,
                             duration=dur or default_dur, new_time=new_time)
    await msg.answer(format_result(res, notes))
    await show_day(msg, db)


# ---------- свободный текст: правка через LLM с подтверждением ----------
@router.message(Flow.confirm_edit)
async def confirm_edit_text(msg: Message, state: FSMContext) -> None:
    # На этом шаге ждём нажатия кнопок Применить/Отмена, а не текста.
    await msg.answer("Нажми «Применить» или «Отмена» под предыдущим сообщением.")


@router.callback_query(Cb.filter(F.a == "apply"))
async def on_apply(cq: CallbackQuery, state: FSMContext, db: DB) -> None:
    data = await state.get_data()
    intent = data.get("intent")
    await state.clear()
    if not intent:
        await cq.answer("Нечего применять", show_alert=True)
        return
    await cq.answer()
    res, notes = _apply_intent(db, intent)
    await cq.message.answer(format_result(res, notes))
    await show_day(cq.message, db)


@router.callback_query(Cb.filter(F.a == "drop"))
async def on_drop(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cq.answer("Отменено")
    await cq.message.answer("Ок, ничего не менял.")


def _target_date(intent: dict) -> str | None:
    """Явная дата из intent, либо вычисленная из target_weekday (ближайший день)."""
    if intent.get("target_date"):
        return intent["target_date"]
    wd = intent.get("target_weekday")
    return date_for_weekday(int(wd)) if wd is not None else None


def _apply_intent(db: DB, intent: dict):
    action = intent.get("action")
    sid = intent.get("session_id")
    tt = intent.get("target_time")
    td = _target_date(intent)
    if action == "cancel" and sid:
        return cancel_session(db, int(sid))
    if action == "retime" and sid and tt:
        return retime_session(db, int(sid), tt)
    if action == "move" and sid and td:
        return move_session(db, int(sid), td, tt)
    if action == "add" and td:
        return add_session(db, td, intent.get("new_title") or "Тренировка",
                           intent.get("new_category") or "vb", new_time=tt)
    return "Не понял правку — уточни, пожалуйста (или пользуйся кнопками).", []


# Свободный текст (не в FSM): сначала детерминированный разбор, затем LLM, затем совет.
@router.message(F.text & ~F.text.startswith("/"))
async def free_text(msg: Message, state: FSMContext, db: DB, llm: LLMClient) -> None:
    today = _today()
    # 1) Быстрый детерминированный разбор частых правок — без LLM, без квоты.
    intent = parse_local(db, today, msg.text)
    # 2) Фолбэк на LLM — только если текст ВООБЩЕ похож на правку расписания.
    #    Раньше модель звалась на каждое сообщение (плюс ещё раз в advise) и
    #    вдвое быстрее выжигала бесплатную квоту.
    if intent is None and _looks_like_edit(msg.text):
        intent = parse_edit(llm, db, today, msg.text) or {}
    intent = intent or {}

    if intent.get("action") in ("cancel", "move", "retime", "add"):
        await state.update_data(intent=intent)
        await state.set_state(Flow.confirm_edit)
        human = intent.get("human") or "Внести правку в план?"
        await msg.answer(f"Понял так: <b>{escape(human)}</b>\nПрименяю?",
                         reply_markup=confirm_kb())
        return

    # 2.5) Глоссарий: объяснение упражнения/термина без LLM (0 квоты, надёжно).
    g = glossary_lookup(msg.text)
    if g:
        await msg.answer(g, reply_markup=glossary_kb(msg.text))
        return

    # 2.6) Название/вставка упражнения из программы → объяснение + видео.
    canon = match_exercise(msg.text) if len(msg.text.split()) <= 10 else None
    if canon:
        g2 = glossary_lookup(canon)
        await msg.answer(g2 or f"«{canon}» — посмотри технику на видео:",
                         reply_markup=glossary_kb(canon))
        return

    # 3) Не правка — совет тренера (LLM), с фолбэком на видео для «как делать».
    howto = _is_howto(msg.text)
    reply = advise(llm, db, msg.text)
    if reply and not reply.startswith("[LLM"):
        await msg.answer(escape(reply),
                         reply_markup=glossary_kb(msg.text) if howto else back_kb())
        return
    # LLM недоступен (квота/сеть)
    if howto:
        await msg.answer("Точного описания не нашёл, но вот видео по твоему запросу:",
                         reply_markup=glossary_kb(msg.text))
    else:
        await msg.answer("Пока не понял. Правку можно кнопками под планом, или проще: "
                         "«дождь, отменили», «перенеси на завтра», «в 18:30».",
                         reply_markup=back_kb())
