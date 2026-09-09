"""Загрузка конфигурации из .env. Секреты не логируем."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Не задана обязательная переменная окружения: {name}")
    return val or ""


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: str
    gemini_api_key: str
    llm_provider: str
    llm_model: str
    timezone: str
    push_hour: int
    push_minute: int
    db_path: str


def load_config() -> Config:
    return Config(
        bot_token=_get("TELEGRAM_BOT_TOKEN", required=True),
        chat_id=_get("TELEGRAM_CHAT_ID", default=""),
        gemini_api_key=_get("GEMINI_API_KEY", default=""),
        llm_provider=_get("LLM_PROVIDER", default="gemini"),
        # gemini-2.0-flash снята Google 2026 — бот молча замолчал на полтора
        # месяца. Клиент умеет падать на запасную модель, но пин держим свежим.
        llm_model=_get("LLM_MODEL", default="gemini-3.6-flash"),
        timezone=_get("TIMEZONE", default="Europe/Samara"),
        push_hour=int(_get("MORNING_PUSH_HOUR", default="8")),
        push_minute=int(_get("MORNING_PUSH_MINUTE", default="0")),
        db_path=_get("DB_PATH", default="data/coach.db"),
    )
