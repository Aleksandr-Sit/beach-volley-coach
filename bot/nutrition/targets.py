"""Цели по калориям и макросам — детерминированный расчёт из профиля.

Пример профиля: 30 лет, 188 см, ~82 кг, очень активный (волейбол 4–5/нед + зал +
много ходьбы). Цель: поддержание / лёгкий набор → лёгкий профицит, приоритет белка.
"""
from __future__ import annotations


def compute_targets(profile: dict | None) -> dict:
    p = profile or {}
    weight = float(p.get("weight_kg", 82))
    height = float(p.get("height_cm", 188))
    age = int(p.get("age", 40))
    goal = (p.get("nutrition_goal") or "").lower()

    # Mifflin-St Jeor (мужчина) + активность. Высокий NEAT + спорт → ~1.75.
    bmr = 10 * weight + 6.25 * height - 5 * age + 5
    tdee = bmr * 1.75

    gain = "набор" in goal or "gain" in goal or "масс" in goal
    kcal = tdee + (250 if gain else 0)  # лёгкий профицит для медленного набора

    protein = round(weight * 1.9)          # 1.9 г/кг — верх диапазона для спортсмена
    fat = round(weight * 1.0)              # ~1 г/кг
    carbs = round((kcal - protein * 4 - fat * 9) / 4)
    return {
        "kcal": int(round(kcal / 10) * 10),
        "protein": int(protein),
        "fat": int(fat),
        "carbs": int(carbs),
        "mode": "лёгкий набор" if gain else "поддержание",
    }
