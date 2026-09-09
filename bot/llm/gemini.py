"""Реализация LLMClient на Google Gemini (free tier).

Здесь же — защита от главного тихого отказа проекта. 09.09.2026 выяснилось, что
модель `gemini-2.0-flash` снята Google, и бот полтора месяца отвечал «Пока не
понял» вместо совета: обёртка ловила исключение и возвращала пустую строку, а в
логах не было ни строчки. Поэтому:

  1) отказ «модели больше нет» распознаётся отдельно от сетевого;
  2) клиент один раз автоматически переключается на плавающий алиас
     (gemini-flash-latest), чтобы бот не умирал молча до следующей ревизии;
  3) переключение пишется в лог как WARNING и видно в /status.
"""
from __future__ import annotations

import logging

import google.generativeai as genai

log = logging.getLogger("coach")

# Плавающий алиас: всегда указывает на актуальную flash-модель. Пин в .env
# оставляем ради предсказуемости, а сюда падаем, если пин протух.
FALLBACK_MODEL = "gemini-flash-latest"


def _model_is_gone(exc: Exception) -> bool:
    """Отличает «модель снята провайдером» от сети, квоты и прочих отказов."""
    marker = f"{type(exc).__name__} {exc}".lower()
    return any(w in marker for w in
               ("notfound", "404", "no longer available", "is not found"))


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-3.6-flash",
                 fallback: str = FALLBACK_MODEL) -> None:
        genai.configure(api_key=api_key)
        self._configured = model
        self._fallback = fallback
        self._active = model
        self._switched = False

    @property
    def active_model(self) -> str:
        return self._active

    @property
    def switched(self) -> bool:
        """Работаем ли на запасной модели (значит, пин в .env пора обновить)."""
        return self._switched

    def _generate(self, model_name: str, system: str, user: str) -> str:
        model = genai.GenerativeModel(model_name=model_name,
                                      system_instruction=system)
        return (model.generate_content(user).text or "").strip()

    def complete(self, system: str, user: str) -> str:
        # Наружу исключение не пробрасываем никогда: бот должен деградировать
        # мягко, а не падать. Но и молча умирать он больше не должен.
        try:
            return self._generate(self._active, system, user)
        except Exception as e:  # noqa: BLE001 — нужен любой отказ провайдера
            if _model_is_gone(e) and not self._switched and self._fallback != self._active:
                log.warning(
                    "Модель %s снята провайдером — перехожу на %s. "
                    "Обнови LLM_MODEL в .env.", self._active, self._fallback)
                self._active = self._fallback
                self._switched = True
                try:
                    return self._generate(self._active, system, user)
                except Exception as e2:  # noqa: BLE001
                    log.warning("Запасная модель тоже недоступна: %s",
                                type(e2).__name__)
                    return ""
            log.warning("LLM недоступен (%s): %s", self._active, type(e).__name__)
            return ""
