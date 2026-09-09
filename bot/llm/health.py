"""Самопроверка LLM живым вызовом.

Зачем отдельный модуль: «контейнер запущен» и «модель отвечает» — разные вещи.
Проверка при старте ловит снятую модель и протухший ключ в первую минуту, а не
через полтора месяца молчания.

Вызов блокирующий (google-generativeai синхронный), поэтому в async-коде
запускать через asyncio.to_thread.
"""
from __future__ import annotations

import logging

log = logging.getLogger("coach")

PING_SYSTEM = "Отвечай ровно одним словом."
PING_USER = "Скажи: ок"


def check_llm(llm) -> tuple[bool, str]:
    """Живой вызов модели. Возвращает (жива, короткая деталь для лога/сообщения)."""
    model = getattr(llm, "active_model", "не задана")
    try:
        reply = llm.complete(PING_SYSTEM, PING_USER)
    except Exception as e:  # noqa: BLE001 — самопроверка не должна ронять старт
        return False, f"{model}: {type(e).__name__}"
    if not reply:
        return False, f"{model}: пустой ответ"
    if reply.startswith("[LLM"):
        return False, "ключ не задан"
    if getattr(llm, "switched", False):
        return True, f"{model} (запасная — обнови LLM_MODEL в .env)"
    return True, model


def startup_report(llm) -> tuple[bool, str]:
    """Готовое сообщение владельцу. Молчим, когда всё хорошо, — кроме лога."""
    ok, detail = check_llm(llm)
    if ok:
        log.info("LLM жив: %s", detail)
        return True, f"🧠 Мозг на связи: <code>{detail}</code>"
    log.error("LLM НЕ ОТВЕЧАЕТ: %s", detail)
    return False, (
        "⚠️ <b>Мозг не отвечает.</b>\n"
        f"<i>{detail}</i>\n\n"
        "Расписание, тренировки, питание и разбор недели работают как обычно — "
        "они не требуют модели. Не будет только свободных ответов на вопросы.\n"
        "Причина обычно одна: модель сняли или кончился ключ."
    )
