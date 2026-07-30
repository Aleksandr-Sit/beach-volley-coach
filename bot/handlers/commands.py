"""Команды и постоянная панель снизу.

Панель шлёт обычный текст — эти хендлеры ловят его ДО общего free_text,
поэтому роутер подключается раньше роутера расписания.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, KeyboardButton, Message, ReplyKeyboardMarkup

from ..clock import today
from ..content.texts import HELP_TEXT
from ..db import DB
from ..modules.weekly_adapt import run_weekly_adapt
from ..views import day_view, nutrition_view, products_view, week_view, weight_view

router = Router(name="commands")

BTN_TODAY, BTN_WEEK, BTN_REVIEW, BTN_FOOD = (
    "📋 Сегодня", "🗓 Неделя", "📊 Разбор", "🍽 Питание")

PANEL = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_WEEK)],
              [KeyboardButton(text=BTN_REVIEW), KeyboardButton(text=BTN_FOOD)]],
    resize_keyboard=True, is_persistent=True,
)

BOT_COMMANDS = [
    BotCommand(command="today", description="📋 План на сегодня"),
    BotCommand(command="week", description="🗓 Вся неделя (правка любой сессии)"),
    BotCommand(command="food", description="🍽 Питание: цель, съедено, добавки"),
    BotCommand(command="weight", description="⚖️ Вес тела: записать и динамика"),
    BotCommand(command="product", description="🧾 Свои продукты (добавить/список)"),
    BotCommand(command="review", description="📊 Разбор недели и прогрессия"),
    BotCommand(command="help", description="❓ Что я умею"),
    BotCommand(command="start", description="🔄 Перезапуск / приветствие"),
]


async def _send_day(msg: Message, db: DB) -> None:
    text, kb = day_view(db)
    await msg.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def start(msg: Message, db: DB) -> None:
    await msg.answer(
        "Привет! Я твой тренер по пляжному волейболу. 🏐\n"
        "Каждое утро присылаю план дня. Внизу — быстрые кнопки, слева от поля "
        "ввода — меню ☰ со всеми командами. Подробнее: /help",
        reply_markup=PANEL,
    )
    await _send_day(msg, db)


@router.message(Command("help"))
async def help_cmd(msg: Message) -> None:
    await msg.answer(HELP_TEXT, reply_markup=PANEL)


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def today_cmd(msg: Message, db: DB) -> None:
    await _send_day(msg, db)


@router.message(Command("week"))
@router.message(F.text == BTN_WEEK)
async def week_cmd(msg: Message, db: DB) -> None:
    text, kb = week_view(db)
    await msg.answer(text, reply_markup=kb)


@router.message(Command("review"))
@router.message(F.text == BTN_REVIEW)
async def review_cmd(msg: Message, db: DB) -> None:
    await msg.answer(run_weekly_adapt(db, today()))


@router.message(Command("food"))
@router.message(F.text == BTN_FOOD)
async def food_cmd(msg: Message, db: DB) -> None:
    text, kb = nutrition_view(db)
    await msg.answer(text, reply_markup=kb)


@router.message(Command("weight"))
async def weight_cmd(msg: Message, db: DB) -> None:
    await msg.answer(weight_view(db))


@router.message(Command("product"))
async def product_cmd(msg: Message, db: DB) -> None:
    await msg.answer(products_view(db))
