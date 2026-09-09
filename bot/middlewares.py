"""Middleware: доступ только владельцу + отметка последней активности.

OwnerOnly закрывает дыру, найденную ревизией: у бота не было ни одного фильтра
по chat_id. Любой, кто узнал имя бота, мог читать план тренировок, отменять
сессии и писать в лог еды — а данные тут личные, включая травмы и вес.

LastSeen нужен, чтобы бот замечал молчание. За месяц без единого ответа он
продолжал слать утренний план и напоминания о добавках в пустоту.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .activity import touch
from .db import DB

log = logging.getLogger("coach")


def _chat_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery) and event.message:
        return event.message.chat.id
    return None


class OwnerOnly(BaseMiddleware):
    """Пропускает только владельца. Чужие апдейты отбрасываются молча.

    Молча — намеренно: отвечать «доступ запрещён» значит подтверждать чужому,
    что бот жив и чей-то личный.
    """

    def __init__(self, owner_chat_id: str | int | None) -> None:
        self.owner = str(owner_chat_id or "").strip()
        self._warned = False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not self.owner:
            # TELEGRAM_CHAT_ID не задан: не запираем бота намертво (иначе
            # первый запуск невозможен), но предупреждаем один раз.
            if not self._warned:
                log.warning("TELEGRAM_CHAT_ID не задан — бот открыт всем. "
                            "Узнай свой id через /start и впиши в .env.")
                self._warned = True
            return await handler(event, data)

        chat = _chat_id(event)
        if chat is not None and str(chat) != self.owner:
            log.warning("Отброшен апдейт из чужого чата %s", chat)
            return None
        return await handler(event, data)


class LastSeen(BaseMiddleware):
    """Пишет дату последнего действия владельца — по ней бот понимает молчание."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        db: DB | None = data.get("db")
        if db is not None:
            touch(db)
        return await handler(event, data)
