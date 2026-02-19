from aiohttp import web

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import os
from aiogram.types import FSInputFile

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

IMAGES_DIR = "images"

EXERCISES = {
    "rdl": {
        "title": "🏋️ Русская тяга",
        "image": "rdl.png",
        "text": (
            "Техника выполнения:\n"
            "1) Спина ровная, лопатки сведены.\n"
            "2) Колени слегка согнуты.\n"
            "3) Отводи таз назад.\n"
            "4) Штанга идёт вдоль ног.\n"
            "5) Опускай до растяжения бёдер.\n"
            "6) Вверх — за счёт ягодиц.\n\n"
            "Ошибки:\n"
            "— округлять спину\n"
            "— приседать вместо наклона"
        )
    }
}

BANK_NAME = os.getenv("BANK_NAME", "Сбербанк")
CARD_NUMBER = os.getenv("CARD_NUMBER", "0000 0000 0000 0000")
CARD_HOLDER = os.getenv("CARD_HOLDER", "ИМЯ ФАМИЛИЯ")

DB_PATH = os.getenv("DB_PATH", "bot.db")

# (опционально) file_id картинок для техник:
# пример: TECH_IMG_SQUAT="<file_id>"
TECH_IMG = {
    "squat": os.getenv("TECH_IMG_SQUAT", ""),
    "bench": os.getenv("TECH_IMG_BENCH", ""),
    "row": os.getenv("TECH_IMG_ROW", ""),
    "pulldown": os.getenv("TECH_IMG_PULLDOWN", ""),
    "pullup": os.getenv("TECH_IMG_PULLUP", ""),
    "ohp": os.getenv("TECH_IMG_OHP", ""),
    "deadlift_rdl": os.getenv("TECH_IMG_RDL", ""),
    "lateral_raise": os.getenv("TECH_IMG_LATERAL", ""),
    "biceps_curl": os.getenv("TECH_IMG_CURL", ""),
    "triceps_pushdown": os.getenv("TECH_IMG_TRICEPS", ""),
    "leg_press": os.getenv("TECH_IMG_LEGPRESS", ""),
}

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
# ✅ Панель управления — ТОЛЬКО 4 кнопки (как ты попросил)
def control_panel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Оплата / Доступ"), KeyboardButton(text="⚙️ Профиль")],
            [KeyboardButton(text="❓ FAQ"), KeyboardButton(text="🆘 Поддержка")],
        ],
        resize_keyboard=True
    )

# ✅ Меню планов (inline, чтобы не засорять чат)
def plans_menu_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍽 Мой план питания", callback_data="open:nutrition")],
        [InlineKeyboardButton(text="🏋️ Мои тренировки", callback_data="open:workouts")],
        [InlineKeyboardButton(text="📓 Дневник тренировок", callback_data="open:diary")],
        [InlineKeyboardButton(text="📏 Замеры", callback_data="open:measures")],
        [InlineKeyboardButton(text="📚 Техники выполнения", callback_data="open:tech")],
        [InlineKeyboardButton(text="🏠 Панель управления", callback_data="open:panel")],
    ])

def go_plans_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")],
    ])

def pay_tariff_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟩 1 месяц — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
        [InlineKeyboardButton(text=f"🟦 3 месяца — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
        [InlineKeyboardButton(text=f"🟨 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def pay_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="pay_i_paid")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
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
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def measures_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Вес (кг)", callback_data="mtype:weight")],
        [InlineKeyboardButton(text="📏 Талия (см)", callback_data="mtype:waist")],
        [InlineKeyboardButton(text="💪 Рука (см)", callback_data="mtype:arm")],
        [InlineKeyboardButton(text="胸 Грудь (см)", callback_data="mtype:chest")],
        [InlineKeyboardButton(text="🦵 Бедро (см)", callback_data="mtype:thigh")],
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")],
    ])

def diary_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Записать тренировку", callback_data="d:new")],
        [InlineKeyboardButton(text="📜 История (последние 10)", callback_data="d:history")],
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")],
    ])

def faq_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплата и доступ", callback_data="faq:pay")],
        [InlineKeyboardButton(text="🧠 Как строится план", callback_data="faq:plan")],
        [InlineKeyboardButton(text="🏋️ Прогресс/отказ", callback_data="faq:progress")],
        [InlineKeyboardButton(text="🍽 Калории/БЖУ", callback_data="faq:nutrition")],
        [InlineKeyboardButton(text="📌 Как считать калории", callback_data="faq:count")],
        [InlineKeyboardButton(text="⚠️ Нет результата", callback_data="faq:stuck")],
        [InlineKeyboardButton(text="😴 Сон", callback_data="faq:recovery")],
        [InlineKeyboardButton(text="🦵 Боль/техника", callback_data="faq:safety")],
        [InlineKeyboardButton(text="📓 Дневник/замеры", callback_data="faq:diary")],
        [InlineKeyboardButton(text="✍️ Задать вопрос", callback_data="faq:ask")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

# =========================
# УТИЛИТЫ: АНТИ-ЗАСОР ЧАТА
# =========================
async def safe_send_chunks_edit_or_new(
    bot: Bot,
    chat_id: int,
    base_message: Message | None,
    text: str,
    reply_markup=None
):
    """
    1) Если есть base_message — пробуем edit_text.
    2) Если слишком длинно — отправляем кусками (и всё равно стараемся не плодить).
    """
    t = (text or "").strip()
    if not t:
        return None

    if len(t) <= TG_SAFE_MSG_LEN and base_message is not None:
        try:
            await base_message.edit_text(t, reply_markup=reply_markup)
            return base_message
        except Exception:
            pass

    # fallback: chunks (придётся несколько сообщений)
    chunks = []
    while len(t) > TG_SAFE_MSG_LEN:
        cut = t.rfind("\n", 0, TG_SAFE_MSG_LEN)
        if cut == -1:
            cut = TG_SAFE_MSG_LEN
        chunks.append(t[:cut].strip())
        t = t[cut:].strip()
    if t:
        chunks.append(t)

    last = None
    for i, ch in enumerate(chunks):
        last = await bot.send_message(
            chat_id=chat_id,
            text=ch,
            reply_markup=reply_markup if i == len(chunks) - 1 else None
        )
    return last

def gen_order_code(user_id: int) -> str:
    rnd = random.randint(100, 999)
    return f"TG{str(user_id)[-3:]}{rnd}"

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

# =========================
# DB (устойчиво + храним last_bot_msg для удаления)
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
        # ✅ UI state: последнее сообщение бота (чтобы удалять и не засорять)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS ui_state (
            user_id INTEGER PRIMARY KEY,
            last_bot_message_id INTEGER
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
            "INSERT OR IGNORE INTO ui_state (user_id, last_bot_message_id) VALUES (?, NULL)",
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

# ✅ last bot message id
async def get_last_bot_msg_id(user_id: int) -> int | None:
    async with db() as conn:
        async with conn.execute("SELECT last_bot_message_id FROM ui_state WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else None

async def set_last_bot_msg_id(user_id: int, msg_id: int | None):
    async with db() as conn:
        await conn.execute("UPDATE ui_state SET last_bot_message_id=? WHERE user_id=?", (msg_id, user_id))
        await conn.commit()

async def clean_send(bot: Bot, chat_id: int, user_id: int, text: str, reply_markup=None):
    """
    ✅ стараемся удалять прошлое сообщение бота и отправлять новое
    (чат чище). Если удалить нельзя — просто отправим.
    """
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass

    m = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    await set_last_bot_msg_id(user_id, m.message_id)
    return m

async def clean_edit(base_message: Message, user_id: int, text: str, reply_markup=None):
    """
    ✅ редактируем текущее сообщение (это лучший вариант — не засоряет)
    """
    try:
        await base_message.edit_text(text, reply_markup=reply_markup)
        # message_id не меняется — оставляем last_bot_message_id как есть
        return base_message
    except Exception:
        # если нельзя редактировать — просто отправим новое (и удалим старое)
        bot = base_message.bot
        return await clean_send(bot, base_message.chat.id, user_id, text, reply_markup=reply_markup)

# =========================
# ПРОФИЛЬ (сохранённый + загрузка)
# =========================
PROFILE_STEPS_TOTAL = 8

def _loading_bar(step: int, total: int = PROFILE_STEPS_TOTAL, width: int = 12) -> str:
    step = max(1, min(step, total))
    filled = int(round((step / total) * width))
    filled = max(1, min(filled, width))
    bar = "■" * filled + "□" * (width - filled)
    return f"[{bar}]  {step}/{total}"

def sex_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Мужчина", callback_data="sex:m"),
         InlineKeyboardButton(text="👩 Женщина", callback_data="sex:f")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def age_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="12–17", callback_data="age:12-17"),
         InlineKeyboardButton(text="18–25", callback_data="age:18-25")],
        [InlineKeyboardButton(text="26–35", callback_data="age:26-35"),
         InlineKeyboardButton(text="36–45", callback_data="age:36-45")],
        [InlineKeyboardButton(text="46–55", callback_data="age:46-55")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def height_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="150–160 см", callback_data="height:150-160"),
         InlineKeyboardButton(text="161–170 см", callback_data="height:161-170")],
        [InlineKeyboardButton(text="171–180 см", callback_data="height:171-180"),
         InlineKeyboardButton(text="181–190 см", callback_data="height:181-190")],
        [InlineKeyboardButton(text="191+ см", callback_data="height:191+")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def weight_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="30–50", callback_data="weight:30-50"),
         InlineKeyboardButton(text="50–60", callback_data="weight:50-60")],
        [InlineKeyboardButton(text="60–80", callback_data="weight:60-80"),
         InlineKeyboardButton(text="80–100", callback_data="weight:80-100")],
        [InlineKeyboardButton(text="100–120", callback_data="weight:100-120"),
         InlineKeyboardButton(text="120+", callback_data="weight:120+")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def exp_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌱 Новичок (0)", callback_data="exp:0")],
        [InlineKeyboardButton(text="📈 1–2 года", callback_data="exp:1-2")],
        [InlineKeyboardButton(text="💪 2+ года", callback_data="exp:2+")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def freq_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3", callback_data="freq:3"),
         InlineKeyboardButton(text="4", callback_data="freq:4"),
         InlineKeyboardButton(text="5", callback_data="freq:5")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def profile_view_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить профиль", callback_data="prof_edit")],
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def _range_mid_int(s: str, default_mid: int) -> int:
    s = (s or "").strip()
    if s.endswith("+"):
        return default_mid
    if "-" in s:
        a, b = s.split("-", 1)
        a = int(re.sub(r"\D", "", a) or "0")
        b = int(re.sub(r"\D", "", b) or "0")
        if a and b:
            return int(round((a + b) / 2))
    return default_mid

def _range_mid_float(s: str, default_mid: float) -> float:
    s = (s or "").strip()
    if s.endswith("+"):
        return float(default_mid)
    if "-" in s:
        a, b = s.split("-", 1)
        a = float(re.sub(r"[^\d]", "", a) or "0")
        b = float(re.sub(r"[^\d]", "", b) or "0")
        if a and b:
            return float((a + b) / 2)
    return float(default_mid)

def _profile_complete(u: dict) -> bool:
    need = ["goal", "sex", "age", "height", "weight", "place", "exp", "freq"]
    return all(u.get(k) for k in need)

def _format_profile(u: dict) -> str:
    return (
        "⚙️ Твой профиль (сохранён)\n\n"
        f"🎯 Цель: {u.get('goal','—')}\n"
        f"👤 Пол: {u.get('sex','—')}\n"
        f"🎂 Возраст: {u.get('age','—')}\n"
        f"📏 Рост: {u.get('height','—')} см\n"
        f"⚖️ Вес: {u.get('weight','—')} кг\n"
        f"🏠/🏋️ Где: {u.get('place','—')}\n"
        f"📚 Опыт: {u.get('exp','—')}\n"
        f"📅 Частота: {u.get('freq','—')}×/нед\n"
    )

# =========================
# ТРЕНИРОВКИ / ПИТАНИЕ (оставил твою логику)
# =========================
def _pick(rnd: random.Random, items: list[str]) -> str:
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
        "📌 Прогрессия:\n"
        "1) Доводи подходы до верхней границы повторов\n"
        "2) Потом добавляй вес (+2.5–5%)\n"
        "3) Техника важнее веса\n\n"
        + "\n".join(days)
    )

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

def _sum_nutr(items: list[tuple[str, float]]):
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
    eggs_g = 180.0  # ~3 яйца
    rice_g_1 = 90.0
    rice_g_2 = 90.0
    chicken_g_1 = 200.0
    chicken_g_2 = 200.0
    veg_g_1 = 250.0
    veg_g_2 = 250.0
    oil_g = 10.0
    curd_g = 250.0
    banana_g = 120.0

    day_meals: list[list[tuple[str, float]]] = []
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
    three_days = build_3day_meal_plan(calories, p, f, c, meals)
    return (
        "🍽 МОЙ ПЛАН ПИТАНИЯ (3 дня)\n\n"
        f"Цель: {goal}\n"
        f"Калории: ~{calories} ккал/день\n"
        f"БЖУ: Белки {p}г / Жиры {f}г / Углеводы {c}г\n"
        f"Приёмов пищи: {meals}\n\n"
        "⚠️ Важно:\n"
        "• крупы указаны в СУХОМ виде\n"
        "• масло считать всегда\n\n"
        + three_days
    )

# =========================
# FAQ
# =========================
def faq_text(topic: str) -> str:
    if topic == "pay":
        return (
            "💳 Оплата и доступ\n\n"
            "Как оплатить:\n"
            "1) «💳 Оплата / Доступ» → выбери тариф\n"
            "2) переведи на карту\n"
            "3) нажми «✅ Я оплатил»\n"
            "4) отправь СКРИН/ФОТО ЧЕКА (только чек)\n\n"
            "Доступ откроется после подтверждения админом."
        )
    if topic == "plan":
        return (
            "🧠 Как строится план\n\n"
            "План под тебя:\n"
            "• цель, место (дом/зал)\n"
            "• опыт и частота\n\n"
            "В тренировках: база + изоляция.\n"
            "В питании: простые однотипные блюда (удобно соблюдать)."
        )
    if topic == "progress":
        return (
            "🏋️ Прогресс/отказ\n\n"
            "Лучше всего:\n"
            "• 1–2 повтора в запасе\n"
            "• прибавляй повторы → потом вес\n"
            "• техника всегда важнее"
        )
    if topic == "nutrition":
        return (
            "🍽 Калории/БЖУ\n\n"
            "Главное: калории + белок.\n"
            "Если 10–14 дней нет движения:\n"
            "• масса: +150–200 ккал\n"
            "• сушка: -150–200 ккал"
        )
    if topic == "count":
        return (
            "📌 Как считать калории\n\n"
            "Частые ошибки:\n"
            "• не считают масло\n"
            "• путают сухой/готовый вес\n"
            "• «на глаз»\n\n"
            "Правильно: взвешивать и записывать."
        )
    if topic == "stuck":
        return (
            "⚠️ Нет результата\n\n"
            "Проверь:\n"
            "1) калории реально те (масло/перекусы)\n"
            "2) есть прогрессия\n"
            "3) сон 7+ часов"
        )
    if topic == "recovery":
        return "😴 Сон\n\n7–9 часов. Если усталость копится — неделя полегче (-20–30% объёма)."
    if topic == "safety":
        return (
            "🦵 Боль/техника\n\n"
            "Резкая боль в суставе — стоп.\n"
            "Снизь вес / замени упражнение.\n"
            "Если не проходит — специалист."
        )
    if topic == "diary":
        return (
            "📓 Дневник/замеры\n\n"
            "Вес: 3–4 раза/нед утром → смотри среднее.\n"
            "Талия: 1–2 раза/нед."
        )
    return "Выбери тему."

# =========================
# ТЕХНИКИ ВЫПОЛНЕНИЯ (очень понятно + поддержка картинок)
# =========================
def techniques_index_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Присед", callback_data="tech:squat"),
         InlineKeyboardButton(text="Жим лёжа", callback_data="tech:bench")],
        [InlineKeyboardButton(text="Тяга (гребля)", callback_data="tech:row"),
         InlineKeyboardButton(text="Верхний блок", callback_data="tech:pulldown")],
        [InlineKeyboardButton(text="Подтягивания", callback_data="tech:pullup"),
         InlineKeyboardButton(text="Жим вверх", callback_data="tech:ohp")],
        [InlineKeyboardButton(text="Румынская тяга", callback_data="tech:deadlift_rdl"),
         InlineKeyboardButton(text="Разведения в стороны", callback_data="tech:lateral_raise")],
        [InlineKeyboardButton(text="Бицепс сгибания", callback_data="tech:biceps_curl"),
         InlineKeyboardButton(text="Трицепс на блоке", callback_data="tech:triceps_pushdown")],
        [InlineKeyboardButton(text="Жим ногами", callback_data="tech:leg_press")],
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans"),
         InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

def technique_text(key: str) -> str:
    t = {
        "squat": (
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
            "• пятки отрываются\n"
        ),
        "bench": (
            "📚 Жим лёжа\n\n"
            "Настройка:\n"
            "1) Лопатки сведи и опусти вниз (как будто «в карманы»).\n"
            "2) Стопы уверенно в пол, ягодицы на скамье.\n"
            "3) Хват чуть шире плеч.\n\n"
            "Движение:\n"
            "1) Опускай штангу к нижней части груди (примерно линия сосков/ниже).\n"
            "2) Локти не «в стороны 90°», держи 45–70°.\n"
            "3) Жми вверх и чуть назад, сохраняя лопатки зафиксированными.\n\n"
            "Ошибки:\n"
            "• плечи «вылезают» вперёд\n"
            "• отрываешь таз\n"
            "• слишком широкий/узкий хват без контроля\n"
        ),
        "row": (
            "📚 Тяга (гребля): горизонтальный блок / гантель\n\n"
            "Главное правило: тянем локтем, не кистью.\n\n"
            "Техника:\n"
            "1) Корпус стабильный, лёгкий наклон.\n"
            "2) Плечи вниз, шея расслаблена.\n"
            "3) Тяни рукоять к низу живота.\n"
            "4) В конце — лопатки свести, но без «перегиба» спиной.\n\n"
            "Ошибки:\n"
            "• дёргаешь корпусом\n"
            "• поднимаешь плечи к ушам\n"
        ),
        "pulldown": (
            "📚 Верхний блок (тяга к груди)\n\n"
            "Техника:\n"
            "1) Сядь, зафиксируй бёдра.\n"
            "2) Плечи вниз.\n"
            "3) Тяни к верхней части груди, локти вниз и чуть назад.\n"
            "4) Вверх возвращай медленно, не теряя контроль.\n\n"
            "Ошибки:\n"
            "• тянешь за голову\n"
            "• раскачка корпусом\n"
        ),
        "pullup": (
            "📚 Подтягивания\n\n"
            "Техника:\n"
            "1) Начни с «плеч вниз» (лопатки опусти).\n"
            "2) Тянись грудью к перекладине.\n"
            "3) Локти идут вниз.\n"
            "4) Внизу — полностью выпрямись, но не «проваливайся» в плечах.\n\n"
            "Если тяжело:\n"
            "• резинка / негативы (медленно вниз 3–5 сек)\n"
        ),
        "ohp": (
            "📚 Жим вверх (штанга/гантели)\n\n"
            "Техника:\n"
            "1) Пресс напряжён, ягодицы сжаты.\n"
            "2) Старт у ключиц.\n"
            "3) Жми вверх по прямой линии, голову чуть назад/вперёд (чтобы гриф прошёл).\n"
            "4) Вверху — не прогибай поясницу.\n\n"
            "Ошибки:\n"
            "• сильный прогиб в пояснице\n"
            "• «шраги» плечами к ушам\n"
        ),
        "deadlift_rdl": (
            "📚 Румынская тяга\n\n"
            "Цель: растянуть заднюю поверхность бедра, спина нейтральная.\n\n"
            "Техника:\n"
            "1) Колени чуть согнуты.\n"
            "2) Таз уходит назад, штанга скользит вдоль ног.\n"
            "3) Опускай до ощущения растяжения, спина ровная.\n"
            "4) Вверх — через ягодицы/заднюю поверхность.\n\n"
            "Ошибки:\n"
            "• округление спины\n"
            "• штанга далеко от ног\n"
        ),
        "lateral_raise": (
            "📚 Разведения в стороны (дельты)\n\n"
            "Техника:\n"
            "1) Лёгкий наклон корпуса вперёд.\n"
            "2) Локоть чуть выше кисти.\n"
            "3) Поднимай до уровня плеч (не выше).\n"
            "4) Вниз — медленно.\n\n"
            "Ошибки:\n"
            "• раскачка\n"
            "• поднимаешь плечи к ушам\n"
        ),
        "biceps_curl": (
            "📚 Сгибания на бицепс\n\n"
            "Техника:\n"
            "1) Локти прижаты к корпусу.\n"
            "2) Поднимай без раскачки.\n"
            "3) Вверху — короткая пауза.\n"
            "4) Вниз — медленно, полностью разгибай.\n\n"
            "Ошибки:\n"
            "• корпусом помогаешь\n"
            "• локти уезжают вперёд\n"
        ),
        "triceps_pushdown": (
            "📚 Разгибания на блоке (трицепс)\n\n"
            "Техника:\n"
            "1) Локти закрепи у корпуса.\n"
            "2) Разгибай до конца, внизу — пауза.\n"
            "3) Вверх — до 90° в локте, не поднимай плечи.\n\n"
            "Ошибки:\n"
            "• локти гуляют\n"
            "• включаешь спину/плечи\n"
        ),
        "leg_press": (
            "📚 Жим ногами\n\n"
            "Техника:\n"
            "1) Спина и таз плотно прижаты.\n"
            "2) Стопы примерно на ширине плеч.\n"
            "3) Опускай платформу до комфортной глубины без отрыва таза.\n"
            "4) Вверх — не «выпрямляй колени в замок».\n\n"
            "Ошибки:\n"
            "• таз отрывается\n"
            "• колени заваливаются внутрь\n"
        ),
    }
    return t.get(key, "Не нашёл технику.")

async def send_tech(bot: Bot, chat_id: int, user_id: int, key: str, base_message: Message | None = None):
    txt = technique_text(key)
    img_id = (TECH_IMG.get(key) or "").strip()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="open:tech")],
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans"),
         InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])

    # Если есть картинка — отправим фото с подписью (одним сообщением)
    if img_id:
        # стараемся удалить прошлое сообщение бота (чисто)
        last_id = await get_last_bot_msg_id(user_id)
        if last_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=last_id)
            except Exception:
                pass
        m = await bot.send_photo(chat_id=chat_id, photo=img_id, caption=txt, reply_markup=kb)
        await set_last_bot_msg_id(user_id, m.message_id)
        return m

    # без картинки — редактируем/отправляем текст
    if base_message is not None:
        return await clean_edit(base_message, user_id, txt, reply_markup=kb)
    return await clean_send(bot, chat_id, user_id, txt, reply_markup=kb)

# =========================
# ПЛАНЫ (сбор)
# =========================
def access_status_str(a: dict) -> str:
    if not a or a.get("paid") != 1:
        return "Статус: ❌ нет доступа"
    if a.get("tariff") == "life":
        return "Статус: ✅ доступ активен (НАВСЕГДА)"
    exp = a.get("expires_at")
    return f"Статус: ✅ доступ активен до {exp[:10]}" if exp else "Статус: ✅ доступ активен"

async def build_plans_for_user(user_id: int):
    u = await get_user(user_id)
    if not _profile_complete(u):
        return False, "⚠️ Заполни профиль: «⚙️ Профиль»"

    workout = generate_workout_plan(
        u["goal"], u["place"], u["exp"], int(u["freq"]),
        user_id=user_id
    )
    nutrition = generate_nutrition_plan(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )
    await save_workout_plan(user_id, workout)
    await save_nutrition_plan(user_id, nutrition)
    return True, "✅ Готово! Планы собраны."

# =========================
# ХЕНДЛЕРЫ: /start и навигация
# =========================
async def send_exercise(message: Message, key: str):
    ex = EXERCISES.get(key)
    if not ex:
        await message.answer("Упражнение не найдено.")
        return

    image_path = os.path.join(IMAGES_DIR, ex["image"])
    if not os.path.exists(image_path):
        await message.answer(
            f"Фото не найдено: {image_path}\n"
            f"Проверь, что файл лежит в папке {IMAGES_DIR} и имя совпадает."
        )
        return

    photo = FSInputFile(image_path)
    caption = f"{ex['title']}\n\n{ex['text']}"
    await message.answer_photo(photo=photo, caption=caption)

@dp.callback_query(F.data == "ex_rdl")
async def rdl_callback(callback: CallbackQuery):
    await send_exercise(callback.message, "rdl")
    await callback.answer()

async def cmd_start(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id, message.from_user.username or "")

    text = (
        "Привет! Я умею:\n"
        "• сохранять профиль\n"
        "• принимать оплату и проверять чек\n"
        "• выдавать питание/тренировки/дневник/замеры\n"
        "• показывать техники упражнений\n\n"
        "Нажми «⚙️ Профиль» → заполни.\n"
        "Потом «💳 Оплата / Доступ».\n\n"
        "А чтобы открыть планы — нажми кнопку ниже 👇"
    )
    # ✅ Панель управления (4 кнопки) + отдельная inline-кнопка “Меню”
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=go_plans_inline_kb())
    await message.answer("Панель управления 👇", reply_markup=control_panel_kb())

async def cb_open_panel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    # ✅ по твоей просьбе: при нажатии “Панель” / “Меню” — предыдущее сообщение исчезает (редактируем)
    await clean_edit(callback.message, callback.from_user.id, "Панель управления 👇\nВыбери раздел:", reply_markup=go_plans_inline_kb())
    await callback.answer()

async def cb_open_plans(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    # ✅ ТЗ: при нажатии «Меню» пропадает “твой профиль” и появляется меню планов
    await clean_edit(callback.message, callback.from_user.id, "📋 Меню планов — выбери раздел:", reply_markup=plans_menu_inline_kb())
    await callback.answer()

async def cb_open_tech(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await clean_edit(callback.message, callback.from_user.id, "📚 Техники выполнения — выбери упражнение:", reply_markup=techniques_index_kb())
    await callback.answer()

async def cb_open_section(callback: CallbackQuery, state: FSMContext):
    """
    open:nutrition / open:workouts / open:diary / open:measures / open:tech / open:panel / open:plans
    """
    sec = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    await ensure_user(uid, callback.from_user.username or "")

    if sec in ("panel", "plans", "tech"):
        await callback.answer()
        return

    if not await is_access_active(uid):
        await clean_edit(callback.message, uid, locked_text(), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплата", callback_data="open:pay")],
            [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
        ]))
        await callback.answer()
        return

    ok, msg = await build_plans_for_user(uid)
    if not ok:
        await clean_edit(callback.message, uid, msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Профиль", callback_data="open:panel")],
        ]))
        await callback.answer()
        return

    if sec == "workouts":
        plan = await get_workout_plan(uid) or "План тренировок пока не собран."
        # если очень длинно — может уйти в несколько сообщений (редко), но стараемся
        await safe_send_chunks_edit_or_new(callback.message.bot, callback.message.chat.id, callback.message, plan, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")],
        ]))
    elif sec == "nutrition":
        plan = await get_nutrition_plan(uid) or "План питания пока не собран."
        await safe_send_chunks_edit_or_new(callback.message.bot, callback.message.chat.id, callback.message, plan, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")],
        ]))
    elif sec == "diary":
        await clean_edit(callback.message, uid, "📓 Дневник тренировок", reply_markup=diary_inline_kb())
        await state.clear()
    elif sec == "measures":
        await clean_edit(callback.message, uid, "📏 Выбери замер:", reply_markup=measures_inline_kb())
        await state.set_state(MeasureFlow.choose_type)

    await callback.answer()

# =========================
# ПРОФИЛЬ: открыть/редакт
# =========================
async def open_profile(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id, message.from_user.username or "")
    u = await get_user(message.from_user.id)

    if _profile_complete(u):
        await clean_send(bot, message.chat.id, message.from_user.id, _format_profile(u), reply_markup=profile_view_kb())
        return

    text = (
        "⚙️ Профиль — заполним быстро (кнопками)\n"
        f"{_loading_bar(1)}\n\n"
        "Выбери цель:"
    )
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=goal_inline_kb())
    await state.set_state(ProfileFlow.goal)

async def cb_profile_edit(callback: CallbackQuery, state: FSMContext):
    text = (
        "⚙️ Профиль — заполним быстро (кнопками)\n"
        f"{_loading_bar(1)}\n\n"
        "Выбери цель:"
    )
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=goal_inline_kb())
    await state.set_state(ProfileFlow.goal)
    await callback.answer()

async def cb_goal(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    goal = {"mass": "масса", "cut": "сушка", "fit": "форма"}.get(v, v)
    await update_user(callback.from_user.id, goal=goal)

    text = "⚙️ Профиль\n" + _loading_bar(2) + "\n\n👤 Выбери пол:"
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=sex_inline_kb())
    await state.set_state(ProfileFlow.sex)
    await callback.answer()

async def cb_sex(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    sex = "м" if v == "m" else "ж"
    await update_user(callback.from_user.id, sex=sex)

    text = "⚙️ Профиль\n" + _loading_bar(3) + "\n\n🎂 Выбери возраст:"
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=age_inline_kb())
    await state.set_state(ProfileFlow.age)
    await callback.answer()

async def cb_age(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    age = _range_mid_int(v, default_mid=15 if v == "12-17" else 22)
    await update_user(callback.from_user.id, age=age)

    text = "⚙️ Профиль\n" + _loading_bar(4) + "\n\n📏 Выбери рост:"
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=height_inline_kb())
    await state.set_state(ProfileFlow.height)
    await callback.answer()

async def cb_height(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    height = _range_mid_int(v, default_mid=195 if v == "191+" else 175)
    await update_user(callback.from_user.id, height=height)

    text = "⚙️ Профиль\n" + _loading_bar(5) + "\n\n⚖️ Выбери вес:"
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=weight_inline_kb())
    await state.set_state(ProfileFlow.weight)
    await callback.answer()

async def cb_weight(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    weight = _range_mid_float(v, default_mid=125.0 if v == "120+" else 70.0)
    await update_user(callback.from_user.id, weight=weight)

    text = "⚙️ Профиль\n" + _loading_bar(6) + "\n\n🏠/🏋️ Где тренируешься?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Дом", callback_data="place:home"),
         InlineKeyboardButton(text="🏋️ Зал", callback_data="place:gym")],
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ])
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=kb)
    await state.set_state(ProfileFlow.place)
    await callback.answer()

async def cb_place(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    place = "дом" if v == "home" else "зал"
    await update_user(callback.from_user.id, place=place)

    text = "⚙️ Профиль\n" + _loading_bar(7) + "\n\n📚 Выбери опыт:"
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=exp_inline_kb())
    await state.set_state(ProfileFlow.exp)
    await callback.answer()

async def cb_exp(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    exp_map = {"0": "0", "1-2": "1-2 года", "2+": "2+ года"}
    exp = exp_map.get(v, v)
    await update_user(callback.from_user.id, exp=exp)

    lvl = exp_level(exp)
    if lvl == "novice":
        await update_user(callback.from_user.id, freq=3)
        u = await get_user(callback.from_user.id)
        text = "✅ Профиль заполнен!\n\n" + _format_profile(u)
        await clean_edit(callback.message, callback.from_user.id, text, reply_markup=profile_view_kb())
        await state.clear()
        await callback.answer()
        return

    text = "⚙️ Профиль\n" + _loading_bar(8) + "\n\n📅 Сколько тренировок в неделю?"
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=freq_inline_kb())
    await state.set_state(ProfileFlow.freq)
    await callback.answer()

async def cb_freq(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[1]
    if v not in ("3", "4", "5"):
        await callback.answer("Выбери 3/4/5", show_alert=True)
        return
    await update_user(callback.from_user.id, freq=int(v))

    u = await get_user(callback.from_user.id)
    text = "✅ Профиль заполнен!\n\n" + _format_profile(u)
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=profile_view_kb())
    await state.clear()
    await callback.answer()

# =========================
# ОПЛАТА (только чек)
# =========================
async def open_payment(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id, message.from_user.username or "")
    a = await get_access(message.from_user.id)

    if await is_access_active(message.from_user.id):
        await clean_send(bot, message.chat.id, message.from_user.id, f"✅ Доступ активен.\n{access_status_str(a)}", reply_markup=go_plans_inline_kb())
        return

    text = (
        "💳 Оплата / Доступ\n\n"
        f"{access_status_str(a)}\n\n"
        "Выбери тариф:\n"
        f"• 1 месяц — {TARIFFS['t1']['price']}₽\n"
        f"• 3 месяца — {TARIFFS['t3']['price']}₽\n"
        f"• навсегда — {TARIFFS['life']['price']}₽\n\n"
        "После оплаты: «✅ Я оплатил» → отправляешь ТОЛЬКО скрин/фото чека."
    )
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=pay_tariff_kb())
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
        "После оплаты нажми «✅ Я оплатил» и отправь СКРИН ЧЕКА."
    )
    await clean_edit(callback.message, callback.from_user.id, text, reply_markup=pay_inline_kb())
    await callback.answer()

async def cb_i_paid(callback: CallbackQuery, state: FSMContext):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")

    if await is_access_active(callback.from_user.id):
        await clean_edit(callback.message, callback.from_user.id, "✅ У тебя уже активный доступ.", reply_markup=go_plans_inline_kb())
        await callback.answer()
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if not tariff:
        await clean_edit(callback.message, callback.from_user.id, "Сначала выбери тариф в «💳 Оплата / Доступ».", reply_markup=pay_tariff_kb())
        await callback.answer()
        return

    if await has_recent_pending_payment(callback.from_user.id):
        await clean_edit(callback.message, callback.from_user.id, "⏳ У тебя уже есть активная заявка (до 2 часов).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
        ]))
        await callback.answer()
        return

    await clean_edit(callback.message, callback.from_user.id, "Отправь скрин/фото чека оплаты (как фото):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ]))
    await state.set_state(PaymentFlow.waiting_receipt)
    await callback.answer()

async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await clean_send(bot, message.chat.id, message.from_user.id, "Нужно фото/скрин чека. Отправь как фото.", reply_markup=None)
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if tariff not in TARIFFS:
        await clean_send(bot, message.chat.id, message.from_user.id, "Не вижу выбранный тариф. Зайди в «💳 Оплата / Доступ» и выбери тариф заново.", reply_markup=go_plans_inline_kb())
        await state.clear()
        return

    receipt_file_id = message.photo[-1].file_id
    code = gen_order_code(message.from_user.id)
    amount = int(TARIFFS[tariff]["price"])
    last4 = ""  # больше не собираем

    payment_id = await create_payment(message.from_user.id, tariff, amount, last4, code, receipt_file_id)
    await clean_send(bot, message.chat.id, message.from_user.id, "✅ Заявка отправлена. Как подтвержу — доступ откроется.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
    ]))

    u = await get_user(message.from_user.id)
    uname = f"@{u.get('username')}" if u.get("username") else "(без юзернейма)"

    caption = (
        "🧾 Новая заявка\n\n"
        f"payment_id: {payment_id}\n"
        f"user: {uname}\n"
        f"user_id: {message.from_user.id}\n"
        f"tariff: {tariff} ({TARIFFS[tariff]['title']})\n"
        f"expected_amount: {amount}\n"
        f"code: {code}\n"
        "Проверка: только по чеку (фото выше)."
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
                "Открой «📋 Меню» и выбирай раздел."
            ),
            reply_markup=go_plans_inline_kb()
        )
        await callback.answer("Подтверждено ✅")
    else:
        await set_payment_status(pid, "rejected")
        await bot.send_message(
            chat_id=user_id,
            text="❌ Оплата отклонена. Проверь чек и попробуй снова: «💳 Оплата / Доступ»"
        )
        await callback.answer("Отклонено ❌")

# =========================
# ДНЕВНИК
# =========================
async def diary_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введи дату (YYYY-MM-DD) или напиши: сегодня", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")]
    ]))
    await state.set_state(DiaryFlow.choose_day)
    await callback.answer()

async def diary_choose_day(message: Message, state: FSMContext, bot: Bot):
    t = (message.text or "").strip().lower()
    if t == "сегодня":
        d = datetime.now().strftime("%Y-%m-%d")
    else:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            await clean_send(bot, message.chat.id, message.from_user.id, "Формат даты: 2026-02-13 или слово 'сегодня'", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")]
            ]))
            return
        d = t
    await state.update_data(session_date=d)
    await clean_send(bot, message.chat.id, message.from_user.id, "Название тренировки? (например: День 1 / Push / Ноги)", reply_markup=None)
    await state.set_state(DiaryFlow.enter_title)

async def diary_enter_title(message: Message, state: FSMContext, bot: Bot):
    title = (message.text or "").strip()
    if not title:
        await clean_send(bot, message.chat.id, message.from_user.id, "Напиши название (например: День 1)", reply_markup=None)
        return
    data = await state.get_data()
    session_id = await create_diary_session(message.from_user.id, data["session_date"], title)
    await state.update_data(session_id=session_id)

    await clean_send(
        bot, message.chat.id, message.from_user.id,
        "Теперь вводи упражнения по одному.\n"
        "Формат: Упражнение: 60x8, 60x8, 60x7\n"
        "Когда закончишь — напиши: стоп",
        reply_markup=None
    )
    await state.set_state(DiaryFlow.enter_sets)

async def diary_enter_sets(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip()
    if txt.lower() == "стоп":
        await clean_send(bot, message.chat.id, message.from_user.id, "✅ Тренировка записана.", reply_markup=go_plans_inline_kb())
        await state.clear()
        return

    if ":" not in txt:
        await clean_send(bot, message.chat.id, message.from_user.id, "Нужен формат: Упражнение: 60x8, 60x8", reply_markup=None)
        return

    ex, sets_str = [x.strip() for x in txt.split(":", 1)]
    parts = [p.strip() for p in sets_str.split(",") if p.strip()]
    if not ex or not parts:
        await clean_send(bot, message.chat.id, message.from_user.id, "Пример: Жим: 60x8, 60x8", reply_markup=None)
        return

    data = await state.get_data()
    session_id = data["session_id"]

    for i, p in enumerate(parts, start=1):
        m = re.match(r"^(\d+(\.\d+)?)\s*[xх]\s*(\d+)$", p.lower())
        if not m:
            await clean_send(bot, message.chat.id, message.from_user.id, f"Не понял подход: '{p}'. Пример: 60x8", reply_markup=None)
            return
        w = float(m.group(1))
        r = int(m.group(3))
        await add_set(session_id, ex, i, w, r)

    await clean_send(bot, message.chat.id, message.from_user.id, f"✅ Записал: {ex} ({len(parts)} подходов). Следующее упражнение или 'стоп'.", reply_markup=None)

async def diary_history(callback: CallbackQuery):
    history = await get_diary_history(callback.from_user.id, 10)
    if not history:
        await clean_edit(callback.message, callback.from_user.id, "Истории пока нет. Нажми ➕ Записать тренировку", reply_markup=diary_inline_kb())
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

    await safe_send_chunks_edit_or_new(callback.message.bot, callback.message.chat.id, callback.message, msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")]
    ]))
    await callback.answer()

# =========================
# ЗАМЕРЫ
# =========================
async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await clean_edit(callback.message, callback.from_user.id, "Введи значение числом (например 72.5):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Меню", callback_data="open:plans")]
    ]))
    await state.set_state(MeasureFlow.enter_value)
    await callback.answer()

async def measure_value(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        val = float(txt)
    except:
        await clean_send(bot, message.chat.id, message.from_user.id, "Нужно число. Например 72.5", reply_markup=None)
        return

    data = await state.get_data()
    mtype = data.get("mtype")

    await add_measure(message.from_user.id, mtype, val)
    rows = await get_last_measures(message.from_user.id, mtype, 6)

    name = {"weight": "Вес", "waist": "Талия", "arm": "Рука", "chest": "Грудь", "thigh": "Бедро"}.get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    await clean_send(bot, message.chat.id, message.from_user.id, f"✅ Записал: {name} = {val:g}\n\nПоследние:\n{hist}", reply_markup=go_plans_inline_kb())
    await state.clear()

# =========================
# FAQ
# =========================
async def open_faq(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await clean_send(bot, message.chat.id, message.from_user.id, "❓ FAQ — выбери тему:", reply_markup=faq_inline_kb())

async def cb_faq(callback: CallbackQuery, state: FSMContext):
    topic = callback.data.split(":")[1]
    if topic == "ask":
        await clean_edit(callback.message, callback.from_user.id, "✍️ Напиши свой вопрос одним сообщением.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")]
        ]))
        await state.set_state(FAQFlow.ask)
    else:
        await clean_edit(callback.message, callback.from_user.id, faq_text(topic), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="open:faq")],
            [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")],
        ]))
    await callback.answer()

async def faq_ask(message: Message, state: FSMContext, bot: Bot):
    # простая авто-логика ответа
    q = (message.text or "").lower()
    if "оплат" in q or "чек" in q:
        ans = faq_text("pay")
    elif "ккал" in q or "бжу" in q or "питан" in q:
        ans = faq_text("nutrition")
    elif "прогресс" in q or "вес" in q or "сил" in q:
        ans = faq_text("progress")
    else:
        ans = "Я понял. Сообщение передано в поддержку. Если нужно — уточни вопрос одним предложением."
        await bot.send_message(chat_id=ADMIN_ID, text=f"📩 Вопрос FAQ от @{message.from_user.username or 'no_username'} (id={message.from_user.id}):\n\n{message.text}")

    await clean_send(bot, message.chat.id, message.from_user.id, ans, reply_markup=go_plans_inline_kb())
    await state.clear()

# =========================
# ПОДДЕРЖКА
# =========================
async def open_support(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await clean_send(
        bot, message.chat.id, message.from_user.id,
        "🆘 Поддержка\n\nНапиши проблему одним сообщением — я перешлю админу.\nЕсли есть логи/ошибка — пришли текст.",
        reply_markup=go_plans_inline_kb()
    )

async def forward_to_admin(message: Message, bot: Bot):
    if message.from_user.id == ADMIN_ID:
        return
    if not message.text or message.text.startswith("/"):
        return
    if message.text in {"💳 Оплата / Доступ", "⚙️ Профиль", "❓ FAQ", "🆘 Поддержка"}:
        return
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📩 Поддержка от @{message.from_user.username or 'no_username'} (id={message.from_user.id}):\n\n{message.text}"
    )

# =========================
# ТЕХНИКИ callbacks
# =========================
async def cb_tech(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]
    await send_tech(callback.message.bot, callback.message.chat.id, callback.from_user.id, key, base_message=callback.message)
    await callback.answer()

# =========================
# CALLBACK OPEN ROUTER
# =========================
async def cb_open_router(callback: CallbackQuery, state: FSMContext):
    what = callback.data.split(":", 1)[1]
    if what == "panel":
        await cb_open_panel(callback, state)
        return
    if what == "plans":
        await cb_open_plans(callback, state)
        return
    if what == "tech":
        await cb_open_tech(callback, state)
        return
    if what == "faq":
        # открыть FAQ меню
        await clean_edit(callback.message, callback.from_user.id, "❓ FAQ — выбери тему:", reply_markup=faq_inline_kb())
        await callback.answer()
        return
    if what == "pay":
        # мягко перекидываем: здесь проще показать “нажми кнопку в панели”
        await clean_edit(callback.message, callback.from_user.id, "Открой оплату через кнопку «💳 Оплата / Доступ» в панели управления.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Панель", callback_data="open:panel")]
        ]))
        await callback.answer()
        return

    # open:nutrition/workouts/diary/measures
    await cb_open_section(callback, state)

# =========================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
def setup_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, CommandStart())

    # Панель управления (4 кнопки)
    dp.message.register(open_payment, F.text == "💳 Оплата / Доступ")
    dp.message.register(open_profile, F.text == "⚙️ Профиль")
    dp.message.register(open_faq, F.text == "❓ FAQ")
    dp.message.register(open_support, F.text == "🆘 Поддержка")

    # profile callbacks
    dp.callback_query.register(cb_profile_edit, F.data == "prof_edit")
    dp.callback_query.register(cb_goal, F.data.startswith("goal:"))
    dp.callback_query.register(cb_sex, F.data.startswith("sex:"))
    dp.callback_query.register(cb_age, F.data.startswith("age:"))
    dp.callback_query.register(cb_height, F.data.startswith("height:"))
    dp.callback_query.register(cb_weight, F.data.startswith("weight:"))
    dp.callback_query.register(cb_place, F.data.startswith("place:"))
    dp.callback_query.register(cb_exp, F.data.startswith("exp:"))
    dp.callback_query.register(cb_freq, F.data.startswith("freq:"))

    # payment callbacks
    dp.callback_query.register(cb_tariff, F.data.startswith("tariff:"))
    dp.callback_query.register(cb_i_paid, F.data == "pay_i_paid")
    dp.callback_query.register(admin_actions, F.data.startswith("admin_approve:") | F.data.startswith("admin_reject:"))

    # navigation callbacks
    dp.callback_query.register(cb_open_router, F.data.startswith("open:"))

    # diary callbacks
    dp.callback_query.register(diary_new, F.data == "d:new")
    dp.callback_query.register(diary_history, F.data == "d:history")

    # measures callbacks
    dp.callback_query.register(cb_measure_type, F.data.startswith("mtype:"))

    # faq callbacks
    dp.callback_query.register(cb_faq, F.data.startswith("faq:"))

    # techniques callbacks
    dp.callback_query.register(cb_tech, F.data.startswith("tech:"))

    # FSM
    dp.message.register(pay_receipt, PaymentFlow.waiting_receipt)

    dp.message.register(diary_choose_day, DiaryFlow.choose_day)
    dp.message.register(diary_enter_title, DiaryFlow.enter_title)
    dp.message.register(diary_enter_sets, DiaryFlow.enter_sets)

    dp.message.register(measure_value, MeasureFlow.enter_value)
    dp.message.register(faq_ask, FAQFlow.ask)

    dp.message.register(forward_to_admin)

# =========================
# WEB SERVER (health check)
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
# MAIN (устойчивый запуск)
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

