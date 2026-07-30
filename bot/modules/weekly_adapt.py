"""weekly-adapt: недельный разбор (факт vs план) + авто-прогрессия рабочих весов.

Детерминированные правила (0 токенов). Приоритет — волейбол, зал дополняющий,
прогрессия консервативная (40 лет, submaximal): +2.5 кг при хорошей неделе,
иначе держим/осторожно.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..db import DB
from .schedule_sync import monday_of

STEP = 2.5  # микро-прогрессия, кг
_HARD = ("тяжел", "не добил", "трудно", "не смог", "тяжко")

# По каким словам в записи понимаем, что движение реально делалось.
LIFT_KEYS = {
    "squat": ("присед", "squat"),
    "trapbar": ("трап", "трэп", "trapbar", "становая"),
}

# ВАЖНО: только целые слова. Подстрока «бол» ловила «Болгарский сплит-присед»
# и «больше» → прогрессия блокировалась навсегда.
_PAIN_RE = re.compile(
    r"\b(бол[ьи]|болит|болел[оа]?|больно|заболел\w*|ноет|ноют|"
    r"прострел\w*|щ[ёе]лк\w*|дискомфорт\w*|тянет плечо)\b",
    re.IGNORECASE,
)


def has_pain(text: str) -> bool:
    """Есть ли в записи признак боли (целые слова, без ложных срабатываний)."""
    return bool(_PAIN_RE.search(text or ""))


DELOAD_EVERY = 5  # каждая 5-я неделя прогрессии — разгрузочная


def is_deload_week(db: DB) -> bool:
    """Пора ли разгружаться: каждые DELOAD_EVERY применённых недель прогрессии.

    Для 40+ и submaximal-режима разгрузка раз в ~5 недель снижает риск травмы
    и позволяет весам расти дальше.
    """
    applied = db.conn.execute(
        "SELECT COUNT(*) c FROM progression_applied").fetchone()["c"]
    return applied > 0 and applied % DELOAD_EVERY == 0


def _fmt_weights(weights: dict) -> str:
    names = {"squat": "присед", "trapbar": "тяга"}
    return ", ".join(f"{names.get(k, k)} {v:g} кг" for k, v in sorted(weights.items()))


def _progression(db: DB, start: str, end: str, tired: int,
                 apply: bool = True) -> tuple[dict, str]:
    """Решает прогрессию по записям зала за неделю. Возвращает (изменения, комментарий).

    Идемпотентно: веса меняются РОВНО ОДИН РАЗ за неделю. Повторный /review
    только показывает итог, ничего не накручивая.
    """
    if db.is_progression_applied(start):
        return {}, (f"Прогрессия за эту неделю уже применена. Текущие рабочие: "
                    f"{_fmt_weights(db.get_weights())}.")

    gym_logs = db.logs_between(start, end, category="gym")
    weights = db.get_weights()
    if not gym_logs:
        return {}, ("Записей зала за неделю нет — веса не меняю. Логируй результат "
                    "кнопкой «📝 Записать результат», тогда буду вести прогрессию.")
    text = " ".join((r["text"] or "") for r in gym_logs)
    if has_pain(text):
        return {}, "Была боль — прогрессию держу, добавь prehab и не форсируй."
    if any(h in text.lower() for h in _HARD) or tired >= 2:
        return {}, "Неделя тяжёлая (усталость/тяжело шло) — веса держим, закрепляемся."

    # Прибавляем только тем движениям, которые реально делались на неделе,
    # иначе неделя одного верха поднимала бы и присед.
    done = {k for k, keys in LIFT_KEYS.items()
            if any(kw in text.lower() for kw in keys)}
    if not done:
        return {}, ("В записях нет базовых (присед / трап-гриф) — веса не меняю. "
                    "Прибавлю к тому, что реально делал.")

    changes = {k: round(v + STEP, 1) for k, v in weights.items() if k in done}
    if apply:
        for k, v in changes.items():
            db.set_weight(k, v)
        db.mark_progression_applied(start)
    return changes, "Шло нормально — микро-прогрессия +2.5 кг на том, что делал."


def run_weekly_adapt(db: DB, ref_date: date) -> str:
    """Разбирает НЕДЕЛЮ, содержащую ref_date, применяет прогрессию, возвращает текст."""
    mon = monday_of(ref_date)
    sun = mon + timedelta(days=6)
    start, end = mon.isoformat(), sun.isoformat()

    sessions = db.sessions_between(start, end)
    vb = [s for s in sessions if s["category"] == "vb"]
    gym = [s for s in sessions if s["category"] == "gym"]
    vb_done = sum(1 for s in vb if s["status"] in ("done", "confirmed"))
    vb_cancel = sum(1 for s in vb if s["status"] == "cancelled")
    gym_done = sum(1 for s in gym if s["status"] == "done")
    games = sum(1 for s in vb if s["kind"] == "game" and s["status"] != "cancelled")

    checkins = db.checkins_between(start, end)
    rc = {"fresh": 0, "ok": 0, "tired": 0}
    for c in checkins:
        if c["readiness"] in rc:
            rc[c["readiness"]] += 1

    changes, prog_comment = _progression(db, start, end, rc["tired"])
    div = "➖➖➖➖➖➖➖➖➖➖"
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]

    if mon.month == sun.month:
        rng = f"{mon.day}–{sun.day} {months[sun.month - 1]}"
    else:
        rng = f"{mon.day} {months[mon.month - 1]} – {sun.day} {months[sun.month - 1]}"
    lines = ["📊 <b>Итоги недели</b>", f"<i>{rng}</i>", div]
    vb_extra = f" · отменено {vb_cancel}" if vb_cancel else ""
    lines.append(f"🏐 Волейбол · <b>{len(vb)}</b> сессий (выполнено {vb_done}{vb_extra})")
    lines.append(f"🏋️ Зал · <b>{len(gym)}</b> (записано {gym_done})")
    if any(rc.values()):
        lines.append(f"💬 Самочувствие · 😀×{rc['fresh']} · 😐×{rc['ok']} · 😩×{rc['tired']}")
    lines.append("")

    lines.append("📈 <b>Прогрессия на следующую неделю</b>")
    if changes:
        names = {"squat": "Присед", "trapbar": "Тяга трап-гриф"}
        for k, v in changes.items():
            lines.append(f"   • {names.get(k, k)} → <b>{v:g} кг</b>")
    lines.append(f"   <i>{prog_comment}</i>")
    lines.append("")

    # Вес тела: замыкает петлю питания (растёт ли по плану).
    w = db.latest_weight()
    if w:
        trend = db.weight_trend()
        t_s = ""
        if trend is not None:
            arrow = "↗️" if trend > 0 else ("↘️" if trend < 0 else "→")
            t_s = f" · {arrow} {trend:+g} кг за месяц"
        lines.insert(4, f"⚖️ Вес · <b>{w[1]:g} кг</b>{t_s}")

    if is_deload_week(db):
        lines.append("🪫 <b>Разгрузочная неделя</b>")
        lines.append("   <i>Работал 4 недели подряд — снижаю объём: подходов "
                     "меньше, веса те же, прыжковый блок убираю. Это часть плана, "
                     "а не откат.</i>")
        lines.append("")

    lines.append("💡 <b>Рекомендации</b>")
    lines.append("   • Плечо (главный лимит): ротаторы и face pull — каждую неделю.")
    if w is None:
        lines.append("   • Записывай вес (⚖️ в «Питание») — без него цель по "
                     "калориям не подстраивается.")
    if games >= 2:
        lines.append("   • Много игровых/прыжков → вечерняя эксцентрика на голеностоп + сон 8+ч.")
    if rc["tired"] >= 2:
        lines.append("   • Была усталость → приоритет сну и восстановлению, не геройствуй.")
    if vb_cancel and vb_done <= 2:
        lines.append("   • Волейбола мало — если погода мешает, добавь зал/технику дома.")
    lines.append("")
    lines.append("<i>В зале на след. неделю сменю часть подсобки — чтобы не застаиваться.</i>")
    return "\n".join(lines)
