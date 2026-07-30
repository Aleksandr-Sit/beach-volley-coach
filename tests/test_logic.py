"""Тесты чистой логики: прогрессия, генерация недели, разбор питания и времени.

Не требуют aiogram/сети — только stdlib + модули проекта.
Запуск: python -m pytest -q
"""
from __future__ import annotations

import datetime

import pytest

from bot.db import DB
from bot.intent import normalize_time, parse_time_and_duration
from bot.modules.profile_seed import seed_if_empty
from bot.modules.schedule_sync import autoregulate_week, generate_week
from bot.modules.weekly_adapt import has_pain, is_deload_week, run_weekly_adapt
from bot.mutations import move_session
from bot.nutrition.foods import estimate_meal, parse_manual, parse_product
from bot.nutrition.targets import compute_targets

MON = datetime.date(2026, 7, 27)  # понедельник
SUN = datetime.date(2026, 8, 2)


@pytest.fixture()
def db(tmp_path) -> DB:
    d = DB(str(tmp_path / "test.db"))
    seed_if_empty(d)
    return d


# ---------------------------------------------------------------- боль
class TestPainDetection:
    """Регрессия: подстрока «бол» ловила «Болгарский» и «больше»."""

    @pytest.mark.parametrize("text", [
        "Болгарский сплит-присед — 3×8",
        "присед 100 5×5, больше не смог",
        "сделал больше повторов",
        "присед 100, всё ок",
    ])
    def test_no_false_positive(self, text):
        assert has_pain(text) is False

    @pytest.mark.parametrize("text", [
        "плечо болит",
        "боль в плече на жиме",
        "прострелило поясницу",
        "щёлкает плечо",
        "колено ноет после игры",
        "дискомфорт в плече",
    ])
    def test_detects_real_pain(self, text):
        assert has_pain(text) is True


# -------------------------------------------------- прогрессия (идемпотентность)
class TestProgression:
    def _log_good_week(self, db: DB) -> None:
        gym = [s for s in db.sessions_between(MON.isoformat(), SUN.isoformat())
               if s["category"] == "gym"][0]
        db.add_workout_log(gym["id"], "gym", gym["kind"], MON.isoformat(),
                           "присед 95 5×5, легко")

    def test_applies_once_per_week(self, db):
        generate_week(db, MON)
        self._log_good_week(db)
        before = db.get_weights()

        run_weekly_adapt(db, MON)
        after_first = db.get_weights()
        assert after_first["squat"] == before["squat"] + 2.5

        for _ in range(5):  # повторные /review не должны ничего менять
            run_weekly_adapt(db, MON)
        assert db.get_weights() == after_first

    def test_pain_blocks_progression(self, db):
        generate_week(db, MON)
        gym = [s for s in db.sessions_between(MON.isoformat(), SUN.isoformat())
               if s["category"] == "gym"][0]
        db.add_workout_log(gym["id"], "gym", gym["kind"], MON.isoformat(),
                           "жим 40, плечо болит")
        before = db.get_weights()
        run_weekly_adapt(db, MON)
        assert db.get_weights() == before

    def test_bulgarian_split_does_not_block(self, db):
        """Ключевая регрессия: болгарский сплит больше не читается как «боль»."""
        generate_week(db, MON)
        gym = [s for s in db.sessions_between(MON.isoformat(), SUN.isoformat())
               if s["category"] == "gym"][0]
        db.add_workout_log(gym["id"], "gym", gym["kind"], MON.isoformat(),
                           "Присед — 100 5×5; Болгарский сплит-присед — 3×8")
        before = db.get_weights()
        run_weekly_adapt(db, MON)
        assert db.get_weights()["squat"] == before["squat"] + 2.5

    def test_no_logs_no_change(self, db):
        generate_week(db, MON)
        before = db.get_weights()
        run_weekly_adapt(db, MON)
        assert db.get_weights() == before

    def test_only_logged_lifts_progress(self, db):
        """Неделя одного верха не должна поднимать присед."""
        generate_week(db, MON)
        gym = [s for s in db.sessions_between(MON.isoformat(), SUN.isoformat())
               if s["category"] == "gym"][0]
        db.add_workout_log(gym["id"], "gym", gym["kind"], MON.isoformat(),
                           "Жим гантелей — 2×12.5; Тяга гантели в наклоне — 22.5×10")
        before = db.get_weights()
        run_weekly_adapt(db, MON)
        assert db.get_weights() == before  # присед/трап не трогаем

    def test_squat_only_progresses_squat(self, db):
        generate_week(db, MON)
        gym = [s for s in db.sessions_between(MON.isoformat(), SUN.isoformat())
               if s["category"] == "gym"][0]
        db.add_workout_log(gym["id"], "gym", gym["kind"], MON.isoformat(),
                           "Присед со штангой — 95 5×5")
        before = db.get_weights()
        run_weekly_adapt(db, MON)
        after = db.get_weights()
        assert after["squat"] == before["squat"] + 2.5
        assert after["trapbar"] == before["trapbar"]  # трап не делал — не растёт


# ------------------------------------------------------- генерация недели
class TestWeekGeneration:
    def test_generates_template(self, db):
        generate_week(db, MON)
        sessions = db.sessions_between(MON.isoformat(), SUN.isoformat())
        assert len(sessions) == 8  # 5 волейбол + зал ноги/верх + восстановление

    def test_idempotent(self, db):
        generate_week(db, MON)
        generate_week(db, MON)
        assert len(db.sessions_between(MON.isoformat(), SUN.isoformat())) == 8

    def test_move_to_next_week_does_not_block_it(self, db):
        """Регрессия: перенос сессии на будущую неделю убивал её генерацию."""
        generate_week(db, MON)
        sunday_session = db.sessions_for(SUN.isoformat())[0]
        move_session(db, sunday_session["id"], "2026-08-09")  # Вс следующей недели

        next_mon = MON + datetime.timedelta(days=7)
        generate_week(db, next_mon)
        nxt = db.sessions_between(next_mon.isoformat(),
                                  (next_mon + datetime.timedelta(days=6)).isoformat())
        assert len(nxt) == 9  # 8 из шаблона + перенесённая

    def test_backfill_prevents_duplication(self, tmp_path):
        """Существующая база (без реестра) не должна продублировать неделю."""
        path = str(tmp_path / "legacy.db")
        d1 = DB(path)
        seed_if_empty(d1)
        generate_week(d1, MON)
        d1.conn.execute("DELETE FROM generated_weeks")  # имитируем старую схему
        d1.conn.commit()
        d1.conn.close()

        d2 = DB(path)
        seed_if_empty(d2)  # запускает backfill
        generate_week(d2, MON)
        assert len(d2.sessions_between(MON.isoformat(), SUN.isoformat())) == 8


# --------------------------------------------------- автопрегуляция нагрузки
class TestAutoregulation:
    def _mon_gym(self, db):
        return [s for s in db.sessions_between(MON.isoformat(), SUN.isoformat())
                if s["kind"] == "lower"][0]

    def test_tired_lowers_then_restores(self, db):
        """Регрессия «храповик»: нагрузка должна ВОЗВРАЩАТЬСЯ, а не падать навсегда."""
        generate_week(db, MON)
        gym = self._mon_gym(db)
        assert gym["load"] == "heavy"

        db.add_checkin(gym["date"], readiness="tired")
        autoregulate_week(db, MON)
        assert db.get_session(gym["id"])["load"] == "light"

        db.add_checkin(gym["date"], readiness="fresh")  # отдохнул
        autoregulate_week(db, MON)
        assert db.get_session(gym["id"])["load"] == "heavy"  # вернулось

    def test_idempotent(self, db):
        generate_week(db, MON)
        gym = self._mon_gym(db)
        for _ in range(3):
            autoregulate_week(db, MON)
        assert db.get_session(gym["id"])["load"] == "heavy"

    def test_time_hint_survives_autoregulation(self, db):
        """Регрессия: автопрегуляция затирала notes и «утро» терялось."""
        generate_week(db, MON)
        vb = [s for s in db.sessions_between(MON.isoformat(), SUN.isoformat())
              if s["category"] == "vb"][0]
        assert vb["time_hint"] == "утро"
        db.add_checkin(vb["date"], readiness="tired")
        autoregulate_week(db, MON)
        assert db.get_session(vb["id"])["time_hint"] == "утро"


class TestCheckins:
    def test_one_per_day(self, db):
        """Регрессия: каждый тап плодил строку, разбор считал tired дважды."""
        for r in ("tired", "tired", "fresh"):
            db.add_checkin("2026-07-27", readiness=r)
        rows = db.checkins_between("2026-07-27", "2026-08-02")
        assert len(rows) == 1
        assert rows[0]["readiness"] == "fresh"  # победил последний ответ

    def test_dedupe_migration(self, tmp_path):
        """Старая база с дублями схлопывается миграцией."""
        path = str(tmp_path / "legacy.db")
        d1 = DB(path)
        d1.conn.execute("DROP INDEX IF EXISTS idx_checkins_date")
        for r in ("tired", "tired", "fresh"):
            d1.conn.execute(
                "INSERT INTO checkins(date, readiness, created_at) VALUES(?,?,?)",
                ("2026-07-27", r, "x"))
        d1.conn.execute("PRAGMA user_version = 1")  # откатываем к версии до дедупа
        d1.conn.commit()
        d1.conn.close()

        d2 = DB(path)
        rows = d2.checkins_between("2026-07-27", "2026-07-27")
        assert len(rows) == 1 and rows[0]["readiness"] == "fresh"


class TestClock:
    def test_today_uses_athlete_timezone(self):
        """Дата берётся в поясе атлета, а не сервера (контейнер жил в UTC)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from bot.clock import today
        assert today() == datetime.now(ZoneInfo("Europe/Samara")).date()


# ------------------------------------------------------------- питание
class TestMealEstimation:
    def test_without_commas(self):
        """Регрессия: без запятых считался только первый продукт."""
        k, p, m = estimate_meal(
            "Овсянка 100 яблоко 0,5 банан 0,5 масло 10 творог 150 греческий йогурт 100")
        assert len(m) == 6
        assert 750 < k < 900

    def test_repeats_accumulate(self):
        """Регрессия: три банана считались как один."""
        k1, _, _ = estimate_meal("банан 1")
        k3, _, m = estimate_meal("банан 1, банан 1, банан 1")
        assert m.count("Банан") == 3
        assert k3 == pytest.approx(k1 * 3, rel=0.01)

    @pytest.mark.parametrize("text,expected_kcal", [
        ("яблоко 0,5", 45),      # дробь = половина штуки, не полкило
        ("полбанана", 50),
        ("американо", 5),
        ("кофе с молоком", 45),  # не должен задваиваться с «молоко»
    ])
    def test_quantities(self, text, expected_kcal):
        k, _, _ = estimate_meal(text)
        assert k == pytest.approx(expected_kcal, abs=5)

    def test_unknown_dish_falls_through(self):
        """Блюдо из кафе не должно ложно матчиться (раньше ловило масло)."""
        k, p, m = estimate_meal("панкейки с пряной грушей и сливочным кремом 240г")
        assert m == [] and k == 0

    def test_manual_macros_parsed(self):
        assert parse_manual("панкейки 240г 495 ккал") == (495.0, None)
        assert parse_manual("салат 450 ккал 25 белка") == (450.0, 25.0)

    def test_custom_product_roundtrip(self, db):
        assert parse_product("добавь продукт: рис бурый 130 3") == (
            "рис бурый", 100, 130.0, 3.0, "weight")
        db.add_custom_food("рис бурый", 100, 130, 3.0, "weight")
        k, _, m = estimate_meal("бурый рис 200", extra=db.get_custom_foods())
        assert m == ["рис бурый"] and k == 260


class TestTargets:
    def test_reasonable_range(self):
        t = compute_targets({"weight_kg": 82, "height_cm": 188, "age": 30,
                             "nutrition_goal": "поддержание"})
        assert 3000 < t["kcal"] < 3800
        assert t["protein"] == 156
        assert t["mode"] == "поддержание"

    def test_uses_measured_weight(self):
        """Цель считается от факта, а не от значения в профиле."""
        prof = {"weight_kg": 82, "height_cm": 188, "age": 30,
                "nutrition_goal": "поддержание"}
        assert compute_targets(prof, 90)["protein"] > compute_targets(prof)["protein"]


class TestBodyWeight:
    def test_one_measurement_per_day(self, db):
        db.add_weight("2026-07-27", 91.0)
        db.add_weight("2026-07-27", 91.4)  # перевзвесился
        assert db.latest_weight() == ("2026-07-27", 91.4)

    def test_trend(self, db):
        for i, kg in enumerate([90.0, 90.0, 91.0, 91.0, 92.0, 92.0]):
            d = (datetime.date.today() - datetime.timedelta(days=25 - i * 4)).isoformat()
            db.add_weight(d, kg)
        assert db.weight_trend() > 0  # растём

    def test_trend_needs_two_points(self, db):
        db.add_weight(datetime.date.today().isoformat(), 91.0)
        assert db.weight_trend() is None


class TestDeload:
    def test_every_fifth_progression(self, db):
        assert is_deload_week(db) is False
        for i in range(4):
            db.mark_progression_applied(f"2026-0{i + 1}-05")
        assert is_deload_week(db) is False  # 4 недели работы
        db.mark_progression_applied("2026-05-05")
        assert is_deload_week(db) is True   # 5-я — разгрузка

    def test_deload_workout_has_no_jumps(self):
        from bot.content.workout import build_workout
        s = {"category": "gym", "kind": "lower", "load": "heavy",
             "title": "Зал: ноги", "date": "2026-07-27"}
        normal = build_workout(s, weights={"squat": 95, "trapbar": 90})
        deload = build_workout(s, weights={"squat": 95, "trapbar": 90}, deload=True)
        assert "Выпрыгивания" in normal and "Выпрыгивания" not in deload
        assert "Разгрузка" in deload


# ---------------------------------------------------------------- время
class TestTimeParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("11", "11:00"), ("11:00", "11:00"), ("11 00", "11:00"),
        ("14;00", "14:00"), ("11.00", "11:00"), ("1100", "11:00"),
        ("19ч", "19:00"), ("25", None), ("abc", None),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_time(raw) == expected

    @pytest.mark.parametrize("raw,tm,dur", [
        ("18:00 90", "18:00", 90),
        ("18:00 2ч", "18:00", 120),
        ("20 00 3 часа", "20:00", 180),
        ("10:00 1,5ч", "10:00", 90),
        ("18:00", "18:00", None),
        ("2ч", None, 120),
    ])
    def test_time_and_duration(self, raw, tm, dur):
        assert parse_time_and_duration(raw) == (tm, dur)
