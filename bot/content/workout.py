"""Наполнение тренировок: конкретные упражнения под профиль атлета.

Детерминированные данные (0 токенов на рендер). Приоритет — пляжный волейбол:
зал дополняющий, submaximal, плечо-safe (правое плечо — главный лимит),
автопрегуляция по нагрузке сессии (heavy/moderate/light).

Профиль-константы (пример): присед база 100 (5×5, не предел), трап-гриф 120
(не предел), жимы — плечо-дружественно, голеностоп/колено — мониторим,
поясница — кор защищает.
"""
from __future__ import annotations

from html import escape

_LOAD_BADGE = {"heavy": "🔴 тяжёлая", "moderate": "🟡 средняя", "light": "🟢 лёгкая"}
_CAT_ICON = {"gym": "🏋️", "recovery": "🧘", "vb": "🏐"}
_DIVIDER = "➖➖➖➖➖➖➖➖➖➖"

# Блок prehab плеча — главный приоритет, идёт в верх, волейбол и восстановление.
SHOULDER_PREHAB = [
    "Наружная ротация с резинкой — 3×15 (медленно, локоть прижат)",
    "Face pull — 3×15 (свести лопатки, без рывка)",
    "Y-T-W лопаточные лёжа — 2×10 (лёгкий вес)",
]

ANKLE_PREHAB = [
    "Подъёмы на носки, эксцентрика — 3×15 (медленно вниз)",
    "Баланс на одной ноге — 2×30с (после прыжковых дней)",
]


def _lower(load: str, weights: dict, var: int) -> list[tuple[str, list[str]]]:
    if load == "light":
        return [("Разгрузка", [
            "Сегодня по самочувствию — только мобильность + кор.",
            "Кор антиротация (паллоф) — 2×30с/сторону",
        ] + ANKLE_PREHAB)]
    heavy = load == "heavy"
    sq = f"{weights.get('squat', 100):g}"
    tb = f"{weights.get('trapbar', 120):g}"
    blocks = []
    if heavy:
        blocks.append(("Прыжковый блок (низкий объём, техника приземления)", [
            "Выпрыгивания с места — 3×5 (мягкое приземление, беречь голеностоп)",
            "Дроп-приземления с короба — 3×3 (тихо, колени наружу)",
        ]))
    # Вариативность подсобки по чётности недели.
    if var == 0:
        uni = f"Болгарский сплит-присед — {'3×8' if heavy else '2×8'}/нога (контроль колена)"
        post = f"Румынская тяга — {'3×8' if heavy else '2×10'}"
    else:
        uni = f"Выпады с гантелями — {'3×10' if heavy else '2×10'}/нога (колено не заваливать)"
        post = f"Ягодичный мост со штангой — {'3×10' if heavy else '2×12'}"
    blocks += [
        ("Сила ног", [
            f"Присед со штангой — {'5×5' if heavy else '4×5'} @RPE7 (не до отказа, рабочий ~{sq} кг)",
            f"Тяга трап-гриф — {'3×6' if heavy else '3×5'} (рабочий ~{tb} кг, спина нейтральна)",
        ]),
        ("Односторонняя + задняя цепь", [uni, post]),
        ("Голеностоп + кор", ANKLE_PREHAB + [
            f"Планка / паллоф-антиротация — {'3' if heavy else '2'}×30–45с (защита поясницы)",
        ]),
    ]
    return blocks


def _upper(load: str, var: int) -> list[tuple[str, list[str]]]:
    if load == "light":
        return [("Разгрузка", [
            "Только prehab плеча + лёгкие тяги.",
        ] + SHOULDER_PREHAB)]
    full = load == "heavy"
    press = ("Ландмайн-жим (нейтральный хват)" if var == 0
             else "Жим гантелей нейтральным хватом сидя")
    if var == 0:
        row = "Тяга гантели в наклоне"
        row_reps = f"{'3×10' if full else '2×10'}/рука"  # гантель — по одной руке
    else:
        row = "Тяга штанги в наклоне (нейтральный хват)"
        row_reps = f"{'3×10' if full else '2×10'}"        # штанга — двумя руками
    return [
        ("Разогрев плеча (обязательно)", SHOULDER_PREHAB),
        ("Жим — только плечо-safe (без штанги над головой)", [
            f"{press} — {'3×8' if full else '2×10'} (без боли!)",
            "Отжимания на кольцах/от пола нейтрально — 2×макс комфортно",
        ]),
        ("Тяги (приоритет — их больше, чем жимов)", [
            f"{row} — {row_reps}",
            f"Тяга верхнего блока / подтягивания — {'3×8' if full else '2×8'}",
        ]),
        ("Плечи здоровые + кор", [
            "Наружная ротация на блоке — 3×15",
            f"Антиэкстензия кор (ролик/планка) — {'3' if full else '2'}×30с",
        ]),
    ]


def _recovery() -> list[tuple[str, list[str]]]:
    return [
        ("Раскатка роллом (5–8 мин)", [
            "Квадрицепс (особенно левый, у колена), голени, грудной отдел",
        ]),
        ("Мобильность", [
            "Раскрытие грудного отдела — 2×10",
            "Мобилизация голеностопа (колено за носок) — 2×10/нога",
        ]),
        ("Плечо (поддержка)", SHOULDER_PREHAB),
        ("Дыхание/сон", [
            "5 мин диафрагмального дыхания — помогает восстановлению и сну.",
        ]),
    ]


def _volleyball(kind: str) -> list[tuple[str, list[str]]]:
    blocks = [
        ("Разминка перед тренировкой (8–10 мин)", [
            "Суставная разминка + 5 мин лёгкое кардио/динамика",
            "Разогрев плеча резинкой — 2×15 (до ударов обязательно)",
            "Голеностоп: круги + баланс — 1–2 мин",
        ]),
    ]
    if kind == "game":
        blocks.append(("Перед игрой", [
            "Несколько подводящих прыжков нарастающе (не с максимума сразу).",
            "Береги правое плечо: первые удары — вполсилы, разогреться.",
        ]))
    blocks.append(("После тренировки", [
        "Заминка + лёгкая растяжка плеча и бедра 3–5 мин.",
        "Если много прыгал — вечером эксцентрика на голеностоп (см. восстановление).",
    ]))
    blocks.append(("Техника", [
        "Ведёт твой тренер — бот в неё не вмешивается. Отметь потом факт: /week.",
    ]))
    return blocks


def _week_var(date_iso: str) -> int:
    try:
        import datetime
        return datetime.date.fromisoformat(date_iso).isocalendar()[1] % 2
    except Exception:
        return 0


def build_workout(session, last_log=None, weights: dict | None = None) -> str:
    """Рендерит конкретную тренировку. last_log — прошлый результат для ориентира,
    weights — текущие рабочие веса (прогрессия)."""
    category = session["category"]
    kind = session["kind"] or ""
    load = session["load"] or "moderate"
    title = session["title"]
    weights = weights or {"squat": 95, "trapbar": 90}
    var = _week_var(session["date"])

    if category == "gym" and kind == "lower":
        blocks = _lower(load, weights, var)
    elif category == "gym":  # upper или добавленный «Зал» — верх плечо-safe
        blocks = _upper(load, var)
    elif category == "recovery":
        blocks = _recovery()
    elif category == "vb":
        blocks = _volleyball(kind)
    else:
        return f"📋 {title}\n\nДля этой сессии упражнения не заданы."

    icon = _CAT_ICON.get(category, "📋")
    lines = [f"{icon} <b>{escape(title)}</b>"]
    if category == "gym" and load in _LOAD_BADGE:
        lines.append(f"{_LOAD_BADGE[load]} нагрузка")
    lines.append("")
    if last_log:
        lines.append(f"📈 <b>Последняя запись</b> · {last_log['date']}")
        lines.append(f"<i>{escape(last_log['text'])}</i>")
        lines.append("<blockquote>Ориентир: добавь вес или повтор, где зашло легко.</blockquote>")
        lines.append("")
    for name, items in blocks:
        lines.append(f"<b>▸ {escape(name)}</b>")
        lines += [f"   • {escape(it)}" for it in items]
        lines.append("")
    lines.append(_DIVIDER)
    tips = []
    if category == "gym":
        tips.append("RPE7 = усилие ~7/10 (2–3 повтора в запасе, не до отказа).")
    tips.append("Незнакомое упражнение — напиши его название, дам разбор и видео.")
    if category in ("gym", "vb"):
        tips.append("📝 Записать веса/повторы — кнопка в меню сессии.")
    lines += [f"<i>{escape(t)}</i>" for t in tips]
    return "\n".join(lines).strip()
