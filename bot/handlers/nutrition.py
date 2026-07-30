"""Питание: приёмы пищи, вес тела, добавки, свои продукты.

Текстовые сокращения («вес 91.4», «добавь продукт: …») ловятся фильтрами
роутера, а не разбором внутри общего free_text — поэтому подключается ДО
роутера расписания.
"""
from __future__ import annotations

import re
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..clock import today
from ..coach import estimate_food
from ..db import DB
from ..llm.client import LLMClient
from ..nutrition.foods import estimate_meal, parse_manual, parse_product
from ..states import Flow
from ..ui import Cb
from ..views import nutrition_view

router = Router(name="nutrition")

WEIGHT_RE = re.compile(
    r"^\s*(?:вес|взвес\w*|weight)\D{0,12}(\d{2,3}(?:[.,]\d)?)\s*(?:кг)?\s*$", re.I)
PRODUCT_RE = re.compile(r"добав\w*\s+продукт|^\s*продукт[:\s]", re.I)
MIN_KG, MAX_KG = 40, 200


async def _show_nutrition(msg: Message, db: DB) -> None:
    text, kb = nutrition_view(db)
    await msg.answer(text, reply_markup=kb)


async def _save_weight(msg: Message, db: DB, kg: float) -> None:
    db.add_weight(today().isoformat(), kg)
    trend = db.weight_trend()
    note = f" Динамика за месяц: <b>{trend:+g} кг</b>." if trend else ""
    await msg.answer(f"⚖️ Записал: <b>{kg:g} кг</b>.{note}")
    await _show_nutrition(msg, db)


# ---------------------------------------------------------------- вес тела
@router.callback_query(Cb.filter(F.a == "weight_add"))
async def on_weight_add(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.wait_weight)
    await cq.answer()
    await cq.message.answer(
        "Какой вес? Напиши число, например <b>91.4</b>.\n"
        "<i>Взвешивайся утром натощак после туалета — так замеры сопоставимы.</i>")


@router.message(Flow.wait_weight)
async def got_weight(msg: Message, state: FSMContext, db: DB) -> None:
    m = re.search(r"\d{2,3}(?:[.,]\d)?", msg.text or "")
    if not m:
        await msg.answer("Не понял вес. Напиши числом, например 91.4.")
        return  # остаёмся в состоянии
    kg = float(m.group(0).replace(",", "."))
    if not MIN_KG <= kg <= MAX_KG:
        await msg.answer(f"Похоже на опечатку — вес должен быть {MIN_KG}–{MAX_KG} кг.")
        return
    await state.clear()
    await _save_weight(msg, db, kg)


@router.message(F.text.regexp(WEIGHT_RE))
async def weight_shortcut(msg: Message, db: DB) -> None:
    """Быстрая запись без диалога: «вес 91.4»."""
    kg = float(WEIGHT_RE.match(msg.text).group(1).replace(",", "."))
    if MIN_KG <= kg <= MAX_KG:
        await _save_weight(msg, db, kg)


# ------------------------------------------------------------- приёмы пищи
@router.callback_query(Cb.filter(F.a == "food_add"))
async def on_food_add(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.wait_food)
    await cq.answer()
    await cq.message.answer(
        "Что съел? Напиши приём, например:\n"
        "«овсянка 60, банан, 3 яйца» или «обед: рис 200, индейка 150».\n"
        "Блюдо из кафе — можно указать калории из меню: "
        "«панкейки 240г 500 ккал» (и «25 белка», если знаешь).\n\n"
        "<i>Вес: <b>мясо — сырое</b> (как на упаковке), гарнир — <b>готовый</b> "
        "(на тарелке), овсянка — <b>сухая</b> крупа.</i>")


@router.message(Flow.wait_food)
async def got_food(msg: Message, state: FSMContext, db: DB, llm: LLMClient) -> None:
    await state.clear()
    text = msg.text.strip()
    k, p, matched = estimate_meal(text, extra=db.get_custom_foods())
    manual_k, manual_p = parse_manual(text)
    src = "staples" if matched else None
    # Ручные числа ДОБАВЛЯЮТСЯ к распознанному из базы (блюдо в общем списке).
    if manual_k is not None:
        k += manual_k
        src = "manual"
    if manual_p is not None:
        p += manual_p
    # Ничего не распознали и чисел нет → оценка через ИИ.
    if manual_k is None and not matched:
        est = estimate_food(llm, text)
        k, p = est["kcal"], est["protein"]
        src = "llm" if (k or p) else None

    db.add_food(today().isoformat(), text, k, p)
    note = {"manual": " <i>(вкл. твои числа)</i>", "llm": " <i>(оценка ИИ)</i>"}
    extra = note.get(src, "") if src else \
        " <i>(не распознал — укажи ккал: «… 500 ккал» или состав)</i>"
    await msg.answer(f"🍽 <b>Добавил:</b> {escape(text)} — "
                     f"≈{round(k)} ккал, {round(p)} г белка{extra}")
    await _show_nutrition(msg, db)


@router.callback_query(Cb.filter(F.a == "food_undo"))
async def on_food_undo(cq: CallbackQuery, db: DB) -> None:
    ok = db.delete_last_food(today().isoformat())
    await cq.answer("Убрал" if ok else "Нечего убирать")
    await _show_nutrition(cq.message, db)


# ---------------------------------------------------------------- добавки
@router.callback_query(Cb.filter(F.a == "supp"))
async def on_supp(cq: CallbackQuery, callback_data: Cb, db: DB) -> None:
    """Тап = +1 доза; на максимуме следующий тап сбрасывает (для правок)."""
    supps = (db.get_profile() or {}).get("supplements") or []
    try:
        s = supps[int(callback_data.v)]
    except (ValueError, IndexError, TypeError):
        await cq.answer("Не найдено", show_alert=True)
        return
    name = s.get("name", "")
    target = max(1, int(s.get("doses", 1)))
    d = today().isoformat()
    taken = db.supplement_counts(d).get(name, 0)
    if taken < target:
        db.take_supplement(d, name)
        await cq.answer(f"{name}: {taken + 1}/{target}")
    else:
        db.clear_supplement(d, name)
        await cq.answer(f"{name}: сброс")
    await _show_nutrition(cq.message, db)


# ---------------------------------------------------------- свои продукты
@router.message(F.text.regexp(PRODUCT_RE))
async def add_product(msg: Message, db: DB) -> None:
    prod = parse_product(msg.text)
    if not prod:
        await msg.answer("Формат: <b>добавь продукт: рис бурый 130 3</b> "
                         "(ккал и белок на 100 г). Для штучного добавь «шт».")
        return
    db.add_custom_food(*prod)
    name, _base, kcal, prot, ftype = prod
    unit = "шт" if ftype == "count" else "100 г"
    await msg.answer(f"✅ Добавил продукт <b>«{escape(name)}»</b>: "
                     f"{kcal:g} ккал, {prot:g} г белка на {unit}.\n"
                     "<i>Теперь пиши его в приёмах — посчитаю по твоим числам.</i>")
