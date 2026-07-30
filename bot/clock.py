"""Единый источник «сегодня» — в часовом поясе атлета.

Контейнер по умолчанию живёт в UTC, а планировщик — в Europe/Samara. Из-за этого
`date.today()` с 00:00 до 04:00 по Самаре возвращал ВЧЕРАШНЮЮ дату, и еда/добавки
писались не в тот день. Все модули должны брать дату отсюда.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Samara"))


def tz() -> ZoneInfo:
    return _TZ


def today() -> date:
    """Текущая дата в часовом поясе атлета (не сервера)."""
    return datetime.now(_TZ).date()


def today_iso() -> str:
    return today().isoformat()
