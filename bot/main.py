"""Точка входа: бот (long-polling) + планировщик утреннего пуша."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, KeyboardButton, Message, ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import load_config
from .db import DB
from .handlers.schedule import nutrition_view, router as schedule_router
from .llm.client import build_llm
from .modules.profile_seed import seed_if_empty
from .modules.schedule_sync import generate_week, monday_of
from .modules.weekly_adapt import run_weekly_adapt
from .ui import day_keyboard, render_day, render_week, week_keyboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coach")

# Постоянная панель снизу (частые действия в один тап).
BTN_TODAY, BTN_WEEK, BTN_REVIEW, BTN_FOOD = (
    "📋 Сегодня", "🗓 Неделя", "📊 Разбор", "🍽 Питание")
PANEL = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_WEEK)],
              [KeyboardButton(text=BTN_REVIEW), KeyboardButton(text=BTN_FOOD)]],
    resize_keyboard=True, is_persistent=True,
)

# Список команд для нативного меню (☰) и автоподсказки по «/».
BOT_COMMANDS = [
    BotCommand(command="today", description="📋 План на сегодня"),
    BotCommand(command="week", description="🗓 Вся неделя (правка любой сессии)"),
    BotCommand(command="food", description="🍽 Питание: цель, съедено, добавки"),
    BotCommand(command="product", description="🧾 Свои продукты (добавить/список)"),
    BotCommand(command="review", description="📊 Разбор недели и прогрессия"),
    BotCommand(command="help", description="❓ Что я умею"),
    BotCommand(command="start", description="🔄 Перезапуск / приветствие"),
]

HELP_TEXT = (
    "Я веду твою подготовку с приоритетом пляжного волейбола.\n\n"
    "📋 <b>Сегодня</b> — план дня, самочувствие, упражнения, правки.\n"
    "🗓 <b>Неделя</b> — вся неделя, правка/перенос любой сессии.\n"
    "🍽 <b>Питание</b> — цель по ккал/белку, что съедено, добавки.\n"
    "📊 <b>Разбор</b> — итоги недели + прогрессия весов (сам приходит в Вс 20:00).\n\n"
    "Ещё умею:\n"
    "• Правки текстом: «дождь, отменили», «перенеси на завтра», «в 18:30».\n"
    "• Объяснить упражнение — напиши его название (дам и видео).\n"
    "• Записать результат зала (кнопка «📝» в меню сессии) — на их основе растут веса.\n"
    "• Свои продукты для питания: «добавь продукт: рис бурый 130 3» (см. /product).\n"
    "• Отметка добавок — на экране «🍽 Питание».\n"
    "Кнопки внизу и меню ☰ слева от поля ввода — для быстрого доступа."
)


async def send_morning_push(bot: Bot, db: DB, chat_id: str, tz: ZoneInfo) -> None:
    if not chat_id:
        log.warning("TELEGRAM_CHAT_ID не задан — пуш некому слать. Напиши боту /start.")
        return
    today = date.today()
    generate_week(db, today)
    sessions = db.sessions_for(today.isoformat())
    await bot.send_message(chat_id, render_day(today, sessions),
                           reply_markup=day_keyboard(today, sessions))


async def send_weekly_review(bot: Bot, db: DB, chat_id: str) -> None:
    if not chat_id:
        return
    await bot.send_message(chat_id, run_weekly_adapt(db, date.today()))


LABS_ANNUAL_TEXT = (
    "🩺 <b>Годовой чек здоровья</b>\n"
    "Пора сдать кровь — покажи этот список в лаборатории (<i>утром, натощак</i>):\n\n"
    "<b>База:</b> ОАК · глюкоза + HbA1c · липидный профиль · АЛТ/АСТ · "
    "креатинин + СКФ · ТТГ.\n"
    "<b>Дополнительно:</b> витамин D (25-OH-D) · ферритин/железо. "
    "⚠️ Скажи в лаборатории, если пьёшь <b>креатин</b> — он может завышать креатинин.\n"
    "<b>Опционально:</b> тестостерон общий + свободный (сдать 7–10 утра, натощак) · "
    "измерь давление.\n\n"
    "<i>Набор и трактовку согласуй с терапевтом (это не диагноз).</i>"
)

VITD_SEASONAL_TEXT = (
    "☀️ <b>Витамин D — сезонный замер</b> (конец зимы)\n\n"
    "Зимой на северных широтах (октябрь–март) солнце почти не даёт витамин D — "
    "это годовой минимум. Хорошее время сдать <b>25-OH-D</b>: если сейчас в норме, "
    "значит в порядке круглый год.\n\n"
    "Если низкий — начни <b>D3</b> (доза по результату) и пересдай через 2–3 мес.\n"
    "<i>Не диагноз — согласуй с врачом.</i>"
)


async def send_annual_labs(bot: Bot, chat_id: str) -> None:
    if chat_id:
        await bot.send_message(chat_id, LABS_ANNUAL_TEXT)


async def send_vitd_seasonal(bot: Bot, chat_id: str) -> None:
    if chat_id:
        await bot.send_message(chat_id, VITD_SEASONAL_TEXT)


async def send_supplement_reminder(bot: Bot, db: DB, chat_id: str) -> None:
    if not chat_id:
        return
    profile = db.get_profile() or {}
    supps = profile.get("supplements") or []
    counts = db.supplement_counts(date.today().isoformat())
    missing = []
    for s in supps:
        name = s.get("name", "")
        target = max(1, int(s.get("doses", 1)))
        c = counts.get(name, 0)
        if c < target:
            missing.append(f"{name} ({c}/{target})")
    if not missing:
        return  # всё принято — не беспокоим
    await bot.send_message(
        chat_id, "💊 Не забудь добавки: " + ", ".join(missing)
        + ".\n<i>Отметить — в /food.</i>")


async def main() -> None:
    cfg = load_config()
    tz = ZoneInfo(cfg.timezone)
    db = DB(cfg.db_path)
    seed_if_empty(db)
    generate_week(db, date.today())

    llm = build_llm(cfg)
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(schedule_router)

    # Глобальный обработчик ошибок: любой сбой в хендлере → честный ответ, не молчание.
    @dp.errors()
    async def on_error(event) -> bool:
        log.exception("Ошибка обработки апдейта: %s", event.exception)
        try:
            upd = event.update
            chat = None
            if upd.message:
                chat = upd.message.chat.id
            elif upd.callback_query and upd.callback_query.message:
                chat = upd.callback_query.message.chat.id
            if chat:
                await bot.send_message(chat, "⚠️ Упс, что-то сбойнуло. Попробуй ещё раз "
                                             "или пользуйся кнопками под планом.")
        except Exception:
            pass
        return True

    # --- общая логика команд (используется и командами, и панелью снизу) ---
    async def send_today(msg: Message) -> None:
        today = date.today()
        generate_week(db, today)
        sessions = db.sessions_for(today.isoformat())
        cancelled = db.cancelled_for(today.isoformat())
        await msg.answer(render_day(today, sessions, cancelled),
                         reply_markup=day_keyboard(today, sessions, cancelled))

    async def send_week(msg: Message) -> None:
        today = date.today()
        generate_week(db, today)
        mon = monday_of(today)
        sessions = db.sessions_between(mon.isoformat(), (mon + timedelta(days=6)).isoformat())
        sessions = [s for s in sessions if s["status"] != "cancelled"]
        await msg.answer(render_week(today, sessions), reply_markup=week_keyboard(sessions))

    async def send_review(msg: Message) -> None:
        await msg.answer(run_weekly_adapt(db, date.today()))

    async def send_nutrition(msg: Message) -> None:
        txt, kb = nutrition_view(db)
        await msg.answer(txt, reply_markup=kb)

    @dp.message(CommandStart())
    async def start(msg: Message) -> None:
        await msg.answer(
            "Привет! Я твой тренер по пляжному волейболу. 🏐\n"
            f"Каждое утро в {cfg.push_hour:02d}:{cfg.push_minute:02d} присылаю план дня.\n"
            "Внизу — быстрые кнопки, слева от поля ввода — меню ☰ со всеми командами. "
            "Подробнее: /help",
            reply_markup=PANEL,
        )
        await send_today(msg)

    @dp.message(Command("help"))
    async def help_cmd(msg: Message) -> None:
        await msg.answer(HELP_TEXT, parse_mode="HTML", reply_markup=PANEL)

    @dp.message(Command("today"))
    async def today_cmd(msg: Message) -> None:
        await send_today(msg)

    @dp.message(Command("week"))
    async def week_cmd(msg: Message) -> None:
        await send_week(msg)

    @dp.message(Command("review"))
    async def review_cmd(msg: Message) -> None:
        await send_review(msg)

    @dp.message(Command("food"))
    async def food_cmd(msg: Message) -> None:
        await send_nutrition(msg)

    @dp.message(Command("product"))
    async def product_cmd(msg: Message) -> None:
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
        await msg.answer("\n".join(lines))

    # --- панель снизу: кнопки шлют текст-ярлык, ловим их ДО free_text ---
    @dp.message(F.text == BTN_TODAY)
    async def panel_today(msg: Message) -> None:
        await send_today(msg)

    @dp.message(F.text == BTN_WEEK)
    async def panel_week(msg: Message) -> None:
        await send_week(msg)

    @dp.message(F.text == BTN_REVIEW)
    async def panel_review(msg: Message) -> None:
        await send_review(msg)

    @dp.message(F.text == BTN_FOOD)
    async def panel_food(msg: Message) -> None:
        await send_nutrition(msg)

    # Планировщик утреннего пуша.
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        send_morning_push, "cron",
        hour=cfg.push_hour, minute=cfg.push_minute,
        args=[bot, db, cfg.chat_id, tz],
    )
    # Воскресный разбор недели + прогрессия — Вс 20:00.
    scheduler.add_job(
        send_weekly_review, "cron",
        day_of_week="sun", hour=20, minute=0,
        args=[bot, db, cfg.chat_id],
    )
    # Вечернее напоминание про добавки — 21:00 (только если не всё принято).
    scheduler.add_job(
        send_supplement_reminder, "cron",
        hour=21, minute=0,
        args=[bot, db, cfg.chat_id],
    )
    # Годовой чек здоровья (пример: 15 января 10:00) — поменяй дату под себя.
    scheduler.add_job(
        send_annual_labs, "cron",
        month=1, day=15, hour=10, minute=0,
        args=[bot, cfg.chat_id],
    )
    # Сезонный витамин D — 25 февраля 10:00 (зимний минимум на северных широтах).
    scheduler.add_job(
        send_vitd_seasonal, "cron",
        month=2, day=25, hour=10, minute=0,
        args=[bot, cfg.chat_id],
    )
    scheduler.start()

    await bot.set_my_commands(BOT_COMMANDS)  # нативное меню ☰ + автоподсказка «/»
    log.info("Бот запущен. Провайдер LLM: %s", cfg.llm_provider)
    await dp.start_polling(bot, db=db, llm=llm)


if __name__ == "__main__":
    asyncio.run(main())
