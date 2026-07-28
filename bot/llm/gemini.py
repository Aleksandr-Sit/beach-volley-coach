"""Реализация LLMClient на Google Gemini (free tier)."""
from __future__ import annotations

import logging

import google.generativeai as genai


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        genai.configure(api_key=api_key)
        self._model_name = model

    def complete(self, system: str, user: str) -> str:
        # Никогда не пробрасываем исключение наверх (429/квота/сеть) —
        # возвращаем "", чтобы обработчик деградировал мягко, а не падал молча.
        try:
            model = genai.GenerativeModel(
                model_name=self._model_name,
                system_instruction=system,
            )
            resp = model.generate_content(user)
            return (resp.text or "").strip()
        except Exception as e:
            logging.getLogger("coach").warning("LLM недоступен: %s", type(e).__name__)
            return ""
