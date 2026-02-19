from aiohttp import web

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional, List, Tuple

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
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


# ✅ УПРОЩЕНО: после "Я оплатил" просим ТОЛЬКО чек
class PaymentFlow(StatesGroup):
    choose_tariff = State()
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


# ✅ ПОСТЫ (админ) + рассылка
class PostFlow(StatesGroup):
    waiting_content = State()
    waiting_confirm = State()


# =========================
# ✅ ТЕХНИКИ ВЫПОЛНЕНИЯ (картинка + текст)
# =========================
TECH = {
    "squat": {
        "title": "Присед",
        "img": "media/tech/squat.jpg",
        "text": (
            "📚 Присед (база)\n\n"
            "Настройка:\n"
            "1) Стопы на ширине плеч (можно чуть шире), носки чуть наружу.\n"
            "2) Корпус «жёсткий»: вдох животом, напряги пресс.\n\n"
            "Движение:\n"
            "1) Сначала таз назад чуть-чуть, потом колени вперёд.\n"
            "2) Колени идут по направлению носков (не заваливай внутрь).\n"
            "3) Опускайся до комфортной глубины без округления поясницы.\n"
            "4) Вставай через середину стопы (не на носки).\n\n"
            "Ошибки:\n"
            "• округляется спина\n"
            "• колени заваливаются внутрь\n"
            "• пятки отрываются"
        )
    },
    "bench": {"title": "Жим лёжа", "img": "media/tech/bench.jpg",
              "text": "📚 Жим лёжа\n\nКоротко: лопатки сведены, локти ~45°, штанга на низ груди. Без отрыва таза."},
    "row": {"title": "Тяга (гребля)", "img": "media/tech/row.jpg",
            "text": "📚 Тяга (гребля)\n\nКоротко: спина стабильна, тяни локтём назад, лопатка работает, без рывков."},
    "latpulldown": {"title": "Верхний блок", "img": "media/tech/latpulldown.jpg",
                    "text": "📚 Верхний блок\n\nКоротко: тяни к верху груди, плечи вниз, корпус не раскачивай."},
    "pullup": {"title": "Подтягивания", "img": "media/tech/pullup.jpg",
               "text": "📚 Подтягивания\n\nКоротко: сначала лопатки вниз, потом тяни локти к рёбрам. Без раскачки."},
    "rdl": {"title": "Румынская тяга", "img": "media/tech/rdl.jpg",
            "text": "📚 Румынская тяга\n\nКоротко: таз назад, спина ровная, гриф близко к ногам, колени чуть согнуты."},
    "ohp": {"title": "Жим вверх", "img": "media/tech/ohp.jpg",
            "text": "📚 Жим вверх\n\nКоротко: пресс напряжён, не прогибайся, штанга по линии лица, локти под грифом."},
    "lateralraise": {"title": "Разведения в стороны", "img": "media/tech/lateralraise.jpg",
                     "text": "📚 Разведения в стороны\n\nКоротко: локоть чуть выше кисти, без рывков, плечи вниз."},
    "biceps": {"title": "Бицепс сгибания", "img": "media/tech/biceps.jpg",
               "text": "📚 Бицепс сгибания\n\nКоротко: локти фиксируй, корпус не качай, движение контролируй."},
    "triceps": {"title": "Трицепс на блоке", "img": "media/tech/triceps.jpg",
                "text": "📚 Трицепс на блоке\n\nКоротко: локти прижаты, разгибай до конца без читинга."},
    "legpress": {"title": "Жим ногами", "img": "media/tech/legpress.jpg",
                 "text": "📚 Жим ногами\n\nКоротко: колени по носкам, пятки не отрывай, поясницу не отрывай от спинки."},
}


def tech_kb():
    rows = [
        [InlineKeyboardButton(text=TECH["squat"]["title"], callback_data="tech:squat"),
         InlineKeyboardButton(text=TECH["bench"]["title"], callback_data="tech:bench")],

        [InlineKeyboardButton(text=TECH["row"]["title"], callback_data="tech:row"),
         InlineKeyboardButton(text=TECH["latpulldown"]["title"], callback_data="tech:latpulldown")],

        [InlineKeyboardButton(text=TECH["pullup"]["title"], callback_data="tech:pullup"),
         InlineKeyboardButton(text=TECH["ohp"]["title"], callback_data="tech:ohp")],

        [InlineKeyboardButton(text=TECH["rdl"]["title"], callback_data="tech:rdl"),
         InlineKeyboardButton(text=TECH["lateralraise"]["title"], callback_data="tech:lateralraise")],

        [InlineKeyboardButton(text=TECH["biceps"]["title"], callback_data="tech:biceps"),
         InlineKeyboardButton(text=TECH["triceps"]["title"], callback_data="tech:triceps")],

        [InlineKeyboardButton(text=TECH["legpress"]["title"], callback_data="tech:legpress")],

        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tech_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="tech:list")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")],
    ])


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
            [KeyboardButton(text="📚 Техники выполнения")],
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


# ✅ АДМИН-ПОСТЫ
def admin_posts_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать пост", callback_data="post:new")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="go_menu")],
    ])


def post_confirm_kb(post_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить всем", callback_data=f"post:send:{post_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel")],
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
    fat = int(round(weight_kg * 0.8))
    carbs_kcal = max(calories - (protein * 4 + fat * 9), 0)
    carbs = int(round(carbs_kcal / 4))
    return protein, fat, carbs


def suggest_meals_count(calories: int) -> int:
    if calories >= 3200:
        return 5
    if calories >= 2600:
        return 4
    return 3


# ✅ Анти-спам: запоминаем последнее сообщение бота и удаляем перед новым
async def get_last_bot_msg_id(user_id: int) -> Optional[int]:
    async with db() as conn:
        async with conn.execute("SELECT last_bot_msg_id FROM bot_state WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    try:
        return int(row[0]) if row[0] is not None else None
    except Exception:
        return None


async def set_last_bot_msg_id(user_id: int, msg_id: int):
    async with db() as conn:
        await conn.execute("""
            INSERT INTO bot_state (user_id, last_bot_msg_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_bot_msg_id=excluded.last_bot_msg_id
        """, (user_id, int(msg_id)))
        await conn.commit()


async def clean_send(bot: Bot, chat_id: int, user_id: int, text: str, reply_markup=None):
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass
    m = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    await set_last_bot_msg_id(user_id, m.message_id)


# =========================
# ✅ ТЕХНИКИ: ХЕНДЛЕРЫ
# =========================
async def open_techniques(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📚 Техники выполнения — выбери упражнение:", reply_markup=tech_kb())


async def cb_tech_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("📚 Техники выполнения — выбери упражнение:", reply_markup=tech_kb())
    await callback.answer()


async def cb_tech_show(callback: CallbackQuery):
    key = callback.data.split("tech:", 1)[1]
    item = TECH.get(key)
    if not item:
        await callback.answer("Не нашёл упражнение", show_alert=True)
        return

    text = item["text"]
    img_path = item["img"]

    caption = text[:1024]
    rest = text[1024:].strip()

    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        await callback.message.answer_photo(photo=photo, caption=caption, reply_markup=tech_back_kb())
        if rest:
            await safe_send(callback.message, rest, reply_markup=tech_back_kb())
    else:
        await safe_send(
            callback.message,
            f"⚠️ Картинка не найдена по пути:\n{img_path}\n\n{text}",
            reply_markup=tech_back_kb()
        )

    await callback.answer()


# =========================
# ТРЕНИРОВКИ (БАЗА + ИЗОЛЯЦИЯ)
# =========================
def _pick(rnd: random.Random, items: List[str]) -> str:
    items = [x for x in items if x]
    if not items:
        return "—"
    return rnd.choice(items)


def generate_workout_plan(goal: str, place: str, exp: str, freq: int, user_id: int = 0) -> str:
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym")
    where = "ЗАЛ" if is_gym else "ДОМ"

    lvl = exp_level(exp)
    seed = (user_id or 0) + int(datetime.utcnow().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    if is_gym:
        push_base = ["Жим лёжа (штанга)", "Жим гантелей лёжа", "Жим в тренажёре", "Отжимания"]
        pull_base = ["Тяга горизонтального блока", "Тяга гантели одной рукой", "Верхний блок", "Подтягивания (если можешь)"]
        legs_base = ["Присед со штангой", "Жим ногами", "Гоблет-присед", "Румынская тяга (лёгкая)"]

        shoulders_iso = ["Разведения в стороны (гантели)", "Face pull (канат)"]
        bi_iso = ["Сгибания гантелей", "Сгибания на блоке"]
        tri_iso = ["Разгибания на блоке", "Французский жим (лёгко)", "Отжимания узкие"]
        legs_iso = ["Икры стоя/сидя", "Разгибания ног", "Сгибания ног"]
        core = ["Планка", "Скручивания", "Подъёмы ног"]
    else:
        push_base = ["Отжимания", "Отжимания с упором ног", "Жим гантелей лёжа (если есть скамья/пол)"]
        pull_base = ["Подтягивания (если есть турник)", "Тяга гантели одной рукой", "Тяга резинки к поясу (если есть резинка)"]
        legs_base = ["Приседания", "Болгарские выпады", "Ягодичный мост", "Гоблет-присед (гантель)"]

        shoulders_iso = ["Разведения в стороны (гантели)", "Разведения в наклоне (задняя дельта)"]
        bi_iso = ["Сгибания гантелей", "Молотки"]
        tri_iso = ["Отжимания узкие", "Французский жим гантелью"]
        legs_iso = ["Икры стоя", "Статические выпады"]
        core = ["Планка", "Скручивания", "Подъём ног лёжа"]

    reps_base = "6–10" if lvl != "novice" else "8–12"
    reps_iso = "10–15"
    base_sets = "3–4" if lvl != "novice" else "3"
    iso_sets = "3"

    f = int(freq or 3)
    f = max(3, min(f, 5))

    g = (goal or "").lower()
    if "суш" in g:
        note = "Сушка: держи 1–2 повтора в запасе (RIR 1–2), отказ редко.\n"
    elif "мас" in g:
        note = "Масса: прогрессируй по повторам/весу, отказ редко, техника важнее.\n"
    else:
        note = "Форма: прогрессируй плавно, без постоянного отказа.\n"

    days = []
    for d in range(f):
        push = _pick(rnd, push_base)
        pull = _pick(rnd, pull_base)
        legs = _pick(rnd, legs_base)

        sh = _pick(rnd, shoulders_iso)
        bi = _pick(rnd, bi_iso)
        tri = _pick(rnd, tri_iso)
        lg = _pick(rnd, legs_iso)
        cr = _pick(rnd, core)

        iso_lines = [
            f"• {sh} — {iso_sets}×{reps_iso}",
            f"• {bi} — {iso_sets}×{reps_iso}",
            f"• {tri} — {iso_sets}×{reps_iso}",
        ]
        if f >= 4:
            iso_lines.append(f"• {lg} — {iso_sets}×{reps_iso}")
        if f >= 5:
            iso_lines.append(f"• {cr} — {iso_sets}×12–20")

        day_text = (
            f"День {d+1}\n"
            f"БАЗА:\n"
            f"• {push} — {base_sets}×{reps_base}\n"
            f"• {pull} — {base_sets}×{reps_base}\n"
            f"• {legs} — {base_sets}×{reps_base}\n\n"
            f"ИЗОЛЯЦИЯ:\n" + "\n".join(iso_lines) +
            "\n\n"
        )
        days.append(day_text)

    return (
        f"🏋️ ТРЕНИРОВКИ ({where}) — {f}×/нед\n\n"
        f"Цель: {goal}\n"
        f"{note}\n"
        "📌 Прогрессия (самое важное):\n"
        "1) Доводи подходы до верхней границы повторов\n"
        "2) Потом добавляй вес (+2.5–5%) и снова работай в диапазоне\n"
        "3) Если техника ломается — вес не повышай\n"
        "4) Если усталость копится 7–10 дней — сделай неделю легче (-20–30% объёма)\n\n"
        + "\n".join(days)
    )


# =========================
# ПИТАНИЕ (однотипное, простое)
# =========================
FOOD_DB = {
    "oats":      {"name": "Овсянка (сухая)",      "kcal": 370, "p": 13.0, "f": 7.0,   "c": 62.0},
    "rice":      {"name": "Рис (сухой)",          "kcal": 360, "p": 7.0,  "f": 0.7,   "c": 78.0},
    "veg":       {"name": "Овощи (микс)",         "kcal": 30,  "p": 1.5,  "f": 0.2,   "c": 6.0},

    "chicken":   {"name": "Куриная грудка",       "kcal": 165, "p": 31.0, "f": 3.6,   "c": 0.0},
    "eggs":      {"name": "Яйца",                 "kcal": 143, "p": 12.6, "f": 10.0,  "c": 1.1},

    "curd_0_5":  {"name": "Творог 0–5%",          "kcal": 120, "p": 18.0, "f": 5.0,   "c": 3.0},
    "banana":    {"name": "Банан",                "kcal": 89,  "p": 1.1,  "f": 0.3,   "c": 23.0},

    "oil":       {"name": "Оливковое масло",      "kcal": 900, "p": 0.0,  "f": 100.0, "c": 0.0},
}


def _nutr_of(item_key: str, grams: float):
    it = FOOD_DB[item_key]
    k = grams / 100.0
    return {"kcal": it["kcal"] * k, "p": it["p"] * k, "f": it["f"] * k, "c": it["c"] * k}


def _sum_nutr(items: List[Tuple[str, float]]):
    tot = {"kcal": 0.0, "p": 0.0, "f": 0.0, "c": 0.0}
    for key, g in items:
        n = _nutr_of(key, g)
        for kk in tot:
            tot[kk] += n[kk]
    return tot


def _fmt_tot(t):
    return f"{int(round(t['kcal']))} ккал | Б {int(round(t['p']))}г Ж {int(round(t['f']))}г У {int(round(t['c']))}г"


def _build_day_items(meals: int, calories: int, protein_g: int, fat_g: int, carbs_g: int):
    meals = max(3, min(int(meals or 3), 5))

    oats_g = 70.0
    eggs_g = 180.0
    rice_g_1 = 90.0
    rice_g_2 = 90.0
    chicken_g_1 = 200.0
    chicken_g_2 = 200.0
    veg_g_1 = 250.0
    veg_g_2 = 250.0
    oil_g = 10.0
    curd_g = 250.0
    banana_g = 120.0

    day_meals: List[List[Tuple[str, float]]] = []
    day_meals.append([("oats", oats_g), ("eggs", eggs_g)])
    day_meals.append([("rice", rice_g_1), ("chicken", chicken_g_1), ("veg", veg_g_1), ("oil", oil_g)])
    day_meals.append([("rice", rice_g_2), ("chicken", chicken_g_2), ("veg", veg_g_2)])

    if meals >= 4:
        day_meals.append([("curd_0_5", curd_g)])
    if meals >= 5:
        day_meals.append([("banana", banana_g)])

    def totals():
        flat = [x for m in day_meals for x in m]
        return _sum_nutr(flat)

    def add_rice(step=10.0):
        day_meals[1] = [(k, (g + step if k == "rice" else g)) for (k, g) in day_meals[1]]
        day_meals[2] = [(k, (g + step if k == "rice" else g)) for (k, g) in day_meals[2]]

    def add_oats(step=10.0):
        day_meals[0] = [(k, (g + step if k == "oats" else g)) for (k, g) in day_meals[0]]

    def add_oil(step=3.0):
        day_meals[1] = [(k, (g + step if k == "oil" else g)) for (k, g) in day_meals[1]]

    def add_chicken(step=50.0):
        day_meals[1] = [(k, (g + step if k == "chicken" else g)) for (k, g) in day_meals[1]]
        day_meals[2] = [(k, (g + step if k == "chicken" else g)) for (k, g) in day_meals[2]]

    target = {"kcal": float(calories), "p": float(protein_g), "f": float(fat_g), "c": float(carbs_g)}

    for _ in range(10):
        t = totals()
        if t["p"] + 8 >= target["p"]:
            break
        add_chicken(50.0)

    for _ in range(16):
        t = totals()
        if t["kcal"] + 80 >= target["kcal"]:
            break
        if t["c"] + 15 < target["c"]:
            add_rice(10.0)
        else:
            add_oats(10.0)

    for _ in range(12):
        t = totals()
        if t["f"] + 3 >= target["f"]:
            break
        add_oil(3.0)

    return day_meals, totals()


def build_3day_meal_plan(calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> str:
    out = []
    for day_i in range(1, 4):
        day_meals, tot = _build_day_items(meals, calories, protein_g, fat_g, carbs_g)

        lines = [f"📅 День {day_i}", ""]
        for mi, m in enumerate(day_meals, start=1):
            mt = _sum_nutr(m)
            lines.append(f"Приём {mi}  ({_fmt_tot(mt)})")
            for k, g in m:
                if k == "eggs":
                    est = max(1, int(round(g / 60.0)))
                    lines.append(f"• {FOOD_DB[k]['name']} — ~{est} шт (≈{int(round(g))} г)")
                else:
                    lines.append(f"• {FOOD_DB[k]['name']} — {int(round(g))} г")
            lines.append("")

        lines.append(f"✅ Итог дня: {_fmt_tot(tot)}")
        lines.append(f"🎯 Цель:    {int(calories)} ккал | Б {int(protein_g)}г Ж {int(fat_g)}г У {int(carbs_g)}г")
        lines.append("")
        out.append("\n".join(lines))

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
        "• Белок держи стабильно, калории проще крутить углеводами/жирами\n"
        "\n"
        "⚠️ Важно:\n"
        "• Крупы в плане указаны в СУХОМ виде.\n"
        "• Масло считаем всегда (это самая частая ошибка).\n"
        "• План однотипный — так проще соблюдать и не путаться.\n"
    )

    three_days = build_3day_meal_plan(calories, p, f, c, meals)

    return (
        "🍽 МОЙ ПЛАН ПИТАНИЯ (3 дня)\n\n"
        f"Цель: {goal}\n"
        f"Калории: ~{calories} ккал/день\n"
        f"БЖУ (ориентир): Белки {p}г / Жиры {f}г / Углеводы {c}г\n"
        f"Приёмов пищи: {meals}\n\n"
        "Правила (коротко и по делу):\n"
        "1) Попади в калории и белок — это главное\n"
        "2) План повторяй 5–7 дней — так меньше ошибок\n"
        "3) Если не идёт прогресс 10–14 дней — крути калории на 150–200\n\n"
        + three_days +
        "\n\n🔁 Простые замены (без усложнений):\n"
        "• курица ↔ индейка ↔ рыба\n"
        "• рис ↔ гречка ↔ макароны\n"
        "• творог ↔ йогурт/кефир\n\n"
        + tips
    )


# =========================
# FAQ (объёмнее и понятнее)
# =========================
def faq_text(topic: str) -> str:
    if topic == "pay":
        return (
            "💳 Оплата и доступ — как это работает\n\n"
            "Как оплатить (по шагам):\n"
            "1) Нажми «💳 Оплата / Доступ»\n"
            "2) Выбери тариф (1м / 3м / навсегда)\n"
            "3) Переведи сумму на карту\n"
            "4) В комментарии укажи код, который покажет бот\n"
            "5) Нажми «✅ Я оплатил» и отправь чек (скрин как фото)\n\n"
            "Почему подтверждение вручную:\n"
            "— это перевод на карту, без платёжного сервиса, поэтому админ сверяет чек.\n\n"
            "Если доступ не открылся за 5–15 минут:\n"
            "— зайди в «🆘 Поддержка» и пришли: тариф + чек."
        )
    if topic == "plan":
        return (
            "🧠 Как строится план\n\n"
            "План подстраивается под тебя по:\n"
            "• цели (масса/сушка/форма)\n"
            "• месту (дом/зал)\n"
            "• опыту\n"
            "• частоте\n\n"
            "Каждая тренировка:\n"
            "1) 3 базовых упражнения\n"
            "2) 3–4 изоляции\n\n"
            "Так проще соблюдать и прогрессировать."
        )
    if topic == "progress":
        return (
            "🏋️ Объём, прогрессия и отказ\n\n"
            "Прогрессия:\n"
            "• +1–2 повтора\n"
            "• или +2.5–5% веса\n\n"
            "Отказ постоянно = быстрее устанешь.\n"
            "Лучше держать 1–2 повтора в запасе (RIR 1–2)."
        )
    if topic == "nutrition":
        return (
            "🍽 Калории и БЖУ\n\n"
            "Главное:\n"
            "1) попасть в калории\n"
            "2) закрыть белок\n\n"
            "Если 10–14 дней нет движения:\n"
            "— масса: +150–200 ккал\n"
            "— сушка: -150–200 ккал"
        )
    if topic == "count":
        return (
            "📌 Как считать калории\n\n"
            "Ошибки:\n"
            "• не считают масло/соусы\n"
            "• путают сухой/готовый вес круп\n"
            "• считают на глаз\n\n"
            "Как правильно:\n"
            "• взвешивать продукты\n"
            "• крупы считать сухими\n"
            "• масло считать всегда"
        )
    if topic == "stuck":
        return (
            "⚠️ Если нет результата\n\n"
            "Проверь:\n"
            "1) калории реально совпадают?\n"
            "2) есть прогрессия?\n"
            "3) сон 7+ часов?\n\n"
            "Меняй один параметр и смотри 10–14 дней."
        )
    if topic == "recovery":
        return (
            "😴 Сон и восстановление\n\n"
            "Минимум 7 часов.\n"
            "Если усталость копится:\n"
            "• убери отказ\n"
            "• снизь объём на 20–30% на неделю"
        )
    if topic == "safety":
        return (
            "🦵 Боль и техника\n\n"
            "Нормально: жжение/крепатура.\n"
            "Плохо: резкая боль в суставе/онемение.\n"
            "Если не проходит — лучше к врачу/реабилитологу."
        )
    if topic == "diary":
        return (
            "📓 Дневник и замеры\n\n"
            "Дневник нужен, чтобы видеть прогресс.\n"
            "Замеры:\n"
            "• вес 3–4 раза/нед\n"
            "• талия 1–2 раза/нед\n"
            "• остальные раз в 2 недели"
        )
    if topic == "refund":
        return (
            "🔄 Ошибки/спорные случаи\n\n"
            "Если оплатил, но доступа нет:\n"
            "• пришли чек в поддержку.\n"
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
        # Оставляем amount/last4 (совместимость), но в логике мы их больше не спрашиваем.
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
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            user_id INTEGER PRIMARY KEY,
            last_bot_msg_id INTEGER
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            post_media_type TEXT,
            post_media_file_id TEXT,
            post_text TEXT,
            status TEXT,
            created_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS post_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            user_id INTEGER,
            status TEXT,
            error TEXT,
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
        await conn.execute(
            "INSERT OR IGNORE INTO bot_state (user_id, last_bot_msg_id) VALUES (?, NULL)",
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


async def create_post_draft(admin_id: int, media_type: str, media_file_id: Optional[str], text: Optional[str]) -> int:
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        cur = await conn.execute("""
            INSERT INTO posts (admin_id, post_media_type, post_media_file_id, post_text, status, created_at)
            VALUES (?, ?, ?, ?, 'draft', ?)
        """, (admin_id, media_type, media_file_id or "", text or "", now))
        await conn.commit()
        return cur.lastrowid


async def get_post(post_id: int):
    async with db() as conn:
        async with conn.execute("""
            SELECT id, admin_id, post_media_type, post_media_file_id, post_text, status, created_at
            FROM posts WHERE id=?
        """, (post_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return {}
    return {
        "id": row[0], "admin_id": row[1], "media_type": row[2],
        "media_file_id": row[3], "text": row[4], "status": row[5], "created_at": row[6]
    }


async def set_post_status(post_id: int, status: str):
    async with db() as conn:
        await conn.execute("UPDATE posts SET status=? WHERE id=?", (status, post_id))
        await conn.commit()


async def get_all_user_ids():
    async with db() as conn:
        async with conn.execute("SELECT user_id FROM users") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows] if rows else []


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
        "• замеры прогресса\n"
        "• техники выполнения с картинками\n\n"
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
    except Exception:
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

    code = gen_order_code(callback.from_user.id)
    await state.update_data(tariff=tariff_code, code=code)

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
        "Ок ✅\n"
        "Теперь отправь чек/скрин оплаты как **фото** (не файл)."
    )
    await state.set_state(PaymentFlow.waiting_receipt)
    await callback.answer()


async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await message.answer("Нужно фото/скрин чека. Отправь как фото.")
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    code = data.get("code") or gen_order_code(message.from_user.id)

    if tariff not in TARIFFS:
        await message.answer("Не вижу выбранный тариф. Зайди в «💳 Оплата / Доступ» и выбери тариф заново.")
        await state.clear()
        return

    receipt_file_id = message.photo[-1].file_id

    # ✅ amount/last4 больше не спрашиваем:
    amount = int(TARIFFS[tariff]["price"])
    last4 = "0000"

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
    except Exception:
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
        "📓 Дневник тренировок", "📏 Замеры", "⚙️ Профиль", "❓ FAQ / Частые вопросы", "🆘 Поддержка",
        "📚 Техники выполнения"
    }:
        return
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📩 Поддержка от @{message.from_user.username or 'no_username'} (id={message.from_user.id}):\n\n{message.text}"
    )


# =========================
# ✅ ПОСТЫ С КАРТИНКАМИ (АДМИН)
# =========================
async def cmd_posts(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer("🗞 Управление постами (админ):", reply_markup=admin_posts_kb())


async def cb_post_new(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    txt = (
        "📝 Создание поста\n\n"
        "Пришли:\n"
        "• текст (обычный пост)\n"
        "ИЛИ\n"
        "• фото + подпись (пост с картинкой)\n"
        "ИЛИ\n"
        "• просто фото/видео (пост без текста)\n\n"
        "После этого я покажу превью и кнопку «Отправить всем»."
    )
    await callback.message.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel")]
    ]))
    await state.set_state(PostFlow.waiting_content)
    await callback.answer()


async def cb_post_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=admin_posts_kb())
    await callback.answer()


async def post_waiting_content(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return

    media_type = "none"
    media_file_id = ""
    text = ""

    if message.photo:
        media_type = "photo"
        media_file_id = message.photo[-1].file_id
        text = (message.caption or "").strip()
    elif message.video:
        media_type = "video"
        media_file_id = message.video.file_id
        text = (message.caption or "").strip()
    else:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Нужно прислать текст или фото/видео.")
            return

    post_id = await create_post_draft(ADMIN_ID, media_type, media_file_id, text)
    await state.update_data(post_id=post_id)
    await state.set_state(PostFlow.waiting_confirm)

    preview_title = f"✅ Превью поста (id={post_id})\n\n"
    chat_id = message.chat.id
    uid = message.from_user.id

    caption = (preview_title + (text or "")).strip()
    if len(caption) > 1024:
        caption = caption[:1020] + "…"

    if media_type == "photo":
        m = await bot.send_photo(chat_id=chat_id, photo=media_file_id, caption=caption, reply_markup=post_confirm_kb(post_id))
        await set_last_bot_msg_id(uid, m.message_id)
    elif media_type == "video":
        m = await bot.send_video(chat_id=chat_id, video=media_file_id, caption=caption, reply_markup=post_confirm_kb(post_id))
        await set_last_bot_msg_id(uid, m.message_id)
    else:
        await clean_send(bot, chat_id, uid, preview_title + text, reply_markup=post_confirm_kb(post_id))


async def cb_post_send(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split(":")
    post_id = int(parts[2])

    post = await get_post(post_id)
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return
    if post.get("status") == "sent":
        await callback.answer("Уже отправлен", show_alert=True)
        return

    await callback.message.answer("📤 Рассылаю всем пользователям...")
    await callback.answer()

    user_ids = await get_all_user_ids()
    ok = 0
    fail = 0

    caption = (post.get("text") or "").strip()
    if len(caption) > 1024:
        caption = caption[:1020] + "…"

    for uid in user_ids:
        try:
            if post["media_type"] == "photo":
                await bot.send_photo(chat_id=uid, photo=post["media_file_id"], caption=caption if caption else None)
            elif post["media_type"] == "video":
                await bot.send_video(chat_id=uid, video=post["media_file_id"], caption=caption if caption else None)
            else:
                await bot.send_message(chat_id=uid, text=post.get("text") or "")
            ok += 1
        except Exception as e:
            fail += 1
            try:
                async with db() as conn:
                    await conn.execute("""
                        INSERT INTO post_sends (post_id, user_id, status, error, created_at)
                        VALUES (?, ?, 'fail', ?, ?)
                    """, (post_id, uid, str(e)[:500], datetime.utcnow().isoformat()))
                    await conn.commit()
            except Exception:
                pass

        await asyncio.sleep(0.03)

    await set_post_status(post_id, "sent")
    await callback.message.answer(f"✅ Готово! Отправлено: {ok}\nОшибок: {fail}", reply_markup=admin_posts_kb())
    await state.clear()


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

    # ✅ ТЕХНИКИ (кнопка в меню)
    dp.message.register(open_techniques, F.text == "📚 Техники выполнения")

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

    # ✅ ТЕХНИКИ (callback)
    dp.callback_query.register(cb_tech_list, F.data == "tech:list")
    dp.callback_query.register(cb_tech_show, F.data.startswith("tech:"))

    dp.message.register(profile_sex, ProfileFlow.sex)
    dp.message.register(profile_age, ProfileFlow.age)
    dp.message.register(profile_height, ProfileFlow.height)
    dp.message.register(profile_weight, ProfileFlow.weight)
    dp.message.register(profile_exp, ProfileFlow.exp)
    dp.message.register(profile_freq, ProfileFlow.freq)

    # ✅ Оплата: теперь только чек
    dp.message.register(pay_receipt, PaymentFlow.waiting_receipt)

    dp.message.register(diary_choose_day, DiaryFlow.choose_day)
    dp.message.register(diary_enter_title, DiaryFlow.enter_title)
    dp.message.register(diary_enter_sets, DiaryFlow.enter_sets)

    dp.message.register(measure_value, MeasureFlow.enter_value)
    dp.message.register(faq_ask, FAQFlow.ask)

    dp.message.register(cmd_posts, Command("posts"))
    dp.callback_query.register(cb_post_new, F.data == "post:new")
    dp.callback_query.register(cb_post_cancel, F.data == "post:cancel")
    dp.callback_query.register(cb_post_send, F.data.startswith("post:send:"))
    dp.message.register(post_waiting_content, PostFlow.waiting_content)

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

