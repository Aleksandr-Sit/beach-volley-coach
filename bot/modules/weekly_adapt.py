"""weekly-adapt: недельный разбор (факт vs план) + авто-прогрессия рабочих весов.

Детерминированные правила (0 токенов). Приоритет — волейбол, зал дополняющий,
прогрессия консервативная (submaximal): +2.5 кг при хорошей неделе,
иначе держим/осторожно.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..db import DB
from .schedule_sync import WEEKDAY_RU, monday_of

STEP = 2.5  # микро-прогрессия, кг
_HARD = ("тяжел", "не добил", "трудно", "не смог", "тяжко")
_PAIN = ("бол", "прострел", "щёлк", "щелк")


def _progression(db: DB, start: str, end: str, tired: int) -> tuple[dict, str]:
    """Решает прогрессию по записям зала за неделю. Возвращает (изменения, комментарий)."""
    gym_logs = db.logs_between(start, end, category="gym")
    weights = db.get_weights()
    if not gym_logs:
        return {}, ("Записей зала за неделю нет — веса не меняю. Логируй результат "
                    "кнопкой «📝 Записать результат», тогда буду вести прогрессию.")
    text = " ".join((r["text"] or "").lower() for r in gym_logs)
    if any(p in text for p in _PAIN):
        return {}, "Была боль — прогрессию держу, добавь prehab и не форсируй."
    if any(h in text for h in _HARD) or tired >= 2:
        return {}, "Неделя тяжёлая (усталость/тяжело шло) — веса держим, закрепляемся."
    changes = {k: round(v + STEP, 1) for k, v in weights.items()}
    for k, v in changes.items():
        db.set_weight(k, v)
    return changes, "Шло нормально — микро-прогрессия +2.5 кг на базовых."


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

    lines.append("💡 <b>Рекомендации</b>")
    lines.append("   • Плечо (главный лимит): ротаторы и face pull — каждую неделю.")
    if games >= 2:
        lines.append("   • Много игровых/прыжков → вечерняя эксцентрика на голеностоп + сон 8+ч.")
    if rc["tired"] >= 2:
        lines.append("   • Была усталость → приоритет сну и восстановлению, не геройствуй.")
    if vb_cancel and vb_done <= 2:
        lines.append("   • Волейбола мало — если погода мешает, добавь зал/технику дома.")
    lines.append("")
    lines.append("<i>В зале на след. неделю сменю часть подсобки — чтобы не застаиваться.</i>")
    return "\n".join(lines)
