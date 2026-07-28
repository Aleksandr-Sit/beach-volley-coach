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
    ("Капучино/латте", ["капучино", "латте", "раф", "cappuccino", "latte", "флэт", "flat"], 250, 90, 4.5, "count"),
    ("Американо", ["американо", "эспрессо", "espresso", "черный кофе"], 200, 5, 0.3, "count"),
    ("Кофе с молоком", ["кофе"], 200, 45, 2.0, "count"),
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
    """Грубо оценивает (ккал, белок, список продуктов). Разбирает по сегментам
    (запятая/точка с запятой/«и»), число ищет в пределах своего сегмента.
    extra — свои продукты (проверяются ПЕРЕД встроенной базой)."""
    t = text.lower().replace("ё", "е")
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)  # десятичная запятая 0,5 -> 0.5 (до сплита)
    segments = re.split(r"\s*[,;]\s*|\s+и\s+", t)
    total_k = 0.0
    total_p = 0.0
    matched: list[str] = []
    catalog = (extra or []) + FOODS
    for seg in segments:
        for name, keys, base_g, kcal, prot, typ in catalog:
            if not any(k.replace("ё", "е") in seg for k in keys):
                continue
            qm = re.search(r"(\d+(?:[.,]\d+)?)\s*(г|гр|шт|мл)?", seg)
            if qm:
                qty = float(qm.group(1).replace(",", "."))
                unit = qm.group(2)
            elif re.search(r"половин|\bпол[\s-]|полбанан|пол[- ]?яблок", seg):
                qty, unit = 0.5, None  # «полбанана», «половина», «пол яблока»
            else:
                qty, unit = None, None
            if typ == "count":
                n = qty if (qty and qty <= 20 and unit in (None, "шт")) else 1.0
                k_add, p_add = kcal * n, prot * n
            else:  # weight
                if qty and (unit in ("г", "гр", "мл") or qty >= 30):
                    factor = qty / base_g
                else:
                    factor = 1.0
                k_add, p_add = kcal * factor, prot * factor
            total_k += k_add
            total_p += p_add
            matched.append(name)
            break  # один продукт на сегмент
    return round(total_k), round(total_p), matched
