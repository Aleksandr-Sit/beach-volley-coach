"""Учёт активности владельца: заходил ли он и сколько дней молчит.

Отдельный модуль без aiogram намеренно: логика молчания нужна планировщику,
экрану статуса и тестам, а тянуть ради неё Telegram-зависимость незачем
(локально aiogram под Python 3.14 не собирается, тесты бы отвалились).

Зачем это вообще: ревизия показала месяц пушей в пустоту. Бот слал план каждое
утро и напоминал о добавках, хотя владелец давно перестал открывать чат.
"""
from __future__ import annotations

from datetime import date

from .clock import today
from .db import DB

S_LAST_SEEN = "last_seen"


def touch(db: DB) -> None:
    """Отметить, что владелец сегодня заходил. Пишем не чаще раза в день."""
    iso = today().isoformat()
    if db.get_setting(S_LAST_SEEN) != iso:
        db.set_setting(S_LAST_SEEN, iso)


def days_silent(db: DB) -> int:
    """Сколько дней не было ни одного действия. 0 — был сегодня.

    Пока отметки нет вообще (старая база, первый запуск), возвращаем 0: это
    «ещё не знаем», а не «месяц молчит». Иначе бот ушёл бы в паузу сразу после
    обновления, не дав себе шанса.
    """
    raw = db.get_setting(S_LAST_SEEN)
    if not raw:
        return 0
    try:
        return max(0, (today() - date.fromisoformat(raw)).days)
    except ValueError:
        return 0
