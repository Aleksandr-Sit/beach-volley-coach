"""Общие FSM-состояния (используются разными роутерами)."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Flow(StatesGroup):
    # расписание
    wait_retime = State()        # ждём время «сегодня»
    wait_moveday_time = State()  # ждём время для переноса на день
    wait_add_time = State()      # ждём время для добавляемой тренировки
    confirm_edit = State()       # ждём подтверждения свободной правки
    wait_log = State()           # ждём результат тренировки (веса/повторы)
    # питание
    wait_food = State()          # ждём описание приёма пищи
    wait_weight = State()        # ждём замер веса тела
