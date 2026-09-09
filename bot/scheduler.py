"""Плановые задачи: утренний пуш, недельный разбор, добавки, анализы, бэкап.

Главное правило этого модуля появилось после ревизии: бот месяц слал план и
напоминания в пустоту — владелец не открывал чат, а расписание работало как
ни в чём не бывало. Уведомление, которое никто не читает, обесценивает
все остальные: человек перестаёт открывать чат вообще.

Поэтому все пуши смотрят на days_silent() и при долгом молчании переходят в
режим паузы: раз в неделю вместо каждого дня.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .activity import days_silent
from .clock import today
from .content.texts import LABS_ANNUAL_TEXT, VITD_SEASONAL_TEXT
from .db import DB
from .modules.weekly_adapt import run_weekly_adapt
from .views import day_view

log = logging.getLogger("coach")

# Сколько дней молчания переводят бота в недельный режим.
SILENCE_PAUSE_DAYS = 7
# Мелкие напоминания глушим раньше: они дешёвые, но раздражают быстрее.
SILENCE_MUTE_DAYS = 3


async def send_morning_push(bot: Bot, db: DB, chat_id: str) -> None:
    if not chat_id:
        log.warning("TELEGRAM_CHAT_ID не задан — пуш некому слать.")
        return
    silent = days_silent(db)
    # В режиме паузы шлём только по понедельникам — раз в неделю, а не каждый день.
    if silent >= SILENCE_PAUSE_DAYS and today().weekday() != 0:
        log.info("Молчание %d дн. — утренний пуш пропущен (режим паузы).", silent)
        return

    text, kb = day_view(db)
    if silent >= SILENCE_PAUSE_DAYS:
        text = (
            f"👋 <b>Тебя не было {silent} дней.</b>\n"
            "<i>Перешёл на один план в неделю, чтобы не мешать. Ответь чем "
            "угодно — вернусь к ежедневному. Если поменялся сезон или "
            "расписание, это чинится за минуту: /program.</i>\n\n"
        ) + text
    await bot.send_message(chat_id, text, reply_markup=kb)


async def send_weekly_review(bot: Bot, db: DB, chat_id: str) -> None:
    if chat_id:
        await bot.send_message(chat_id, run_weekly_adapt(db, today()))


async def send_supplement_reminder(bot: Bot, db: DB, chat_id: str) -> None:
    """Напоминаем только о недобранных дозах; всё принято или молчит — молчим."""
    if not chat_id:
        return
    if days_silent(db) >= SILENCE_MUTE_DAYS:
        return  # человека нет в чате: напоминание только обесценит остальные
    supps = (db.get_profile() or {}).get("supplements") or []
    counts = db.supplement_counts(today().isoformat())
    missing = [
        f"{s.get('name', '')} ({counts.get(s.get('name', ''), 0)}/{max(1, int(s.get('doses', 1)))})"
        for s in supps
        if counts.get(s.get("name", ""), 0) < max(1, int(s.get("doses", 1)))
    ]
    if missing:
        await bot.send_message(
            chat_id, "💊 Не забудь добавки: " + ", ".join(missing)
            + ".\n<i>Отметить — в /food.</i>")


async def send_annual_labs(bot: Bot, chat_id: str) -> None:
    if chat_id:
        await bot.send_message(chat_id, LABS_ANNUAL_TEXT)


async def send_vitd_seasonal(bot: Bot, chat_id: str) -> None:
    if chat_id:
        await bot.send_message(chat_id, VITD_SEASONAL_TEXT)


async def send_db_backup(bot: Bot, db: DB, chat_id: str) -> None:
    """Офсайт-бэкап без инфраструктуры: копия базы документом в тот же чат.

    Локальные копии лежат на том же диске, что и боевая база, — от смерти диска
    они не спасают. База весит меньше 200 КБ, поэтому Telegram здесь и есть
    самое дешёвое внешнее хранилище.
    """
    if not chat_id:
        return
    stamp = today().isoformat()
    tmp = os.path.join(tempfile.gettempdir(), f"coach-{stamp}.db")
    try:
        dst = sqlite3.connect(tmp)
        with dst:
            db.conn.backup(dst)  # консистентно, переживает WAL
        dst.close()
        await bot.send_document(
            chat_id, FSInputFile(tmp, filename=f"coach-{stamp}.db"),
            caption=f"💾 <b>Бэкап базы</b> · {stamp}\n"
                    "<i>Копия вне сервера. Сохрани, если чистишь чат.</i>")
    except Exception as e:  # noqa: BLE001 — бэкап не должен ронять планировщик
        log.exception("Бэкап в чат не ушёл: %s", type(e).__name__)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def build_scheduler(bot: Bot, db: DB, cfg, tz: ZoneInfo) -> AsyncIOScheduler:
    """Все плановые задачи в одном месте — видно расписание целиком."""
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(send_morning_push, "cron", args=[bot, db, cfg.chat_id],
                  hour=cfg.push_hour, minute=cfg.push_minute)
    sched.add_job(send_weekly_review, "cron", args=[bot, db, cfg.chat_id],
                  day_of_week="sun", hour=20, minute=0)
    sched.add_job(send_supplement_reminder, "cron", args=[bot, db, cfg.chat_id],
                  hour=21, minute=0)
    sched.add_job(send_db_backup, "cron", args=[bot, db, cfg.chat_id],
                  day_of_week="sun", hour=3, minute=45)
    sched.add_job(send_annual_labs, "cron", args=[bot, cfg.chat_id],
                  month=4, day=23, hour=10, minute=0)
    sched.add_job(send_vitd_seasonal, "cron", args=[bot, cfg.chat_id],
                  month=2, day=25, hour=10, minute=0)
    return sched
