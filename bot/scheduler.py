"""Плановые задачи: утренний пуш, недельный разбор, добавки, анализы."""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .clock import today
from .content.texts import LABS_ANNUAL_TEXT, VITD_SEASONAL_TEXT
from .db import DB
from .modules.weekly_adapt import run_weekly_adapt
from .views import day_view

log = logging.getLogger("coach")


async def send_morning_push(bot: Bot, db: DB, chat_id: str) -> None:
    if not chat_id:
        log.warning("TELEGRAM_CHAT_ID не задан — пуш некому слать.")
        return
    text, kb = day_view(db)
    await bot.send_message(chat_id, text, reply_markup=kb)


async def send_weekly_review(bot: Bot, db: DB, chat_id: str) -> None:
    if chat_id:
        await bot.send_message(chat_id, run_weekly_adapt(db, today()))


async def send_supplement_reminder(bot: Bot, db: DB, chat_id: str) -> None:
    """Напоминаем только о недобранных дозах; всё принято — молчим."""
    if not chat_id:
        return
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


def build_scheduler(bot: Bot, db: DB, cfg, tz: ZoneInfo) -> AsyncIOScheduler:
    """Все плановые задачи в одном месте — видно расписание целиком."""
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(send_morning_push, "cron", args=[bot, db, cfg.chat_id],
                  hour=cfg.push_hour, minute=cfg.push_minute)
    sched.add_job(send_weekly_review, "cron", args=[bot, db, cfg.chat_id],
                  day_of_week="sun", hour=20, minute=0)
    sched.add_job(send_supplement_reminder, "cron", args=[bot, db, cfg.chat_id],
                  hour=21, minute=0)
    sched.add_job(send_annual_labs, "cron", args=[bot, cfg.chat_id],
                  month=4, day=23, hour=10, minute=0)
    sched.add_job(send_vitd_seasonal, "cron", args=[bot, cfg.chat_id],
                  month=2, day=25, hour=10, minute=0)
    return sched
