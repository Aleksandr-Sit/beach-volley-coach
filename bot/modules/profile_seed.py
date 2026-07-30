"""Первичный сид: профиль атлета и недельный шаблон волейбола.

Источник данных — docs/profile.md (интейк). Запускается один раз при пустой БД.

ВНИМАНИЕ: ниже — ПРИМЕР-ПРОФИЛЬ (обобщённый атлет) для демонстрации.
Замени значения на свои в docs/profile.md и здесь перед первым запуском.
"""
from __future__ import annotations

from ..db import DB

# --- ПРИМЕР профиля (sample). Настрой под себя перед первым запуском. ---
PROFILE = {
    "name": "athlete",
    "sex": "male",
    "age": 30,
    "height_cm": 188,
    "weight_kg": 82,
    "build": "спортивное телосложение",
    "timezone": "Europe/Samara",
    "sport": "пляжный волейбол",
    "vb_level": "любитель (~1 год)",
    "vb_strength": "нападение (использовать рост)",
    "goals": "всестороннее развитие; поддержание веса / медленный lean-набор",
    "plan_style": "коротко и по делу",
    "gym_access": "полный коммерческий зал",
    "gym_experience_years": 5,
    "lifts": {"squat": "100x5x5 (не предел)", "trapbar_dl": "120x4x4 (не предел)", "press": "плечо-дружественно, прогрессивно"},
    "injuries": [
        {"area": "правое плечо", "status": "лёгкий дискомфорт при жимах над головой; prehab резинками", "severity": "приоритет prehab"},
        {"area": "голеностопы", "status": "мониторинг после большого объёма прыжков", "severity": "мониторинг"},
    ],
    "sleep": "7-8ч",
    "stress": "низкий-умеренный; высокий NEAT (много ходит)",
    "nutrition_goal": "поддержание / lean-набор",
    "nutrition_logging": "текст в обычные дни, фото при сомнении",
    "supplements": [
        {"name": "креатин", "dose": "5 г", "timing": "днём с водой", "doses": 1},
        {"name": "омега-3", "dose": "2 капс ×3", "timing": "утро/обед/вечер", "doses": 3},
        {"name": "магний", "dose": "400 мг", "timing": "перед сном", "doses": 1},
    ],
    "supplement_gap": "витамин D (зимой мало солнца) — обсудить тест 25-OH-D",
}

# weekday: 0=Пн .. 6=Вс. Время плавающее -> храним подсказку, не точное время.
VB_TEMPLATE = [
    # (weekday, title, kind, duration_min, time_hint, flexible)
    (1, "Техника (персональная)", "technique", 60, "утро", 1),
    (2, "Техника с напарником", "technique", 60, "утро", 1),
    (2, "Игровая, группа", "game", 120, "вечер", 1),
    (3, "Техника (персональная)", "technique", 60, "утро", 1),
    (4, "Игровая с тренером", "game", 105, "вечер", 1),
]


def seed_if_empty(db: DB) -> None:
    # Разовая миграция реестра сгенерированных недель (см. generate_week).
    from .schedule_sync import backfill_generated_weeks
    backfill_generated_weeks(db)

    prof = db.get_profile()
    if prof is None:
        db.set_profile(PROFILE)
    elif not all("doses" in s for s in prof.get("supplements", [])):
        # миграция: добавки без числа приёмов -> обновить на каноничные с doses
        prof["supplements"] = PROFILE["supplements"]
        db.set_profile(prof)
    db.seed_template(VB_TEMPLATE)
    # Рабочие веса для прогрессии — из профиля (не предельные).
    db.seed_weights({"squat": 100.0, "trapbar": 120.0})
