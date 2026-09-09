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
from .handlers.program import router as program_router
from .handlers.schedule import router as schedule_router
from .llm.client import build_llm
from .llm.health import startup_report
from .middlewares import LastSeen, OwnerOnly
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


SELFTEST_TIMEOUT = 90  # сек: дальше ждать бессмысленно, это уже отказ


async def run_selftest(bot: Bot, llm, chat_id: str) -> None:
    """Живая проверка модели в фоне. Молчим, когда всё хорошо.

    Смысл проверки: снятая провайдером модель иначе остаётся незамеченной
    месяцами — обёртка возвращает пустую строку, и бот просто «перестаёт
    понимать», не роняя ни одной ошибки в лог.
    """
    try:
        ok, report = await asyncio.wait_for(
            asyncio.to_thread(startup_report, llm), timeout=SELFTEST_TIMEOUT)
    except TimeoutError:  # с 3.11 asyncio.TimeoutError — это он же
        log.error("Самопроверка LLM не уложилась в %d с", SELFTEST_TIMEOUT)
        ok, report = False, (
            f"⚠️ <b>Мозг не ответил за {SELFTEST_TIMEOUT} секунд.</b>\n"
            "<i>Похоже на сеть или провайдера. Расписание, тренировки и "
            "питание работают без него — проверить: /status.</i>")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — самопроверка не должна ронять бота
        log.exception("Самопроверка LLM упала")
        return

    if not ok and chat_id:
        try:
            await bot.send_message(chat_id, report)
        except Exception:  # noqa: BLE001 — предупредить не вышло, работаем дальше
            log.exception("Не удалось отправить предупреждение о LLM")


async def main() -> None:
    cfg = load_config()
    db = DB(cfg.db_path)
    seed_if_empty(db)
    generate_week(db, today())

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # Внешние middleware — до фильтров: чужой апдейт не должен дойти до хендлера.
    owner = OwnerOnly(cfg.chat_id)
    seen = LastSeen()
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(owner)
        observer.outer_middleware(seen)

    # Порядок важен: команды и кнопки панели ловятся до общего free_text.
    dp.include_router(commands_router)
    dp.include_router(nutrition_router)
    dp.include_router(program_router)
    dp.include_router(schedule_router)
    setup_error_handler(dp, bot)

    scheduler = build_scheduler(bot, db, cfg, ZoneInfo(cfg.timezone))
    scheduler.start()

    await bot.set_my_commands(BOT_COMMANDS)
    log.info("Бот запущен. LLM: %s, TZ: %s", cfg.llm_provider, cfg.timezone)

    llm = build_llm(cfg)
    # Самопроверка идёт ФОНОМ. Живой вызов модели занимает от 8 до 80 секунд,
    # и если ждать его перед polling, бот всё это время просто не отвечает —
    # замеряно на проде 09.09.2026: 77 секунд немоты после рестарта.
    selftest = asyncio.create_task(run_selftest(bot, llm, cfg.chat_id))

    try:
        await dp.start_polling(bot, db=db, llm=llm)
    finally:
        selftest.cancel()
        scheduler.shutdown(wait=False)
        db.conn.close()


if __name__ == "__main__":
    asyncio.run(main())
