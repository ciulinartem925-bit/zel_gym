from aiohttp import web

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

# =========================
# НАСТРОЙКИ (через ENV — безопасно для GitHub/Render)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_NEW_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

BANK_NAME = os.getenv("BANK_NAME", "Сбербанк")
CARD_NUMBER = os.getenv("CARD_NUMBER", "0000 0000 0000 0000")
CARD_HOLDER = os.getenv("CARD_HOLDER", "ИМЯ ФАМИЛИЯ")

DB_PATH = os.getenv("DB_PATH", "bot.db")

# ТАРИФЫ
TARIFFS = {
    "t1": {"title": "1 месяц", "days": 30, "price": 1150},
    "t3": {"title": "3 месяца", "days": 90, "price": 2790},
    "life": {"title": "Навсегда", "days": None, "price": 6990},
}

TG_SAFE_MSG_LEN = 3800

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trainer_bot")


# =========================
# FSM СТЕЙТЫ
# =========================
class ProfileFlow(StatesGroup):
    goal = State()
    sex = State()
    age = State()
    height = State()
    weight = State()
    place = State()
    exp = State()
    freq = State()


class PaymentFlow(StatesGroup):
    choose_tariff = State()
    waiting_amount = State()
    waiting_last4 = State()
    waiting_receipt = State()


class DiaryFlow(StatesGroup):
    choose_day = State()
    enter_title = State()
    enter_sets = State()


class MeasureFlow(StatesGroup):
    choose_type = State()
    enter_value = State()


class FAQFlow(StatesGroup):
    ask = State()


# =========================
# UI: КНОПКИ
# =========================
def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧠 Собрать мой план"), KeyboardButton(text="💳 Оплата / Доступ")],
            [KeyboardButton(text="🏋️ Мои тренировки"), KeyboardButton(text="🍽 Мой план питания")],
            [KeyboardButton(text="📓 Дневник тренировок"), KeyboardButton(text="📏 Замеры")],
            [KeyboardButton(text="⚙️ Профиль"), KeyboardButton(text="❓ FAQ / Частые вопросы")],
            [KeyboardButton(text="🆘 Поддержка")],
        ],
        resize_keyboard=True
    )


def pay_tariff_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟩 1 месяц — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
        [InlineKeyboardButton(text=f"🟦 3 месяца — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
        [InlineKeyboardButton(text=f"🟨 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")],
    ])


def pay_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="pay_i_paid")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")],
    ])


def admin_review_kb(payment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_approve:{payment_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject:{payment_id}")],
    ])


def goal_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💪 Масса", callback_data="goal:mass")],
        [InlineKeyboardButton(text="🔥 Сушка", callback_data="goal:cut")],
        [InlineKeyboardButton(text="🧩 Форма", callback_data="goal:fit")],
    ])


def place_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Дом", callback_data="place:home")],
        [InlineKeyboardButton(text="🏋️ Зал", callback_data="place:gym")],
    ])


def measures_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Вес (кг)", callback_data="mtype:weight")],
        [InlineKeyboardButton(text="📏 Талия (см)", callback_data="mtype:waist")],
        [InlineKeyboardButton(text="💪 Рука (см)", callback_data="mtype:arm")],
        [InlineKeyboardButton(text="胸 Грудь (см)", callback_data="mtype:chest")],
        [InlineKeyboardButton(text="🦵 Бедро (см)", callback_data="mtype:thigh")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")],
    ])


def diary_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Записать тренировку", callback_data="d:new")],
        [InlineKeyboardButton(text="📜 История (последние 10)", callback_data="d:history")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")],
    ])


def faq_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплата и доступ", callback_data="faq:pay")],
        [InlineKeyboardButton(text="🧠 Как строится план", callback_data="faq:plan")],
        [InlineKeyboardButton(text="🏋️ Объём/прогресс/отказ", callback_data="faq:progress")],
        [InlineKeyboardButton(text="🍽 Калории/БЖУ", callback_data="faq:nutrition")],
        [InlineKeyboardButton(text="📌 Как считать калории", callback_data="faq:count")],
        [InlineKeyboardButton(text="⚠️ Если нет результата", callback_data="faq:stuck")],
        [InlineKeyboardButton(text="😴 Сон/восстановление", callback_data="faq:recovery")],
        [InlineKeyboardButton(text="🦵 Боль/техника", callback_data="faq:safety")],
        [InlineKeyboardButton(text="📓 Дневник/замеры", callback_data="faq:diary")],
        [InlineKeyboardButton(text="🔄 Ошибки/возврат", callback_data="faq:refund")],
        [InlineKeyboardButton(text="✍️ Задать вопрос", callback_data="faq:ask")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")],
    ])


# =========================
# УТИЛИТЫ
# =========================
def gen_order_code(user_id: int) -> str:
    rnd = random.randint(100, 999)
    return f"TG{str(user_id)[-3:]}{rnd}"


async def safe_send(message: Message, text: str, reply_markup=None):
    if not text:
        return
    t = text.strip()
    chunks = []
    while len(t) > TG_SAFE_MSG_LEN:
        cut = t.rfind("\n", 0, TG_SAFE_MSG_LEN)
        if cut == -1:
            cut = TG_SAFE_MSG_LEN
        chunks.append(t[:cut].strip())
        t = t[cut:].strip()
    if t:
        chunks.append(t)

    for i, ch in enumerate(chunks):
        await message.answer(ch, reply_markup=reply_markup if i == len(chunks) - 1 else None)


def locked_text() -> str:
    return "🔒 Раздел доступен после оплаты.\nОткрой: 💳 Оплата / Доступ"


def exp_level(exp: str) -> str:
    t = (exp or "").strip().lower()
    if t in ("0", "новичок", "нов", "beginner"):
        return "novice"
    if "2+" in t or "2 +" in t or "2 года" in t or "3" in t or "4" in t or "5" in t:
        return "adv"
    return "mid"


def _activity_factor(freq: int, place: str) -> float:
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym")
    f = int(freq or 3)

    if f <= 2:
        return 1.35
    if f == 3:
        return 1.45 if is_gym else 1.40
    if f == 4:
        return 1.55 if is_gym else 1.50
    return 1.65 if is_gym else 1.55


def calc_calories(height_cm: int, weight_kg: float, age: int, sex: str, goal: str, freq: int = 3, place: str = "дом") -> int:
    sx = (sex or "м").lower()
    if sx == "м":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    af = _activity_factor(int(freq or 3), place)
    tdee = bmr * af

    g = (goal or "").lower()
    if "мас" in g:
        target = tdee * 1.10
    elif "суш" in g:
        target = tdee * 0.82
    else:
        target = tdee * 1.00

    return int(round(target))


def calc_macros(calories: int, weight_kg: float, goal: str):
    g = (goal or "").lower()
    protein = int(round(weight_kg * (2.2 if "суш" in g else 1.8)))
    fat = int(round(weight_kg * 0.8))  # минимум
    carbs_kcal = max(calories - (protein * 4 + fat * 9), 0)
    carbs = int(round(carbs_kcal / 4))
    return protein, fat, carbs


def suggest_meals_count(calories: int) -> int:
    if calories >= 3200:
        return 5
    if calories >= 2600:
        return 4
    return 3


# =========================
# ТРЕНИРОВКИ (индивидуальная сборка)
# =========================
def _choose_split(freq: int, lvl: str, is_gym: bool) -> str:
    f = int(freq or 3)
    if lvl == "novice":
        return "fullbody_3"
    if f == 3:
        return "fullbody_3" if not is_gym else "fullbody_3_gym"
    if f == 4:
        return "upper_lower_4"
    return "ppl_5" if is_gym else "ul_4_plus_arms"


def _exercise_pool(is_gym: bool):
    if is_gym:
        return {
            "squat": ["Присед со штангой", "Жим ногами", "Гакк-присед"],
            "hinge": ["Румынская тяга", "Становая тяга (лёгкий вариант)", "Гиперэкстензии с весом"],
            "hpush": ["Жим лёжа", "Жим гантелей лёжа", "Жим в тренажёре"],
            "vpush": ["Жим штанги стоя", "Жим гантелей сидя", "Жим в Смите"],
            "pull_v": ["Подтягивания", "Верхний блок", "Подтягивания в гравитроне"],
            "pull_h": ["Тяга горизонтального блока", "Тяга штанги в наклоне", "Тяга гантели одной рукой"],
            "delts": ["Разведения в стороны", "Задняя дельта (пек-дек)", "Тяга каната к лицу (face pull)"],
            "arms_bi": ["Сгибания на бицепс (штанга)", "Сгибания гантелей", "Сгибания на блоке"],
            "arms_tri": ["Разгибания на блоке", "Французский жим", "Брусья (с ассистом)"],
            "legs_iso": ["Сгибание ног", "Разгибание ног", "Икры стоя/сидя"],
            "core": ["Скручивания", "Подъёмы ног", "Планка"],
        }
    else:
        return {
            "squat": ["Присед с гантелями", "Гоблет-присед", "Болгарские выпады"],
            "hinge": ["Румынская тяга с гантелями", "Ягодичный мост", "Наклоны с гантелями"],
            "hpush": ["Отжимания", "Отжимания с упором ног", "Жим гантелей лёжа (скамья)"],
            "vpush": ["Жим гантелей вверх", "Пайк-отжимания", "Жим одной рукой (лёгкий)"],
            "pull_v": ["Подтягивания (если есть турник)", "Тяга резинки сверху", "Тяга к поясу резинкой"],
            "pull_h": ["Тяга гантели одной рукой", "Тяга двух гантелей в наклоне", "Тяга резинки к поясу"],
            "delts": ["Разведения в стороны", "Разведения в наклоне (задняя дельта)", "Face pull резинкой"],
            "arms_bi": ["Сгибания гантелей", "Молотки", "Сгибания с резинкой"],
            "arms_tri": ["Французский жим гантелью", "Разгибания из-за головы", "Отжимания узкие"],
            "legs_iso": ["Икры стоя", "Сгибания ног с резинкой", "Статические выпады"],
            "core": ["Планка", "Скручивания", "Подъём ног лёжа"],
        }


def _pick(pool_list, rnd: random.Random, k: int = 1):
    if not pool_list:
        return []
    if k <= 1:
        return [rnd.choice(pool_list)]
    items = pool_list[:]
    rnd.shuffle(items)
    return items[:k]


def _volume_by_goal(goal: str, lvl: str):
    g = (goal or "").lower()
    if "суш" in g:
        return {"rir": "RIR 2–3 чаще, отказ редко", "sets_main": (2, 4), "sets_iso": (2, 3)}
    if "мас" in g:
        return {"rir": "RIR 1–2 чаще, отказ редко", "sets_main": (3, 5), "sets_iso": (2, 4)}
    return {"rir": "RIR 1–3, по самочувствию", "sets_main": (3, 4), "sets_iso": (2, 3)}


def _rep_ranges(lvl: str):
    if lvl == "novice":
        return {"main": "6–10", "iso": "10–15", "core": "12–20"}
    if lvl == "mid":
        return {"main": "5–10", "iso": "10–20", "core": "12–20"}
    return {"main": "4–10", "iso": "12–20", "core": "12–25"}


def _plan_header(goal: str, place: str, exp: str, freq: int):
    lvl = exp_level(exp)
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym")
    where = "ЗАЛ" if is_gym else "ДОМ"
    return lvl, is_gym, f"🏋️ ТРЕНИРОВКИ ({where}) — {freq}×/нед"


def generate_workout_plan(goal: str, place: str, exp: str, freq: int, user_id: int = 0) -> str:
    lvl, is_gym, header = _plan_header(goal, place, exp, freq)
    f = int(freq or 3)

    seed = (user_id or 0) + int(datetime.utcnow().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    split = _choose_split(f, lvl, is_gym)
    pool = _exercise_pool(is_gym)
    vol = _volume_by_goal(goal, lvl)
    reps = _rep_ranges(lvl)

    main_min, main_max = vol["sets_main"]
    iso_min, iso_max = vol["sets_iso"]
    if lvl == "novice":
        main_max = max(main_min, main_max - 1)

    def sets_main():
        return rnd.randint(main_min, main_max)

    def sets_iso():
        return rnd.randint(iso_min, iso_max)

    def day(title: str, items: list[str]) -> str:
        return title + "\n" + "\n".join([f"• {x}" for x in items])

    def main_block(key: str):
        ex = _pick(pool[key], rnd, 1)[0]
        return f"{ex} — {sets_main()}×{reps['main']}"

    def iso_block(key: str):
        ex = _pick(pool[key], rnd, 1)[0]
        return f"{ex} — {sets_iso()}×{reps['iso']}"

    def core_block():
        ex = _pick(pool["core"], rnd, 1)[0]
        return f"{ex} — {sets_iso()}×{reps['core']}"

    days = []

    if split.startswith("fullbody"):
        for i in range(1, 4):
            items = [
                main_block("squat"),
                main_block("hpush"),
                main_block("pull_v"),
                main_block("hinge"),
                iso_block("delts"),
                core_block(),
            ]
            if rnd.random() < 0.6:
                items.append(iso_block("arms_bi"))
            if rnd.random() < 0.6:
                items.append(iso_block("arms_tri"))
            days.append(day(f"День {i} — Full Body", items))

    elif split == "upper_lower_4":
        upper1 = [
            main_block("hpush"),
            main_block("pull_h"),
            main_block("vpush"),
            main_block("pull_v"),
            iso_block("delts"),
            iso_block("arms_bi"),
            iso_block("arms_tri"),
        ]
        lower1 = [
            main_block("squat"),
            main_block("hinge"),
            iso_block("legs_iso"),
            iso_block("legs_iso"),
            core_block(),
        ]
        upper2 = [
            main_block("vpush"),
            main_block("pull_v"),
            main_block("hpush"),
            main_block("pull_h"),
            iso_block("delts"),
            iso_block("arms_bi"),
            iso_block("arms_tri"),
        ]
        lower2 = [
            main_block("squat"),
            main_block("hinge"),
            iso_block("legs_iso"),
            core_block(),
        ]
        days = [
            day("День 1 — Верх", upper1),
            day("День 2 — Низ", lower1),
            day("День 3 — Верх", upper2),
            day("День 4 — Низ", lower2),
        ]

    elif split == "ppl_5":
        push = [
            main_block("hpush"),
            main_block("vpush"),
            iso_block("delts"),
            iso_block("arms_tri"),
            iso_block("arms_tri"),
        ]
        pull = [
            main_block("pull_v"),
            main_block("pull_h"),
            iso_block("delts"),
            iso_block("arms_bi"),
            iso_block("arms_bi"),
        ]
        legs = [
            main_block("squat"),
            main_block("hinge"),
            iso_block("legs_iso"),
            iso_block("legs_iso"),
            core_block(),
        ]
        upper = [
            main_block("hpush"),
            main_block("pull_h"),
            main_block("vpush"),
            main_block("pull_v"),
            iso_block("delts"),
        ]
        arms = [
            iso_block("arms_bi"),
            iso_block("arms_bi"),
            iso_block("arms_tri"),
            iso_block("arms_tri"),
            iso_block("delts"),
            core_block(),
        ]
        days = [
            day("День 1 — PUSH", push),
            day("День 2 — PULL", pull),
            day("День 3 — LEGS", legs),
            day("День 4 — UPPER", upper),
            day("День 5 — ARMS/DELTS", arms),
        ]

    else:
        upper1 = [
            main_block("pull_v"),
            main_block("hpush"),
            main_block("pull_h"),
            main_block("vpush"),
            iso_block("delts"),
        ]
        lower1 = [
            main_block("squat"),
            main_block("hinge"),
            iso_block("legs_iso"),
            core_block(),
        ]
        upper2 = [
            main_block("hpush"),
            main_block("pull_h"),
            main_block("vpush"),
            main_block("pull_v"),
            iso_block("delts"),
        ]
        arms = [
            iso_block("arms_bi"),
            iso_block("arms_tri"),
            iso_block("arms_bi"),
            iso_block("arms_tri"),
            core_block(),
        ]
        days = [
            day("День 1 — Верх", upper1),
            day("День 2 — Низ", lower1),
            day("День 3 — Верх", upper2),
            day("День 4 — Руки/Кор", arms),
        ]

    g = (goal or "").lower()
    cardio_note = ""
    if "суш" in g:
        cardio_note = "• Сушка: шаги 8–12k/день или 2–3 лёгких кардио по 20–30 мин.\n"
    elif "мас" in g:
        cardio_note = "• Масса: кардио умеренно (1–2× по 15–25 мин), чтобы не мешало восстановлению.\n"

    return (
        f"{header}\n\n"
        f"Цель: {goal}\n"
        f"Интенсивность: {vol['rir']}\n"
        "Паузы: 90–180 сек базовые, 60–90 сек изоляция\n"
        f"{cardio_note}\n"
        + "\n\n".join(days) +
        "\n\n📌 Прогрессия (самое важное):\n"
        "1) Сначала доводи подходы до верхней границы повторов\n"
        "2) Потом добавляй вес (+2.5–5%) и снова работай в диапазоне\n"
        "3) Если техника ломается — вес не повышай\n"
        "4) Если усталость копится 7–10 дней — сделай неделю легче (-20–30% подходов)\n"
    )


# =========================
# ПИТАНИЕ (3 дня, ккал/БЖУ сходятся внутри вывода)
# =========================
# Важно: крупы/макароны указаны В СУХОМ ВИДЕ (как на упаковке).
FOOD_DB = {
    "oats":      {"name": "Овсянка (сухая)",      "kcal": 370, "p": 13.0, "f": 7.0,   "c": 62.0},
    "rice":      {"name": "Рис (сухой)",          "kcal": 360, "p": 7.0,  "f": 0.7,   "c": 78.0},
    "buckwheat": {"name": "Гречка (сухая)",       "kcal": 343, "p": 13.0, "f": 3.4,   "c": 71.5},
    "pasta":     {"name": "Макароны (сухие)",     "kcal": 350, "p": 12.0, "f": 1.5,   "c": 72.0},
    "potato":    {"name": "Картофель",            "kcal": 77,  "p": 2.0,  "f": 0.1,   "c": 17.0},
    "banana":    {"name": "Банан",                "kcal": 89,  "p": 1.1,  "f": 0.3,   "c": 23.0},
    "veg":       {"name": "Овощи (микс)",          "kcal": 30,  "p": 1.5,  "f": 0.2,   "c": 6.0},

    "chicken":   {"name": "Куриная грудка",       "kcal": 165, "p": 31.0, "f": 3.6,   "c": 0.0},
    "turkey":    {"name": "Индейка (филе)",       "kcal": 135, "p": 29.0, "f": 1.5,   "c": 0.0},
    "fish":      {"name": "Белая рыба",           "kcal": 105, "p": 23.0, "f": 1.0,   "c": 0.0},
    "eggs":      {"name": "Яйца",                 "kcal": 143, "p": 12.6, "f": 10.0,  "c": 1.1},

    "curd_0_5":  {"name": "Творог 0–5%",          "kcal": 120, "p": 18.0, "f": 5.0,   "c": 3.0},
    "yogurt":    {"name": "Йогурт/кефир 2%",      "kcal": 60,  "p": 4.0,  "f": 2.0,   "c": 6.0},

    "oil":       {"name": "Оливковое масло",      "kcal": 900, "p": 0.0,  "f": 100.0, "c": 0.0},
    "nuts":      {"name": "Орехи",                "kcal": 600, "p": 15.0, "f": 55.0,  "c": 15.0},
}

def _nutr_of(item_key: str, grams: float):
    it = FOOD_DB[item_key]
    k = grams / 100.0
    return {"kcal": it["kcal"] * k, "p": it["p"] * k, "f": it["f"] * k, "c": it["c"] * k}

def _sum_nutr(items: list[tuple[str, float]]):
    tot = {"kcal": 0.0, "p": 0.0, "f": 0.0, "c": 0.0}
    for key, g in items:
        n = _nutr_of(key, g)
        for kk in tot:
            tot[kk] += n[kk]
    return tot

def _fmt_tot(t):
    return f"{int(round(t['kcal']))} ккал | Б {int(round(t['p']))}г Ж {int(round(t['f']))}г У {int(round(t['c']))}г"

def build_3day_meal_plan(calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> str:
    # 3 разных дня — заранее заменяем блюда
    # Далее алгоритм мягко добирает Б/Ж/У, чтобы цифры сходились (в выводе есть итог и цель).
    day_templates = [
        [  # День 1
            ["oats", "yogurt", "banana"],
            ["nuts", "yogurt"],
            ["rice", "chicken", "veg", "oil"],
            ["curd_0_5"],
            ["banana", "yogurt"],
        ],
        [  # День 2
            ["oats", "eggs", "banana"],
            ["yogurt"],
            ["buckwheat", "turkey", "veg", "oil"],
            ["curd_0_5", "nuts"],
            ["banana"],
        ],
        [  # День 3
            ["oats", "yogurt"],
            ["eggs", "veg"],
            ["pasta", "fish", "veg", "oil"],
            ["curd_0_5"],
            ["banana", "yogurt"],
        ],
    ]

    # Базовые граммы (потом добор)
    base = {
        "oats": 80, "yogurt": 300, "banana": 120,
        "rice": 90, "buckwheat": 90, "pasta": 90,
        "chicken": 220, "turkey": 220, "fish": 250,
        "veg": 300, "curd_0_5": 300,
        "eggs": 180,     # ~3 яйца
        "oil": 12, "nuts": 20,
    }

    target = {"kcal": float(calories), "p": float(protein_g), "f": float(fat_g), "c": float(carbs_g)}
    out = []

    def add_protein(items, need_p):
        # добор белка курицей по 50г
        while need_p > 8:
            items.append(("chicken", 50.0))
            need_p -= _nutr_of("chicken", 50.0)["p"]
        return items

    def add_fat(items, need_f):
        # добор жира маслом по 5г
        while need_f > 4:
            items.append(("oil", 5.0))
            need_f -= _nutr_of("oil", 5.0)["f"]
        return items

    def add_carbs(items, need_c):
        # добор углеводов рисом по 20г (сухой)
        while need_c > 12:
            items.append(("rice", 20.0))
            need_c -= _nutr_of("rice", 20.0)["c"]
        return items

    for day_i in range(3):
        tpl = day_templates[day_i][:meals]

        day_items_by_meal: list[list[tuple[str, float]]] = []
        day_items_flat: list[tuple[str, float]] = []

        for keys in tpl:
            meal_items = []
            for k in keys:
                g = float(base.get(k, 100))
                meal_items.append((k, g))
            day_items_by_meal.append(meal_items)
            day_items_flat.extend(meal_items)

        # добираем до цели по БЖУ
        tot = _sum_nutr(day_items_flat)

        need_p = target["p"] - tot["p"]
        if need_p > 0:
            day_items_flat = add_protein(day_items_flat, need_p)
        tot = _sum_nutr(day_items_flat)

        need_f = target["f"] - tot["f"]
        if need_f > 0:
            day_items_flat = add_fat(day_items_flat, need_f)
        tot = _sum_nutr(day_items_flat)

        need_c = target["c"] - tot["c"]
        if need_c > 0:
            day_items_flat = add_carbs(day_items_flat, need_c)
        tot = _sum_nutr(day_items_flat)

        # лёгкая подгонка по ккал (не ломая БЖУ сильно)
        delta_kcal = target["kcal"] - tot["kcal"]
        step_g = 20.0
        if abs(delta_kcal) > 140:
            if delta_kcal > 0:
                day_items_flat.append(("rice", step_g))
            else:
                for idx in range(len(day_items_flat) - 1, -1, -1):
                    k, g = day_items_flat[idx]
                    if k in ("rice", "oats", "pasta", "buckwheat") and g >= step_g + 10:
                        day_items_flat[idx] = (k, g - step_g)
                        break
        tot = _sum_nutr(day_items_flat)

        # считаем "добор" (что добавили сверх базовых приёмов)
        grouped = {}
        for k, g in day_items_flat:
            grouped[k] = grouped.get(k, 0.0) + g

        base_flat = []
        for meal_items in day_items_by_meal:
            base_flat.extend(meal_items)
        base_grouped = {}
        for k, g in base_flat:
            base_grouped[k] = base_grouped.get(k, 0.0) + g

        extras = []
        for k, g in grouped.items():
            extra = g - base_grouped.get(k, 0.0)
            if extra > 0.1:
                extras.append((k, extra))

        # текст
        day_text = [f"📅 День {day_i + 1}", ""]
        for mi, meal_items in enumerate(day_items_by_meal, start=1):
            meal_tot = _sum_nutr(meal_items)
            day_text.append(f"Приём {mi}  ({_fmt_tot(meal_tot)})")
            for k, g in meal_items:
                day_text.append(f"• {FOOD_DB[k]['name']} — {int(round(g))} г")
            day_text.append("")

        if extras:
            extra_tot = _sum_nutr(extras)
            day_text.append(f"➕ Добор под цель  ({_fmt_tot(extra_tot)})")
            for k, g in extras:
                day_text.append(f"• {FOOD_DB[k]['name']} — +{int(round(g))} г")
            day_text.append("")

        day_text.append(f"✅ Итог дня: {_fmt_tot(tot)}")
        day_text.append(f"🎯 Цель:    {int(target['kcal'])} ккал | Б {int(target['p'])}г Ж {int(target['f'])}г У {int(target['c'])}г")
        day_text.append("")
        out.append("\n".join(day_text))

    return "\n\n".join(out)


def generate_nutrition_plan(goal: str, sex: str, age: int, height: int, weight: float, exp: str, freq: int = 3, place: str = "дом") -> str:
    calories = calc_calories(height, weight, age, sex, goal, freq=freq, place=place)
    p, f, c = calc_macros(calories, weight, goal)
    meals = suggest_meals_count(calories)

    tips = (
        "Как держать прогресс правильно:\n"
        "• Смотри не один день, а среднее за 7 дней (тренд)\n"
        "• Взвешивайся утром после туалета, до еды\n"
        "• Если вес стоит 10–14 дней:\n"
        "  — масса: +150–200 ккал\n"
        "  — сушка: -150–200 ккал\n"
        "• Белок держи стабильно, калории проще крутить углеводами\n"
        "\n"
        "⚠️ Важно:\n"
        "• Крупы/макароны в плане указаны в СУХОМ виде.\n"
        "• Масло/орехи/соусы считаем всегда.\n"
    )

    three_days = build_3day_meal_plan(calories, p, f, c, meals)

    return (
        "🍽 МОЙ ПЛАН ПИТАНИЯ (3 дня)\n\n"
        f"Цель: {goal}\n"
        f"Калории: ~{calories} ккал/день\n"
        f"БЖУ (ориентир): Белки {p}г / Жиры {f}г / Углеводы {c}г\n"
        f"Приёмов пищи: {meals}\n\n"
        "Правила:\n"
        "1) Сначала попади в калории и белок\n"
        "2) Ешь шаблоны 5–7 дней — так проще не ошибаться\n"
        "3) Масло/орехи/соусы учитывай всегда\n\n"
        + three_days +
        "\n\n🔁 Замены:\n"
        "• курица ↔ индейка ↔ рыба\n"
        "• рис ↔ гречка ↔ макароны\n"
        "• творог ↔ йогурт/кефир\n\n"
        + tips
    )


# =========================
# FAQ (понятнее + объёмнее)
# =========================
def faq_text(topic: str) -> str:
    if topic == "pay":
        return (
            "💳 Оплата и доступ — как это работает\n\n"
            "Как оплатить (пошагово):\n"
            "1) Нажми «💳 Оплата / Доступ»\n"
            "2) Выбери тариф (1м / 3м / навсегда)\n"
            "3) Переведи ровно сумму тарифа на карту\n"
            "4) В комментарии к переводу укажи код, который покажет бот\n"
            "5) Нажми «✅ Я оплатил» → введи сумму → последние 4 цифры карты → пришли чек фото\n\n"
            "Почему подтверждение вручную:\n"
            "— это перевод на карту (без платёжного API), поэтому админ сверяет чек.\n\n"
            "Если доступ не открылся за 5–15 минут:\n"
            "— зайди в «🆘 Поддержка» и пришли: дату/сумму/тариф/чек."
        )

    if topic == "plan":
        return (
            "🧠 Как строится план (почему он индивидуальный)\n\n"
            "План зависит от:\n"
            "• цель (масса/сушка/форма)\n"
            "• где тренируешься (дом/зал)\n"
            "• опыт (0 / 1–2 / 2+)\n"
            "• сколько раз в неделю реально удобно\n\n"
            "Что меняется:\n"
            "— Новичок: простая база, техника, без отказа\n"
            "— 1–2 года: больше недельный объём, распределение нагрузок\n"
            "— 2+ года: больше специализация (PPL/акценты), но без тупого «убивания»\n"
        )

    if topic == "progress":
        return (
            "🏋️ Объём, прогрессия и отказ — максимально просто\n\n"
            "Прогрессия = ты реально становишься сильнее.\n"
            "Как прогрессировать:\n"
            "1) Сначала добавь повторы до верхней границы диапазона\n"
            "2) Потом добавь вес (+2.5–5%) и снова работай в диапазоне\n\n"
            "Про отказ:\n"
            "— Новичку почти не нужен\n"
            "— Опытному можно дозировано (чаще изоляция, в конце)\n\n"
            "Ориентир: чаще держи 1–2 повтора «в запасе» (RIR 1–2)."
        )

    if topic == "nutrition":
        return (
            "🍽 Калории и БЖУ — понятная логика\n\n"
            "Калории — это сколько энергии ты съел за день.\n"
            "БЖУ — из чего эти калории: белки/жиры/углеводы.\n\n"
            "Что важнее всего:\n"
            "1) Белок каждый день\n"
            "2) Калории под цель\n\n"
            "Если 10–14 дней нет движения:\n"
            "— масса: +150–200 ккал\n"
            "— сушка: -150–200 ккал"
        )

    if topic == "count":
        return (
            "📌 Как правильно считать калории (без ошибок)\n\n"
            "Главное:\n"
            "✅ Считаем то, что реально съел → в граммах → по этикетке/приложению.\n\n"
            "1) Крупы/макароны — лучше в СУХОМ виде.\n"
            "2) Самая частая ошибка: не считают масло/соусы/перекусы.\n"
            "3) Контроль веса: 3–4 раза/нед утром → смотри среднее за неделю."
        )

    if topic == "stuck":
        return (
            "⚠️ Если нет результата — почти всегда причина тут\n\n"
            "1) Ошибки в калориях (масло/соусы/перекусы)\n"
            "2) Нет прогрессии в тренировках\n"
            "3) Сон и восстановление просели\n\n"
            "Что делать:\n"
            "— 7–10 дней учёта\n"
            "— смотри среднее за неделю\n"
            "— корректируй калории на 150–200"
        )

    if topic == "recovery":
        return (
            "😴 Сон и восстановление\n\n"
            "Минимум: 7 часов. Идеально: 7.5–9.\n"
            "Если силовые падают/вечно устал:\n"
            "1) убери отказ на неделю\n"
            "2) снизь объём на 20–30%\n"
            "3) питание держи стабильным"
        )

    if topic == "safety":
        return (
            "🦵 Боль и техника — как отличить «норм» от «опасно»\n\n"
            "Нормально:\n"
            "— жжение в мышцах\n"
            "— умеренная крепатура\n\n"
            "Плохо (стоп):\n"
            "— резкая боль в суставе\n"
            "— онемение/прострел\n"
            "— боль усиливается от тренировки к тренировке\n\n"
            "Что сделать:\n"
            "1) снизить вес/амплитуду\n"
            "2) заменить упражнение\n"
            "3) проверить технику (лучше видео)"
        )

    if topic == "diary":
        return (
            "📓 Дневник и замеры — чтобы видеть прогресс\n\n"
            "Зачем дневник:\n"
            "— видишь рост силовых/повторов\n"
            "— понимаешь, когда повышать нагрузку\n\n"
            "Как часто:\n"
            "• вес: 3–4 раза/нед утром\n"
            "• талия: 1–2 раза/нед\n"
            "• рука/грудь/бедро: раз в 2 недели"
        )

    if topic == "refund":
        return (
            "🔄 Ошибки / спорные случаи / возврат\n\n"
            "Если оплатил, но доступ не открылся:\n"
            "1) проверь, что отправил чек фото\n"
            "2) проверь сумму и код в комментарии\n"
            "3) напиши в «🆘 Поддержка» и приложи чек\n\n"
            "Оплата на карту → подтверждение вручную."
        )

    return "Выбери тему."


def faq_match(q: str) -> str:
    t = (q or "").lower()
    if any(k in t for k in ["оплат", "доступ", "чек", "карта", "перевод", "тариф"]):
        return faq_text("pay")
    if any(k in t for k in ["считать", "подсчет", "подсчёт", "взвеш", "этикет", "калории считать"]):
        return faq_text("count")
    if any(k in t for k in ["план", "программ", "сплит", "тренировк"]):
        return faq_text("plan")
    if any(k in t for k in ["прогресс", "силов", "отказ", "объем", "объём", "подход"]):
        return faq_text("progress")
    if any(k in t for k in ["ккал", "калор", "бжу", "белок", "жир", "углевод", "питани"]):
        return faq_text("nutrition")
    if any(k in t for k in ["не раст", "стоит", "нет результата", "плато"]):
        return faq_text("stuck")
    if any(k in t for k in ["сон", "восстанов", "устал", "перетрен"]):
        return faq_text("recovery")
    if any(k in t for k in ["боль", "травм", "плечо", "колено", "локоть", "спина"]):
        return faq_text("safety")
    if any(k in t for k in ["дневник", "замер", "талия"]):
        return faq_text("diary")
    if any(k in t for k in ["возврат", "ошибка", "не работает", "баг"]):
        return faq_text("refund")
    return "Не понял вопрос. Напиши: оплата / план / питание / прогресс / сон / боль / как считать калории."


# =========================
# DB (стабильные подключения)
# =========================
@asynccontextmanager
async def db():
    conn = await aiosqlite.connect(DB_PATH)
    try:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.execute("PRAGMA busy_timeout=5000;")
        yield conn
    finally:
        await conn.close()


async def init_db():
    async with db() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            goal TEXT,
            sex TEXT,
            age INTEGER,
            height INTEGER,
            weight REAL,
            place TEXT,
            exp TEXT,
            freq INTEGER,
            created_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS access (
            user_id INTEGER PRIMARY KEY,
            paid INTEGER DEFAULT 0,
            tariff TEXT,
            expires_at TEXT,
            paid_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            tariff TEXT,
            amount INTEGER,
            last4 TEXT,
            code TEXT,
            status TEXT,
            receipt_file_id TEXT,
            created_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_plans (
            user_id INTEGER PRIMARY KEY,
            plan_text TEXT,
            updated_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_plans (
            user_id INTEGER PRIMARY KEY,
            plan_text TEXT,
            updated_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS diary_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            session_date TEXT,
            title TEXT,
            created_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS diary_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            exercise TEXT,
            set_no INTEGER,
            weight REAL,
            reps INTEGER
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            mtype TEXT,
            value REAL,
            created_at TEXT
        )
        """)
        await conn.commit()


async def ensure_user(user_id: int, username: str):
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
            (user_id, username or "", now)
        )
        await conn.execute(
            "INSERT OR IGNORE INTO access (user_id, paid, tariff, expires_at, paid_at) VALUES (?, 0, NULL, NULL, NULL)",
            (user_id,)
        )
        await conn.commit()


async def get_user(user_id: int):
    async with db() as conn:
        async with conn.execute("""
            SELECT user_id, username, goal, sex, age, height, weight, place, exp, freq
            FROM users WHERE user_id=?
        """, (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return {}
    return {
        "user_id": row[0], "username": row[1], "goal": row[2], "sex": row[3],
        "age": row[4], "height": row[5], "weight": row[6], "place": row[7],
        "exp": row[8], "freq": row[9]
    }


async def update_user(user_id: int, **fields):
    if not fields:
        return
    keys, vals = [], []
    for k, v in fields.items():
        keys.append(f"{k}=?")
        vals.append(v)
    vals.append(user_id)
    q = "UPDATE users SET " + ", ".join(keys) + " WHERE user_id=?"
    async with db() as conn:
        await conn.execute(q, tuple(vals))
        await conn.commit()


async def get_access(user_id: int):
    async with db() as conn:
        async with conn.execute(
            "SELECT paid, tariff, expires_at, paid_at FROM access WHERE user_id=?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"paid": 0, "tariff": None, "expires_at": None, "paid_at": None}
    return {"paid": row[0], "tariff": row[1], "expires_at": row[2], "paid_at": row[3]}


async def is_access_active(user_id: int) -> bool:
    a = await get_access(user_id)
    if a["paid"] != 1:
        return False
    if a["tariff"] == "life":
        return True
    if not a["expires_at"]:
        return False
    try:
        exp = datetime.fromisoformat(a["expires_at"])
    except Exception:
        return False
    return datetime.utcnow() < exp


async def set_paid_tariff(user_id: int, tariff_code: str):
    t = TARIFFS.get(tariff_code)
    if not t:
        raise ValueError("Unknown tariff")
    now = datetime.utcnow()
    now_iso = now.isoformat()
    expires_at = None if t["days"] is None else (now + timedelta(days=int(t["days"]))).isoformat()

    async with db() as conn:
        await conn.execute(
            "UPDATE access SET paid=1, tariff=?, expires_at=?, paid_at=? WHERE user_id=?",
            (tariff_code, expires_at, now_iso, user_id)
        )
        await conn.commit()


async def save_workout_plan(user_id: int, text: str):
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        await conn.execute("""
            INSERT INTO workout_plans (user_id, plan_text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET plan_text=excluded.plan_text, updated_at=excluded.updated_at
        """, (user_id, text, now))
        await conn.commit()


async def save_nutrition_plan(user_id: int, text: str):
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        await conn.execute("""
            INSERT INTO nutrition_plans (user_id, plan_text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET plan_text=excluded.plan_text, updated_at=excluded.updated_at
        """, (user_id, text, now))
        await conn.commit()


async def get_workout_plan(user_id: int):
    async with db() as conn:
        async with conn.execute("SELECT plan_text FROM workout_plans WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def get_nutrition_plan(user_id: int):
    async with db() as conn:
        async with conn.execute("SELECT plan_text FROM nutrition_plans WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def create_payment(user_id: int, tariff: str, amount: int, last4: str, code: str, receipt_file_id: str):
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        cur = await conn.execute("""
            INSERT INTO payments (user_id, tariff, amount, last4, code, status, receipt_file_id, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        """, (user_id, tariff, amount, last4, code, receipt_file_id, now))
        await conn.commit()
        return cur.lastrowid


async def get_payment(payment_id: int):
    async with db() as conn:
        async with conn.execute("""
            SELECT id, user_id, tariff, amount, last4, code, status, receipt_file_id, created_at
            FROM payments WHERE id=?
        """, (payment_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return {}
    return {
        "id": row[0], "user_id": row[1], "tariff": row[2], "amount": row[3],
        "last4": row[4], "code": row[5], "status": row[6], "receipt_file_id": row[7], "created_at": row[8]
    }


async def set_payment_status(payment_id: int, status: str):
    async with db() as conn:
        await conn.execute("UPDATE payments SET status=? WHERE id=?", (status, payment_id))
        await conn.commit()


async def has_recent_pending_payment(user_id: int) -> bool:
    since = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    async with db() as conn:
        async with conn.execute("""
            SELECT COUNT(*) FROM payments
            WHERE user_id=? AND status='pending' AND created_at>=?
        """, (user_id, since)) as cur:
            row = await cur.fetchone()
    return bool(row and row[0] > 0)


async def create_diary_session(user_id: int, session_date: str, title: str):
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        cur = await conn.execute("""
            INSERT INTO diary_sessions (user_id, session_date, title, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, session_date, title, now))
        await conn.commit()
        return cur.lastrowid


async def add_set(session_id: int, exercise: str, set_no: int, weight: float, reps: int):
    async with db() as conn:
        await conn.execute("""
            INSERT INTO diary_sets (session_id, exercise, set_no, weight, reps)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, exercise, set_no, weight, reps))
        await conn.commit()


async def get_diary_history(user_id: int, limit_sessions: int = 10):
    async with db() as conn:
        async with conn.execute("""
            SELECT id, session_date, title
            FROM diary_sessions
            WHERE user_id=?
            ORDER BY id DESC LIMIT ?
        """, (user_id, limit_sessions)) as cur:
            sessions = await cur.fetchall()

        out = []
        for s in sessions:
            sid = s[0]
            async with conn.execute("""
                SELECT exercise, set_no, weight, reps
                FROM diary_sets
                WHERE session_id=?
                ORDER BY id ASC
            """, (sid,)) as cur2:
                sets = await cur2.fetchall()
            out.append((s, sets))
    return out


async def add_measure(user_id: int, mtype: str, value: float):
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        await conn.execute(
            "INSERT INTO measurements (user_id, mtype, value, created_at) VALUES (?, ?, ?, ?)",
            (user_id, mtype, value, now)
        )
        await conn.commit()


async def get_last_measures(user_id: int, mtype: str, limit: int = 8):
    async with db() as conn:
        async with conn.execute("""
            SELECT value, created_at
            FROM measurements
            WHERE user_id=? AND mtype=?
            ORDER BY id DESC LIMIT ?
        """, (user_id, mtype, limit)) as cur:
            rows = await cur.fetchall()
    return rows or []


# =========================
# ХЕНДЛЕРЫ
# =========================
async def cmd_start(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await message.answer(
        "Привет! Я составлю тебе:\n"
        "• тренировки под цель и опыт\n"
        "• питание (ккал/БЖУ) + 3 дня примеров\n"
        "• дневник тренировок\n"
        "• замеры прогресса\n\n"
        "Порядок:\n"
        "1) ⚙️ Профиль\n"
        "2) 💳 Оплата / Доступ\n"
        "3) 🧠 Собрать мой план",
        reply_markup=main_menu_kb()
    )


async def cb_go_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Ок 👇", reply_markup=main_menu_kb())
    await callback.answer()


# ---- Профиль ----
async def open_profile(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    await message.answer(
        "⚙️ Профиль\n\n"
        f"Цель: {u.get('goal') or '—'}\n"
        f"Пол: {u.get('sex') or '—'}\n"
        f"Возраст: {u.get('age') or '—'}\n"
        f"Рост: {u.get('height') or '—'}\n"
        f"Вес: {u.get('weight') or '—'}\n"
        f"Где тренируешься: {u.get('place') or '—'}\n"
        f"Опыт: {u.get('exp') or '—'}\n"
        f"Частота: {u.get('freq') or '—'}\n\n"
        "Выбери цель:",
        reply_markup=goal_inline_kb()
    )
    await state.set_state(ProfileFlow.goal)


async def cb_goal(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    goal = {"mass": "масса", "cut": "сушка", "fit": "форма"}.get(v, v)
    await update_user(callback.from_user.id, goal=goal)
    await callback.message.answer("Пол? Напиши: м или ж")
    await state.set_state(ProfileFlow.sex)
    await callback.answer()


async def profile_sex(message: Message, state: FSMContext):
    t = (message.text or "").strip().lower()
    if t not in ("м", "ж", "муж", "жен", "мужской", "женский"):
        await message.answer("Напиши просто: м или ж")
        return
    sex = "м" if t.startswith("м") else "ж"
    await update_user(message.from_user.id, sex=sex)
    await message.answer("Возраст (числом), например 19:")
    await state.set_state(ProfileFlow.age)


async def profile_age(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("Возраст нужен числом, например 19")
        return
    age = int(message.text)
    if age < 10 or age > 90:
        await message.answer("Возраст странный. Введи ещё раз.")
        return
    await update_user(message.from_user.id, age=age)
    await message.answer("Рост (см), например 175:")
    await state.set_state(ProfileFlow.height)


async def profile_height(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("Рост числом в см, например 175")
        return
    h = int(message.text)
    if h < 120 or h > 230:
        await message.answer("Рост странный. Введи ещё раз.")
        return
    await update_user(message.from_user.id, height=h)
    await message.answer("Вес (кг), например 72 или 72.5:")
    await state.set_state(ProfileFlow.weight)


async def profile_weight(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        w = float(txt)
    except:
        await message.answer("Вес числом, например 72 или 72.5")
        return
    if w < 30 or w > 250:
        await message.answer("Вес странный. Введи ещё раз.")
        return
    await update_user(message.from_user.id, weight=w)
    await message.answer("Где тренируешься? Выбери:", reply_markup=place_inline_kb())
    await state.set_state(ProfileFlow.place)


async def cb_place(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    place = "дом" if v == "home" else "зал"
    await update_user(callback.from_user.id, place=place)
    await callback.message.answer("Опыт? Напиши: 0 / 1-2 года / 2+ года")
    await state.set_state(ProfileFlow.exp)
    await callback.answer()


async def profile_exp(message: Message, state: FSMContext):
    exp = (message.text or "").strip()
    await update_user(message.from_user.id, exp=exp)

    lvl = exp_level(exp)
    if lvl == "novice":
        await update_user(message.from_user.id, freq=3)
        await message.answer("✅ Профиль заполнен (для новичка будет 3×/нед).", reply_markup=main_menu_kb())
        await state.clear()
        return

    await message.answer("Сколько тренировок в неделю удобно? Напиши: 3 / 4 / 5")
    await state.set_state(ProfileFlow.freq)


async def profile_freq(message: Message, state: FSMContext):
    t = re.sub(r"[^\d]", "", message.text or "")
    if t not in ("3", "4", "5"):
        await message.answer("Напиши просто цифру: 3 или 4 или 5")
        return
    await update_user(message.from_user.id, freq=int(t))
    await message.answer("✅ Профиль заполнен. Теперь: 💳 Оплата / Доступ", reply_markup=main_menu_kb())
    await state.clear()


# ---- Оплата ----
def access_status_str(a: dict) -> str:
    if not a or a.get("paid") != 1:
        return "Статус: ❌ нет доступа"
    if a.get("tariff") == "life":
        return "Статус: ✅ доступ активен (НАВСЕГДА)"
    exp = a.get("expires_at")
    return f"Статус: ✅ доступ активен до {exp[:10]}" if exp else "Статус: ✅ доступ активен"


async def open_payment(message: Message, state: FSMContext):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    a = await get_access(message.from_user.id)

    if await is_access_active(message.from_user.id):
        await message.answer(f"✅ Доступ активен.\n{access_status_str(a)}", reply_markup=main_menu_kb())
        return

    text = (
        "💳 Оплата / Доступ\n\n"
        f"{access_status_str(a)}\n\n"
        "Выбери тариф:\n"
        f"• 1 месяц — {TARIFFS['t1']['price']}₽\n"
        f"• 3 месяца — {TARIFFS['t3']['price']}₽\n"
        f"• навсегда — {TARIFFS['life']['price']}₽\n\n"
        "После выбора я покажу реквизиты и код для комментария."
    )
    await message.answer(text, reply_markup=pay_tariff_kb())
    await state.set_state(PaymentFlow.choose_tariff)


async def cb_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_code = callback.data.split(":")[1]
    if tariff_code not in TARIFFS:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return

    await state.update_data(tariff=tariff_code)
    code = gen_order_code(callback.from_user.id)

    text = (
        "💳 Оплата доступа\n\n"
        f"Тариф: {TARIFFS[tariff_code]['title']}\n"
        f"Сумма: {TARIFFS[tariff_code]['price']}₽\n\n"
        "Переведи на карту:\n"
        f"• Банк: {BANK_NAME}\n"
        f"• Карта: {CARD_NUMBER}\n"
        f"• Получатель: {CARD_HOLDER}\n\n"
        "⚠️ В комментарии к переводу укажи код:\n"
        f"{code}\n\n"
        "После оплаты нажми «✅ Я оплатил» и отправь чек/скрин (как фото)."
    )
    await callback.message.answer(text, reply_markup=pay_inline_kb())
    await callback.answer()


async def cb_i_paid(callback: CallbackQuery, state: FSMContext):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")

    if await is_access_active(callback.from_user.id):
        await callback.message.answer("✅ У тебя уже активный доступ.")
        await callback.answer()
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if not tariff:
        await callback.message.answer("Сначала выбери тариф в «💳 Оплата / Доступ».")
        await callback.answer()
        return

    if await has_recent_pending_payment(callback.from_user.id):
        await callback.message.answer("⏳ У тебя уже есть активная заявка (до 2 часов).")
        await callback.answer()
        return

    await callback.message.answer(
        f"Введи сумму, которую перевёл.\n"
        f"Ожидаемая сумма для тарифа «{TARIFFS[tariff]['title']}»: {TARIFFS[tariff]['price']}₽"
    )
    await state.set_state(PaymentFlow.waiting_amount)
    await callback.answer()


async def pay_amount(message: Message, state: FSMContext):
    txt = re.sub(r"[^\d]", "", message.text or "")
    if not txt:
        await message.answer("Сумму числом, например 1150")
        return
    await state.update_data(amount=int(txt))
    await message.answer("Введи последние 4 цифры карты отправителя (или 0000):")
    await state.set_state(PaymentFlow.waiting_last4)


async def pay_last4(message: Message, state: FSMContext):
    txt = re.sub(r"[^\d]", "", message.text or "")
    if len(txt) != 4:
        await message.answer("Нужно ровно 4 цифры. Например 1234 (или 0000)")
        return
    await state.update_data(last4=txt)
    await message.answer("Отправь чек/скрин оплаты как фото:")
    await state.set_state(PaymentFlow.waiting_receipt)


async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await message.answer("Нужно фото/скрин чека. Отправь как фото.")
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if tariff not in TARIFFS:
        await message.answer("Не вижу выбранный тариф. Зайди в «💳 Оплата / Доступ» и выбери тариф заново.")
        await state.clear()
        return

    amount = int(data.get("amount", 0))
    last4 = data.get("last4", "0000")
    receipt_file_id = message.photo[-1].file_id
    code = gen_order_code(message.from_user.id)

    payment_id = await create_payment(message.from_user.id, tariff, amount, last4, code, receipt_file_id)
    await message.answer("✅ Заявка отправлена. Как подтвержу — доступ откроется.")

    u = await get_user(message.from_user.id)
    uname = f"@{u.get('username')}" if u.get("username") else "(без юзернейма)"

    caption = (
        "🧾 Новая заявка\n\n"
        f"payment_id: {payment_id}\n"
        f"user: {uname}\n"
        f"user_id: {message.from_user.id}\n"
        f"tariff: {tariff} ({TARIFFS[tariff]['title']})\n"
        f"amount: {amount}\n"
        f"last4: {last4}\n"
        f"code: {code}\n"
    )
    await bot.send_photo(
        chat_id=ADMIN_ID,
        photo=receipt_file_id,
        caption=caption,
        reply_markup=admin_review_kb(payment_id)
    )
    await state.clear()


async def admin_actions(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    action, pid = callback.data.split(":")
    pid = int(pid)

    p = await get_payment(pid)
    if not p:
        await callback.answer("Платёж не найден", show_alert=True)
        return
    if p["status"] != "pending":
        await callback.answer(f"Уже обработано: {p['status']}", show_alert=True)
        return

    user_id = p["user_id"]
    tariff = p.get("tariff")

    if action == "admin_approve":
        if tariff not in TARIFFS:
            await callback.answer("У платежа нет тарифа", show_alert=True)
            return
        await set_payment_status(pid, "approved")
        await set_paid_tariff(user_id, tariff)

        a = await get_access(user_id)
        await bot.send_message(
            chat_id=user_id,
            text=(
                "✅ Оплата подтверждена!\n"
                f"Тариф: {TARIFFS[tariff]['title']}\n"
                f"{access_status_str(a)}\n\n"
                "Теперь жми: 🧠 Собрать мой план"
            ),
            reply_markup=main_menu_kb()
        )
        await callback.answer("Подтверждено ✅")
    else:
        await set_payment_status(pid, "rejected")
        await bot.send_message(
            chat_id=user_id,
            text="❌ Оплата отклонена. Проверь сумму/чек/комментарий и попробуй снова: 💳 Оплата / Доступ"
        )
        await callback.answer("Отклонено ❌")


# ---- Планы ----
async def build_plan(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    if not await is_access_active(message.from_user.id):
        await message.answer(locked_text())
        return

    u = await get_user(message.from_user.id)
    need = ["goal", "sex", "age", "height", "weight", "place", "exp", "freq"]
    if any(not u.get(k) for k in need):
        await message.answer("⚠️ Не хватает данных профиля. Заполни: ⚙️ Профиль")
        return

    workout = generate_workout_plan(
        u["goal"], u["place"], u["exp"], int(u["freq"]),
        user_id=message.from_user.id
    )
    nutrition = generate_nutrition_plan(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )

    await save_workout_plan(message.from_user.id, workout)
    await save_nutrition_plan(message.from_user.id, nutrition)

    await message.answer("✅ Готово! Я собрал твой план. Открой разделы 👇", reply_markup=main_menu_kb())


async def open_workouts(message: Message):
    if not await is_access_active(message.from_user.id):
        await message.answer(locked_text())
        return
    plan = await get_workout_plan(message.from_user.id)
    if not plan:
        await message.answer("Плана пока нет. Нажми 🧠 Собрать мой план")
        return
    await safe_send(message, plan)


async def open_nutrition(message: Message):
    if not await is_access_active(message.from_user.id):
        await message.answer(locked_text())
        return
    plan = await get_nutrition_plan(message.from_user.id)
    if not plan:
        await message.answer("Плана питания пока нет. Нажми 🧠 Собрать мой план")
        return
    await safe_send(message, plan)


# ---- Дневник ----
async def open_diary(message: Message):
    if not await is_access_active(message.from_user.id):
        await message.answer(locked_text())
        return
    await message.answer("📓 Дневник тренировок", reply_markup=diary_inline_kb())


async def diary_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введи дату (YYYY-MM-DD) или напиши: сегодня")
    await state.set_state(DiaryFlow.choose_day)
    await callback.answer()


async def diary_choose_day(message: Message, state: FSMContext):
    t = (message.text or "").strip().lower()
    if t == "сегодня":
        d = datetime.now().strftime("%Y-%m-%d")
    else:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            await message.answer("Формат даты: 2026-02-13 или слово 'сегодня'")
            return
        d = t
    await state.update_data(session_date=d)
    await message.answer("Название тренировки? (например: День 1 / Push / Ноги)")
    await state.set_state(DiaryFlow.enter_title)


async def diary_enter_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("Напиши название (например: День 1)")
        return
    data = await state.get_data()
    session_id = await create_diary_session(message.from_user.id, data["session_date"], title)
    await state.update_data(session_id=session_id)

    await message.answer(
        "Теперь вводи упражнения по одному.\n"
        "Формат: Упражнение: 60x8, 60x8, 60x7\n"
        "Когда закончишь — напиши: стоп"
    )
    await state.set_state(DiaryFlow.enter_sets)


async def diary_enter_sets(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "стоп":
        await message.answer("✅ Тренировка записана.", reply_markup=main_menu_kb())
        await state.clear()
        return

    if ":" not in txt:
        await message.answer("Нужен формат: Упражнение: 60x8, 60x8")
        return

    ex, sets_str = [x.strip() for x in txt.split(":", 1)]
    parts = [p.strip() for p in sets_str.split(",") if p.strip()]
    if not ex or not parts:
        await message.answer("Заполни и название, и подходы. Пример: Жим: 60x8, 60x8")
        return

    data = await state.get_data()
    session_id = data["session_id"]

    for i, p in enumerate(parts, start=1):
        m = re.match(r"^(\d+(\.\d+)?)\s*[xх]\s*(\d+)$", p.lower())
        if not m:
            await message.answer(f"Не понял подход: '{p}'. Пример: 60x8")
            return
        w = float(m.group(1))
        r = int(m.group(3))
        await add_set(session_id, ex, i, w, r)

    await message.answer(f"✅ Записал: {ex} ({len(parts)} подходов). Следующее упражнение или 'стоп'.")


async def diary_history(callback: CallbackQuery):
    history = await get_diary_history(callback.from_user.id, 10)
    if not history:
        await callback.message.answer("Истории пока нет. Нажми ➕ Записать тренировку")
        await callback.answer()
        return

    msg = "📜 Последние тренировки:\n\n"
    for (s, sets) in history:
        sid, session_date, title = s
        msg += f"🗓 {session_date} — {title}\n"
        cur_ex = None
        line = ""
        for ex, set_no, w, reps in sets:
            if cur_ex != ex:
                if line:
                    msg += line + "\n"
                cur_ex = ex
                line = f"  • {ex}: "
            line += f"{w:g}x{reps}  "
        if line:
            msg += line + "\n"
        msg += "\n"

    await safe_send(callback.message, msg)
    await callback.answer()


# ---- Замеры ----
async def open_measures(message: Message, state: FSMContext):
    if not await is_access_active(message.from_user.id):
        await message.answer(locked_text())
        return
    await message.answer("📏 Выбери замер:", reply_markup=measures_inline_kb())
    await state.set_state(MeasureFlow.choose_type)


async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await callback.message.answer("Введи значение числом (например 72.5):")
    await state.set_state(MeasureFlow.enter_value)
    await callback.answer()


async def measure_value(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        val = float(txt)
    except:
        await message.answer("Нужно число. Например 72.5")
        return

    data = await state.get_data()
    mtype = data.get("mtype")

    await add_measure(message.from_user.id, mtype, val)
    rows = await get_last_measures(message.from_user.id, mtype, 6)

    name = {"weight": "Вес", "waist": "Талия", "arm": "Рука", "chest": "Грудь", "thigh": "Бедро"}.get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    await message.answer(f"✅ Записал: {name} = {val:g}\n\nПоследние:\n{hist}", reply_markup=main_menu_kb())
    await state.clear()


# ---- FAQ ----
async def open_faq(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❓ FAQ / Частые вопросы\nВыбери тему:", reply_markup=faq_inline_kb())


async def cb_faq(callback: CallbackQuery, state: FSMContext):
    topic = callback.data.split(":")[1]
    if topic == "ask":
        await callback.message.answer("✍️ Напиши свой вопрос одним сообщением.")
        await state.set_state(FAQFlow.ask)
    else:
        await safe_send(callback.message, faq_text(topic))
    await callback.answer()


async def faq_ask(message: Message, state: FSMContext):
    await safe_send(message, faq_match(message.text or ""), reply_markup=main_menu_kb())
    await state.clear()


# ---- Поддержка ----
async def open_support(message: Message):
    await message.answer(
        "🆘 Поддержка\n\n"
        "Напиши проблему одним сообщением — я перешлю админу.\n"
        "Если есть ошибка в консоли/логах — пришли текст.",
        reply_markup=main_menu_kb()
    )


async def forward_to_admin(message: Message, bot: Bot):
    if message.from_user.id == ADMIN_ID:
        return
    if not message.text or message.text.startswith("/"):
        return
    if message.text in {
        "🧠 Собрать мой план", "💳 Оплата / Доступ", "🏋️ Мои тренировки", "🍽 Мой план питания",
        "📓 Дневник тренировок", "📏 Замеры", "⚙️ Профиль", "❓ FAQ / Частые вопросы", "🆘 Поддержка"
    }:
        return
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📩 Поддержка от @{message.from_user.username or 'no_username'} (id={message.from_user.id}):\n\n{message.text}"
    )


# =========================
# РЕГИСТРАЦИЯ
# =========================
def setup_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, CommandStart())

    dp.message.register(build_plan, F.text == "🧠 Собрать мой план")
    dp.message.register(open_payment, F.text == "💳 Оплата / Доступ")
    dp.message.register(open_workouts, F.text == "🏋️ Мои тренировки")
    dp.message.register(open_nutrition, F.text == "🍽 Мой план питания")
    dp.message.register(open_diary, F.text == "📓 Дневник тренировок")
    dp.message.register(open_measures, F.text == "📏 Замеры")
    dp.message.register(open_profile, F.text == "⚙️ Профиль")
    dp.message.register(open_faq, F.text == "❓ FAQ / Частые вопросы")
    dp.message.register(open_support, F.text == "🆘 Поддержка")

    dp.callback_query.register(cb_goal, F.data.startswith("goal:"))
    dp.callback_query.register(cb_place, F.data.startswith("place:"))

    dp.callback_query.register(cb_tariff, F.data.startswith("tariff:"))
    dp.callback_query.register(cb_i_paid, F.data == "pay_i_paid")
    dp.callback_query.register(admin_actions, F.data.startswith("admin_approve:") | F.data.startswith("admin_reject:"))

    dp.callback_query.register(diary_new, F.data == "d:new")
    dp.callback_query.register(diary_history, F.data == "d:history")

    dp.callback_query.register(cb_measure_type, F.data.startswith("mtype:"))
    dp.callback_query.register(cb_faq, F.data.startswith("faq:"))
    dp.callback_query.register(cb_go_menu, F.data == "go_menu")

    dp.message.register(profile_sex, ProfileFlow.sex)
    dp.message.register(profile_age, ProfileFlow.age)
    dp.message.register(profile_height, ProfileFlow.height)
    dp.message.register(profile_weight, ProfileFlow.weight)
    dp.message.register(profile_exp, ProfileFlow.exp)
    dp.message.register(profile_freq, ProfileFlow.freq)

    dp.message.register(pay_amount, PaymentFlow.waiting_amount)
    dp.message.register(pay_last4, PaymentFlow.waiting_last4)
    dp.message.register(pay_receipt, PaymentFlow.waiting_receipt)

    dp.message.register(diary_choose_day, DiaryFlow.choose_day)
    dp.message.register(diary_enter_title, DiaryFlow.enter_title)
    dp.message.register(diary_enter_sets, DiaryFlow.enter_sets)

    dp.message.register(measure_value, MeasureFlow.enter_value)
    dp.message.register(faq_ask, FAQFlow.ask)

    dp.message.register(forward_to_admin)


async def run_web_server():
    app = web.Application()

    async def health(request):
        return web.Response(text="ok")

    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"Web server started on port {port}")

    while True:
        await asyncio.sleep(3600)


# =========================
# MAIN (устойчивый запуск: автоперезапуск при ошибках)
# =========================
async def main():
    if "PASTE_NEW_TOKEN_HERE" in BOT_TOKEN or not BOT_TOKEN or BOT_TOKEN == "0":
        raise RuntimeError("Нужно задать BOT_TOKEN через переменные окружения (ENV).")

    if ADMIN_ID == 0:
        logger.warning("ADMIN_ID не задан. Подтверждение оплат админом работать не будет.")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook cleared, starting polling...")

    dp = Dispatcher()
    setup_handlers(dp)

    async def bot_loop():
        backoff = 2
        while True:
            try:
                logger.info("Bot polling started.")
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            except Exception:
                logger.exception("Polling crashed. Restarting...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            else:
                backoff = 2
                await asyncio.sleep(2)

    await asyncio.gather(
        bot_loop(),
        run_web_server(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

