"""Точка входа: сборка бота, роутеров и планировщика."""
from __future__ import annotations

import asyncio
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .clock import today
from .config import load_config
from .db import DB
from .handlers.commands import BOT_COMMANDS
from .handlers.commands import router as commands_router
from .handlers.nutrition import router as nutrition_router
from .handlers.schedule import router as schedule_router
from .llm.client import build_llm
from .modules.profile_seed import seed_if_empty
from .modules.schedule_sync import generate_week
from .scheduler import build_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coach")


def setup_error_handler(dp: Dispatcher, bot: Bot) -> None:
    """Любой сбой в хендлере → честный ответ пользователю, а не молчание."""

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
                await bot.send_message(
                    chat, "⚠️ Упс, что-то сбойнуло. Попробуй ещё раз "
                          "или пользуйся кнопками под планом.")
        except Exception:  # noqa: BLE001 — обработчик ошибок не должен падать сам
            pass
        return True


async def main() -> None:
    cfg = load_config()
    db = DB(cfg.db_path)
    seed_if_empty(db)
    generate_week(db, today())

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    # Порядок важен: команды и кнопки панели ловятся до общего free_text.
    dp.include_router(commands_router)
    dp.include_router(nutrition_router)
    dp.include_router(schedule_router)
    setup_error_handler(dp, bot)

    scheduler = build_scheduler(bot, db, cfg, ZoneInfo(cfg.timezone))
    scheduler.start()

    await bot.set_my_commands(BOT_COMMANDS)
    log.info("Бот запущен. LLM: %s, TZ: %s", cfg.llm_provider, cfg.timezone)
    try:
        await dp.start_polling(bot, db=db, llm=build_llm(cfg))
    finally:
        scheduler.shutdown(wait=False)
        db.conn.close()


if __name__ == "__main__":
    asyncio.run(main())
