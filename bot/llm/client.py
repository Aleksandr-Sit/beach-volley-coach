"""Интерфейс LLM. Смена провайдера (Gemini -> Anthropic/OpenAI) = 1 строка конфига.

Держим тонкий контракт: complete(system, user) -> str и parse_edit(...) -> dict.
Всё общение бота с моделью идёт ТОЛЬКО через этот интерфейс.
"""
from __future__ import annotations

import json
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def build_llm(cfg) -> LLMClient:
    """Фабрика по конфигу. Сейчас поддержан gemini; заглушка при отсутствии ключа."""
    provider = (cfg.llm_provider or "gemini").lower()
    if provider == "gemini" and cfg.gemini_api_key:
        from .gemini import GeminiClient

        return GeminiClient(api_key=cfg.gemini_api_key, model=cfg.llm_model)
    # Fallback: без ключа бот работает, но «мозг» отвечает заглушкой.
    return _NullClient()


class _NullClient:
    def complete(self, system: str, user: str) -> str:
        return (
            "[LLM не настроен: задай GEMINI_API_KEY в .env] "
            "Пока отвечаю по правилам без модели."
        )


def safe_json(text: str) -> dict:
    """Достаёт JSON из ответа модели (иногда обёрнут в ```json ... ```)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        t = t[start : end + 1]
    try:
        return json.loads(t)
    except Exception:
        return {}
