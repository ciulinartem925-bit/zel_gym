from aiohttp import web

import asyncio
import logging
import os
import random
import re
import json
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional, List, Tuple, Dict

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
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

MIN_DAYS = 3
MAX_DAYS = 5

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trainer_bot")


# =========================
# FSM
# =========================
class PaymentFlow(StatesGroup):
    choose_tariff = State()
    waiting_receipt = State()   # ✅ теперь только скрин


class DiaryFlow(StatesGroup):
    choosing_exercise = State()
    enter_sets = State()


class MeasureFlow(StatesGroup):
    choose_type = State()
    enter_value = State()


class PostFlow(StatesGroup):
    waiting_content = State()
    waiting_confirm = State()


class ProfileWizard(StatesGroup):
    goal = State()
    sex = State()
    age = State()          # текстом
    height = State()       # текстом
    weight = State()       # текстом
    place = State()
    exp = State()
    freq = State()
    limits = State()       # текстом (ограничения)
    state = State()        # ✅ текстом (состояние/самочувствие)


# =========================
# ✅ ТЕХНИКИ ВЫПОЛНЕНИЯ (картинка + текст) — короче, “живым” языком
# =========================
TECH = {
    "squat": {
        "title": "Присед",
        "img": "media/tech/squat.jpg",
        "text": (
            "📚 Присед\n\n"
            "1) Стопы на ширине плеч, носки чуть наружу.\n"
            "2) Вдох животом, пресс напряг — и пошёл вниз.\n"
            "3) Колени смотрят туда же, куда носки.\n"
            "4) Спина ровная, пятки не отрывай.\n"
            "5) Вставай через пятку/середину стопы.\n\n"
            "Если колени/поясница капризничают — уменьши глубину и вес, сделай чище технику."
        )
    },
    "bench": {
        "title": "Жим лёжа",
        "img": "media/tech/bench.jpg",
        "text": (
            "📚 Жим лёжа\n\n"
            "1) Лопатки свёл и прижал к лавке — это твоя «броня».\n"
            "2) Локти не в стороны 90°, держи примерно под 45°.\n"
            "3) Опускай контролируемо к нижней части груди.\n"
            "4) Жми вверх и чуть назад (к стойкам).\n\n"
            "Главное: плечи не тянем вперёд, жмём грудью и трицепсом, без суеты."
        )
    },
    "row": {
        "title": "Отжимания",
        "img": "media/tech/pushup.jpg",
        "text": (
            "📚 Отжимания\n\n"
            "1) Корпус как доска: пресс + ягодицы включены.\n"
            "2) Локти веди назад под углом (не разводи в стороны).\n"
            "3) Опустился — и выжал пол от себя.\n\n"
            "Если тяжело — делай от лавки/опоры. Если легко — ноги на опору или пауза внизу."
        )
    },
    "latpulldown": {
        "title": "Верхний блок",
        "img": "media/tech/latpulldown.jpg",
        "text": (
            "📚 Тяга верхнего блока\n\n"
            "1) Сначала «опусти плечи вниз», только потом тяни.\n"
            "2) Тяни к верхней части груди, локти вниз/назад.\n"
            "3) Вверх возвращай медленно, без бросков.\n\n"
            "Фишка простая: тянем спиной, руки — просто крюки."
        )
    },
    "pullup": {
        "title": "Подтягивания",
        "img": "media/tech/pullup.jpg",
        "text": (
            "📚 Подтягивания\n\n"
            "1) Сначала лопатки вниз — как будто «плечи от ушей».\n"
            "2) Тяни локты к рёбрам, грудь к перекладине.\n"
            "3) Вниз опускайся медленно 2–3 сек.\n\n"
            "Если не идёт — резинка/гравитрон и делай качественно."
        )
    },
    "rdl": {
        "title": "Румынская тяга",
        "img": "media/tech/rdl.jpg",
        "text": (
            "📚 Румынская тяга\n\n"
            "1) Колени чуть согнуты и почти не меняются.\n"
            "2) Движение — таз назад. Гриф скользит близко к ногам.\n"
            "3) Опускайся до сильного натяжения в бёдрах.\n"
            "4) Вверх — таз вперёд, поясницу не перегибай.\n\n"
            "Чувствуешь заднюю поверхность бедра — значит делаешь правильно."
        )
    },
    "ohp": {
        "title": "Жим вверх",
        "img": "media/tech/ohp.jpg",
        "text": (
            "📚 Жим вверх\n\n"
            "1) Пресс напряг, рёбра «вниз», чтобы не ломаться в пояснице.\n"
            "2) Жми вверх и чуть назад, гриф идёт близко к лицу.\n"
            "3) Вниз — медленно и под контролем.\n\n"
            "Если плечо болит — не геройствуй, уменьши вес/амплитуду."
        )
    },
    "lateralraise": {
        "title": "Разведения в стороны",
        "img": "media/tech/lateralraise.jpg",
        "text": (
            "📚 Разведения в стороны\n\n"
            "1) Плечи вниз, шея расслаблена.\n"
            "2) Локоть чуть согнут и фиксирован.\n"
            "3) Поднимай до уровня плеч, без махов корпусом.\n"
            "4) Вниз — медленно 2–3 сек.\n\n"
            "Думай не про кисть, а про локоть — он ведёт движение."
        )
    },
    "biceps": {
        "title": "Бицепс сгибания",
        "img": "media/tech/biceps.jpg",
        "text": (
            "📚 Сгибания на бицепс\n\n"
            "1) Локти прижал к корпусу — и держишь.\n"
            "2) Без раскачки, поднимай чисто.\n"
            "3) Вниз опускай медленно.\n\n"
            "Лучше меньше вес, но чтобы прям «горело» в бицепсе."
        )
    },
    "triceps": {
        "title": "Трицепс на блоке",
        "img": "media/tech/triceps.jpg",
        "text": (
            "📚 Трицепс на блоке\n\n"
            "1) Локти прижаты и не гуляют.\n"
            "2) Разгибай до конца без боли.\n"
            "3) Вверх — медленно, не бросай.\n\n"
            "Если локоть ноет — снизь вес и работай мягче."
        )
    },
    "legpress": {
        "title": "Жим ногами",
        "img": "media/tech/legpress.jpg",
        "text": (
            "📚 Жим ногами\n\n"
            "1) Поясница прижата, таз не отрывай.\n"
            "2) Колени по линии носков.\n"
            "3) Опускайся до комфортной глубины.\n"
            "4) Не выпрямляй колени в «замок».\n\n"
            "Делаем безопасно: техника важнее веса на платформе."
        )
    },
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

        [InlineKeyboardButton(text="⬅️ Назад к тренировкам", callback_data="nav:workouts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tech_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="tech:list")],
    ])


# =========================
# ✅ ПОСТОЯННАЯ КЛАВИАТУРА (2×2)
# =========================
def control_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚙️ Профиль"), KeyboardButton(text="🏠 Меню")],
            [KeyboardButton(text="💳 Оплата/доступ"), KeyboardButton(text="🆘 Поддержка")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Тыкай на кнопки снизу 👇"
    )


# =========================
# Inline: меню разделов
# =========================
def menu_main_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Мои тренировки", callback_data="nav:workouts"), InlineKeyboardButton(text="🍽 Моё питание", callback_data="nav:nutrition")],
        [InlineKeyboardButton(text="📏 Замеры", callback_data="nav:measures"), InlineKeyboardButton(text="📓 Дневник", callback_data="nav:diary")],
    ])


def simple_back_to_menu_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


# =========================
# ✅ Тренировки: кнопки дней + обновить
# =========================
def workout_days_kb(freq: int):
    freq = max(MIN_DAYS, min(int(freq or 3), MAX_DAYS))
    rows = []
    btns = [InlineKeyboardButton(text=f"📅 День {i}", callback_data=f"wday:{i}") for i in range(1, freq + 1)]
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])

    rows += [
        [InlineKeyboardButton(text="🔁 Обновить план", callback_data="plan:refresh")],
        [InlineKeyboardButton(text="📚 Техники выполнения упражнений", callback_data="tech:list")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def workouts_inline_kb():
    # совместимость
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Обновить план", callback_data="plan:refresh")],
        [InlineKeyboardButton(text="📚 Техники выполнения упражнений", callback_data="tech:list")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


# =========================
# Оплата: кнопки
# =========================
def pay_tariff_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟩 1 месяц — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
        [InlineKeyboardButton(text=f"🟦 3 месяца — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
        [InlineKeyboardButton(text=f"🟨 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def pay_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="pay_i_paid")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def admin_review_kb(payment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_approve:{payment_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject:{payment_id}")],
    ])


# =========================
# ✅ Профиль: прогресс "■■■■■■■□□□ 100%"
# =========================
TOTAL_PROFILE_STEPS = 10


def _bar(step: int, total: int = TOTAL_PROFILE_STEPS, width: int = 10) -> str:
    step = max(0, min(step, total))
    pct = int(round(step / total * 100)) if total else 0
    filled = int(round(pct / 100 * width))
    filled = max(0, min(filled, width))
    return f"{'■'*filled}{'□'*(width-filled)} {pct}%"


def _profile_header(step: int) -> str:
    return f"🧩 Профиль {step}/{TOTAL_PROFILE_STEPS}\n{_bar(step)}\n\n"


def profile_done_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️🍽 Открыть тренировки и питание", callback_data="nav:menu")]
    ])


def profile_view_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить профиль", callback_data="p:edit")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_goal():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💪 Масса", callback_data="p:goal:mass"),
         InlineKeyboardButton(text="🔥 Сушка", callback_data="p:goal:cut")],
        [InlineKeyboardButton(text="🧩 Форма", callback_data="p:goal:fit")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_sex():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Мужчина", callback_data="p:sex:m"),
         InlineKeyboardButton(text="👩 Женщина", callback_data="p:sex:f")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:goal")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_place():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Дома", callback_data="p:place:home"),
         InlineKeyboardButton(text="🏋️ В зале", callback_data="p:place:gym")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:weight")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_exp():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="0 (новичок)", callback_data="p:exp:0")],
        [InlineKeyboardButton(text="1–2 года", callback_data="p:exp:mid"),
         InlineKeyboardButton(text="2+ года", callback_data="p:exp:adv")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:place")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_freq():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3×/нед", callback_data="p:freq:3"),
         InlineKeyboardButton(text="4×/нед", callback_data="p:freq:4")],
        [InlineKeyboardButton(text="5×/нед", callback_data="p:freq:5")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:exp")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_text_step(back_to: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:back:{back_to}")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_state():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😄 Отличное", callback_data="p:state:отличное"),
         InlineKeyboardButton(text="🙂 Норм", callback_data="p:state:норм")],
        [InlineKeyboardButton(text="😴 Устал/не выспался", callback_data="p:state:устал"),
         InlineKeyboardButton(text="😖 Есть дискомфорт/болит", callback_data="p:state:болит")],
        [InlineKeyboardButton(text="✍️ Напишу текстом", callback_data="p:state:text")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:limits")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


# =========================
# УТИЛИТЫ
# =========================
def dumps_plan(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False)


def loads_plan(text: str) -> dict:
    try:
        return json.loads(text or "")
    except Exception:
        return {}


def weekday_schedule(freq: int) -> str:
    if freq <= 3:
        return "Пн / Ср / Пт (или Вт / Чт / Сб)"
    if freq == 4:
        return "Пн / Вт / Чт / Сб (или Пн / Ср / Пт / Вс)"
    return "Пн / Вт / Ср / Пт / Сб (или 5 дней подряд, если ок)"


def gen_order_code(user_id: int) -> str:
    rnd = random.randint(100, 999)
    return f"TG{str(user_id)[-3:]}{rnd}"


def locked_text() -> str:
    return "🔒 Этот раздел доступен после оплаты.\nНажми снизу: 💳 Оплата/доступ"


def exp_level(exp: str) -> str:
    t = (exp or "").strip().lower()
    if t in ("0", "новичок", "нов", "beginner"):
        return "novice"
    if "2+" in t or "2 +" in t or "2 года" in t or "3" in t or "4" in t or "5" in t:
        return "adv"
    return "mid"


def _activity_factor(freq: int, place: str) -> float:
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym") or ("gym" in pl)
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


async def try_delete_user_message(bot: Bot, message: Message):
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass


# =========================
# ✅ АНТИ-ЗАСОРЕНИЕ ЧАТА
# =========================
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
    return m.message_id


async def clean_edit(callback: CallbackQuery, user_id: int, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        await set_last_bot_msg_id(user_id, callback.message.message_id)
    except Exception:
        await clean_send(callback.bot, callback.message.chat.id, user_id, text, reply_markup=reply_markup)


# =========================
# DB
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
            limits TEXT,
            state TEXT,
            created_at TEXT
        )
        """)

        # ✅ мягкая миграция для старых баз (если колонок нет)
        for col, typ in [
            ("limits", "TEXT"),
            ("state", "TEXT"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
            except Exception:
                pass

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

        # ✅ тренировки: добавили plan_json
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_plans (
            user_id INTEGER PRIMARY KEY,
            plan_text TEXT,
            plan_json TEXT,
            updated_at TEXT
        )
        """)
        try:
            await conn.execute("ALTER TABLE workout_plans ADD COLUMN plan_json TEXT")
        except Exception:
            pass

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
            SELECT user_id, username, goal, sex, age, height, weight, place, exp, freq, limits, state
            FROM users WHERE user_id=?
        """, (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return {}
    return {
        "user_id": row[0], "username": row[1], "goal": row[2], "sex": row[3],
        "age": row[4], "height": row[5], "weight": row[6], "place": row[7],
        "exp": row[8], "freq": row[9], "limits": row[10], "state": row[11]
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


async def save_workout_plan(user_id: int, text: str, plan_json: Optional[str] = None):
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        await conn.execute("""
            INSERT INTO workout_plans (user_id, plan_text, plan_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan_text=excluded.plan_text,
                plan_json=excluded.plan_json,
                updated_at=excluded.updated_at
        """, (user_id, text, plan_json or "", now))
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
        async with conn.execute("SELECT plan_text, plan_json FROM workout_plans WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None, {}
    return (row[0] or ""), loads_plan(row[1] or "")


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


async def get_or_create_today_session(user_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with db() as conn:
        async with conn.execute("""
            SELECT id FROM diary_sessions
            WHERE user_id=? AND session_date=?
            ORDER BY id DESC LIMIT 1
        """, (user_id, today)) as cur:
            row = await cur.fetchone()
        if row:
            return int(row[0])

        title = f"Тренировка {today}"
        cur2 = await conn.execute("""
            INSERT INTO diary_sessions (user_id, session_date, title, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, today, title, datetime.utcnow().isoformat()))
        await conn.commit()
        return int(cur2.lastrowid)


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


async def get_last_measures_any(user_id: int, limit: int = 30):
    async with db() as conn:
        async with conn.execute("""
            SELECT mtype, value, created_at
            FROM measurements
            WHERE user_id=?
            ORDER BY id DESC LIMIT ?
        """, (user_id, limit)) as cur:
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
# ✅ ТРЕНИРОВКИ: индивидуально + новичок/средний + RIR каждый день + акцент + без повторов + дни кнопками
# =========================
def _limits_tags(limits: str) -> Dict[str, bool]:
    t = (limits or "").lower()
    return {
        "knee": any(x in t for x in ["колен", "колено", "мениск", "связк"]),
        "back": any(x in t for x in ["спин", "поясниц", "грыж", "протруз", "сколиоз"]),
        "shoulder": any(x in t for x in ["плеч", "лопат", "ротатор"]),
        "elbow": any(x in t for x in ["локт", "тендинит"]),
    }


def _state_tags(state: str) -> Dict[str, bool]:
    s = (state or "").lower()
    return {
        "tired": any(x in s for x in ["устал", "устав", "мало сна", "сон плох", "не высп", "стресс", "перегруз"]),
        "backoff": any(x in s for x in ["после перерыва", "давно не", "возвращаюсь", "только начал"]),
        "pain": any(x in s for x in ["болит", "боль", "дискомфорт", "тянет", "щелкает"]),
        "good": any(x in s for x in ["норм", "хорош", "отлич", "заряжен", "энерг", "восстановился"]),
    }


def generate_workout_plan(goal: str, place: str, exp: str, freq: int, limits: str, state_text: str, user_id: int = 0) -> Tuple[str, dict]:
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym") or ("gym" in pl)
    where = "ЗАЛ" if is_gym else "ДОМ"

    lvl = exp_level(exp)  # novice / mid / adv
    is_novice = (lvl == "novice")
    g = (goal or "").lower()
    is_cut = ("суш" in g)

    tags = _limits_tags(limits)
    st = _state_tags(state_text)

    f = int(freq or 3)
    f = max(MIN_DAYS, min(f, MAX_DAYS))
    if (st["tired"] or st["backoff"]) and f >= 5:
        f = 4
    if st["backoff"] and f >= 4:
        f = 3

    reps_base = "6–10" if not is_cut else "5–8"
    reps_iso = "10–15" if not is_cut else "12–20"

    base_sets = "3" if is_novice else "3–4"
    iso_sets = "2–3" if is_novice else "3"

    rir = "2–3" if (st["tired"] or st["backoff"] or st["pain"]) else "1–2"

    seed = (user_id or 0) + int(datetime.utcnow().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    avoid_knee = ["присед", "жим ног", "выпад", "болгар", "разгиб"]
    avoid_back = ["тяга", "станов", "наклон", "гребл", "румын"]
    avoid_shoulder = ["жим вверх", "жим лёжа", "жим в тренаж", "отжим"]
    avoid_elbow = ["разгиб", "француз", "трицепс", "сгибан"]

    def avoid_keys_for_base():
        keys = []
        if tags["knee"] or st["pain"]:
            keys += avoid_knee
        if tags["back"] or st["pain"]:
            keys += avoid_back
        if tags["shoulder"] or st["pain"]:
            keys += avoid_shoulder
        if tags["elbow"] or st["pain"]:
            keys += avoid_elbow
        return keys

    def pick_unique(pool: List[str], used: set, avoid_keys: List[str]) -> str:
        candidates = []
        for it in pool:
            low = it.lower()
            if any(k in low for k in avoid_keys):
                continue
            if it in used:
                continue
            candidates.append(it)
        if candidates:
            pick = rnd.choice(candidates)
            used.add(pick)
            return pick

        safe = [it for it in pool if not any(k in it.lower() for k in avoid_keys)]
        if safe:
            pick = rnd.choice(safe)
            used.add(pick)
            return pick

        pick = rnd.choice(pool) if pool else "—"
        used.add(pick)
        return pick

    used_bases = set()

    if is_gym:
        PUSH_BASE_N = ["Жим в тренажёре", "Жим гантелей лёжа", "Отжимания", "Сведения в тренажёре/кроссовере"]
        PULL_BASE_N = ["Верхний блок", "Тяга горизонтального блока", "Тяга гантели одной рукой"]
        LEGS_BASE_N = ["Жим ногами (лёгко)", "Гоблет-присед", "Ягодичный мост", "Сгибания ног (тренажёр)"]

        PUSH_BASE_M = ["Жим лёжа (штанга)", "Жим гантелей лёжа", "Жим в тренажёре", "Отжимания"]
        PULL_BASE_M = ["Подтягивания", "Верхний блок", "Тяга горизонтального блока", "Тяга гантели одной рукой"]
        LEGS_BASE_M = ["Присед (вариант)", "Жим ногами", "Румынская тяга (лёгкая)", "Ягодичный мост"]
    else:
        PUSH_BASE_N = ["Отжимания", "Отжимания с упором ног", "Жим гантелей лёжа"]
        PULL_BASE_N = ["Подтягивания (резинка/негативы)", "Тяга резинки к поясу", "Тяга гантели одной рукой"]
        LEGS_BASE_N = ["Приседания", "Гоблет-присед", "Ягодичный мост", "Болгарские (лёгко)"]

        PUSH_BASE_M = ["Отжимания", "Жим гантелей лёжа", "Отжимания с упором ног"]
        PULL_BASE_M = ["Подтягивания", "Тяга гантели одной рукой", "Тяга резинки к поясу"]
        LEGS_BASE_M = ["Приседания", "Болгарские выпады", "Ягодичный мост", "Румынская тяга (гантели)"]

    PUSH_BASE = PUSH_BASE_N if is_novice else PUSH_BASE_M
    PULL_BASE = PULL_BASE_N if is_novice else PULL_BASE_M
    LEGS_BASE = LEGS_BASE_N if is_novice else LEGS_BASE_M

    ISO_SHOULDERS = ["Разведения в стороны (гантели)", "Face pull (канат)", "Задняя дельта (тренажёр/резинка)"]
    ISO_BACK = ["Гиперэкстензия (лёгко)", "Тяга прямыми руками (блок)", "Пуловер (блок/гантель)"]
    ISO_ARMS_BI = ["Сгибания гантелей", "Сгибания на блоке", "Молотки"]
    ISO_ARMS_TRI = ["Разгибания на блоке", "Разгибания одной рукой (лёгко)", "Отжимания узкие"]
    ISO_LEGS = ["Икры стоя/сидя", "Сгибания ног", "Разгибания ног"]

    if tags["elbow"] or st["pain"]:
        ISO_ARMS_TRI = ["Разгибания на блоке (лёгко)", "Разгибания одной рукой (лёгко)"]

    if tags["knee"] or st["pain"]:
        ISO_LEGS = ["Икры стоя/сидя", "Сгибания ног", "Ягодичный мост"]

    avoid_keys = avoid_keys_for_base()

    st_low = (state_text or "").lower()
    if any(x in st_low for x in ["спина", "широч", "тяга"]):
        focus_cycle = ["спина", "плечи", "руки"]
    elif any(x in st_low for x in ["груд", "жим"]):
        focus_cycle = ["грудь", "плечи", "руки"]
    elif any(x in st_low for x in ["ног", "бедр", "ягод"]):
        focus_cycle = ["ноги/ягодицы", "спина", "плечи"]
    else:
        focus_cycle = ["плечи", "спина", "руки"]

    def focus_for_day(d: int) -> str:
        return focus_cycle[(d - 1) % len(focus_cycle)]

    def fmt_base(name: str) -> str:
        return f"{name} — {base_sets}×{reps_base}"

    def fmt_iso(name: str) -> str:
        return f"{name} — {iso_sets}×{reps_iso}"

    limits_line = (limits or "").strip() or "нет"
    state_line = (state_text or "").strip() or "норм"

    prog_name = "Старт Гипертрофии" if is_novice else "Гипертрофия PRO"
    basis = (
        "Основа: базовые движения (жим/тяга/ноги) + изоляция на акцент дня.\n"
        f"Принцип: прогрессия (повторы → вес), контроль техники, RIR {rir}."
    )

    intro = (
        f"🏋️ Мои тренировки — {prog_name} ({where})\n\n"
        f"Частота: {f}×/нед\n"
        f"Рекомендуемые дни: {weekday_schedule(f)}\n\n"
        f"Цель: {goal}\n"
        f"Уровень: {'новичок' if is_novice else 'средний+'}\n"
        f"Ограничения: {limits_line}\n"
        f"Состояние: {state_line}\n\n"
        "Как построено:\n"
        "• Разминка 5–10 мин + 1–2 разминочных подхода\n"
        f"• База: {base_sets} подхода, {reps_base} повторов, отдых 2–3 мин, RIR {rir}\n"
        f"• Изоляция: {iso_sets} подхода, {reps_iso} повторов, отдых 60–90 сек\n"
        "• Акцент дня — добиваем слабую группу (без дублирования упражнений)\n\n"
        "📌 Прогрессия:\n"
        "• дошёл до верхней границы повторов — +2.5–5% к весу\n"
        "• техника ломается — вес не трогаем\n"
        "• усталость копится — 1 лёгкая неделя\n\n"
        "Ниже выбери день кнопкой 👇"
    )

    days: Dict[str, str] = {}
    for d in range(1, f + 1):
        focus = focus_for_day(d)

        push = pick_unique(PUSH_BASE, used_bases, avoid_keys)
        pull = pick_unique(PULL_BASE, used_bases, avoid_keys)
        legs = pick_unique(LEGS_BASE, used_bases, avoid_keys)

        extra = None
        if (not is_novice) and not (st["tired"] or st["backoff"] or st["pain"]):
            extra_pool = ["Жим вверх", "Подтягивания", "Верхний блок", "Жим ногами"] if is_gym else ["Подтягивания", "Отжимания (усложнённые)", "Ягодичный мост (утяж.)"]
            extra = pick_unique(extra_pool, used_bases, avoid_keys)

        iso = []
        if focus == "плечи":
            iso += [rnd.choice(ISO_SHOULDERS), rnd.choice(ISO_SHOULDERS)]
        elif focus == "спина":
            iso += [rnd.choice(ISO_BACK), rnd.choice(ISO_BACK)]
        elif focus == "руки":
            iso += [rnd.choice(ISO_ARMS_BI), rnd.choice(ISO_ARMS_TRI)]
        elif focus == "грудь":
            iso += ["Сведения в тренажёре/кроссовере", "Отжимания узкие"]
        elif focus == "ноги/ягодицы":
            iso += [rnd.choice(ISO_LEGS), "Ягодичный мост (добивка)"]
        else:
            iso += [rnd.choice(ISO_SHOULDERS), rnd.choice(ISO_ARMS_BI)]

        if st["tired"] or st["backoff"] or st["pain"]:
            iso = iso[:2]
            extra = None

        lines = []
        lines.append(f"RIR: {rir}")
        lines.append(f"Акцент: {focus}")
        lines.append("")
        lines.append(f"• {fmt_base(push)}")
        lines.append(f"• {fmt_base(pull)}")
        lines.append(f"• {fmt_base(legs)}")
        if extra:
            lines.append(f"• {fmt_base(extra)}")

        lines.append("")
        lines.append("Изоляция:")
        for it in iso:
            lines.append(f"• {fmt_iso(it)}")

        days[str(d)] = "\n".join([f"День {d}", ""] + lines)

    plan_struct = {
        "name": prog_name,
        "where": where,
        "freq": f,
        "schedule": weekday_schedule(f),
        "basis": basis,
        "rir": rir,
        "days": days,
        "updated_at": datetime.utcnow().isoformat(),
    }

    return intro, plan_struct


# =========================
# ✅ ПИТАНИЕ (как у тебя было)
# =========================
FOOD_DB = {
    "oats":      {"name": "Овсянка (сухая)",              "kcal": 370, "p": 13.0, "f": 7.0,   "c": 62.0},
    "rice":      {"name": "Рис (сухой)",                  "kcal": 360, "p": 7.0,  "f": 0.7,   "c": 78.0},
    "buckwheat": {"name": "Гречка (сухая)",               "kcal": 340, "p": 12.0, "f": 3.0,   "c": 66.0},
    "pasta":     {"name": "Макароны (сухие)",             "kcal": 350, "p": 12.0, "f": 2.0,   "c": 70.0},
    "potato":    {"name": "Картофель",                    "kcal": 77,  "p": 2.0,  "f": 0.1,   "c": 17.0},
    "bread":     {"name": "Хлеб",                         "kcal": 250, "p": 8.0,  "f": 3.0,   "c": 49.0},
    "veg":       {"name": "Овощи (микс)",                 "kcal": 30,  "p": 1.5,  "f": 0.2,   "c": 6.0},

    "chicken":   {"name": "Куриная грудка",               "kcal": 165, "p": 31.0, "f": 3.6,   "c": 0.0},
    "turkey":    {"name": "Индейка (филе)",               "kcal": 150, "p": 29.0, "f": 2.0,   "c": 0.0},
    "fish":      {"name": "Рыба (белая)",                 "kcal": 110, "p": 22.0, "f": 2.0,   "c": 0.0},
    "beef":      {"name": "Говядина (постная)",           "kcal": 190, "p": 26.0, "f": 9.0,   "c": 0.0},
    "eggs":      {"name": "Яйца",                         "kcal": 143, "p": 12.6, "f": 10.0,  "c": 1.1},
    "curd_0_5":  {"name": "Творог 0–5%",                  "kcal": 120, "p": 18.0, "f": 5.0,   "c": 3.0},
    "yogurt":    {"name": "Йогурт натуральный",           "kcal": 60,  "p": 5.0,  "f": 2.5,   "c": 4.0},

    "oil":       {"name": "Оливковое масло",              "kcal": 900, "p": 0.0,  "f": 100.0, "c": 0.0},
    "nuts":      {"name": "Орехи",                        "kcal": 600, "p": 15.0, "f": 55.0,  "c": 15.0},
    "cheese":    {"name": "Сыр",                          "kcal": 350, "p": 25.0, "f": 27.0,  "c": 1.0},

    "banana":    {"name": "Банан",                        "kcal": 89,  "p": 1.1,  "f": 0.3,   "c": 23.0},
    "apple":     {"name": "Яблоко",                       "kcal": 52,  "p": 0.3,  "f": 0.2,   "c": 14.0},
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

def _flatten(day_meals: List[List[Tuple[str, float]]]) -> List[Tuple[str, float]]:
    return [x for m in day_meals for x in m]

def _totals_of_day(day_meals: List[List[Tuple[str, float]]]) -> Dict[str, float]:
    return _sum_nutr(_flatten(day_meals))

def _add_grams(day_meals: List[List[Tuple[str, float]]], key: str, delta: float):
    if delta == 0:
        return
    for mi in range(len(day_meals)):
        for ii in range(len(day_meals[mi])):
            k, g = day_meals[mi][ii]
            if k == key:
                day_meals[mi][ii] = (k, max(0.0, g + delta))
                return
    day_meals[-1].append((key, max(0.0, float(delta))))

def _adjust_to_target(day_meals: List[List[Tuple[str, float]]], target: Dict[str, float]) -> Dict[str, float]:
    protein_keys = ["chicken", "turkey", "fish", "curd_0_5", "yogurt"]
    carb_keys = ["rice", "buckwheat", "pasta", "oats", "potato", "bread", "banana", "apple"]
    fat_keys = ["oil", "nuts", "cheese"]

    for _ in range(60):
        t = _totals_of_day(day_meals)
        dk = target["kcal"] - t["kcal"]
        dp = target["p"] - t["p"]
        df = target["f"] - t["f"]
        dc = target["c"] - t["c"]

        if abs(dk) <= 35 and abs(dp) <= 6 and abs(df) <= 4 and abs(dc) <= 8:
            return t

        if dp > 6:
            _add_grams(day_meals, protein_keys[0], 30.0)
            continue
        if dp < -10:
            _add_grams(day_meals, protein_keys[0], -30.0)
            continue

        if df > 4:
            _add_grams(day_meals, "oil", 3.0)
            continue
        if df < -6:
            _add_grams(day_meals, "oil", -3.0)
            continue

        if dc > 10 or dk > 80:
            _add_grams(day_meals, "rice", 10.0)
            continue
        if dc < -12 or dk < -90:
            _add_grams(day_meals, "rice", -10.0)
            continue

        if dk > 60:
            _add_grams(day_meals, "oats", 10.0)
        elif dk < -60:
            _add_grams(day_meals, "oats", -10.0)

    return _totals_of_day(day_meals)

def _build_day_variant(variant: int, meals: int) -> List[List[Tuple[str, float]]]:
    meals = max(3, min(int(meals or 3), 5))

    if variant == 1:
        day = [
            [("oats", 70.0), ("banana", 120.0), ("eggs", 180.0)],
            [("rice", 90.0), ("chicken", 200.0), ("veg", 250.0), ("oil", 10.0)],
            [("buckwheat", 80.0), ("fish", 220.0), ("veg", 250.0)],
        ]
        if meals >= 4:
            day.append([("curd_0_5", 250.0)])
        if meals >= 5:
            day.append([("apple", 200.0), ("nuts", 25.0)])
        return day

    if variant == 2:
        day = [
            [("eggs", 180.0), ("bread", 80.0), ("cheese", 30.0)],
            [("pasta", 90.0), ("beef", 180.0), ("veg", 300.0), ("oil", 8.0)],
            [("potato", 400.0), ("chicken", 180.0), ("veg", 250.0), ("oil", 8.0)],
        ]
        if meals >= 4:
            day.append([("yogurt", 300.0), ("banana", 120.0)])
        if meals >= 5:
            day.append([("nuts", 25.0), ("apple", 200.0)])
        return day

    day = [
        [("oats", 60.0), ("curd_0_5", 200.0), ("apple", 200.0)],
        [("rice", 80.0), ("turkey", 220.0), ("veg", 300.0), ("oil", 8.0)],
        [("pasta", 80.0), ("fish", 220.0), ("veg", 250.0)],
    ]
    if meals >= 4:
        day.append([("yogurt", 300.0)])
    if meals >= 5:
        day.append([("banana", 120.0), ("nuts", 20.0)])
    return day

def build_meal_day_text(day_i: int, calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> str:
    target = {"kcal": float(calories), "p": float(protein_g), "f": float(fat_g), "c": float(carbs_g)}
    day_meals = _build_day_variant(day_i, meals)
    tot = _adjust_to_target(day_meals, target)

    final_k = int(round(tot["kcal"]))
    final_p = int(round(tot["p"]))
    final_f = int(round(tot["f"]))
    final_c = int(round(tot["c"]))

    lines = [f"📅 Пример {day_i} (разнообразный день)", ""]
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
    lines.append(f"✅ Итог дня: {final_k} ккал | Б {final_p}г Ж {final_f}г У {final_c}г")
    lines.append(f"🎯 Цель дня: {final_k} ккал | Б {final_p}г Ж {final_f}г У {final_c}г")
    lines.append("⚠️ Крупы/макароны в СУХОМ виде. Овощи можно смело больше.")
    return "\n".join(lines)

def nutrition_examples_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥣 Пример 1 (овсянка/рис/рыба)", callback_data="nutr:ex:1")],
        [InlineKeyboardButton(text="🍝 Пример 2 (паста/говядина/картофель)", callback_data="nutr:ex:2")],
        [InlineKeyboardButton(text="🍚 Пример 3 (индейка/рис/паста)", callback_data="nutr:ex:3")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])

def nutrition_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к примерам", callback_data="nutr:back")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])

def generate_nutrition_summary(goal: str, sex: str, age: int, height: int, weight: float, exp: str, freq: int = 3, place: str = "дом") -> Tuple[str, int, int, int, int, int]:
    calories = calc_calories(height, weight, age, sex, goal, freq=freq, place=place)
    p, f, c = calc_macros(calories, weight, goal)
    meals = suggest_meals_count(calories)

    summary = (
        "🍽 Моё питание\n\n"
        f"Цель: {goal}\n"
        f"Ориентир по калориям: ~{calories} ккал/день\n"
        f"Ориентир по БЖУ: Б {p}г / Ж {f}г / У {c}г\n"
        f"Сколько приёмов пищи: {meals}\n\n"
        "Я сделал 3 реально разные схемы на день.\n"
        "Нажми на пример — и я покажу готовый день, подогнанный по ккал/БЖУ."
    )
    return summary, calories, p, f, c, meals


# =========================
# МЕНЮ / START
# =========================
async def show_main_menu(bot: Bot, chat_id: int, user_id: int):
    text = (
        "👋 Привет! Я твой «Зелёный тренер».\n\n"
        "Я помогу тебе по-человечески, без занудства:\n"
        "• соберу тренировки под цель, место и самочувствие\n"
        "• сделаю питание без кулинарного цирка\n"
        "• дневник и замеры — чтобы ты видел прогресс, а не гадал\n\n"
        "Выбирай, куда пойдём 👇\n\n"
        "ℹ️ Оплата / профиль / поддержка — всегда на кнопках снизу."
    )
    await clean_send(bot, chat_id, user_id, text, reply_markup=menu_main_inline_kb())


async def cmd_start(message: Message, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await bot.send_message(
        chat_id=message.chat.id,
        text="✅ Всё ок, я на месте. Кнопки управления закрепил снизу 👇",
        reply_markup=control_reply_kb()
    )
    await show_main_menu(bot, message.chat.id, message.from_user.id)
    await try_delete_user_message(bot, message)


# =========================
# ✅ Навигация
# =========================
async def cb_nav(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")
    await state.clear()

    key = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    chat_id = callback.message.chat.id

    if key == "menu":
        await show_main_menu(bot, chat_id, uid)
    elif key == "workouts":
        await open_workouts(user_id=uid, chat_id=chat_id, bot=bot, callback=callback)
    elif key == "nutrition":
        await open_nutrition(user_id=uid, chat_id=chat_id, bot=bot, callback=callback)
    elif key == "measures":
        await open_measures(user_id=uid, chat_id=chat_id, bot=bot, state=state, callback=callback)
    elif key == "diary":
        await open_diary(user_id=uid, chat_id=chat_id, bot=bot, state=state, callback=callback)
    else:
        await show_main_menu(bot, chat_id, uid)

    await callback.answer()


# =========================
# ✅ Панель управления (ReplyKeyboard)
# =========================
async def open_payment_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()

    a = await get_access(message.from_user.id)

    if await is_access_active(message.from_user.id):
        text = f"✅ Доступ активен.\n{access_status_str(a)}\n\nЕсли надо — просто пользуйся разделами из меню 👇"
        await clean_send(bot, message.chat.id, message.from_user.id, text)
    else:
        text = (
            "💳 Оплата / Доступ\n\n"
            f"{access_status_str(a)}\n\n"
            "Выбирай тариф — я сразу покажу реквизиты.\n"
            "После перевода попросу только скрин перевода, ничего лишнего."
        )
        await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=pay_tariff_kb())
        await state.set_state(PaymentFlow.choose_tariff)

    await try_delete_user_message(bot, message)


def _profile_summary_text(u: dict) -> str:
    return (
        "⚙️ Твой профиль\n\n"
        f"Цель: {u.get('goal')}\n"
        f"Пол: {u.get('sex')}\n"
        f"Возраст: {u.get('age')}\n"
        f"Рост: {u.get('height')}\n"
        f"Вес: {u.get('weight')}\n"
        f"Где тренишь: {u.get('place')}\n"
        f"Опыт: {u.get('exp')}\n"
        f"Частота: {u.get('freq')}×/нед\n"
        f"Ограничения: {(u.get('limits') or 'нет')}\n"
        f"Состояние: {(u.get('state') or 'норм')}"
    )


async def open_profile_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await try_delete_user_message(bot, message)
    await state.clear()

    u = await get_user(message.from_user.id)
    if await ensure_profile_ready(message.from_user.id):
        await clean_send(bot, message.chat.id, message.from_user.id, _profile_summary_text(u), reply_markup=profile_view_kb())
        return

    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "Давай быстро настроим профиль 🙂\n\n🎯 С чего цель?"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_goal())


async def cb_profile_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "Ок, обновим профиль.\n\n🎯 Какая цель сейчас?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_goal())
    await callback.answer()


async def open_support_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    text = (
        "🆘 Поддержка\n\n"
        "Напиши проблему одним сообщением.\n"
        "Если есть ошибка — просто вставь текст/скрин, я разберусь и отвечу."
    )
    await clean_send(bot, message.chat.id, message.from_user.id, text)
    await try_delete_user_message(bot, message)


async def open_menu_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    await show_main_menu(bot, message.chat.id, message.from_user.id)
    await try_delete_user_message(bot, message)


# =========================
# ПРОФИЛЬ-МАСТЕР
# =========================
async def cb_profile_back(callback: CallbackQuery, state: FSMContext):
    step = callback.data.split(":")[2]
    uid = callback.from_user.id

    if step == "goal":
        await state.set_state(ProfileWizard.goal)
        text = _profile_header(1) + "🎯 Какая цель?"
        await clean_edit(callback, uid, text, reply_markup=kb_goal())
    elif step == "sex":
        await state.set_state(ProfileWizard.sex)
        text = _profile_header(2) + "👤 Пол?"
        await clean_edit(callback, uid, text, reply_markup=kb_sex())
    elif step == "age":
        await state.set_state(ProfileWizard.age)
        text = _profile_header(3) + "🎂 Возраст напиши числом (например 23):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("sex"))
    elif step == "height":
        await state.set_state(ProfileWizard.height)
        text = _profile_header(4) + "📏 Рост в см (например 178):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("age"))
    elif step == "weight":
        await state.set_state(ProfileWizard.weight)
        text = _profile_header(5) + "⚖️ Вес в кг (например 72.5):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("height"))
    elif step == "place":
        await state.set_state(ProfileWizard.place)
        text = _profile_header(6) + "🏠 Где тренируешься?"
        await clean_edit(callback, uid, text, reply_markup=kb_place())
    elif step == "exp":
        await state.set_state(ProfileWizard.exp)
        text = _profile_header(7) + "📈 Какой опыт?"
        await clean_edit(callback, uid, text, reply_markup=kb_exp())
    elif step == "freq":
        await state.set_state(ProfileWizard.freq)
        text = _profile_header(8) + "📅 Сколько тренировок в неделю реально удобно?"
        await clean_edit(callback, uid, text, reply_markup=kb_freq())
    elif step == "limits":
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(9) + "⛔️ Есть ограничения/травмы? Напиши (или «нет»):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("freq"))
    elif step == "state":
        await state.set_state(ProfileWizard.state)
        text = _profile_header(10) + "🙂 Как самочувствие/настроение сейчас? Можно кнопкой или текстом:"
        await clean_edit(callback, uid, text, reply_markup=kb_state())
    else:
        await clean_send(callback.bot, callback.message.chat.id, uid, "🏠 Меню", reply_markup=menu_main_inline_kb())

    await callback.answer()


async def cb_profile_goal(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    goal = {"mass": "масса", "cut": "сушка", "fit": "форма"}.get(v, v)
    await update_user(callback.from_user.id, goal=goal)

    await state.set_state(ProfileWizard.sex)
    text = _profile_header(2) + "👤 Ок. Теперь пол:"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_sex())
    await callback.answer()


async def cb_profile_sex(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    sex = "м" if v == "m" else "ж"
    await update_user(callback.from_user.id, sex=sex)

    await state.set_state(ProfileWizard.age)
    text = _profile_header(3) + "🎂 Возраст напиши числом (например 23):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("sex"))
    await callback.answer()


def _parse_int_from_text(s: str) -> Optional[int]:
    s = (s or "").strip()
    m = re.search(r"(\d+)", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_float_from_text(s: str) -> Optional[float]:
    s = (s or "").strip().replace(",", ".")
    m = re.search(r"(\d+(\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


async def profile_age_text(message: Message, state: FSMContext, bot: Bot):
    age = _parse_int_from_text(message.text or "")
    if age is None or age < 10 or age > 90:
        await message.answer("Возраст числом 🙂 Например: 23")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, age=age)

    await state.set_state(ProfileWizard.height)
    text = _profile_header(4) + "📏 Рост в см (например 178):"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_text_step("age"))
    await try_delete_user_message(bot, message)


async def profile_height_text(message: Message, state: FSMContext, bot: Bot):
    h = _parse_int_from_text(message.text or "")
    if h is None or h < 120 or h > 230:
        await message.answer("Рост в см 🙂 Например: 178")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, height=h)

    await state.set_state(ProfileWizard.weight)
    text = _profile_header(5) + "⚖️ Вес в кг (например 72.5):"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_text_step("height"))
    await try_delete_user_message(bot, message)


async def profile_weight_text(message: Message, state: FSMContext, bot: Bot):
    w = _parse_float_from_text(message.text or "")
    if w is None or w < 30 or w > 250:
        await message.answer("Вес в кг 🙂 Например: 72.5")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, weight=w)

    await state.set_state(ProfileWizard.place)
    text = _profile_header(6) + "🏠 Где тренируешься?"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_place())
    await try_delete_user_message(bot, message)


async def cb_profile_place(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    place = "дом" if v == "home" else "зал"
    await update_user(callback.from_user.id, place=place)

    await state.set_state(ProfileWizard.exp)
    text = _profile_header(7) + "📈 Какой опыт?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_exp())
    await callback.answer()


async def cb_profile_exp(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    if v == "0":
        await update_user(callback.from_user.id, exp="0", freq=3)
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(9) + "⛔️ Есть ограничения/травмы? Напиши (или «нет»):"
        await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("freq"))
        await callback.answer()
        return

    exp_text = "1-2 года" if v == "mid" else "2+ года"
    await update_user(callback.from_user.id, exp=exp_text)

    await state.set_state(ProfileWizard.freq)
    text = _profile_header(8) + "📅 Сколько тренировок в неделю реально удобно?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_freq())
    await callback.answer()


async def cb_profile_freq(callback: CallbackQuery, state: FSMContext):
    f = int(callback.data.split(":")[2])
    await update_user(callback.from_user.id, freq=f)

    await state.set_state(ProfileWizard.limits)
    text = _profile_header(9) + "⛔️ Есть ограничения/травмы? Напиши (или «нет»):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("freq"))
    await callback.answer()


async def profile_limits_text(message: Message, state: FSMContext, bot: Bot):
    limits = (message.text or "").strip()
    if not limits:
        await message.answer("Напиши ограничения текстом или просто «нет».")
        await try_delete_user_message(bot, message)
        return
    if limits.lower() in ("нет", "нету", "никаких", "no"):
        limits = ""

    await update_user(message.from_user.id, limits=limits)

    await state.set_state(ProfileWizard.state)
    text = _profile_header(10) + "🙂 Как самочувствие/настроение сейчас? Можно кнопкой или текстом:"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_state())
    await try_delete_user_message(bot, message)


async def cb_profile_state_pick(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":", 2)[2]

    if v == "text":
        await state.set_state(ProfileWizard.state)
        text = _profile_header(10) + (
            "Ок, напиши самочувствие текстом 🙂\n"
            "Например: «норм», «не выспался», «после перерыва», «побаливает плечо»."
        )
        await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("limits"))
        await callback.answer()
        return

    mapping = {
        "отличное": "отличное, заряжен",
        "норм": "норм",
        "устал": "устал / мало сна",
        "болит": "есть дискомфорт/боль",
    }
    st_txt = mapping.get(v, v)

    await update_user(callback.from_user.id, state=st_txt)
    await state.clear()

    u = await get_user(callback.from_user.id)
    summary = (
        _profile_header(10) +
        "✅ Готово! Профиль сохранил.\n\n"
        f"Цель: {u.get('goal')}\n"
        f"Пол: {u.get('sex')}\n"
        f"Возраст: {u.get('age')}\n"
        f"Рост: {u.get('height')}\n"
        f"Вес: {u.get('weight')}\n"
        f"Где тренишь: {u.get('place')}\n"
        f"Опыт: {u.get('exp')}\n"
        f"Частота: {u.get('freq')}×/нед\n"
        f"Ограничения: {(u.get('limits') or 'нет')}\n"
        f"Состояние: {(u.get('state') or 'норм')}\n\n"
        "Теперь открывай питание/тренировки — всё подстрою под эти данные."
    )
    await clean_edit(callback, callback.from_user.id, summary, reply_markup=profile_done_kb())
    await callback.answer()


async def profile_state_text(message: Message, state: FSMContext, bot: Bot):
    st_txt = (message.text or "").strip()
    if not st_txt:
        st_txt = "норм"

    await update_user(message.from_user.id, state=st_txt)
    await state.clear()

    u = await get_user(message.from_user.id)
    summary = (
        _profile_header(10) +
        "✅ Готово! Профиль сохранил.\n\n"
        f"Цель: {u.get('goal')}\n"
        f"Пол: {u.get('sex')}\n"
        f"Возраст: {u.get('age')}\n"
        f"Рост: {u.get('height')}\n"
        f"Вес: {u.get('weight')}\n"
        f"Где тренишь: {u.get('place')}\n"
        f"Опыт: {u.get('exp')}\n"
        f"Частота: {u.get('freq')}×/нед\n"
        f"Ограничения: {(u.get('limits') or 'нет')}\n"
        f"Состояние: {(u.get('state') or 'норм')}\n\n"
        "Теперь открывай питание/тренировки — всё подстрою под эти данные."
    )
    await clean_send(bot, message.chat.id, message.from_user.id, summary, reply_markup=profile_done_kb())
    await try_delete_user_message(bot, message)


# =========================
# ОПЛАТА — теперь только скрин
# =========================
def access_status_str(a: dict) -> str:
    if not a or a.get("paid") != 1:
        return "Статус: ❌ доступа нет"
    if a.get("tariff") == "life":
        return "Статус: ✅ доступ активен (НАВСЕГДА)"
    exp = a.get("expires_at")
    return f"Статус: ✅ доступ активен до {exp[:10]}" if exp else "Статус: ✅ доступ активен"


async def cb_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_code = callback.data.split(":")[1]
    if tariff_code not in TARIFFS:
        await callback.answer("Не понял тариф 😅", show_alert=True)
        return

    await state.update_data(tariff=tariff_code)
    code = gen_order_code(callback.from_user.id)

    text = (
        "💳 Оплата доступа\n\n"
        f"Тариф: {TARIFFS[tariff_code]['title']}\n"
        f"Сумма: {TARIFFS[tariff_code]['price']}₽\n\n"
        "Перевод по реквизитам:\n"
        f"• Банк: {BANK_NAME}\n"
        f"• Карта: {CARD_NUMBER}\n"
        f"• Получатель: {CARD_HOLDER}\n\n"
        "⚠️ В комментарии к переводу напиши код:\n"
        f"{code}\n\n"
        "Как оплатишь — жми «✅ Я оплатил» и пришли скрин перевода."
    )
    await callback.message.answer(text, reply_markup=pay_inline_kb())
    await callback.answer()


async def cb_i_paid(callback: CallbackQuery, state: FSMContext):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")

    if await is_access_active(callback.from_user.id):
        await callback.message.answer("✅ У тебя уже активный доступ. Всё норм 🙂")
        await callback.answer()
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if not tariff:
        await callback.message.answer("Сначала выбери тариф в «💳 Оплата/доступ» 🙂")
        await callback.answer()
        return

    if await has_recent_pending_payment(callback.from_user.id):
        await callback.message.answer("⏳ У тебя уже есть заявка на проверке (до 2 часов).")
        await callback.answer()
        return

    await callback.message.answer(
        "Ок, кидай скрин перевода (как фото). Больше ничего не нужно ✅"
    )
    await state.set_state(PaymentFlow.waiting_receipt)
    await callback.answer()


async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await message.answer("Нужно именно фото/скрин 🙂 Отправь как фото.")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if tariff not in TARIFFS:
        await message.answer("Я потерял выбранный тариф 😅 Открой «Оплата/доступ» и выбери заново.")
        await state.clear()
        await try_delete_user_message(bot, message)
        return

    amount = int(TARIFFS[tariff]["price"])
    last4 = "----"
    receipt_file_id = message.photo[-1].file_id
    code = gen_order_code(message.from_user.id)

    payment_id = await create_payment(message.from_user.id, tariff, amount, last4, code, receipt_file_id)
    await message.answer("✅ Принял! Сейчас проверю и открою доступ как подтвержу.")
    await try_delete_user_message(bot, message)

    u = await get_user(message.from_user.id)
    uname = f"@{u.get('username')}" if u.get("username") else "(без юзернейма)"

    caption = (
        "🧾 Новая заявка (скрин)\n\n"
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
                "✅ Подтвердил оплату!\n"
                f"Тариф: {TARIFFS[tariff]['title']}\n"
                f"{access_status_str(a)}\n\n"
                "Открывай меню и пользуйся 👇"
            ),
            reply_markup=menu_main_inline_kb()
        )
        await callback.answer("Подтверждено ✅")
    else:
        await set_payment_status(pid, "rejected")
        await bot.send_message(
            chat_id=user_id,
            text="❌ Отклонил. Проверь перевод/скрин и попробуй ещё раз (кнопка снизу: 💳 Оплата/доступ)."
        )
        await callback.answer("Отклонено ❌")


# =========================
# РАЗДЕЛЫ
# =========================
async def ensure_profile_ready(user_id: int) -> bool:
    u = await get_user(user_id)
    need = ["goal", "sex", "age", "height", "weight", "place", "exp", "freq", "state"]
    if any(not u.get(k) for k in need):
        return False
    return True


async def build_plans_if_needed(user_id: int, force: bool = False):
    u = await get_user(user_id)

    intro, plan_struct = generate_workout_plan(
        u["goal"], u["place"], u["exp"], int(u["freq"]),
        limits=u.get("limits") or "",
        state_text=u.get("state") or "",
        user_id=user_id
    )

    summary, cal, p, f, c, meals = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )
    nutrition_full = (
        summary
        + "\n\n🔁 Простые замены (чтобы не надоедало):\n"
          "• курица ↔ индейка ↔ рыба\n"
          "• рис ↔ гречка ↔ макароны\n"
          "• творог ↔ йогурт\n"
          "• банан ↔ яблоко\n"
    )

    if force:
        await save_workout_plan(user_id, intro, dumps_plan(plan_struct))
        await save_nutrition_plan(user_id, nutrition_full)
        return

    plan_text, plan_json = await get_workout_plan(user_id)
    nutr_text = await get_nutrition_plan(user_id)

    if not plan_text or not plan_json:
        await save_workout_plan(user_id, intro, dumps_plan(plan_struct))
    if not nutr_text:
        await save_nutrition_plan(user_id, nutrition_full)


TRACK_EXERCISES = [
    "Жим лёжа",
    "Присед",
    "Отжимания",
    "Верхний блок",
    "Подтягивания",
    "Румынская тяга",
    "Жим вверх",
    "Жим ногами",
]

def diary_exercises_kb():
    rows = []
    for i in range(0, len(TRACK_EXERCISES), 2):
        left = InlineKeyboardButton(text=TRACK_EXERCISES[i], callback_data=f"d:ex:{TRACK_EXERCISES[i]}")
        if i + 1 < len(TRACK_EXERCISES):
            right = InlineKeyboardButton(text=TRACK_EXERCISES[i+1], callback_data=f"d:ex:{TRACK_EXERCISES[i+1]}")
            rows.append([left, right])
        else:
            rows.append([left])

    rows.append([InlineKeyboardButton(text="📜 История", callback_data="d:history")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


MEASURE_TYPES = [
    ("weight", "Вес (кг)"),
    ("waist", "Талия (см)"),
    ("arm", "Рука (см)"),
    ("chest", "Грудь (см)"),
    ("thigh", "Бедро (см)"),
]

def measures_kb():
    rows = []
    for i in range(0, len(MEASURE_TYPES), 2):
        k1, t1 = MEASURE_TYPES[i]
        b1 = InlineKeyboardButton(text=t1, callback_data=f"mtype:{k1}")
        if i + 1 < len(MEASURE_TYPES):
            k2, t2 = MEASURE_TYPES[i+1]
            b2 = InlineKeyboardButton(text=t2, callback_data=f"mtype:{k2}")
            rows.append([b1, b2])
        else:
            rows.append([b1])

    rows.append([InlineKeyboardButton(text="📜 История", callback_data="m:history")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_workouts(user_id: int, chat_id: int, bot: Bot, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        await clean_send(bot, chat_id, user_id, locked_text())
        return

    if not await ensure_profile_ready(user_id):
        await clean_send(bot, chat_id, user_id, "⚠️ Сначала заполни профиль (кнопка снизу: ⚙️ Профиль).")
        return

    plan_text, plan_struct = await get_workout_plan(user_id)
    if not plan_text or not plan_struct:
        await build_plans_if_needed(user_id, force=True)
        plan_text, plan_struct = await get_workout_plan(user_id)

    u = await get_user(user_id)
    freq = int(u.get("freq") or plan_struct.get("freq") or 3)
    kb = workout_days_kb(freq)

    if callback:
        await clean_edit(callback, user_id, plan_text or "🏋️ План не найден.", reply_markup=kb)
    else:
        await clean_send(bot, chat_id, user_id, plan_text or "🏋️ План не найден.", reply_markup=kb)


async def cb_workout_day(callback: CallbackQuery, bot: Bot):
    if not await is_access_active(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, locked_text())
        await callback.answer()
        return

    plan_text, plan_struct = await get_workout_plan(callback.from_user.id)
    if not plan_struct:
        await build_plans_if_needed(callback.from_user.id, force=True)
        plan_text, plan_struct = await get_workout_plan(callback.from_user.id)

    day = callback.data.split(":", 1)[1]
    day_text = (plan_struct.get("days") or {}).get(str(day))
    if not day_text:
        await callback.answer("День не найден 😅", show_alert=True)
        return

    u = await get_user(callback.from_user.id)
    kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3))
    await clean_edit(callback, callback.from_user.id, day_text, reply_markup=kb)
    await callback.answer()


async def cb_plan_refresh(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await state.clear()

    if not await is_access_active(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, locked_text())
        await callback.answer()
        return

    if not await ensure_profile_ready(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, "⚠️ Сначала заполни профиль (кнопка снизу: ⚙️ Профиль).")
        await callback.answer()
        return

    await build_plans_if_needed(callback.from_user.id, force=True)

    plan_text, plan_struct = await get_workout_plan(callback.from_user.id)
    u = await get_user(callback.from_user.id)
    kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3))

    await clean_edit(
        callback,
        callback.from_user.id,
        (plan_text or "") + "\n\n🔁 План обновил под твой текущий профиль/самочувствие.",
        reply_markup=kb
    )
    await callback.answer("Готово ✅")


async def open_nutrition(user_id: int, chat_id: int, bot: Bot, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        await clean_send(bot, chat_id, user_id, locked_text())
        return

    if not await ensure_profile_ready(user_id):
        await clean_send(bot, chat_id, user_id, "⚠️ Сначала заполни профиль (кнопка снизу: ⚙️ Профиль).")
        return

    u = await get_user(user_id)
    summary, _, _, _, _, _ = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )

    if callback:
        await clean_edit(callback, user_id, summary, reply_markup=nutrition_examples_kb())
    else:
        await clean_send(bot, chat_id, user_id, summary, reply_markup=nutrition_examples_kb())


async def open_measures(user_id: int, chat_id: int, bot: Bot, state: FSMContext, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        if callback:
            await clean_edit(callback, user_id, locked_text())
        else:
            await clean_send(bot, chat_id, user_id, locked_text())
        return

    await state.set_state(MeasureFlow.choose_type)
    text = "📏 Замеры (как дневник)\n\nВыбирай замер кнопкой — дату я поставлю сам."
    if callback:
        await clean_edit(callback, user_id, text, reply_markup=measures_kb())
    else:
        await clean_send(bot, chat_id, user_id, text, reply_markup=measures_kb())


async def open_diary(user_id: int, chat_id: int, bot: Bot, state: FSMContext, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        if callback:
            await clean_edit(callback, user_id, locked_text())
        else:
            await clean_send(bot, chat_id, user_id, locked_text())
        return

    await state.set_state(DiaryFlow.choosing_exercise)
    text = (
        "📓 Дневник тренировок\n\n"
        "Давай без лишнего — отслеживаем ключевые упражнения.\n"
        "Ты выбираешь упражнение кнопкой, а мне кидаешь только вес и повторы.\n"
        "Дату я поставлю автоматически."
    )
    if callback:
        await clean_edit(callback, user_id, text, reply_markup=diary_exercises_kb())
    else:
        await clean_send(bot, chat_id, user_id, text, reply_markup=diary_exercises_kb())


# =========================
# ✅ ДНЕВНИК: выбор → ввод (весxповторы)
# =========================
async def diary_pick_ex(callback: CallbackQuery, state: FSMContext, bot: Bot):
    exercise = callback.data.split("d:ex:", 1)[1].strip()
    await state.update_data(exercise=exercise)
    await state.set_state(DiaryFlow.enter_sets)

    today = datetime.now().strftime("%Y-%m-%d")
    text = (
        f"🗓 {today}\n"
        f"✅ Упражнение: {exercise}\n\n"
        "Напиши подходы (только вес и повторы):\n"
        "Пример: 60x8, 60x8, 60x7\n"
        "Можно и один: 80x6"
    )
    await clean_edit(callback, callback.from_user.id, text, reply_markup=diary_exercises_kb())
    await callback.answer()


async def diary_enter_sets(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip()
    data = await state.get_data()
    exercise = (data.get("exercise") or "").strip()
    if not exercise:
        await clean_send(bot, message.chat.id, message.from_user.id, "Сначала выбери упражнение в «Дневнике» кнопкой 🙂")
        await try_delete_user_message(bot, message)
        await state.clear()
        return

    parts = [p.strip() for p in txt.split(",") if p.strip()]
    if not parts:
        await message.answer("Формат такой: 60x8, 60x8 (весxповторы)")
        await try_delete_user_message(bot, message)
        return

    session_id = await get_or_create_today_session(message.from_user.id)

    parsed = []
    for p in parts:
        m = re.match(r"^(\d+(\.\d+)?)\s*[xх]\s*(\d+)$", p.lower())
        if not m:
            await message.answer(f"Не понял: '{p}'. Пример: 60x8")
            await try_delete_user_message(bot, message)
            return
        w = float(m.group(1))
        r = int(m.group(3))
        parsed.append((w, r))

    for i, (w, r) in enumerate(parsed, start=1):
        await add_set(session_id, exercise, i, w, r)

    today = datetime.now().strftime("%Y-%m-%d")
    msg = (
        f"✅ Записал!\n"
        f"🗓 {today}\n"
        f"🏷 {exercise}\n"
        f"Подходов: {len(parsed)}\n\n"
        "Хочешь — выбирай следующее упражнение кнопкой ниже."
    )
    await clean_send(bot, message.chat.id, message.from_user.id, msg, reply_markup=diary_exercises_kb())
    await try_delete_user_message(bot, message)
    await state.set_state(DiaryFlow.choosing_exercise)


async def diary_history(callback: CallbackQuery):
    history = await get_diary_history(callback.from_user.id, 10)
    if not history:
        await callback.message.answer("Истории пока нет. Запиши первую тренировку 🙂")
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

    await safe_send(callback.message, msg, reply_markup=simple_back_to_menu_inline_kb())
    await callback.answer()


# =========================
# ✅ ЗАМЕРЫ (дневник)
# =========================
async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await state.set_state(MeasureFlow.enter_value)

    name = dict(MEASURE_TYPES).get(mtype, mtype)
    await callback.message.answer(f"Ок, впиши значение для «{name}» числом (например 72.5):")
    await callback.answer()


async def measure_value(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        val = float(txt)
    except Exception:
        await message.answer("Нужно число 🙂 Например 72.5")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    mtype = data.get("mtype")

    await add_measure(message.from_user.id, mtype, val)
    rows = await get_last_measures(message.from_user.id, mtype, 6)

    name = dict(MEASURE_TYPES).get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    out = f"✅ Записал: {name} = {val:g}\n\nПоследние записи:\n{hist}\n\nВыбирай следующий замер ниже."
    await clean_send(bot, message.chat.id, message.from_user.id, out, reply_markup=measures_kb())
    await state.set_state(MeasureFlow.choose_type)
    await try_delete_user_message(bot, message)


async def measures_history(callback: CallbackQuery):
    rows = await get_last_measures_any(callback.from_user.id, 30)
    if not rows:
        await callback.message.answer("Истории замеров пока нет. Запиши первый замер 🙂")
        await callback.answer()
        return

    name_map = dict(MEASURE_TYPES)
    grouped: Dict[str, List[Tuple[float, str]]] = {}
    for mtype, val, created_at in rows:
        grouped.setdefault(mtype, []).append((val, created_at))

    msg = "📜 История замеров (последние записи):\n\n"
    for mtype, items in grouped.items():
        msg += f"{name_map.get(mtype, mtype)}:\n"
        for val, ts in items[:6]:
            msg += f"• {val:g} ({ts[:10]})\n"
        msg += "\n"

    await safe_send(callback.message, msg, reply_markup=measures_kb())
    await callback.answer()


# =========================
# ✅ ПИТАНИЕ: 3 кнопки → показываем конкретный день
# =========================
async def cb_nutr_example(callback: CallbackQuery, bot: Bot):
    if not await is_access_active(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, locked_text())
        await callback.answer()
        return

    if not await ensure_profile_ready(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, "⚠️ Сначала заполни профиль (кнопка снизу: ⚙️ Профиль).")
        await callback.answer()
        return

    day_i = int(callback.data.split(":")[2])
    u = await get_user(callback.from_user.id)
    summary, calories, p, f, c, meals = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )
    day_text = build_meal_day_text(day_i, calories, p, f, c, meals)
    await clean_edit(callback, callback.from_user.id, day_text, reply_markup=nutrition_back_kb())
    await callback.answer()

async def cb_nutr_back(callback: CallbackQuery, bot: Bot):
    await open_nutrition(callback.from_user.id, callback.message.chat.id, bot, callback=callback)
    await callback.answer()


# =========================
# ✅ ТЕХНИКИ: ХЕНДЛЕРЫ (с картинками)
# =========================
async def cb_tech_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await clean_edit(callback, callback.from_user.id, "📚 Техники — выбирай упражнение:", reply_markup=tech_kb())
    await callback.answer()


async def cb_tech_show(callback: CallbackQuery, bot: Bot):
    key = callback.data.split("tech:", 1)[1]
    item = TECH.get(key)
    if not item:
        await callback.answer("Не нашёл упражнение 😅", show_alert=True)
        return

    text = item["text"]
    img_path = item["img"]

    caption = text[:1024]
    rest = text[1024:].strip()

    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        await callback.message.answer_photo(photo=photo, caption=caption, reply_markup=tech_back_kb())
        if rest:
            await callback.message.answer(rest, reply_markup=tech_back_kb())
    else:
        await callback.message.answer(text, reply_markup=tech_back_kb())

    await callback.answer()


# =========================
# ✅ ПОСТЫ С КАРТИНКАМИ (АДМИН)
# =========================
def admin_posts_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать пост", callback_data="post:new")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def post_confirm_kb(post_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить всем", callback_data=f"post:send:{post_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel")],
    ])


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
        "• фото + подпись\n"
        "ИЛИ\n"
        "• просто фото/видео\n\n"
        "Дальше покажу превью и кнопку «Отправить всем»."
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
    await callback.message.answer("Ок, отменил.", reply_markup=admin_posts_kb())
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
            await message.answer("Нужно прислать текст или фото/видео 🙂")
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

    last_id = await get_last_bot_msg_id(uid)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass

    if media_type == "photo":
        m = await bot.send_photo(chat_id=chat_id, photo=media_file_id, caption=caption, reply_markup=post_confirm_kb(post_id))
        await set_last_bot_msg_id(uid, m.message_id)
    elif media_type == "video":
        m = await bot.send_video(chat_id=chat_id, video=media_file_id, caption=caption, reply_markup=post_confirm_kb(post_id))
        await set_last_bot_msg_id(uid, m.message_id)
    else:
        mid = await clean_send(bot, chat_id, uid, preview_title + text, reply_markup=post_confirm_kb(post_id))
        await set_last_bot_msg_id(uid, mid)

    await try_delete_user_message(bot, message)


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

    await callback.message.answer("📤 Ок, рассылаю всем пользователям…")
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
# ПОДДЕРЖКА: любой текст -> админу
# =========================
async def forward_to_admin(message: Message, bot: Bot):
    if message.from_user.id == ADMIN_ID:
        return
    if not message.text or message.text.startswith("/"):
        return

    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📩 Поддержка от @{message.from_user.username or 'no_username'} (id={message.from_user.id}):\n\n{message.text}"
    )
    await try_delete_user_message(bot, message)
    await clean_send(bot, message.chat.id, message.from_user.id, "✅ Принял. Я отвечу тут, как разберусь.")


# =========================
# ✅ ТЕХНИКИ: callback "tech:list" уже есть, добавим корректный роутер
# =========================
async def cb_tech_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await clean_edit(callback, callback.from_user.id, "📚 Техники — выбирай упражнение:", reply_markup=tech_kb())
    await callback.answer()


# =========================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
def setup_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, CommandStart())

    dp.callback_query.register(cb_nav, F.data.startswith("nav:"))

    dp.callback_query.register(cb_profile_edit, F.data == "p:edit")
    dp.callback_query.register(cb_profile_back, F.data.startswith("p:back:"))
    dp.callback_query.register(cb_profile_goal, F.data.startswith("p:goal:"))
    dp.callback_query.register(cb_profile_sex, F.data.startswith("p:sex:"))
    dp.callback_query.register(cb_profile_place, F.data.startswith("p:place:"))
    dp.callback_query.register(cb_profile_exp, F.data.startswith("p:exp:"))
    dp.callback_query.register(cb_profile_freq, F.data.startswith("p:freq:"))
    dp.callback_query.register(cb_profile_state_pick, F.data.startswith("p:state:"))

    dp.message.register(profile_age_text, ProfileWizard.age)
    dp.message.register(profile_height_text, ProfileWizard.height)
    dp.message.register(profile_weight_text, ProfileWizard.weight)
    dp.message.register(profile_limits_text, ProfileWizard.limits)
    dp.message.register(profile_state_text, ProfileWizard.state)

    dp.callback_query.register(cb_tariff, F.data.startswith("tariff:"))
    dp.callback_query.register(cb_i_paid, F.data == "pay_i_paid")
    dp.callback_query.register(admin_actions, F.data.startswith("admin_approve:") | F.data.startswith("admin_reject:"))
    dp.message.register(pay_receipt, PaymentFlow.waiting_receipt)

    dp.callback_query.register(cb_measure_type, F.data.startswith("mtype:"))
    dp.callback_query.register(measures_history, F.data == "m:history")
    dp.message.register(measure_value, MeasureFlow.enter_value)

    dp.callback_query.register(diary_pick_ex, F.data.startswith("d:ex:"))
    dp.callback_query.register(diary_history, F.data == "d:history")
    dp.message.register(diary_enter_sets, DiaryFlow.enter_sets)

    dp.callback_query.register(cb_tech_list, F.data == "tech:list")
    dp.callback_query.register(cb_tech_show, F.data.startswith("tech:"))

    dp.callback_query.register(cb_nutr_example, F.data.startswith("nutr:ex:"))
    dp.callback_query.register(cb_nutr_back, F.data == "nutr:back")

    # ✅ дни тренировок + обновить план
    dp.callback_query.register(cb_workout_day, F.data.startswith("wday:"))
    dp.callback_query.register(cb_plan_refresh, F.data == "plan:refresh")

    dp.message.register(cmd_posts, Command("posts"))
    dp.callback_query.register(cb_post_new, F.data == "post:new")
    dp.callback_query.register(cb_post_cancel, F.data == "post:cancel")
    dp.callback_query.register(cb_post_send, F.data.startswith("post:send:"))
    dp.message.register(post_waiting_content, PostFlow.waiting_content)

    dp.message.register(open_payment_from_reply, F.text == "💳 Оплата/доступ")
    dp.message.register(open_profile_from_reply, F.text == "⚙️ Профиль")
    dp.message.register(open_support_from_reply, F.text == "🆘 Поддержка")
    dp.message.register(open_menu_from_reply, F.text == "🏠 Меню")

    dp.message.register(forward_to_admin)


# =========================
# WEB SERVER (Render/health)
# =========================
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
# MAIN
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



