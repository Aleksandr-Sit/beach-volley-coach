"""Тренер-«мозг»: системный промпт, разбор свободного текста в намерение, советы."""
from __future__ import annotations

import os
from datetime import date, timedelta

from .db import DB
from .llm.client import LLMClient, safe_json
from .modules.schedule_sync import WEEKDAY_RU, monday_of

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "coach_system_prompt.md")


def system_prompt() -> str:
    try:
        with open(_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "Ты — тренер по пляжному волейболу. Отвечай по-русски, кратко, с 'почему'."


def _week_context(db: DB, today: date) -> str:
    monday = monday_of(today)
    rows = db.sessions_between(monday.isoformat(), (monday + timedelta(days=6)).isoformat())
    lines = []
    for s in rows:
        if s["status"] == "cancelled":
            continue
        d = date.fromisoformat(s["date"])
        lines.append(
            f"id={s['id']} {WEEKDAY_RU[d.weekday()]} {s['date']} "
            f"[{s['category']}] {s['title']} ({s['status']})"
        )
    return "\n".join(lines) or "(сессий нет)"


def parse_edit(llm: LLMClient, db: DB, today: date, text: str) -> dict:
    """Разбирает сообщение пользователя в намерение-правку. Возвращает JSON-словарь."""
    context = _week_context(db, today)
    user = f"""Сегодня {WEEKDAY_RU[today.weekday()]} {today.isoformat()}.
Сессии этой недели:
{context}

Сообщение пользователя: "{text}"

Определи намерение как ПРАВКУ расписания и верни СТРОГО JSON без пояснений:
{{
  "action": "cancel|move|retime|add|none",
  "session_id": <int или null>,
  "target_weekday": <0=Пн..6=Вс или null>,
  "target_time": "<HH:MM или null>",
  "new_title": "<для add или null>",
  "new_category": "vb|gym|recovery|null",
  "human": "<одна фраза: как ты понял правку, по-русски>"
}}
Если это не правка расписания, action="none"."""
    return safe_json(llm.complete(system_prompt(), user))


def estimate_food(llm: LLMClient, text: str) -> dict:
    """Фолбэк-оценка калорий/белка приёма пищи, когда база продуктов не сработала."""
    user = (
        f"Оцени приём пищи и верни СТРОГО JSON без пояснений: "
        f'{{"kcal": <число>, "protein": <число, граммы>}}. Приём: "{text}"'
    )
    data = safe_json(llm.complete(
        "Ты нутрициолог. Оцени калории и белок приёма пищи по описанию. Кратко, JSON.",
        user,
    ))
    try:
        return {"kcal": float(data.get("kcal", 0)), "protein": float(data.get("protein", 0))}
    except (TypeError, ValueError):
        return {"kcal": 0.0, "protein": 0.0}


def profile_digest(profile: dict) -> str:
    """Минимальная выжимка профиля для LLM.

    Приватность: наружу уходит только то, без чего совет невозможен. Раньше
    отправлялся ВЕСЬ профиль (вес, показатели, полная история травм), а на
    бесплатном тарифе данные могут использоваться для обучения модели.
    """
    if not profile:
        return "мужчина, любитель пляжного волейбола"
    injuries = ", ".join(
        i.get("area", "") for i in profile.get("injuries", [])
        if i.get("severity") in ("главный лимит", "мониторинг")
    )
    parts = [
        f"{profile.get('age', '')} лет, мужчина" if profile.get("age") else "мужчина",
        f"спорт: {profile.get('sport', 'пляжный волейбол')} ({profile.get('vb_level', '')})",
        f"цель: {profile.get('goals', '')}",
    ]
    if injuries:
        parts.append(f"ограничения: {injuries}")
    return "; ".join(p for p in parts if p.strip(" ;:"))


def advise(llm: LLMClient, db: DB, text: str) -> str:
    """Свободный совет тренера (питание/восстановление/вопрос), с учётом профиля."""
    user = (
        f"Атлет: {profile_digest(db.get_profile() or {})}\n\n"
        f"Вопрос: {text}\n\n"
        "Ответь как тренер: коротко, по делу, с кратким 'почему'."
    )
    # Может вернуть "" при недоступности LLM — вызывающий обработает фолбэком.
    return llm.complete(system_prompt(), user)
