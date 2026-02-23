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
WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "media/welcome.jpg")

# ТАРИФЫ
TARIFFS = {
    "t1": {"title": "<TARIFF_TITLE_1>", "days": 30, "price": 1150},
    "t3": {"title": "<TARIFF_TITLE_3>", "days": 90, "price": 2790},
    "life": {"title": "<TARIFF_TITLE_LIFE>", "days": None, "price": 6990},
}

TG_SAFE_MSG_LEN = 3800
MIN_DAYS = 3
MAX_DAYS = 5

# =========================
# ✅ ТРИАЛ/ВОРОНКА (тексты — плейсхолдеры)
# =========================
TRIAL_DAYS = 3
FUNNEL_TEXTS = {
    0: "<FUNNEL_DAY_1_TEXT>",
    1: "<FUNNEL_DAY_2_TEXT>",
    2: "<FUNNEL_DAY_3_OFFER_TEXT>",
}

# =========================
# ЛОГИ
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trainer_bot")


# =========================
# FSM
# =========================
class PaymentFlow(StatesGroup):
    choose_tariff = State()
    waiting_receipt = State()   # ✅ только скрин


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
    age = State()
    height = State()
    weight = State()
    place = State()
    exp = State()
    freq = State()
    meals = State()
    limits = State()


# =========================
# ✅ ТЕХНИКИ (тексты — плейсхолдеры)
# =========================
TECH = {
    "squat":       {"title": "<TECH_SQUAT_TITLE>",       "img": "media/tech/squat.jpg",       "text": "<TECH_SQUAT_TEXT>"},
    "bench":       {"title": "<TECH_BENCH_TITLE>",       "img": "media/tech/bench.jpg",       "text": "<TECH_BENCH_TEXT>"},
    "row":         {"title": "<TECH_PUSHUP_TITLE>",      "img": "media/tech/pushup.jpg",      "text": "<TECH_PUSHUP_TEXT>"},
    "latpulldown": {"title": "<TECH_LAT_TITLE>",         "img": "media/tech/latpulldown.jpg", "text": "<TECH_LAT_TEXT>"},
    "pullup":      {"title": "<TECH_PULLUP_TITLE>",      "img": "media/tech/pullup.jpg",      "text": "<TECH_PULLUP_TEXT>"},
    "rdl":         {"title": "<TECH_RDL_TITLE>",         "img": "media/tech/rdl.jpg",         "text": "<TECH_RDL_TEXT>"},
    "ohp":         {"title": "<TECH_OHP_TITLE>",         "img": "media/tech/ohp.jpg",         "text": "<TECH_OHP_TEXT>"},
    "lateralraise":{"title": "<TECH_LATERAL_TITLE>",     "img": "media/tech/lateralraise.jpg","text": "<TECH_LATERAL_TEXT>"},
    "biceps":      {"title": "<TECH_BICEPS_TITLE>",      "img": "media/tech/biceps.jpg",      "text": "<TECH_BICEPS_TEXT>"},
    "triceps":     {"title": "<TECH_TRICEPS_TITLE>",     "img": "media/tech/triceps.jpg",     "text": "<TECH_TRICEPS_TEXT>"},
    "legpress":    {"title": "<TECH_LEGPRESS_TITLE>",    "img": "media/tech/legpress.jpg",    "text": "<TECH_LEGPRESS_TEXT>"},
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
        [InlineKeyboardButton(text="<BTN_BACK_TO_WORKOUTS>", callback_data="nav:workouts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tech_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="<BTN_BACK_TO_TECH_LIST>", callback_data="tech:list")],
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
        input_field_placeholder="<PLACEHOLDER_INPUT_HINT>"
    )


# =========================
# ✅ Inline меню разделов
# =========================
def menu_main_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Мои тренировки", callback_data="nav:workouts")],
        [InlineKeyboardButton(text="🍽 Моё питание", callback_data="nav:nutrition")],
        [InlineKeyboardButton(text="📓 Дневник", callback_data="nav:diary")],
        [InlineKeyboardButton(text="📏 Замеры", callback_data="nav:measures")],
    ])


def simple_back_to_menu_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


# =========================
# ✅ Тренировки: кнопки дней
# =========================
def workout_days_kb(freq: int):
    freq = max(MIN_DAYS, min(int(freq or 3), MAX_DAYS))
    rows = []
    btns = [InlineKeyboardButton(text=f"📅 День {i}", callback_data=f"wday:{i}") for i in range(1, freq + 1)]
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])

    rows += [
        [InlineKeyboardButton(text="📚 Техники упражнений", callback_data="tech:list")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
# Оплата: кнопки
# =========================
def pay_tariff_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟩 {TARIFFS['t1']['title']} — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
        [InlineKeyboardButton(text=f"🟦 {TARIFFS['t3']['title']} — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
        [InlineKeyboardButton(text=f"🟨 {TARIFFS['life']['title']} — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
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
# Профиль
# =========================
TOTAL_PROFILE_STEPS = 10


def _bar(step: int, total: int = TOTAL_PROFILE_STEPS, width: int = 10) -> str:
    step = max(0, min(step, total))
    pct = int(round(step / total * 100)) if total else 0
    filled = int(round(pct / 100 * width))
    filled = max(0, min(filled, width))
    return f"{'■'*filled}{'□'*(width-filled)} {pct}%"


def _profile_header(step: int) -> str:
    return f"🧩 <PROFILE_PROGRESS_TITLE> {step}/{TOTAL_PROFILE_STEPS}\n{_bar(step)}\n\n"


def profile_done_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Открыть меню", callback_data="nav:menu")]
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
        [InlineKeyboardButton(text="🏋️ Сила", callback_data="p:goal:strength"),
         InlineKeyboardButton(text="🏃 Выносливость", callback_data="p:goal:endurance")],
    ])


def kb_sex():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Мужчина", callback_data="p:sex:m"),
         InlineKeyboardButton(text="👩 Женщина", callback_data="p:sex:f")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:goal")],
    ])


def kb_place():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤸 Со своим весом", callback_data="p:place:bodyweight"),
         InlineKeyboardButton(text="🏋️ В зале", callback_data="p:place:gym")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:weight")],
    ])


def kb_exp():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="0 (новичок)", callback_data="p:exp:0")],
        [InlineKeyboardButton(text="1–2 года", callback_data="p:exp:mid"),
         InlineKeyboardButton(text="2+ года", callback_data="p:exp:adv")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:place")],
    ])


def kb_freq():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3×/нед", callback_data="p:freq:3"),
         InlineKeyboardButton(text="4×/нед", callback_data="p:freq:4")],
        [InlineKeyboardButton(text="5×/нед", callback_data="p:freq:5")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:exp")],
    ])


def kb_meals():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3 раза", callback_data="p:meals:3"),
         InlineKeyboardButton(text="4 раза", callback_data="p:meals:4")],
        [InlineKeyboardButton(text="5 раз", callback_data="p:meals:5")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:freq")],
    ])


def kb_text_step(back_to: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:back:{back_to}")],
    ])


# =========================
# УТИЛИТЫ JSON
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
        return "<SCHEDULE_3X>"
    if freq == 4:
        return "<SCHEDULE_4X>"
    return "<SCHEDULE_5X>"


def gen_order_code(user_id: int) -> str:
    rnd = random.randint(100, 999)
    return f"TG{str(user_id)[-3:]}{rnd}"


def locked_text() -> str:
    # заполни позже
    return "<LOCKED_TEXT_WHEN_NO_ACCESS>"


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


def calc_calories(height_cm: int, weight_kg: float, age: int, sex: str, goal: str, freq: int = 3, place: str = "свой вес") -> int:
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
    elif "сил" in g:
        target = tdee * 1.05
    elif "вынос" in g:
        target = tdee * 0.98
    else:
        target = tdee * 1.00

    return int(round(target))


def calc_macros(calories: int, weight_kg: float, goal: str):
    g = (goal or "").lower()

    if "суш" in g:
        protein = int(round(weight_kg * 2.2))
    elif "вынос" in g:
        protein = int(round(weight_kg * 1.7))
    elif "сил" in g:
        protein = int(round(weight_kg * 1.9))
    else:
        protein = int(round(weight_kg * 1.8))

    if "вынос" in g:
        fat = int(round(weight_kg * 0.7))
    else:
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
# АНТИ-ЗАСОРЕНИЕ ЧАТА (1 последнее сообщение бота)
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


async def get_diary_prompt_msg_id(user_id: int) -> Optional[int]:
    async with db() as conn:
        async with conn.execute("SELECT diary_prompt_msg_id FROM bot_state WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    try:
        return int(row[0]) if row[0] is not None else None
    except Exception:
        return None


async def set_diary_prompt_msg_id(user_id: int, msg_id: Optional[int]):
    async with db() as conn:
        await conn.execute("""
            INSERT INTO bot_state (user_id, diary_prompt_msg_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET diary_prompt_msg_id=excluded.diary_prompt_msg_id
        """, (user_id, int(msg_id) if msg_id else None))
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
            meals INTEGER,
            limits TEXT,
            state TEXT,
            created_at TEXT,
            trial_until TEXT,
            funnel_step INTEGER DEFAULT 0,
            last_funnel_at TEXT
        )
        """)

        for col, typ in [
            ("limits", "TEXT"),
            ("state", "TEXT"),
            ("meals", "INTEGER"),
            # ✅ ensure trial/funnel columns (на случай старой базы)
            ("trial_until", "TEXT"),
            ("funnel_step", "INTEGER DEFAULT 0"),
            ("last_funnel_at", "TEXT"),
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
            last_bot_msg_id INTEGER,
            diary_prompt_msg_id INTEGER
        )
        """)
        for col, typ in [
            ("diary_prompt_msg_id", "INTEGER"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE bot_state ADD COLUMN {col} {typ}")
            except Exception:
                pass

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
            "INSERT OR IGNORE INTO bot_state (user_id, last_bot_msg_id, diary_prompt_msg_id) VALUES (?, NULL, NULL)",
            (user_id,)
        )
        await conn.commit()


async def get_user(user_id: int):
    async with db() as conn:
        async with conn.execute("""
            SELECT user_id, username, goal, sex, age, height, weight, place, exp, freq, meals, limits, state,
                   trial_until, funnel_step, last_funnel_at
            FROM users WHERE user_id=?
        """, (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return {}
    return {
        "user_id": row[0], "username": row[1], "goal": row[2], "sex": row[3],
        "age": row[4], "height": row[5], "weight": row[6], "place": row[7],
        "exp": row[8], "freq": row[9], "meals": row[10], "limits": row[11], "state": row[12],
        "trial_until": row[13], "funnel_step": row[14], "last_funnel_at": row[15]
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


# ✅ ТРИАЛ тоже доступ
async def is_trial_active(user_id: int) -> bool:
    u = await get_user(user_id)
    until = (u.get("trial_until") or "").strip()
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(until)
    except Exception:
        return False
    return datetime.utcnow() < dt


async def is_any_access_active(user_id: int) -> bool:
    return (await is_access_active(user_id)) or (await is_trial_active(user_id))


async def start_trial(user_id: int, days: int = TRIAL_DAYS):
    now = datetime.utcnow()
    until = (now + timedelta(days=int(days))).isoformat()
    await update_user(user_id, trial_until=until, funnel_step=0, last_funnel_at="")


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
        # при оплате можно обнулить триал/воронку
        await conn.execute(
            "UPDATE users SET trial_until='', funnel_step=0, last_funnel_at='' WHERE user_id=?",
            (user_id,)
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

        title = f"<DIARY_SESSION_TITLE_PREFIX> {today}"
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
# ТРЕНИРОВКИ: генерация (тексты — плейсхолдеры, структура — твоя)
# =========================
def _limits_tags(limits: str) -> Dict[str, bool]:
    t = (limits or "").lower()
    return {
        "knee": any(x in t for x in ["колен", "колено", "мениск", "связк", "крест"]),
        "back": any(x in t for x in ["спин", "поясниц", "грыж", "протруз", "сколиоз"]),
        "shoulder": any(x in t for x in ["плеч", "лопат", "ротатор", "импиндж"]),
        "elbow": any(x in t for x in ["локт", "тендинит"]),
    }


def generate_workout_plan(goal: str, place: str, exp: str, freq: int, limits: str, user_id: int = 0) -> Tuple[str, dict]:
    # оставил твою генерацию, но вступительный текст заменён плейсхолдерами
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym") or ("gym" in pl)
    where = "ЗАЛ" if is_gym else "СВОЙ ВЕС"

    lvl = exp_level(exp)
    is_novice = (lvl == "novice")
    g = (goal or "").lower()

    is_cut = ("суш" in g)
    is_strength = ("сил" in g)
    is_endurance = ("вынос" in g)

    tags = _limits_tags(limits)

    f = int(freq or 3)
    f = max(MIN_DAYS, min(f, MAX_DAYS))

    if is_strength:
        reps_base = "3–6"
        reps_iso = "8–12"
        base_sets = "3" if is_novice else "4–5"
        iso_sets = "2" if is_novice else "2–3"
        rir = "1–2"
    elif is_endurance:
        reps_base = "12–20"
        reps_iso = "15–25"
        base_sets = "2–3"
        iso_sets = "2–3"
        rir = "2–3"
    else:
        reps_base = "6–10" if not is_cut else "8–12"
        reps_iso = "10–15" if not is_cut else "12–20"
        base_sets = "3" if is_novice else "3–4"
        iso_sets = "2–3" if is_novice else "3"
        rir = "1–2"

    seed = (user_id or 0) + int(datetime.utcnow().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    avoid_knee = ["присед", "жим ног", "выпад", "болгар", "разгиб"]
    avoid_back = ["тяга", "станов", "наклон", "румын", "гребл"]
    avoid_shoulder = ["жим вверх", "жим лёжа", "отжим", "жим в тренаж"]
    avoid_elbow = ["разгиб", "француз", "трицепс", "сгибан"]

    def avoid_keys_for_base():
        keys = []
        if tags["knee"]:
            keys += avoid_knee
        if tags["back"]:
            keys += avoid_back
        if tags["shoulder"]:
            keys += avoid_shoulder
        if tags["elbow"]:
            keys += avoid_elbow
        return keys

    def pick(pool: List[str], avoid_keys: List[str]) -> str:
        safe = [it for it in pool if not any(k in it.lower() for k in avoid_keys)]
        return rnd.choice(safe) if safe else (rnd.choice(pool) if pool else "—")

    avoid_keys = avoid_keys_for_base()

    if is_gym:
        SQUAT = ["Присед (вариант)", "Жим ногами", "Гоблет-присед", "Хакк-присед (лёгко)"]
        HINGE = ["Румынская тяга (лёгкая)", "Ягодичный мост", "Сгибания ног (тренажёр)", "Гиперэкстензия (лёгко)"]
        HPUSH = ["Жим лёжа (штанга)", "Жим гантелей лёжа", "Жим в тренажёре", "Сведения в кроссовере"]
        HPULL = ["Тяга горизонтального блока", "Тяга гантели одной рукой", "Тяга в тренажёре (грудь упор)"]
        VPULL = ["Подтягивания", "Верхний блок", "Тяга верхнего блока узко"]
        VPUSH = ["Жим вверх (гантели)", "Жим вверх (штанга)", "Жим в тренажёре вверх"]
        SHOULD = ["Разведения в стороны (гантели)", "Face pull (канат)", "Задняя дельта (тренажёр)"]
        BI = ["Сгибания гантелей", "Сгибания на блоке", "Молотки"]
        TRI = ["Разгибания на блоке", "Разгибания одной рукой (лёгко)", "Отжимания узкие"]
        CALVES = ["Икры стоя/сидя"]
        CORE = ["Планка", "Скручивания", "Подъёмы ног в висе/упоре"]
    else:
        SQUAT = ["Приседания", "Присед пауза (лёгко)", "Присед сумо", "Полуприсед (если колени капризны)"]
        HINGE = ["Ягодичный мост", "Гиперэкстензия (пол)", "Good-morning (очень легко, контроль)"]
        HPUSH = ["Отжимания", "Отжимания узкие", "Отжимания с паузой"]
        HPULL = ["Тяга резинки к поясу", "Тяга в наклоне (лёгко)", "Тяга гантели одной рукой (если есть)"]
        VPULL = ["Подтягивания (резинка/негативы)", "Тяга резинки сверху", "Подтягивания нейтр. хват (если есть)"]
        VPUSH = ["Жим резинки вверх", "Пайк-отжимания (лёгко)", "Отжимания (ноги на опоре)"]
        SHOULD = ["Разведения в стороны (гантели)", "Тяга резинки к лицу", "Задняя дельта (гантели)"]
        BI = ["Сгибания гантелей", "Молотки"]
        TRI = ["Отжимания узкие", "Разгибание гантели из-за головы (лёгко)"]
        CALVES = ["Подъёмы на носки стоя"]
        CORE = ["Планка", "Скручивания", "Подъёмы ног лёжа"]

    if tags["elbow"]:
        TRI = [x for x in TRI if "француз" not in x.lower()]
    if tags["knee"]:
        SQUAT = [x for x in SQUAT if "выпад" not in x.lower() and "болгар" not in x.lower()]
    if tags["back"]:
        HINGE = [x for x in HINGE if "румын" not in x.lower()]

    if f == 3:
        system = "Фулбади"
        template = ["FB-A", "FB-B", "FB-C"]
    elif f == 4:
        system = "Верх/Низ"
        template = ["UPPER", "LOWER", "UPPER", "LOWER"]
    else:
        system = "PPL + Верх/Низ"
        template = ["PUSH", "PULL", "LEGS", "UPPER", "LOWER"]

    def fmt(name: str, sets: str, reps: str) -> str:
        return f"{name} — {sets}×{reps}"

    def day_block(kind: str) -> List[str]:
        lines = [f"RIR: {rir}", ""]
        if kind.startswith("FB"):
            squat = pick(SQUAT, avoid_keys)
            hinge = pick(HINGE, avoid_keys)
            hpush = pick(HPUSH, avoid_keys)
            hpull = pick(HPULL, avoid_keys)
            should = pick(SHOULD, avoid_keys)
            arms = pick(BI if rnd.random() < 0.5 else TRI, avoid_keys)

            lines.append("<BASE_BLOCK_TITLE>")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            lines.append("")
            lines.append("<ISO_BLOCK_TITLE>")
            lines.append(f"• {fmt(should, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(arms, iso_sets, reps_iso)}")
            if not is_novice:
                lines.append(f"• {fmt(pick(CORE, avoid_keys), '2', '30–60 сек')}")
            return lines

        if kind == "UPPER":
            hpush = pick(HPUSH, avoid_keys)
            hpull = pick(HPULL, avoid_keys)
            vpush = pick(VPUSH, avoid_keys)
            vpull = pick(VPULL, avoid_keys)
            should = pick(SHOULD, avoid_keys)
            bi = pick(BI, avoid_keys)
            tri = pick(TRI, avoid_keys)

            lines.append("<BASE_BLOCK_TITLE>")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(vpull, base_sets, reps_base)}")
            if not tags["shoulder"]:
                lines.append(f"• {fmt(vpush, base_sets, reps_base)}")
            lines.append("")
            lines.append("<ISO_BLOCK_TITLE>")
            lines.append(f"• {fmt(should, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(bi, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(tri, iso_sets, reps_iso)}")
            return lines

        if kind == "LOWER":
            squat = pick(SQUAT, avoid_keys)
            hinge = pick(HINGE, avoid_keys)
            calves = pick(CALVES, avoid_keys)
            core = pick(CORE, avoid_keys)

            lines.append("<BASE_BLOCK_TITLE>")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            lines.append("")
            lines.append("<ISO_BLOCK_TITLE>")
            lines.append(f"• {fmt(calves, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(core, '2', '30–60 сек')}")
            return lines

        if kind == "PUSH":
            hpush = pick(HPUSH, avoid_keys)
            vpush = pick(VPUSH, avoid_keys)
            should = pick(SHOULD, avoid_keys)
            tri = pick(TRI, avoid_keys)

            lines.append("<BASE_BLOCK_TITLE>")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            if not tags["shoulder"]:
                lines.append(f"• {fmt(vpush, base_sets, reps_base)}")
            lines.append("")
            lines.append("<ISO_BLOCK_TITLE>")
            lines.append(f"• {fmt(should, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(tri, iso_sets, reps_iso)}")
            return lines

        if kind == "PULL":
            vpull = pick(VPULL, avoid_keys)
            hpull = pick(HPULL, avoid_keys)
            bi = pick(BI, avoid_keys)
            rear = pick(SHOULD, avoid_keys)

            lines.append("<BASE_BLOCK_TITLE>")
            lines.append(f"• {fmt(vpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            lines.append("")
            lines.append("<ISO_BLOCK_TITLE>")
            lines.append(f"• {fmt(rear, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(bi, iso_sets, reps_iso)}")
            return lines

        if kind == "LEGS":
            squat = pick(SQUAT, avoid_keys)
            hinge = pick(HINGE, avoid_keys)
            calves = pick(CALVES, avoid_keys)

            lines.append("<BASE_BLOCK_TITLE>")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            lines.append("")
            lines.append("<ISO_BLOCK_TITLE>")
            lines.append(f"• {fmt(calves, iso_sets, reps_iso)}")
            return lines

        return ["—"]

    limits_line = (limits or "").strip() or "<LIMITS_NONE>"

    intro = (
        "<WORKOUT_INTRO_PREFIX>\n"
        f"<WORKOUT_WHERE> {where}\n"
        f"<WORKOUT_SYSTEM> {system}\n"
        f"<WORKOUT_FREQ> {f}×/нед • {weekday_schedule(f)}\n"
        f"<WORKOUT_GOAL_LEVEL> {goal} • {'novice' if is_novice else 'mid+'}\n"
        f"<WORKOUT_LIMITS> {limits_line}\n\n"
        f"<WORKOUT_RULES> RIR {rir}\n"
        "<WORKOUT_PICK_DAY_PROMPT>"
    )

    days: Dict[str, str] = {}
    for d in range(1, f + 1):
        kind = template[d - 1]
        header = f"<DAY_HEADER_PREFIX> {d}\n{system}\n\n"
        body = "\n".join(day_block(kind))
        days[str(d)] = header + body

    plan_struct = {
        "system": system,
        "where": where,
        "freq": f,
        "schedule": weekday_schedule(f),
        "rir": rir,
        "days": days,
        "updated_at": datetime.utcnow().isoformat(),
    }
    return intro, plan_struct


# =========================
# ПИТАНИЕ (оставил как есть по логике, но тексты выдачи — плейсхолдеры)
# =========================
FOOD_DB = {
    "oats":      {"name": "Овсянка (сухая)", "kcal": 370, "p": 13.0, "f": 7.0, "c": 62.0},
    "rice":      {"name": "Рис (сухой)",     "kcal": 360, "p": 7.0,  "f": 0.7, "c": 78.0},
    "buckwheat": {"name": "Гречка (сухая)",  "kcal": 340, "p": 12.0, "f": 3.0, "c": 66.0},
    "pasta":     {"name": "Макароны (сухие)","kcal": 350, "p": 12.0, "f": 2.0, "c": 70.0},
    "potato":    {"name": "Картофель",       "kcal": 77,  "p": 2.0,  "f": 0.1, "c": 17.0},
    "bread":     {"name": "Хлеб",            "kcal": 250, "p": 8.0,  "f": 3.0, "c": 49.0},
    "veg":       {"name": "Овощи (микс)",    "kcal": 30,  "p": 1.5,  "f": 0.2, "c": 6.0},
    "chicken":   {"name": "Куриная грудка",  "kcal": 165, "p": 31.0, "f": 3.6, "c": 0.0},
    "turkey":    {"name": "Индейка (филе)",  "kcal": 150, "p": 29.0, "f": 2.0, "c": 0.0},
    "fish":      {"name": "Рыба (белая)",    "kcal": 110, "p": 22.0, "f": 2.0, "c": 0.0},
    "beef":      {"name": "Говядина (постная)","kcal": 190,"p": 26.0,"f": 9.0,"c": 0.0},
    "eggs":      {"name": "Яйца",            "kcal": 143, "p": 12.6, "f": 10.0,"c": 1.1},
    "curd_0_5":  {"name": "Творог 0–5%",     "kcal": 120, "p": 18.0, "f": 5.0, "c": 3.0},
    "yogurt":    {"name": "Йогурт натуральный","kcal": 60,"p": 5.0, "f": 2.5, "c": 4.0},
    "oil":       {"name": "Оливковое масло", "kcal": 900, "p": 0.0,  "f": 100.0,"c": 0.0},
    "nuts":      {"name": "Орехи",           "kcal": 600, "p": 15.0, "f": 55.0,"c": 15.0},
    "cheese":    {"name": "Сыр",             "kcal": 350, "p": 25.0, "f": 27.0,"c": 1.0},
    "banana":    {"name": "Банан",           "kcal": 89,  "p": 1.1,  "f": 0.3,"c": 23.0},
    "apple":     {"name": "Яблоко",          "kcal": 52,  "p": 0.3,  "f": 0.2,"c": 14.0},
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
    for _ in range(60):
        t = _totals_of_day(day_meals)
        dk = target["kcal"] - t["kcal"]
        dp = target["p"] - t["p"]
        df = target["f"] - t["f"]
        dc = target["c"] - t["c"]

        if abs(dk) <= 35 and abs(dp) <= 6 and abs(df) <= 4 and abs(dc) <= 8:
            return t

        if dp > 6:
            _add_grams(day_meals, protein_keys[0], 30.0); continue
        if dp < -10:
            _add_grams(day_meals, protein_keys[0], -30.0); continue

        if df > 4:
            _add_grams(day_meals, "oil", 3.0); continue
        if df < -6:
            _add_grams(day_meals, "oil", -3.0); continue

        if dc > 10 or dk > 80:
            _add_grams(day_meals, "rice", 10.0); continue
        if dc < -12 or dk < -90:
            _add_grams(day_meals, "rice", -10.0); continue

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
    # можно оставить/переписать потом; сейчас выдаём компактный плейсхолдер
    # если хочешь — верни полную детализацию, просто вставь текст сюда
    return f"<NUTRITION_DAY_TEXT day={day_i} kcal={calories} p={protein_g} f={fat_g} c={carbs_g} meals={meals}>"


def nutrition_examples_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥣 Пример 1", callback_data="nutr:ex:1")],
        [InlineKeyboardButton(text="🍝 Пример 2", callback_data="nutr:ex:2")],
        [InlineKeyboardButton(text="🍚 Пример 3", callback_data="nutr:ex:3")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def nutrition_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nutr:back")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def generate_nutrition_summary(goal: str, sex: str, age: int, height: int, weight: float, exp: str,
                             freq: int = 3, place: str = "свой вес", meals_pref: Optional[int] = None) -> Tuple[str, int, int, int, int, int]:
    calories = calc_calories(height, weight, age, sex, goal, freq=freq, place=place)
    p, f, c = calc_macros(calories, weight, goal)
    meals = int(meals_pref or 0) if meals_pref else suggest_meals_count(calories)
    meals = max(3, min(meals, 5))

    summary = (
        f"<NUTRITION_SUMMARY_TEXT kcal={calories} p={p} f={f} c={c} meals={meals}>"
    )
    return summary, calories, p, f, c, meals


# =========================
# МЕНЮ / START
# =========================
async def show_main_menu(bot: Bot, chat_id: int, user_id: int):
    text = "<MAIN_MENU_TEXT>"
    await clean_send(bot, chat_id, user_id, text, reply_markup=menu_main_inline_kb())


def welcome_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 <TRIAL_BUTTON_TEXT>", callback_data="trial:start")],
        [InlineKeyboardButton(text="💪 <OPEN_MENU_TEXT>", callback_data="nav:menu")],
    ])


async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    await try_delete_user_message(bot, message)

    # Постоянная клавиатура снизу
    await bot.send_message(
        chat_id=message.chat.id,
        reply_markup=control_reply_kb()
    )

    welcome_text = "<WELCOME_TEXT>"

    if os.path.exists(WELCOME_IMAGE):
        photo = FSInputFile(WELCOME_IMAGE)
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo,
            caption=welcome_text[:1024],
            reply_markup=welcome_kb()
        )
        if len(welcome_text) > 1024:
            await bot.send_message(message.chat.id, welcome_text[1024:])
    else:
        await bot.send_message(
            chat_id=message.chat.id,
            text=welcome_text,
            reply_markup=welcome_kb()
        )


# =========================
# ✅ ВОРОНКА: старт триала
# =========================
async def cb_trial_start(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    await ensure_user(uid, callback.from_user.username or "")
    await state.clear()

    if await is_access_active(uid):
        await clean_edit(callback, uid, "<ALREADY_PAID_TEXT>", reply_markup=menu_main_inline_kb())
        await callback.answer()
        return

    await start_trial(uid, days=TRIAL_DAYS)
    u = await get_user(uid)
    trial_until = (u.get("trial_until") or "")[:10]

    # отправляем Day1 сразу
    text = f"<TRIAL_STARTED_TEXT until={trial_until}>\n\n{FUNNEL_TEXTS.get(0,'<FUNNEL_DAY_1_TEXT>')}"
    await update_user(uid, funnel_step=1, last_funnel_at=datetime.utcnow().isoformat())

    await clean_edit(callback, uid, text, reply_markup=menu_main_inline_kb())
    await callback.answer()


async def funnel_worker(bot: Bot):
    while True:
        try:
            now = datetime.utcnow()
            async with db() as conn:
                async with conn.execute("""
                    SELECT user_id, trial_until, funnel_step, last_funnel_at
                    FROM users
                    WHERE trial_until IS NOT NULL AND trial_until != ''
                """) as cur:
                    rows = await cur.fetchall()

            for user_id, trial_until, step, last_at in rows:
                uid = int(user_id)
                # если оплатил — не шлём
                if await is_access_active(uid):
                    continue

                try:
                    until_dt = datetime.fromisoformat(trial_until)
                except Exception:
                    continue
                if now >= until_dt:
                    continue

                # 1 раз в 24 часа
                if last_at:
                    try:
                        last_dt = datetime.fromisoformat(last_at)
                        if (now - last_dt) < timedelta(hours=24):
                            continue
                    except Exception:
                        pass

                try:
                    step = int(step or 0)
                except Exception:
                    step = 0

                if step in FUNNEL_TEXTS:
                    text = FUNNEL_TEXTS[step]
                    await clean_send(bot, uid, uid, text, reply_markup=menu_main_inline_kb())
                    await update_user(uid, funnel_step=step + 1, last_funnel_at=now.isoformat())

        except Exception:
            logger.exception("funnel_worker crashed")

        await asyncio.sleep(60 * 15)


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
        text = f"<ACCESS_ACTIVE_TEXT>\n{access_status_str(a)}"
        await clean_send(bot, message.chat.id, message.from_user.id, text)
    else:
        text = (
            "<PAY_MENU_TEXT>\n\n"
            f"{access_status_str(a)}\n\n"
            "<PAY_MENU_HINT_TEXT>"
        )
        await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=pay_tariff_kb())
        await state.set_state(PaymentFlow.choose_tariff)

    await try_delete_user_message(bot, message)


def _profile_summary_text(u: dict) -> str:
    return "<PROFILE_SUMMARY_TEXT>"


async def open_profile_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await try_delete_user_message(bot, message)
    await state.clear()

    u = await get_user(message.from_user.id)
    if await ensure_profile_ready(message.from_user.id):
        await clean_send(bot, message.chat.id, message.from_user.id, _profile_summary_text(u), reply_markup=profile_view_kb())
        return

    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "<PROFILE_STEP_GOAL_TEXT>"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_goal())


async def cb_profile_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "<PROFILE_EDIT_GOAL_TEXT>"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_goal())
    await callback.answer()


async def open_support_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    text = "<SUPPORT_TEXT>"
    await clean_send(bot, message.chat.id, message.from_user.id, text)
    await try_delete_user_message(bot, message)


async def open_menu_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    await show_main_menu(bot, message.chat.id, message.from_user.id)
    await try_delete_user_message(bot, message)


# =========================
# ПРОФИЛЬ-МАСТЕР (тексты — плейсхолдеры)
# =========================
async def cb_profile_back(callback: CallbackQuery, state: FSMContext):
    step = callback.data.split(":")[2]
    uid = callback.from_user.id

    if step == "goal":
        await state.set_state(ProfileWizard.goal)
        text = _profile_header(1) + "<PROFILE_STEP_GOAL_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_goal())

    elif step == "sex":
        await state.set_state(ProfileWizard.sex)
        text = _profile_header(2) + "<PROFILE_STEP_SEX_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_sex())

    elif step == "age":
        await state.set_state(ProfileWizard.age)
        text = _profile_header(3) + "<PROFILE_STEP_AGE_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("sex"))

    elif step == "height":
        await state.set_state(ProfileWizard.height)
        text = _profile_header(4) + "<PROFILE_STEP_HEIGHT_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("age"))

    elif step == "weight":
        await state.set_state(ProfileWizard.weight)
        text = _profile_header(5) + "<PROFILE_STEP_WEIGHT_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("height"))

    elif step == "place":
        await state.set_state(ProfileWizard.place)
        text = _profile_header(6) + "<PROFILE_STEP_PLACE_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_place())

    elif step == "exp":
        await state.set_state(ProfileWizard.exp)
        text = _profile_header(7) + "<PROFILE_STEP_EXP_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_exp())

    elif step == "freq":
        await state.set_state(ProfileWizard.freq)
        text = _profile_header(8) + "<PROFILE_STEP_FREQ_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_freq())

    elif step == "meals":
        await state.set_state(ProfileWizard.meals)
        text = _profile_header(9) + "<PROFILE_STEP_MEALS_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_meals())

    elif step == "limits":
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(10) + "<PROFILE_STEP_LIMITS_TEXT>"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("meals"))

    else:
        await clean_send(callback.bot, callback.message.chat.id, uid, "<MENU_TEXT>", reply_markup=menu_main_inline_kb())

    await callback.answer()


async def cb_profile_goal(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    goal = {
        "mass": "масса",
        "cut": "сушка",
        "strength": "сила",
        "endurance": "выносливость",
    }.get(v, v)

    await update_user(callback.from_user.id, goal=goal)

    await state.set_state(ProfileWizard.sex)
    text = _profile_header(2) + "<PROFILE_STEP_SEX_TEXT>"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_sex())
    await callback.answer()


async def cb_profile_sex(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    sex = "м" if v == "m" else "ж"
    await update_user(callback.from_user.id, sex=sex)

    await state.set_state(ProfileWizard.age)
    text = _profile_header(3) + "<PROFILE_STEP_AGE_TEXT>"
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
    m = re.search(r"(\d+(\.*\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


async def profile_age_text(message: Message, state: FSMContext, bot: Bot):
    age = _parse_int_from_text(message.text or "")
    if age is None or age < 10 or age > 90:
        await message.answer("<ERR_AGE_TEXT>")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, age=age)

    await state.set_state(ProfileWizard.height)
    text = _profile_header(4) + "<PROFILE_STEP_HEIGHT_TEXT>"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_text_step("age"))
    await try_delete_user_message(bot, message)


async def profile_height_text(message: Message, state: FSMContext, bot: Bot):
    h = _parse_int_from_text(message.text or "")
    if h is None or h < 120 or h > 230:
        await message.answer("<ERR_HEIGHT_TEXT>")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, height=h)

    await state.set_state(ProfileWizard.weight)
    text = _profile_header(5) + "<PROFILE_STEP_WEIGHT_TEXT>"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_text_step("height"))
    await try_delete_user_message(bot, message)


async def profile_weight_text(message: Message, state: FSMContext, bot: Bot):
    w = _parse_float_from_text(message.text or "")
    if w is None or w < 30 or w > 250:
        await message.answer("<ERR_WEIGHT_TEXT>")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, weight=w)

    await state.set_state(ProfileWizard.place)
    text = _profile_header(6) + "<PROFILE_STEP_PLACE_TEXT>"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_place())
    await try_delete_user_message(bot, message)


async def cb_profile_place(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    place = "свой вес" if v == "bodyweight" else "зал"
    await update_user(callback.from_user.id, place=place)

    await state.set_state(ProfileWizard.exp)
    text = _profile_header(7) + "<PROFILE_STEP_EXP_TEXT>"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_exp())
    await callback.answer()


async def cb_profile_exp(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    if v == "0":
        await update_user(callback.from_user.id, exp="0", freq=3)
        await state.set_state(ProfileWizard.meals)
        text = _profile_header(9) + "<PROFILE_STEP_MEALS_TEXT>"
        await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_meals())
        await callback.answer()
        return

    exp_text = "1-2 года" if v == "mid" else "2+ года"
    await update_user(callback.from_user.id, exp=exp_text)

    await state.set_state(ProfileWizard.freq)
    text = _profile_header(8) + "<PROFILE_STEP_FREQ_TEXT>"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_freq())
    await callback.answer()


async def cb_profile_freq(callback: CallbackQuery, state: FSMContext):
    f = int(callback.data.split(":")[2])
    await update_user(callback.from_user.id, freq=f)

    await state.set_state(ProfileWizard.meals)
    text = _profile_header(9) + "<PROFILE_STEP_MEALS_TEXT>"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_meals())
    await callback.answer()


async def cb_profile_meals(callback: CallbackQuery, state: FSMContext):
    m = int(callback.data.split(":")[2])
    m = max(3, min(m, 5))
    await update_user(callback.from_user.id, meals=m)

    await state.set_state(ProfileWizard.limits)
    text = _profile_header(10) + "<PROFILE_STEP_LIMITS_TEXT>"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("meals"))
    await callback.answer()


async def profile_limits_text(message: Message, state: FSMContext, bot: Bot):
    limits = (message.text or "").strip()
    if not limits:
        await message.answer("<ERR_LIMITS_TEXT>")
        await try_delete_user_message(bot, message)
        return
    if limits.lower() in ("нет", "нету", "никаких", "no"):
        limits = ""

    await update_user(message.from_user.id, limits=limits)
    await state.clear()

    await build_plans_if_needed(message.from_user.id, force=True)

    summary = _profile_header(10) + "<PROFILE_SAVED_TEXT>"
    await clean_send(bot, message.chat.id, message.from_user.id, summary, reply_markup=profile_done_kb())
    await try_delete_user_message(bot, message)


# =========================
# ОПЛАТА — только скрин
# =========================
def access_status_str(a: dict) -> str:
    if not a or a.get("paid") != 1:
        return "<ACCESS_STATUS_NONE>"
    if a.get("tariff") == "life":
        return "<ACCESS_STATUS_LIFE>"
    exp = a.get("expires_at")
    return f"<ACCESS_STATUS_UNTIL> {exp[:10]}" if exp else "<ACCESS_STATUS_ACTIVE>"


async def cb_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_code = callback.data.split(":")[1]
    if tariff_code not in TARIFFS:
        await callback.answer("<ERR_TARIFF_UNKNOWN>", show_alert=True)
        return

    await state.update_data(tariff=tariff_code)
    code = gen_order_code(callback.from_user.id)

    text = (
        "<PAY_INSTRUCTIONS_TEXT>\n\n"
        f"<PAY_TARIFF> {TARIFFS[tariff_code]['title']}\n"
        f"<PAY_AMOUNT> {TARIFFS[tariff_code]['price']}₽\n\n"
        "<PAY_REQUISITES_HEADER>\n"
        f"<PAY_BANK> {BANK_NAME}\n"
        f"<PAY_CARD> {CARD_NUMBER}\n"
        f"<PAY_HOLDER> {CARD_HOLDER}\n\n"
        "<PAY_COMMENT_CODE_HEADER>\n"
        f"{code}\n\n"
        "<PAY_AFTER_TEXT>"
    )
    await callback.message.answer(text, reply_markup=pay_inline_kb())
    await callback.answer()


async def cb_i_paid(callback: CallbackQuery, state: FSMContext):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")

    if await is_access_active(callback.from_user.id):
        await callback.message.answer("<ACCESS_ALREADY_ACTIVE_TEXT>")
        await callback.answer()
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if not tariff:
        await callback.message.answer("<ERR_NO_TARIFF_SELECTED>")
        await callback.answer()
        return

    if await has_recent_pending_payment(callback.from_user.id):
        await callback.message.answer("<PAYMENT_ALREADY_PENDING_TEXT>")
        await callback.answer()
        return

    await callback.message.answer("<ASK_RECEIPT_SCREENSHOT_TEXT>")
    await state.set_state(PaymentFlow.waiting_receipt)
    await callback.answer()


async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await message.answer("<ERR_NEED_PHOTO_TEXT>")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if tariff not in TARIFFS:
        await message.answer("<ERR_TARIFF_LOST_TEXT>")
        await state.clear()
        await try_delete_user_message(bot, message)
        return

    amount = int(TARIFFS[tariff]["price"])
    receipt_file_id = message.photo[-1].file_id
    code = gen_order_code(message.from_user.id)

    payment_id = await create_payment(message.from_user.id, tariff, amount, "----", code, receipt_file_id)
    await message.answer("<PAYMENT_RECEIVED_TEXT>")
    await try_delete_user_message(bot, message)

    u = await get_user(message.from_user.id)
    uname = f"@{u.get('username')}" if u.get("username") else "(no_username)"

    caption = (
        "<ADMIN_PAYMENT_NEW_TEXT>\n\n"
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
        caption=caption[:1024],
        reply_markup=admin_review_kb(payment_id)
    )
    await state.clear()


async def admin_actions(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("<ERR_NO_ADMIN_ACCESS>", show_alert=True)
        return

    action, pid = callback.data.split(":")
    pid = int(pid)

    p = await get_payment(pid)
    if not p:
        await callback.answer("<ERR_PAYMENT_NOT_FOUND>", show_alert=True)
        return
    if p["status"] != "pending":
        await callback.answer(f"<ERR_ALREADY_HANDLED> {p['status']}", show_alert=True)
        return

    user_id = p["user_id"]
    tariff = p.get("tariff")

    if action == "admin_approve":
        if tariff not in TARIFFS:
            await callback.answer("<ERR_PAYMENT_NO_TARIFF>", show_alert=True)
            return
        await set_payment_status(pid, "approved")
        await set_paid_tariff(user_id, tariff)

        a = await get_access(user_id)
        await bot.send_message(
            chat_id=user_id,
            text=f"<PAYMENT_APPROVED_USER_TEXT>\n{access_status_str(a)}",
            reply_markup=menu_main_inline_kb()
        )
        await callback.answer("<ADMIN_APPROVED_OK>")
    else:
        await set_payment_status(pid, "rejected")
        await bot.send_message(
            chat_id=user_id,
            text="<PAYMENT_REJECTED_USER_TEXT>"
        )
        await callback.answer("<ADMIN_REJECTED_OK>")


# =========================
# РАЗДЕЛЫ
# =========================
async def ensure_profile_ready(user_id: int) -> bool:
    u = await get_user(user_id)
    need = ["goal", "sex", "age", "height", "weight", "place", "exp", "freq", "meals"]
    return not any(not u.get(k) for k in need)


async def build_plans_if_needed(user_id: int, force: bool = False):
    u = await get_user(user_id)

    intro, plan_struct = generate_workout_plan(
        u.get("goal"), u.get("place"), u.get("exp"), int(u.get("freq") or 3),
        limits=u.get("limits") or "",
        user_id=user_id
    )

    summary, _, _, _, _, _ = generate_nutrition_summary(
        u.get("goal"), u.get("sex"), int(u.get("age") or 18), int(u.get("height") or 170), float(u.get("weight") or 70),
        u.get("exp"), freq=int(u.get("freq") or 3), place=u.get("place") or "свой вес", meals_pref=int(u.get("meals") or 0)
    )

    nutrition_full = summary + "\n\n" + "<NUTRITION_REPLACEMENTS_TEXT>"

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
    if not await is_any_access_active(user_id):
        await clean_send(bot, chat_id, user_id, locked_text())
        return

    if not await ensure_profile_ready(user_id):
        await clean_send(bot, chat_id, user_id, "<ERR_PROFILE_NOT_READY_TEXT>")
        return

    plan_text, plan_struct = await get_workout_plan(user_id)
    if not plan_text or not plan_struct:
        await build_plans_if_needed(user_id, force=True)
        plan_text, plan_struct = await get_workout_plan(user_id)

    u = await get_user(user_id)
    kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3))

    if callback:
        await clean_edit(callback, user_id, plan_text or "<ERR_WORKOUT_PLAN_NOT_FOUND>", reply_markup=kb)
    else:
        await clean_send(bot, chat_id, user_id, plan_text or "<ERR_WORKOUT_PLAN_NOT_FOUND>", reply_markup=kb)


async def cb_workout_day(callback: CallbackQuery, bot: Bot):
    if not await is_any_access_active(callback.from_user.id):
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
        await callback.answer("<ERR_DAY_NOT_FOUND>", show_alert=True)
        return

    u = await get_user(callback.from_user.id)
    kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3))
    await clean_edit(callback, callback.from_user.id, day_text, reply_markup=kb)
    await callback.answer()


async def open_nutrition(user_id: int, chat_id: int, bot: Bot, callback: Optional[CallbackQuery] = None):
    if not await is_any_access_active(user_id):
        await clean_send(bot, chat_id, user_id, locked_text())
        return

    if not await ensure_profile_ready(user_id):
        await clean_send(bot, chat_id, user_id, "<ERR_PROFILE_NOT_READY_TEXT>")
        return

    u = await get_user(user_id)
    summary, *_ = generate_nutrition_summary(
        u.get("goal"), u.get("sex"), int(u.get("age") or 18), int(u.get("height") or 170), float(u.get("weight") or 70), u.get("exp"),
        freq=int(u.get("freq") or 3), place=u.get("place") or "свой вес", meals_pref=int(u.get("meals") or 0)
    )

    if callback:
        await clean_edit(callback, user_id, summary, reply_markup=nutrition_examples_kb())
    else:
        await clean_send(bot, chat_id, user_id, summary, reply_markup=nutrition_examples_kb())


async def open_measures(user_id: int, chat_id: int, bot: Bot, state: FSMContext, callback: Optional[CallbackQuery] = None):
    if not await is_any_access_active(user_id):
        if callback:
            await clean_edit(callback, user_id, locked_text())
        else:
            await clean_send(bot, chat_id, user_id, locked_text())
        return

    await state.set_state(MeasureFlow.choose_type)
    text = "<MEASURES_MENU_TEXT>"
    if callback:
        await clean_edit(callback, user_id, text, reply_markup=measures_kb())
    else:
        await clean_send(bot, chat_id, user_id, text, reply_markup=measures_kb())


async def open_diary(user_id: int, chat_id: int, bot: Bot, state: FSMContext, callback: Optional[CallbackQuery] = None):
    if not await is_any_access_active(user_id):
        if callback:
            await clean_edit(callback, user_id, locked_text())
        else:
            await clean_send(bot, chat_id, user_id, locked_text())
        return

    await state.set_state(DiaryFlow.choosing_exercise)
    text = "<DIARY_MENU_TEXT>"
    if callback:
        await clean_edit(callback, user_id, text, reply_markup=diary_exercises_kb())
    else:
        await clean_send(bot, chat_id, user_id, text, reply_markup=diary_exercises_kb())


# =========================
# ✅ ДНЕВНИК
# =========================
async def diary_pick_ex(callback: CallbackQuery, state: FSMContext, bot: Bot):
    exercise = callback.data.split("d:ex:", 1)[1].strip()
    await state.update_data(exercise=exercise)
    await state.set_state(DiaryFlow.enter_sets)

    old_prompt_id = await get_diary_prompt_msg_id(callback.from_user.id)
    if old_prompt_id:
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=old_prompt_id)
        except Exception:
            pass

    today = datetime.now().strftime("%Y-%m-%d")
    text = f"<DIARY_PROMPT_TEXT date={today} ex={exercise}>"
    m = await bot.send_message(chat_id=callback.message.chat.id, text=text)
    await set_diary_prompt_msg_id(callback.from_user.id, m.message_id)

    await callback.answer()


async def diary_enter_sets(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip()
    data = await state.get_data()
    exercise = (data.get("exercise") or "").strip()
    if not exercise:
        await clean_send(bot, message.chat.id, message.from_user.id, "<ERR_DIARY_PICK_EX_FIRST>")
        await try_delete_user_message(bot, message)
        await state.clear()
        return

    parts = [p.strip() for p in txt.split(",") if p.strip()]
    if not parts:
        await message.answer("<ERR_DIARY_FORMAT_TEXT>")
        await try_delete_user_message(bot, message)
        return

    session_id = await get_or_create_today_session(message.from_user.id)

    parsed = []
    for p in parts:
        m = re.match(r"^(\d+(\.\d+)?)\s*[xх]\s*(\d+)$", p.lower())
        if not m:
            await message.answer(f"<ERR_DIARY_BAD_PART> {p}")
            await try_delete_user_message(bot, message)
            return
        w = float(m.group(1))
        r = int(m.group(3))
        parsed.append((w, r))

    for i, (w, r) in enumerate(parsed, start=1):
        await add_set(session_id, exercise, i, w, r)

    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"<DIARY_SAVED_TEXT date={today} ex={exercise} sets={len(parsed)}>"
    await clean_send(bot, message.chat.id, message.from_user.id, msg, reply_markup=diary_exercises_kb())
    await try_delete_user_message(bot, message)

    prompt_id = await get_diary_prompt_msg_id(message.from_user.id)
    if prompt_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
        await set_diary_prompt_msg_id(message.from_user.id, None)

    await state.set_state(DiaryFlow.choosing_exercise)


async def diary_history(callback: CallbackQuery):
    history = await get_diary_history(callback.from_user.id, 10)
    if not history:
        await callback.message.answer("<DIARY_EMPTY_HISTORY_TEXT>")
        await callback.answer()
        return

    # компактная выдача — плейсхолдер
    await safe_send(callback.message, "<DIARY_HISTORY_TEXT>", reply_markup=simple_back_to_menu_inline_kb())
    await callback.answer()


# =========================
# ✅ ЗАМЕРЫ
# =========================
async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await state.set_state(MeasureFlow.enter_value)

    name = dict(MEASURE_TYPES).get(mtype, mtype)
    await callback.message.answer(f"<MEASURE_ENTER_VALUE_TEXT name={name}>")
    await callback.answer()


async def measure_value(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        val = float(txt)
    except Exception:
        await message.answer("<ERR_MEASURE_NEED_NUMBER_TEXT>")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    mtype = data.get("mtype")

    await add_measure(message.from_user.id, mtype, val)
    await clean_send(bot, message.chat.id, message.from_user.id, "<MEASURE_SAVED_TEXT>", reply_markup=measures_kb())
    await state.set_state(MeasureFlow.choose_type)
    await try_delete_user_message(bot, message)


async def measures_history(callback: CallbackQuery):
    rows = await get_last_measures_any(callback.from_user.id, 30)
    if not rows:
        await callback.message.answer("<MEASURES_EMPTY_HISTORY_TEXT>")
        await callback.answer()
        return
    await safe_send(callback.message, "<MEASURES_HISTORY_TEXT>", reply_markup=measures_kb())
    await callback.answer()


# =========================
# ✅ ПИТАНИЕ: 3 кнопки
# =========================
async def cb_nutr_example(callback: CallbackQuery, bot: Bot):
    if not await is_any_access_active(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, locked_text())
        await callback.answer()
        return

    if not await ensure_profile_ready(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, "<ERR_PROFILE_NOT_READY_TEXT>")
        await callback.answer()
        return

    day_i = int(callback.data.split(":")[2])
    u = await get_user(callback.from_user.id)
    summary, calories, p, f, c, meals = generate_nutrition_summary(
        u.get("goal"), u.get("sex"), int(u.get("age") or 18), int(u.get("height") or 170), float(u.get("weight") or 70), u.get("exp"),
        freq=int(u.get("freq") or 3), place=u.get("place") or "свой вес", meals_pref=int(u.get("meals") or 0)
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
    await clean_edit(callback, callback.from_user.id, "<TECH_LIST_TEXT>", reply_markup=tech_kb())
    await callback.answer()


async def cb_tech_show(callback: CallbackQuery, bot: Bot):
    key = callback.data.split("tech:", 1)[1]
    item = TECH.get(key)
    if not item:
        await callback.answer("<ERR_TECH_NOT_FOUND>", show_alert=True)
        return

    text = item["text"]
    img_path = item["img"]

    caption = (text or "")[:1024]
    rest = (text or "")[1024:].strip()

    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        await callback.message.answer_photo(photo=photo, caption=caption if caption else "<TECH_CAPTION_EMPTY>", reply_markup=tech_back_kb())
        if rest:
            await callback.message.answer(rest, reply_markup=tech_back_kb())
    else:
        await callback.message.answer(text or "<TECH_TEXT_EMPTY>", reply_markup=tech_back_kb())

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
    await message.answer("<ADMIN_POSTS_MENU_TEXT>", reply_markup=admin_posts_kb())


async def cb_post_new(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("<ERR_NO_ADMIN_ACCESS>", show_alert=True)
        return

    await state.clear()
    await callback.message.answer("<ADMIN_POST_CREATE_TEXT>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel")]
    ]))
    await state.set_state(PostFlow.waiting_content)
    await callback.answer()


async def cb_post_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await state.clear()
    await callback.message.answer("<ADMIN_CANCEL_OK_TEXT>", reply_markup=admin_posts_kb())
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
            await message.answer("<ERR_ADMIN_POST_NEED_CONTENT_TEXT>")
            return

    post_id = await create_post_draft(ADMIN_ID, media_type, media_file_id, text)
    await state.update_data(post_id=post_id)
    await state.set_state(PostFlow.waiting_confirm)

    preview_title = f"<ADMIN_POST_PREVIEW_TITLE id={post_id}>\n\n"
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
        mid = await clean_send(bot, chat_id, uid, preview_title + (text or ""), reply_markup=post_confirm_kb(post_id))
        await set_last_bot_msg_id(uid, mid)

    await try_delete_user_message(bot, message)


async def cb_post_send(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("<ERR_NO_ADMIN_ACCESS>", show_alert=True)
        return

    post_id = int(callback.data.split(":")[2])
    post = await get_post(post_id)
    if not post:
        await callback.answer("<ERR_POST_NOT_FOUND>", show_alert=True)
        return
    if post.get("status") == "sent":
        await callback.answer("<ERR_POST_ALREADY_SENT>", show_alert=True)
        return

    await callback.message.answer("<ADMIN_SENDING_TEXT>")
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
    await callback.message.answer(f"<ADMIN_SENT_RESULT ok={ok} fail={fail}>", reply_markup=admin_posts_kb())
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
        text=f"<SUPPORT_FORWARD_TEXT from_id={message.from_user.id} from_user=@{message.from_user.username or 'no_username'}>\n\n{message.text}"
    )
    await try_delete_user_message(bot, message)
    await clean_send(bot, message.chat.id, message.from_user.id, "<SUPPORT_RECEIVED_TEXT>")


# =========================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
def setup_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, CommandStart())

    dp.callback_query.register(cb_trial_start, F.data == "trial:start")

    dp.callback_query.register(cb_nav, F.data.startswith("nav:"))

    dp.callback_query.register(cb_profile_edit, F.data == "p:edit")
    dp.callback_query.register(cb_profile_back, F.data.startswith("p:back:"))
    dp.callback_query.register(cb_profile_goal, F.data.startswith("p:goal:"))
    dp.callback_query.register(cb_profile_sex, F.data.startswith("p:sex:"))
    dp.callback_query.register(cb_profile_place, F.data.startswith("p:place:"))
    dp.callback_query.register(cb_profile_exp, F.data.startswith("p:exp:"))
    dp.callback_query.register(cb_profile_freq, F.data.startswith("p:freq:"))
    dp.callback_query.register(cb_profile_meals, F.data.startswith("p:meals:"))

    dp.message.register(profile_age_text, ProfileWizard.age)
    dp.message.register(profile_height_text, ProfileWizard.height)
    dp.message.register(profile_weight_text, ProfileWizard.weight)
    dp.message.register(profile_limits_text, ProfileWizard.limits)

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

    dp.callback_query.register(cb_workout_day, F.data.startswith("wday:"))

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

    logger.info("Web server started on port %s", port)

    while True:
        await asyncio.sleep(3600)


# =========================
# MAIN
# =========================
async def main():
    if "PASTE_NEW_TOKEN_HERE" in BOT_TOKEN or not BOT_TOKEN or BOT_TOKEN == "0":
        raise RuntimeError("Нужно задать BOT_TOKEN через ENV.")

    if ADMIN_ID == 0:
        logger.warning("ADMIN_ID не задан. Подтверждение оплат админом работать не будет.")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook cleared, starting polling...")

    dp = Dispatcher()
    setup_handlers(dp)

    # ✅ web health + funnel worker
    asyncio.create_task(run_web_server())
    asyncio.create_task(funnel_worker(bot))

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


if __name__ == "__main__":
    asyncio.run(main())

