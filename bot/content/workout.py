"""Наполнение тренировок: конкретные упражнения под профиль атлета.

Детерминированные данные (0 токенов на рендер). Приоритет — пляжный волейбол:
зал дополняющий, submaximal, плечо-safe (плечо — лимитирующий фактор),
автопрегуляция по нагрузке сессии (heavy/moderate/light).

Рабочие веса берутся из БД (прогрессия), а не хардкодятся. Жимы штанги над
головой избегаем при чувствительном плече; голеностоп и VMO — мониторим;
поясницу защищает кор.
"""
from __future__ import annotations

from html import escape

from . import pools

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
    sq = f"{weights.get('squat', 95):g}"
    tb = f"{weights.get('trapbar', 90):g}"
    blocks = []
    if heavy:
        jumps = pools.pick(pools.JUMP, var)
        blocks.append(("Прыжковый блок (низкий объём, техника приземления)",
                       [pools.line(j, heavy) for j in jumps]))
    # Базовые движения НЕ ротируем: на них висит прогрессия рабочих весов.
    blocks += [
        ("Сила ног", [
            f"Присед со штангой — {'5×5' if heavy else '4×5'} @RPE7 (не до отказа, рабочий ~{sq} кг)",
            f"Тяга трап-гриф — {'3×6' if heavy else '3×5'} (рабочий ~{tb} кг, спина нейтральна)",
        ]),
        ("Односторонняя + задняя цепь", [
            pools.line(pools.pick(pools.UNILATERAL, var), heavy),
            pools.line(pools.pick(pools.POSTERIOR, var), heavy),
        ]),
        ("Голеностоп + кор", ANKLE_PREHAB + [
            pools.line(pools.pick(pools.LOWER_CORE, var), heavy),
        ]),
    ]
    return blocks


def _upper(load: str, var: int) -> list[tuple[str, list[str]]]:
    if load == "light":
        return [("Разгрузка", [
            "Только prehab плеча + лёгкие тяги.",
        ] + SHOULDER_PREHAB)]
    full = load == "heavy"
    return [
        ("Разогрев плеча (обязательно)", SHOULDER_PREHAB),
        ("Жим — только плечо-safe (без штанги над головой)", [
            pools.line(pools.pick(pools.PRESS, var), full),
            "Отжимания на кольцах/от пола нейтрально — 2×макс комфортно",
        ]),
        ("Тяги (приоритет — их больше, чем жимов)", [
            pools.line(pools.pick(pools.ROW, var), full),
            pools.line(pools.pick(pools.VERTICAL, var), full),
        ]),
        ("Плечи здоровые + кор", [
            pools.line(pools.pick(pools.SHOULDER_EXTRA, var), full),
            pools.line(pools.pick(pools.UPPER_CORE, var), full),
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
            "Береги плечо: первые удары — вполсилы, разогреться.",
        ]))
    blocks.append(("После тренировки", [
        "Заминка + лёгкая растяжка плеча и бедра 3–5 мин.",
        "Если много прыгал — вечером эксцентрика на голеностоп (см. восстановление).",
    ]))
    blocks.append(("Техника", [
        "Ведёт твой тренер — бот в неё не вмешивается. Отметь потом факт: /week.",
    ]))
    return blocks


def _deload_blocks(load: str, weights: dict) -> list[tuple[str, list[str]]]:
    """Разгрузочная неделя: объём вниз, веса те же, без прыжкового блока."""
    sq = f"{weights.get('squat', 95):g}"
    tb = f"{weights.get('trapbar', 90):g}"
    return [
        ("Разгрузка — работаем вполсилы", [
            f"Присед — 3×5 @RPE6 (рабочий ~{sq} кг, легко и технично)",
            f"Тяга трап-гриф — 2×5 (рабочий ~{tb} кг)",
            "Без прыжкового блока и без отказа — на этой неделе восстанавливаемся",
        ]),
        ("Поддержка", ANKLE_PREHAB + ["Планка — 2×30с"]),
        ("Плечо", SHOULDER_PREHAB),
    ]


def build_workout(session, last_log=None, weights: dict | None = None,
                  deload: bool = False, variant: int = 0,
                  badge: str | None = None) -> str:
    """Рендерит конкретную тренировку.

    last_log — прошлый результат для ориентира; weights — рабочие веса;
    variant — номер варианта подсобки (блок мезоцикла + ручной сдвиг), см.
    content/pools.py; badge — «Блок 2 · неделя 3 из 4» для наглядности ротации.
    """
    category = session["category"]
    kind = session["kind"] or ""
    load = session["load"] or "moderate"
    title = session["title"]
    weights = weights or {"squat": 95, "trapbar": 90}
    var = variant

    if category == "gym" and deload:
        blocks = _deload_blocks(load, weights)
    elif category == "gym" and kind == "lower":
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
    if badge and category in ("gym", "recovery"):
        lines.append(f"<i>🔄 {escape(badge)}</i>")
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
        tips.append("Надоело упражнение — «🔁 Другой вариант» сменит подсобку "
                    "на эту неделю.")
    tips.append("Незнакомое упражнение — напиши его название, дам разбор и видео.")
    if category in ("gym", "vb"):
        tips.append("📝 Записать веса/повторы — кнопка в меню сессии.")
    lines += [f"<i>{escape(t)}</i>" for t in tips]
    return "\n".join(lines).strip()
