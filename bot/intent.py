"""Детерминированный разбор частых правок — БЕЗ LLM (быстро, 0 квоты).

Покрывает: отмену (дождь/отменили), перенос на день (завтра/пн..вс),
перенос по времени (в HH:MM сегодня), добавление. Для нестандартных
формулировок вызывающий код падает обратно на LLM (coach.parse_edit).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .db import DB
from .modules.schedule_sync import WEEKDAY_RU

_WEEKDAYS = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "среда": 2, "среду": 2, "ср": 2,
    "четверг": 3, "чт": 3,
    "пятница": 4, "пятницу": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "сб": 5,
    "воскресенье": 6, "вс": 6,
}

_CANCEL = ("отмен", "дожд", "не буд", "не пойд", "пропущ", "болею", "заболел", "отдыха")
_MOVE = ("перенес", "перенос", "сдвин", "подвин")
_ADD = ("добавь", "поставь", "запиши")


def _resolve_date(text: str, today: date) -> date | None:
    t = text.lower()
    if "послезавтра" in t:
        return today + timedelta(days=2)
    if "завтра" in t:
        return today + timedelta(days=1)
    if "сегодня" in t:
        return today
    for name, wd in _WEEKDAYS.items():
        if re.search(r"\b" + re.escape(name) + r"\b", t):
            delta = (wd - today.weekday()) % 7
            if delta == 0:  # назвали день недели → ближайший следующий, не сегодня
                delta = 7
            return today + timedelta(days=delta)
    return None


def normalize_time(text: str) -> str | None:
    """Приводит ввод времени к HH:MM. Принимает 11:00, 11;00, 11.00, 11,00,
    11 00, 1100, 11, 19ч. Разделитель ; и , — потому что на раскладке они рядом с :."""
    t = text.strip().lower()
    m = re.fullmatch(r"([01]?\d|2[0-3])\s*[:.;,\s]\s*([0-5]\d)", t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.fullmatch(r"([01]?\d|2[0-3])([0-5]\d)", t)  # 1130
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.fullmatch(r"([01]?\d|2[0-3])\s*(?:ч|час\w*)?", t)  # «19», «19ч», «19 часов»
    if m:
        return f"{int(m.group(1)):02d}:00"
    return None


def _extract_duration(text: str) -> int | None:
    """Длительность в минутах из «2ч», «3 часа», «1,5ч», «90 мин», «90м»."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:ч|час)", text)
    if m:
        return int(round(float(m.group(1).replace(",", ".")) * 60))
    m = re.search(r"(\d+)\s*(?:м|мин)", text)
    if m:
        return int(m.group(1))
    return None


def parse_time_and_duration(text: str) -> tuple[str | None, int | None]:
    """Разбирает время + длительность в разных форматах:
    «18:00 90», «18:00 2ч», «20 00 3 часа», «18 2ч», «10:00 1,5ч».
    Длительность — в минутах. Любой из двух может отсутствовать."""
    t = text.strip().lower()
    dur = _extract_duration(t)
    # убрать длительность из строки, чтобы не мешала разбору времени
    t_wo = re.sub(r"\d+(?:[.,]\d+)?\s*(?:ч|час\w*|мин\w*|м)\b", " ", t).strip() if dur else t
    tokens = t_wo.split()
    sep_tok = next((tok for tok in tokens if re.search(r"[:.;,]", tok)), None)
    if sep_tok:
        tm = normalize_time(sep_tok)
        if dur is None:  # запасной вариант: голое число минут после времени
            for tok in tokens:
                if tok != sep_tok and re.fullmatch(r"\d{1,3}", tok):
                    dur = int(tok)
                    break
        return tm, dur
    tm = normalize_time(t_wo) if t_wo else None
    return tm, dur


def _extract_time(text: str) -> str | None:
    """Ищет время внутри фразы (нужен разделитель, чтобы не ловить любые числа)."""
    m = re.search(r"\b([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)\b", text)
    if not m:
        m = re.search(r"\b([01]?\d|2[0-3])\s+([0-5]\d)\b", text)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def parse_local(db: DB, today: date, text: str) -> dict | None:
    """Возвращает intent-словарь (как coach.parse_edit) или None, если не распознано."""
    t = text.lower()
    tm = _extract_time(text)
    target = _resolve_date(text, today)
    today_sessions = db.sessions_for(today.isoformat())

    def pick_today() -> int | None:
        if len(today_sessions) == 1:
            return today_sessions[0]["id"]
        for s in today_sessions:
            if ("зал" in t or "силов" in t) and s["category"] == "gym":
                return s["id"]
            if "игр" in t and s["kind"] == "game":
                return s["id"]
            if "техник" in t and s["kind"] == "technique":
                return s["id"]
            if ("мобил" in t or "восстанов" in t) and s["category"] == "recovery":
                return s["id"]
        return None

    is_cancel = any(w in t for w in _CANCEL)
    is_move = any(w in t for w in _MOVE)
    is_add = any(w in t for w in _ADD)

    # 1) перенос на другой день
    if is_move and target and target != today:
        sid = pick_today()
        if sid:
            when = f" в {tm}" if tm else ""
            return {"action": "move", "session_id": sid, "target_date": target.isoformat(),
                    "target_time": tm, "human": f"перенести тренировку на "
                    f"{WEEKDAY_RU[target.weekday()]} {target.strftime('%d.%m')}{when}"}

    # 2) отмена сегодня (дождь и т.п.)
    if is_cancel and not is_move:
        sid = pick_today()
        if sid:
            return {"action": "cancel", "session_id": sid,
                    "human": "отменить сегодняшнюю тренировку"}

    # 3) перенос по времени сегодня
    if tm and not is_move and (target is None or target == today):
        sid = pick_today()
        if sid:
            return {"action": "retime", "session_id": sid, "target_time": tm,
                    "human": f"перенести сегодняшнюю тренировку на {tm}"}

    # 4) добавить сессию
    if is_add and target:
        return {"action": "add", "target_date": target.isoformat(), "target_time": tm,
                "new_title": "Тренировка", "new_category": "vb",
                "human": f"добавить тренировку на {WEEKDAY_RU[target.weekday()]} "
                f"{target.strftime('%d.%m')}"}

    return None
