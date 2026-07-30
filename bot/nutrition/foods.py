"""База продуктов атлета + грубая оценка приёма пищи по тексту (без LLM).

Оценка приблизительная (порции по умолчанию), помечается «≈». Для незнакомого/
фото — фолбэк на LLM в обработчике. Значения — на типовую порцию (base_g).
"""
from __future__ import annotations

import re

# (имя, [ключи-подстроки], base_g, kcal, protein, тип: count|weight)
FOODS: list[tuple[str, list[str], int, int, float, str]] = [
    ("Овсянка", ["овсян", "геркулес"], 50, 180, 6.0, "weight"),
    ("Рис", ["рис"], 150, 195, 3.6, "weight"),
    ("Гречка", ["гречк", "гречн"], 150, 165, 6.0, "weight"),
    ("Индейка (сырая)", ["индейк", "индюш"], 100, 130, 19.0, "weight"),
    ("Курица (сырая)", ["куриц", "куриную", "курин", "грудк"], 100, 113, 23.0, "weight"),
    ("Творог 9%", ["творог", "творож"], 150, 218, 19.5, "weight"),
    ("Греческий йогурт", ["греческ", "греч йогурт", "греческий йогурт"], 150, 100, 12.0, "weight"),
    # Кофе — ДО «Молоко», иначе «кофе с молоком» посчитается как стакан молока.
    ("Капучино/латте", ["кофе капучино", "кофе латте", "кофе раф", "капучино", "латте",
                        "раф", "cappuccino", "latte", "флэт", "flat"], 250, 90, 4.5, "count"),
    ("Американо", ["кофе американо", "кофе эспрессо", "черный кофе", "американо",
                   "эспрессо", "espresso"], 200, 5, 0.3, "count"),
    ("Кофе с молоком", ["кофе с молоком", "кофе"], 200, 45, 2.0, "count"),
    ("Молоко", ["молок"], 200, 120, 6.0, "weight"),
    ("Йогурт", ["йогурт"], 150, 90, 7.0, "weight"),
    ("Яйцо", ["яйц", "яиц"], 55, 80, 7.0, "count"),
    ("Банан", ["банан"], 100, 100, 1.3, "count"),
    ("Яблоко", ["яблок"], 100, 90, 0.5, "count"),
    ("Хлеб с семенами", ["хлеб", "ломт", "тост"], 40, 90, 3.0, "count"),
    ("Масло сливочное", ["масло"], 10, 75, 0.05, "weight"),
    ("Орехи", ["орех", "миндал", "грецк", "кешью"], 30, 190, 5.0, "weight"),
    ("Тыквенные семечки", ["семечк", "тыквенн"], 30, 170, 8.0, "weight"),
    ("Сухофрукты", ["сухофрукт", "изюм", "курага", "финик", "чернослив"], 40, 110, 1.0, "weight"),
    ("Мёд", ["мёд", "мед "], 20, 60, 0.0, "weight"),
    ("Протеин", ["протеин", "изолят", "whey"], 30, 120, 24.0, "weight"),
    ("Овощи", ["помидор", "огур", "перец", "салат", "овощ"], 100, 20, 1.0, "weight"),
]


def parse_manual(text: str) -> tuple[float | None, float | None]:
    """Явные числа из текста: «500 ккал», «25 белка». Приоритетнее базы и ИИ."""
    t = text.lower().replace(",", ".")
    km = re.search(r"(\d+(?:\.\d+)?)\s*(?:ккал|kcal|кал\b)", t)
    pm = (re.search(r"(\d+(?:\.\d+)?)\s*г?\s*белк", t)
          or re.search(r"белк\w*\s*[:\-]?\s*(\d+(?:\.\d+)?)", t))
    kcal = float(km.group(1)) if km else None
    protein = float(pm.group(1)) if pm else None
    return kcal, protein


def parse_product(text: str) -> tuple[str, int, float, float, str] | None:
    """Разбирает «добавь продукт: рис бурый 130 3 [шт]» → (name, base_g, kcal, prot, type).
    Числа: ккал и белок на 100 г (или на 1 шт, если есть «шт»)."""
    t = re.sub(r"^\s*(?:добавь|новый)?\s*продукт[:\s-]*", "", text.strip(), flags=re.IGNORECASE)
    is_count = bool(re.search(r"\bшт\b|штук", t.lower()))
    nums = re.findall(r"\d+(?:[.,]\d+)?", t)
    if len(nums) < 2:
        return None
    kcal = float(nums[-2].replace(",", "."))
    protein = float(nums[-1].replace(",", "."))
    name = re.sub(r"\s+", " ", t[: t.find(nums[-2])]).strip().lower()
    if not name:
        return None
    return (name, 1 if is_count else 100, kcal, protein, "count" if is_count else "weight")


def estimate_meal(text: str, extra: list[tuple] | None = None) -> tuple[int, int, list[str]]:
    """Оценивает (ккал, белок, список продуктов). Находит ВСЕ продукты в тексте
    (не только через запятую) и привязывает к каждому ближайшее число — работает
    и для «рис 200 индейка 150», и для «3 яйца», и для повторов. extra — свои
    продукты (приоритетнее встроенной базы)."""
    t = text.lower().replace("ё", "е")
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)  # десятичная запятая 0,5 -> 0.5
    catalog = (extra or []) + FOODS

    # 1) все вхождения продуктов (позиция, конец, продукт)
    cands = []
    for food in catalog:
        for k in food[1]:
            kk = k.lower().replace("ё", "е")
            start = 0
            while (p := t.find(kk, start)) != -1:
                cands.append((p, p + len(kk), food))
                start = p + len(kk)
    if not cands:
        return 0, 0, []
    # предпочесть более длинное совпадение при пересечении (греческий йогурт > йогурт)
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    anchors: list[tuple] = []
    for p, e, food in cands:
        if all(e <= a[0] or p >= a[1] for a in anchors):
            anchors.append((p, e, food))
    anchors.sort()

    # 2) числовые токены (кроме тех, что помечают ккал/белок — это ручные макросы)
    nums = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(г|гр|шт|мл)?", t):
        tail = t[m.end(): m.end() + 6]
        if re.match(r"\s*(ккал|kcal|кал|белк|бел\b)", tail):
            continue
        nums.append((m.start(), float(m.group(1)), m.group(2)))

    # 3) каждое число → ближайший продукт (одно число на продукт)
    qty_of: dict[int, tuple] = {}
    for npos, val, unit in nums:
        best, best_d = None, 1e9
        for i, (ap, ae, _f) in enumerate(anchors):
            d = 0 if ap <= npos <= ae else min(abs(npos - ap), abs(npos - ae))
            if d < best_d:
                best_d, best = d, i
        if best is not None and best not in qty_of:
            qty_of[best] = (val, unit)

    total_k = total_p = 0.0
    matched: list[str] = []
    for i, (ap, ae, food) in enumerate(anchors):
        name, _keys, base_g, kcal, prot, typ = food
        qty, unit = qty_of.get(i, (None, None))
        if qty is None and re.search(r"половин|\bпол[\s-]|полбанан|пол[- ]?яблок",
                                     t[max(0, ap - 8):ae + 2]):
            qty = 0.5  # «полбанана», «половина»
        if typ == "count":
            n = qty if (qty and qty <= 20 and unit in (None, "шт")) else 1.0
            total_k += kcal * n
            total_p += prot * n
        else:  # weight
            factor = qty / base_g if (qty and (unit in ("г", "гр", "мл") or qty >= 30)) else 1.0
            total_k += kcal * factor
            total_p += prot * factor
        matched.append(name)
    return round(total_k), round(total_p), matched
