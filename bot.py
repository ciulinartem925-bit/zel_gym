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
    "trial": {"title": "Пробный (3 дня)", "days": 3,  "price": 1},
    "t1":    {"title": "1 месяц",          "days": 30, "price": 299},
    "t3":    {"title": "3 месяца",         "days": 90, "price": 2790},
    "life":  {"title": "Навсегда",         "days": None, "price": 6990},
}

# Тарифы, дающие ПОЛНЫЙ доступ (включая питание)
FULL_ACCESS_TARIFFS = {"t1", "t3", "life"}

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
    waiting_receipt = State()


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


class ProfileFieldEdit(StatesGroup):
    """Редактирование одного конкретного поля профиля."""
    age = State()
    height = State()
    weight = State()
    limits = State()


# =========================
# ✅ ТЕХНИКИ ВЫПОЛНЕНИЯ
# =========================
TECH = {
    "squat": {
        "title": "Присед",
        "img": "media/tech/squat.jpg",
        "text": (
            "📚 Присед (безопасно и эффективно)\n\n"
            "✅ Настройка\n"
            "• Стопы: ширина плеч, носки слегка наружу.\n"
            "• Корпус: вдох «в живот», пресс в тонусе.\n\n"
            "✅ Как делать\n"
            "1) Уводи таз назад и вниз одновременно.\n"
            "2) Колени идут по линии носков (не заваливаются внутрь).\n"
            "3) Спина ровная, вес на пятке/середине стопы.\n"
            "4) Внизу — контроль, без «провала».\n"
            "5) Вставай, толкая пол ногами (колени не «замком»).\n\n"
            "⚠️ Частые ошибки\n"
            "• Колени внутрь → снизь вес, думай «раздвигаю пол».\n"
            "• Пятки отрываются → чуть шире стойка/меньше глубина.\n"
            "• Поясница округляется → меньше глубина, сильнее корпус.\n\n"
            "💡 Подсказка\n"
            "Если дискомфорт в коленях/спине — делай вариант: гоблет-присед или жим ногами."
        )
    },
    "bench": {
        "title": "Жим лёжа",
        "img": "media/tech/bench.jpg",
        "text": (
            "📚 Жим лёжа (грудь + трицепс, без боли в плечах)\n\n"
            "✅ Настройка\n"
            "• Лопатки: свёл и прижал к лавке.\n"
            "• Стопы: устойчиво на полу.\n"
            "• Хват: так, чтобы внизу предплечья были почти вертикальны.\n\n"
            "✅ Как делать\n"
            "1) Сними штангу, удерживай лопатки сведёнными.\n"
            "2) Опускай к нижней части груди под контролем.\n"
            "3) Локти держи ~45° к корпусу (не 90° в стороны).\n"
            "4) Жми вверх и чуть назад (к стойкам), без рывка.\n\n"
            "⚠️ Частые ошибки\n"
            "• Плечи «вперёд» → снова сведи лопатки.\n"
            "• Отбив от груди → замедли негатив.\n"
            "• Слишком широкий хват → плечи чаще болят.\n\n"
            "💡 Подсказка\n"
            "Если плечо капризничает — попробуй жим гантелей или тренажёр."
        )
    },
    "row": {
        "title": "Отжимания",
        "img": "media/tech/pushup.jpg",
        "text": (
            "📚 Отжимания (корпус + грудь, правильно)\n\n"
            "✅ Настройка\n"
            "• Ладони под плечами (можно чуть шире).\n"
            "• Корпус «доской»: пресс + ягодицы включены.\n\n"
            "✅ Как делать\n"
            "1) Опускайся, сохраняя прямую линию тела.\n"
            "2) Локти идут назад под углом (не разводи в стороны).\n"
            "3) Внизу — короткая пауза, затем выжимай пол.\n\n"
            "⚠️ Частые ошибки\n"
            "• Провал в пояснице → напряги пресс/ягодицы.\n"
            "• Голова вниз → смотри чуть вперед.\n\n"
            "💡 Упрощение/усложнение\n"
            "• Легче: от лавки/стола.\n"
            "• Тяжелее: ноги на опору или пауза 1–2 сек внизу."
        )
    },
    "latpulldown": {
        "title": "Верхний блок",
        "img": "media/tech/latpulldown.jpg",
        "text": (
            "📚 Тяга верхнего блока (спина, не бицепс)\n\n"
            "✅ Настройка\n"
            "• Сядь устойчиво, грудь «вперёд», плечи опусти вниз.\n\n"
            "✅ Как делать\n"
            "1) Начни движение лопатками: вниз/к карманам.\n"
            "2) Тяни локти вниз и назад.\n"
            "3) Гриф к верхней части груди (без раскачки).\n"
            "4) Вверх — медленно, до растяжения широчайших.\n\n"
            "⚠️ Частые ошибки\n"
            "• Тянешь руками → думай «локти вниз».\n"
            "• Раскачка корпусом → снизь вес.\n\n"
            "💡 Подсказка\n"
            "Если не чувствуешь спину — сделай паузу 1 сек внизу."
        )
    },
    "pullup": {
        "title": "Подтягивания",
        "img": "media/tech/pullup.jpg",
        "text": (
            "📚 Подтягивания (чисто, без читинга)\n\n"
            "✅ Как делать\n"
            "1) Старт: плечи вниз (от ушей), лопатки включились.\n"
            "2) Тяни локти к рёбрам, грудь к перекладине.\n"
            "3) Вверх без рывка, вниз — медленно 2–3 сек.\n\n"
            "⚠️ Частые ошибки\n"
            "• Дёргаешься корпусом → делай медленнее/резинка.\n"
            "• Плечи вверх → сначала лопатки вниз.\n\n"
            "💡 Если пока тяжело\n"
            "Резинка/гравитрон или негативы: запрыгнул — медленно опустился."
        )
    },
    "rdl": {
        "title": "Румынская тяга",
        "img": "media/tech/rdl.jpg",
        "text": (
            "📚 Румынская тяга (задняя поверхность бедра)\n\n"
            "✅ Как делать\n"
            "1) Колени слегка согнуты и почти не меняются.\n"
            "2) Движение — таз назад, спина ровная.\n"
            "3) Снаряд идёт близко к ногам.\n"
            "4) Опускайся до сильного натяжения в бёдрах.\n"
            "5) Вверх — таз вперёд, без перегиба поясницы.\n\n"
            "⚠️ Частые ошибки\n"
            "• Круглая спина → укороти амплитуду.\n"
            "• Превращается в присед → меньше сгибай колени.\n\n"
            "💡 Подсказка\n"
            "Чувствуешь бёдра/ягодицы — всё правильно. Если поясницу — снизь вес."
        )
    },
    "ohp": {
        "title": "Жим вверх",
        "img": "media/tech/ohp.jpg",
        "text": (
            "📚 Жим вверх (плечи, без ломания поясницы)\n\n"
            "✅ Настройка\n"
            "• Пресс напряг, рёбра «вниз».\n"
            "• Ягодицы включены — корпус стабилен.\n\n"
            "✅ Как делать\n"
            "1) Жми вверх и чуть назад (штанга близко к лицу).\n"
            "2) Вверху голова «в окно» под снаряд.\n"
            "3) Вниз — медленно, в контроль.\n\n"
            "⚠️ Частые ошибки\n"
            "• Прогиб в пояснице → снизь вес, сильнее корпус.\n"
            "• Локти уехали назад → держи под грифом.\n\n"
            "💡 Если плечо болит\n"
            "Уменьши амплитуду, попробуй гантели или жим в тренажёре."
        )
    },
    "lateralraise": {
        "title": "Разведения в стороны",
        "img": "media/tech/lateralraise.jpg",
        "text": (
            "📚 Разведения в стороны (средняя дельта)\n\n"
            "✅ Как делать\n"
            "1) Плечи вниз, корпус стабилен.\n"
            "2) Локоть слегка согнут и фиксирован.\n"
            "3) Поднимай до уровня плеч.\n"
            "4) Вниз — медленно 2–3 сек.\n\n"
            "⚠️ Частые ошибки\n"
            "• Махи корпусом → снизь вес.\n"
            "• Поднимаешь кистью → думай «локоть ведёт».\n\n"
            "💡 Подсказка\n"
            "Лучше легче, но чисто — тут техника решает."
        )
    },
    "biceps": {
        "title": "Бицепс сгибания",
        "img": "media/tech/biceps.jpg",
        "text": (
            "📚 Сгибания на бицепс (без раскачки)\n\n"
            "✅ Как делать\n"
            "1) Локти прижаты к корпусу и не гуляют.\n"
            "2) Поднимай до пика, без рывков.\n"
            "3) Вниз — медленно, не бросай.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка спиной → слишком тяжело.\n"
            "• Локти уезжают вперёд → нагрузка уходит.\n\n"
            "💡 Подсказка\n"
            "Хочешь рост — контролируй негатив и не гонись за весом."
        )
    },
    "triceps": {
        "title": "Трицепс на блоке",
        "img": "media/tech/triceps.jpg",
        "text": (
            "📚 Разгибания на блоке (трицепс)\n\n"
            "✅ Как делать\n"
            "1) Локти прижаты к корпусу.\n"
            "2) Разгибай до конца без боли.\n"
            "3) Вверх — медленно, под контролем.\n\n"
            "⚠️ Частые ошибки\n"
            "• Локти «гуляют» → снизь вес.\n"
            "• Бросаешь вверх → теряешь нагрузку.\n\n"
            "💡 Если локоть ноет\n"
            "Уменьши вес и делай мягче, можно одной рукой."
        )
    },
    "legpress": {
        "title": "Жим ногами",
        "img": "media/tech/legpress.jpg",
        "text": (
            "📚 Жим ногами (ноги, безопасно)\n\n"
            "✅ Настройка\n"
            "• Поясница прижата, таз не отрывай.\n"
            "• Стопы так, чтобы колени шли по линии носков.\n\n"
            "✅ Как делать\n"
            "1) Опускай платформу до комфортной глубины.\n"
            "2) Внизу — контроль, без «складывания» таза.\n"
            "3) Жми вверх, но не выпрямляй колени в «замок».\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз отрывается → слишком глубоко.\n"
            "• Колени внутрь → следи за траекторией.\n\n"
            "💡 Подсказка\n"
            "Лучше идеальная техника, чем рекордный вес."
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
        input_field_placeholder="Кнопки снизу 👇"
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
        [InlineKeyboardButton(text=f"🟢 Пробный — {TARIFFS['trial']['price']}₽ (3 дня)", callback_data="tariff:trial")],
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
    return f"🧩 Профиль {step}/{TOTAL_PROFILE_STEPS}\n{_bar(step)}\n\n"


def profile_done_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Собрать программу", callback_data="p:build_program")]
    ])


def profile_ready_kb():
    """После заполнения/просмотра готового профиля."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Собрать программу", callback_data="p:build_program")],
        [InlineKeyboardButton(text="✏️ Изменить профиль", callback_data="p:edit")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def profile_edit_field_kb(u: dict) -> InlineKeyboardMarkup:
    """Меню выбора конкретного поля профиля для редактирования."""
    def val(k, fallback="—"):
        v = u.get(k)
        return str(v) if v else fallback

    rows = [
        [InlineKeyboardButton(text=f"🎯 Цель: {val('goal')}", callback_data="pf:goal")],
        [InlineKeyboardButton(text=f"👤 Пол: {val('sex')}", callback_data="pf:sex")],
        [InlineKeyboardButton(text=f"🎂 Возраст: {val('age')}", callback_data="pf:age")],
        [InlineKeyboardButton(text=f"📏 Рост: {val('height')} см", callback_data="pf:height")],
        [InlineKeyboardButton(text=f"⚖️ Вес: {val('weight')} кг", callback_data="pf:weight")],
        [InlineKeyboardButton(text=f"🏠 Место: {val('place')}", callback_data="pf:place")],
        [InlineKeyboardButton(text=f"📈 Опыт: {val('exp')}", callback_data="pf:exp")],
        [InlineKeyboardButton(text=f"📅 Тренировок/нед: {val('freq')}", callback_data="pf:freq")],
        [InlineKeyboardButton(text=f"🍽 Приёмов еды: {val('meals')}", callback_data="pf:meals")],
        [InlineKeyboardButton(text=f"⛔️ Ограничения: {val('limits', 'нет')}", callback_data="pf:limits")],
        [InlineKeyboardButton(text="🏠 Назад", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_program_tariff_kb():
    """Тарифная кнопка после заполнения профиля."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🟢 Пробный — {TARIFFS['trial']['price']}₽ (3 дня)",
            callback_data="tariff:trial"
        )],
        [InlineKeyboardButton(
            text=f"🟩 Месячный — {TARIFFS['t1']['price']}₽",
            callback_data="tariff:t1"
        )],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def profile_view_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Собрать программу", callback_data="p:build_program")],
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
        [InlineKeyboardButton(text="🏠 Дома", callback_data="p:place:bodyweight"),
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
        return "Пн/Ср/Пт (или Вт/Чт/Сб)"
    if freq == 4:
        return "Верх/Низ ×2 (например Пн/Вт/Чт/Сб)"
    return "PPL + Верх/Низ (например Пн–Пт)"


def gen_order_code(user_id: int) -> str:
    rnd = random.randint(100, 999)
    return f"TG{str(user_id)[-3:]}{rnd}"


def locked_text() -> str:
    return "🔒 Доступ после оплаты.\nНажми снизу: 💳 Оплата/доступ"


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
# АНТИ-ЗАСОРЕНИЕ ЧАТА
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
    """Удаляет предыдущее сообщение бота и отправляет новое — чат не засоряется."""
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
    """Редактирует текущее сообщение (если не удаётся — отправляет новое)."""
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
            created_at TEXT
        )
        """)

        for col, typ in [
            ("limits", "TEXT"),
            ("state", "TEXT"),
            ("meals", "INTEGER"),
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
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            day_num INTEGER,
            completed_date TEXT,
            created_at TEXT
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_day_progress (
            user_id INTEGER,
            day_num INTEGER,
            done_exercises TEXT,
            PRIMARY KEY (user_id, day_num)
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
            SELECT user_id, username, goal, sex, age, height, weight, place, exp, freq, meals, limits, state
            FROM users WHERE user_id=?
        """, (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return {}
    return {
        "user_id": row[0], "username": row[1], "goal": row[2], "sex": row[3],
        "age": row[4], "height": row[5], "weight": row[6], "place": row[7],
        "exp": row[8], "freq": row[9], "meals": row[10], "limits": row[11], "state": row[12]
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


async def is_full_access_active(user_id: int) -> bool:
    """Полный доступ: тренировки + питание (только платные тарифы, не пробный)."""
    a = await get_access(user_id)
    if a["paid"] != 1:
        return False
    if not await is_access_active(user_id):
        return False
    return a.get("tariff") in FULL_ACCESS_TARIFFS


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
# ТРЕНИРОВКИ: прогресс дня
# =========================

EXERCISE_TECH_MAP = [
    ("присед", "squat"),
    ("жим лёж", "bench"),
    ("жим гантел", "bench"),
    ("жим в тренаж", "bench"),
    ("сведени", "bench"),
    ("отжима", "row"),
    ("верхний блок", "latpulldown"),
    ("тяга верхн", "latpulldown"),
    ("подтягива", "pullup"),
    ("румынская тяга", "rdl"),
    ("жим вверх", "ohp"),
    ("жим в тренажёре вверх", "ohp"),
    ("пайк-отжима", "ohp"),
    ("разведени", "lateralraise"),
    ("face pull", "lateralraise"),
    ("задняя дельта", "lateralraise"),
    ("тяга к лицу", "lateralraise"),
    ("сгибани", "biceps"),
    ("молотки", "biceps"),
    ("разгибани", "triceps"),
    ("трицепс", "triceps"),
    ("отжима узк", "triceps"),
    ("жим ног", "legpress"),
    ("гоблет", "squat"),
    ("хакк", "squat"),
    ("болгар", "squat"),
    ("выпад", "squat"),
    ("ягодичный мост", "rdl"),
    ("гиперэкстензи", "rdl"),
    ("good-morning", "rdl"),
    ("сгибания ног", "legpress"),
    ("тяга резинки", "latpulldown"),
    ("тяга гантел", "latpulldown"),
    ("тяга в тренаж", "latpulldown"),
    ("тяга горизонт", "latpulldown"),
    ("тяга резинки сверху", "latpulldown"),
    ("жим резинки вверх", "ohp"),
    ("подъём", "legpress"),
    ("планка", None),
    ("скручива", None),
    ("подъём ног", None),
    ("икры", None),
]


def get_tech_key_for_exercise(name: str) -> Optional[str]:
    n = name.lower()
    for keyword, tech_key in EXERCISE_TECH_MAP:
        if keyword in n:
            return tech_key
    return None


# =========================
# Примерные калории на упражнение (ккал за весь подход × сеты, для 70кг человека)
# =========================
EXERCISE_KCAL_MAP = [
    ("присед",          55),
    ("жим ног",         40),
    ("выпад",           45),
    ("болгар",          45),
    ("гоблет",          40),
    ("хакк",            38),
    ("румынская",       42),
    ("ягодичный мост",  30),
    ("гиперэкстензи",   25),
    ("сгибания ног",    28),
    ("подъём на носки", 20),
    ("икры",            18),
    ("жим лёж",         38),
    ("жим гантел",      36),
    ("жим в тренаж",    34),
    ("сведени",         28),
    ("отжима",          32),
    ("подтягива",       40),
    ("верхний блок",    32),
    ("тяга верхн",      32),
    ("тяга горизонт",   30),
    ("тяга гантел",     28),
    ("тяга в тренаж",   28),
    ("тяга резинки",    22),
    ("жим вверх",       35),
    ("разведени",       20),
    ("face pull",       18),
    ("задняя дельта",   18),
    ("тяга к лицу",     18),
    ("сгибани",         22),
    ("молотки",         20),
    ("разгибани",       20),
    ("трицепс",         20),
    ("планка",          15),
    ("скручива",        18),
    ("подъём ног",      22),
    ("пайк",            28),
    ("good-morning",    30),
]


def get_exercise_kcal(name: str) -> int:
    """Примерные ккал на упражнение (все подходы суммарно)."""
    n = name.lower()
    for keyword, kcal in EXERCISE_KCAL_MAP:
        if keyword in n:
            return kcal
    return 25  # default


# Название дня по типу
DAY_NAMES = {
    "FB-A": "Фулбади А",
    "FB-B": "Фулбади Б",
    "FB-C": "Фулбади В",
    "UPPER": "Верх тела",
    "LOWER": "Низ тела • Ноги",
    "PUSH": "Толчок • Грудь-Плечи",
    "PULL": "Тяга • Спина-Бицепс",
    "LEGS": "Ноги",
}


def get_day_kind_from_text(day_text: str) -> str:
    """Извлекаем тип дня из текста плана."""
    t = day_text.lower()
    if "фулбади" in t or "fullbody" in t:
        return "FB"
    if "верх" in t and "тела" in t:
        return "UPPER"
    if "низ" in t or "ноги" in t:
        return "LOWER"
    if "толчок" in t or "push" in t:
        return "PUSH"
    if "тяга" in t or "pull" in t:
        return "PULL"
    return "FB"


def parse_exercises_from_day_text(day_text: str) -> List[str]:
    """Возвращает список названий упражнений (без подходов/повторов)."""
    exercises = []
    for line in day_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("•"):
            content = stripped.lstrip("•").strip()
            if " — " in content:
                name = content.split(" — ")[0].strip()
            else:
                name = content
            if name:
                exercises.append(name)
    return exercises


def parse_exercises_full(day_text: str) -> List[Tuple[str, str]]:
    """Возвращает список (название, 'подходы×повторы') для отображения."""
    exercises = []
    for line in day_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("•"):
            content = stripped.lstrip("•").strip()
            if " — " in content:
                parts = content.split(" — ", 1)
                name = parts[0].strip()
                sets_reps = parts[1].strip()
            else:
                name = content
                sets_reps = ""
            if name:
                exercises.append((name, sets_reps))
    return exercises


async def get_day_done_exercises(user_id: int, day_num: int) -> List[int]:
    async with db() as conn:
        async with conn.execute(
            "SELECT done_exercises FROM workout_day_progress WHERE user_id=? AND day_num=?",
            (user_id, day_num)
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0])
    except Exception:
        return []


async def set_day_done_exercises(user_id: int, day_num: int, done: List[int]):
    async with db() as conn:
        await conn.execute("""
            INSERT INTO workout_day_progress (user_id, day_num, done_exercises)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, day_num) DO UPDATE SET done_exercises=excluded.done_exercises
        """, (user_id, day_num, json.dumps(done)))
        await conn.commit()


async def clear_day_progress(user_id: int, day_num: int):
    async with db() as conn:
        await conn.execute(
            "DELETE FROM workout_day_progress WHERE user_id=? AND day_num=?",
            (user_id, day_num)
        )
        await conn.commit()


async def mark_day_completed(user_id: int, day_num: int):
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        await conn.execute("""
            INSERT INTO workout_completions (user_id, day_num, completed_date, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, day_num, today, now))
        await conn.commit()


async def is_day_completed_today(user_id: int, day_num: int) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    async with db() as conn:
        async with conn.execute("""
            SELECT COUNT(*) FROM workout_completions
            WHERE user_id=? AND day_num=? AND completed_date=?
        """, (user_id, day_num, today)) as cur:
            row = await cur.fetchone()
    return bool(row and row[0] > 0)


# =========================
# ✅ ПРОГРЕСС-БАР ДЛЯ ТРЕНИРОВОЧНОГО ДНЯ
# =========================
def workout_progress_bar(done: int, total: int, width: int = 10) -> str:
    """Визуальный прогресс-бар тренировочного дня."""
    if total == 0:
        return "□□□□□□□□□□ 0%"
    pct = int(round(done / total * 100))
    filled = int(round(done / total * width))
    filled = max(0, min(filled, width))
    bar = "■" * filled + "□" * (width - filled)
    return f"{bar} {pct}%"


def calc_day_total_kcal(exercises: List[str]) -> int:
    """Считаем суммарные ккал за тренировку."""
    return sum(get_exercise_kcal(ex) for ex in exercises)


def build_day_display_text(day_num: int, day_text: str, exercises: List[str],
                            done: List[int], all_done: bool = False) -> str:
    """Строит красивый текст дня тренировки в стиле скриншота."""
    total = len(exercises)
    done_count = len(done)
    total_kcal = calc_day_total_kcal(exercises)
    done_kcal = sum(get_exercise_kcal(exercises[i]) for i in done if i < len(exercises))
    afterburn_kcal = int(total_kcal * 0.09)  # ~9% afterburn эффект

    # Определяем название дня
    day_kind = get_day_kind_from_text(day_text)
    day_name_map = {
        "UPPER": "Верх тела",
        "LOWER": "Низ тела • Ноги",
        "PUSH":  "Толчок • Грудь, Плечи",
        "PULL":  "Тяга • Спина, Бицепс",
        "LEGS":  "Ноги",
        "FB":    "Фулбади",
    }
    day_name = day_name_map.get(day_kind, "Тренировка")

    lines = []
    lines.append(f"💪 День {day_num}: {day_name}")
    lines.append("")
    lines.append("⚠️ Перед тренировкой обязательно разомнись 5–10 минут")
    lines.append("⏱ Отдых между подходами: ~1.5–2 минуты")
    lines.append("")

    # Список упражнений с галочками и ккал
    ex_full = parse_exercises_full(day_text)
    for idx, (name, sets_reps) in enumerate(ex_full):
        is_done = idx in done
        ex_kcal = get_exercise_kcal(name)
        mark = "✅" if is_done else "🔸"
        kcal_str = f"  |  🔥 {ex_kcal} ккал" if is_done else ""
        if sets_reps:
            lines.append(f"{mark} {name} {sets_reps}{kcal_str}")
        else:
            lines.append(f"{mark} {name}{kcal_str}")

    lines.append("")
    lines.append("🏁 После тренировки выполни заминку и растяжку 5–10 минут")
    lines.append(f"🔥 Итого: ~{total_kcal} ккал (в т.ч. ~{afterburn_kcal} ккал дожигание)")
    lines.append("")

    # Прогресс-бар
    bar = workout_progress_bar(done_count, total)
    if all_done:
        lines.append(f"{bar}")
        lines.append(f"✅ {done_count}/{total} упражнений выполнено | 🔥 {done_kcal} ккал сожжено")
        lines.append("")
        lines.append("🎉 ОТЛИЧНО! День засчитан!")
    else:
        lines.append(f"{bar}")
        lines.append(f"✅ {done_count}/{total} упражнений выполнено | 🔥 {done_kcal} ккал сожжено")

    return "\n".join(lines)


# =========================
# ТРЕНИРОВКИ: генерация
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

            lines.append("База:")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            lines.append("")
            lines.append("Изоляция:")
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

            lines.append("База:")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(vpull, base_sets, reps_base)}")
            if not tags["shoulder"]:
                lines.append(f"• {fmt(vpush, base_sets, reps_base)}")
            lines.append("")
            lines.append("Изоляция:")
            lines.append(f"• {fmt(should, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(bi, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(tri, iso_sets, reps_iso)}")
            return lines

        if kind == "LOWER":
            squat = pick(SQUAT, avoid_keys)
            hinge = pick(HINGE, avoid_keys)
            calves = pick(CALVES, avoid_keys)
            core = pick(CORE, avoid_keys)

            lines.append("База:")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            lines.append("")
            lines.append("Изоляция:")
            lines.append(f"• {fmt(calves, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(core, '2', '30–60 сек')}")
            return lines

        if kind == "PUSH":
            hpush = pick(HPUSH, avoid_keys)
            vpush = pick(VPUSH, avoid_keys)
            should = pick(SHOULD, avoid_keys)
            tri = pick(TRI, avoid_keys)

            lines.append("База:")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            if not tags["shoulder"]:
                lines.append(f"• {fmt(vpush, base_sets, reps_base)}")
            lines.append("")
            lines.append("Изоляция:")
            lines.append(f"• {fmt(should, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(tri, iso_sets, reps_iso)}")
            return lines

        if kind == "PULL":
            vpull = pick(VPULL, avoid_keys)
            hpull = pick(HPULL, avoid_keys)
            bi = pick(BI, avoid_keys)
            rear = pick(SHOULD, avoid_keys)

            lines.append("База:")
            lines.append(f"• {fmt(vpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            lines.append("")
            lines.append("Изоляция:")
            lines.append(f"• {fmt(rear, iso_sets, reps_iso)}")
            lines.append(f"• {fmt(bi, iso_sets, reps_iso)}")
            return lines

        if kind == "LEGS":
            squat = pick(SQUAT, avoid_keys)
            hinge = pick(HINGE, avoid_keys)
            calves = pick(CALVES, avoid_keys)

            lines.append("База:")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            lines.append("")
            lines.append("Изоляция:")
            lines.append(f"• {fmt(calves, iso_sets, reps_iso)}")
            return lines

        return ["—"]

    limits_line = (limits or "").strip() or "нет"

    intro = (
        f"🏋️ Тренировки ({where})\n"
        f"Система: {system}\n"
        f"Частота: {f}×/нед • {weekday_schedule(f)}\n"
        f"Цель: {goal} • Уровень: {'новичок' if is_novice else 'средний+'}\n"
        f"Огр.: {limits_line}\n\n"
        f"Правило: техника > вес • RIR {rir}\n"
        "Выбери день кнопкой 👇"
    )

    days: Dict[str, str] = {}
    for d in range(1, f + 1):
        kind = template[d - 1]
        header = f"День {d}\n{system}\n\n"
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
# ✅ ПИТАНИЕ (исправленные граммы и КБЖУ)
# =========================

# Точная БД продуктов на 100г (проверенные данные)
FOOD_DB = {
    "oats":      {"name": "Овсянка (сухая)",              "kcal": 352, "p": 12.0, "f": 6.2,   "c": 60.0},
    "rice":      {"name": "Рис (сухой)",                  "kcal": 344, "p": 6.7,  "f": 0.7,   "c": 76.0},
    "buckwheat": {"name": "Гречка (сухая)",               "kcal": 313, "p": 12.6, "f": 3.3,   "c": 57.0},
    "pasta":     {"name": "Макароны (сухие)",             "kcal": 350, "p": 10.4, "f": 1.1,   "c": 73.0},
    "potato":    {"name": "Картофель",                    "kcal": 80,  "p": 2.0,  "f": 0.1,   "c": 17.0},
    "bread_rye": {"name": "Хлеб ржаной",                  "kcal": 210, "p": 6.6,  "f": 1.2,   "c": 41.0},
    "veg":       {"name": "Овощи (огурец/помидор/капуста)", "kcal": 25,  "p": 1.2,  "f": 0.2,   "c": 4.5},
    "chicken":   {"name": "Куриная грудка (варёная)",     "kcal": 165, "p": 31.0, "f": 3.6,   "c": 0.0},
    "turkey":    {"name": "Индейка (филе, варёное)",      "kcal": 158, "p": 29.0, "f": 4.0,   "c": 0.0},
    "fish":      {"name": "Рыба белая (треска/минтай)",   "kcal": 82,  "p": 18.0, "f": 0.7,   "c": 0.0},
    "salmon":    {"name": "Лосось",                       "kcal": 208, "p": 20.0, "f": 13.0,  "c": 0.0},
    "beef":      {"name": "Говядина (варёная)",           "kcal": 218, "p": 25.0, "f": 12.5,  "c": 0.0},
    "eggs":      {"name": "Яйца",                         "kcal": 157, "p": 12.7, "f": 11.5,  "c": 0.7},
    "egg_white": {"name": "Белок яичный",                 "kcal": 44,  "p": 11.0, "f": 0.0,   "c": 0.0},
    "curd_0":    {"name": "Творог 0%",                    "kcal": 71,  "p": 18.0, "f": 0.6,   "c": 1.8},
    "curd_5":    {"name": "Творог 5%",                    "kcal": 121, "p": 17.0, "f": 5.0,   "c": 1.8},
    "greek_yog": {"name": "Йогурт греческий 2%",          "kcal": 66,  "p": 5.0,  "f": 2.0,   "c": 6.5},
    "milk":      {"name": "Молоко 2.5%",                  "kcal": 54,  "p": 2.8,  "f": 2.5,   "c": 4.7},
    "oil_olive": {"name": "Масло оливковое",              "kcal": 884, "p": 0.0,  "f": 100.0, "c": 0.0},
    "oil_linseed":{"name": "Масло льняное",               "kcal": 884, "p": 0.0,  "f": 100.0, "c": 0.0},
    "nuts_alm":  {"name": "Миндаль",                      "kcal": 576, "p": 21.2, "f": 49.4,  "c": 6.9},
    "cheese_20": {"name": "Сыр лёгкий 20–30%",           "kcal": 260, "p": 26.0, "f": 16.0,  "c": 1.0},
    "banana":    {"name": "Банан",                        "kcal": 89,  "p": 1.1,  "f": 0.3,   "c": 22.0},
    "apple":     {"name": "Яблоко",                       "kcal": 52,  "p": 0.3,  "f": 0.2,   "c": 12.0},
    "berries":   {"name": "Ягоды (клубника/черника)",     "kcal": 45,  "p": 0.8,  "f": 0.4,   "c": 9.5},
    "avocado":   {"name": "Авокадо",                      "kcal": 160, "p": 2.0,  "f": 15.0,  "c": 2.0},
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
    """Итеративно подгоняем КБЖУ под цель, корректируя ключевые продукты."""
    for _ in range(80):
        t = _totals_of_day(day_meals)
        dk = target["kcal"] - t["kcal"]
        dp = target["p"] - t["p"]
        df = target["f"] - t["f"]
        dc = target["c"] - t["c"]

        # Считаем «хорошо», если всё в пределах допуска
        if abs(dk) <= 40 and abs(dp) <= 5 and abs(df) <= 4 and abs(dc) <= 8:
            return t

        # Белок — через куриную грудку (чистый белок, мало жира)
        if dp > 5:
            _add_grams(day_meals, "chicken", 25.0)
            continue
        if dp < -8:
            _add_grams(day_meals, "chicken", -25.0)
            continue

        # Жиры — через масло (точная регулировка)
        if df > 4:
            _add_grams(day_meals, "oil_olive", 4.0)
            continue
        if df < -5:
            _add_grams(day_meals, "oil_olive", -4.0)
            continue

        # Углеводы и калории — через рис
        if dc > 10 or dk > 80:
            _add_grams(day_meals, "rice", 10.0)
            continue
        if dc < -10 or dk < -80:
            _add_grams(day_meals, "rice", -10.0)
            continue

    return _totals_of_day(day_meals)


def _build_day_variant(variant: int, meals: int) -> List[List[Tuple[str, float]]]:
    """
    Три варианта дня питания с реалистичными порциями.
    Все крупы — в сухом виде. Белок указан в виде готового продукта.
    """
    meals = max(3, min(int(meals or 3), 5))

    if variant == 1:
        # Вариант 1: классика зал/масса
        day = [
            # Завтрак: каша + яйца
            [("oats", 80.0), ("eggs", 120.0), ("banana", 100.0)],
            # Обед: рис + курица + овощи
            [("rice", 90.0), ("chicken", 180.0), ("veg", 200.0), ("oil_olive", 10.0)],
            # Ужин: гречка + рыба + овощи
            [("buckwheat", 80.0), ("fish", 200.0), ("veg", 200.0), ("oil_olive", 8.0)],
        ]
        if meals >= 4:
            # Перекус: творог
            day.append([("curd_0", 200.0), ("berries", 100.0)])
        if meals >= 5:
            # Второй перекус: орехи + яблоко
            day.append([("apple", 150.0), ("nuts_alm", 25.0)])
        return day

    if variant == 2:
        # Вариант 2: с говядиной и картофелем
        day = [
            # Завтрак: яйца + хлеб + сыр
            [("eggs", 150.0), ("bread_rye", 60.0), ("cheese_20", 30.0)],
            # Обед: картофель + говядина + овощи
            [("potato", 300.0), ("beef", 150.0), ("veg", 200.0), ("oil_olive", 8.0)],
            # Ужин: макароны + индейка
            [("pasta", 80.0), ("turkey", 180.0), ("veg", 150.0)],
        ]
        if meals >= 4:
            # Перекус: греческий йогурт + банан
            day.append([("greek_yog", 200.0), ("banana", 100.0)])
        if meals >= 5:
            # Второй перекус
            day.append([("curd_5", 150.0), ("nuts_alm", 20.0)])
        return day

    # Вариант 3: с лососем и авокадо
    day = [
        # Завтрак: овсянка + йогурт
        [("oats", 70.0), ("greek_yog", 150.0), ("berries", 100.0)],
        # Обед: рис + лосось
        [("rice", 90.0), ("salmon", 150.0), ("veg", 200.0)],
        # Ужин: гречка + индейка + авокадо
        [("buckwheat", 80.0), ("turkey", 160.0), ("avocado", 80.0), ("veg", 150.0)],
    ]
    if meals >= 4:
        day.append([("curd_0", 200.0), ("apple", 150.0)])
    if meals >= 5:
        day.append([("eggs", 60.0), ("nuts_alm", 20.0)])
    return day


def build_meal_day_text(day_i: int, calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> str:
    target = {"kcal": float(calories), "p": float(protein_g), "f": float(fat_g), "c": float(carbs_g)}
    day_meals = _build_day_variant(day_i, meals)
    tot = _adjust_to_target(day_meals, target)

    final_k = int(round(tot["kcal"]))
    final_p = int(round(tot["p"]))
    final_f = int(round(tot["f"]))
    final_c = int(round(tot["c"]))

    # Отклонение от цели
    dk = final_k - calories
    dk_str = f"{'+' if dk >= 0 else ''}{dk}"

    meal_names = ["🌅 Завтрак", "🌞 Обед", "🌆 Ужин", "🥗 Перекус 1", "🍎 Перекус 2"]

    lines = [f"📅 Пример {day_i}  (цель: {calories} ккал)", ""]
    for mi, m in enumerate(day_meals, start=1):
        mt = _sum_nutr(m)
        meal_name = meal_names[mi - 1] if mi <= len(meal_names) else f"Приём {mi}"
        lines.append(f"{meal_name}  ({_fmt_tot(mt)})")
        for k, g in m:
            if k == "eggs":
                est = max(1, int(round(g / 60.0)))
                lines.append(f"• {FOOD_DB[k]['name']} — {est} шт (~{int(g)}г)")
            elif k in ("oil_olive", "oil_linseed"):
                lines.append(f"• {FOOD_DB[k]['name']} — {int(round(g))} г (1 ст.л. ≈ 10г)")
            elif k in ("nuts_alm",):
                lines.append(f"• {FOOD_DB[k]['name']} — {int(round(g))} г (~горсть)")
            else:
                lines.append(f"• {FOOD_DB[k]['name']} — {int(round(g))} г")
        lines.append("")

    lines.append(f"✅ Итог дня: {final_k} ккал ({dk_str} от цели)")
    lines.append(f"   Б {final_p}г / Ж {final_f}г / У {final_c}г")
    lines.append("")
    lines.append("⚠️ Крупы и макароны — граммы в сухом (сыром) виде.")
    lines.append("   При варке рис увеличивается ~в 3 раза, гречка ~в 2.5 раза.")
    lines.append("   Курица, рыба, мясо — готовый (варёный/жареный) вес.")
    return "\n".join(lines)


def nutrition_examples_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥣 Вариант 1 (классика)", callback_data="nutr:ex:1")],
        [InlineKeyboardButton(text="🍖 Вариант 2 (говядина)", callback_data="nutr:ex:2")],
        [InlineKeyboardButton(text="🐟 Вариант 3 (лосось)", callback_data="nutr:ex:3")],
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

    # Рассчитываем % от калорий для проверки
    p_kcal = p * 4
    f_kcal = f * 9
    c_kcal = c * 4
    total_check = p_kcal + f_kcal + c_kcal

    summary = (
        "🍽 Питание (персональный расчёт)\n\n"
        f"⚡️ Калории: ~{calories} ккал/день\n"
        f"💪 Белок: {p} г  ({int(round(p_kcal/total_check*100))}% калорий)\n"
        f"🥑 Жиры: {f} г  ({int(round(f_kcal/total_check*100))}%)\n"
        f"🍚 Углеводы: {c} г  ({int(round(c_kcal/total_check*100))}%)\n"
        f"🍽 Приёмов пищи: {meals}\n\n"
        "Выбери готовый пример рациона 👇\n"
        "(Все 3 варианта подходят под твои цифры)"
    )
    return summary, calories, p, f, c, meals


# =========================
# МЕНЮ / START
# =========================
async def show_main_menu(bot: Bot, chat_id: int, user_id: int):
    text = (
        "🏠 Главное меню\n\n"
        "Выбери раздел 👇\n"
        "Профиль / оплата / поддержка — на кнопках снизу."
    )
    await clean_send(bot, chat_id, user_id, text, reply_markup=menu_main_inline_kb())


def welcome_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заполнить профиль", callback_data="p:start_wizard")],
    ])


async def cmd_start(message: Message, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await try_delete_user_message(bot, message)

    await bot.send_message(
        chat_id=message.chat.id,
        text="✅ Я на месте. Кнопки снизу 👇",
        reply_markup=control_reply_kb()
    )

    welcome_text = (
        "👋 Привет! Я твой персональный тренер-бот.\n\n"
        "🏋️ Что я умею:\n"
        "• Составляю программу тренировок под тебя — по системе Фулбади, Верх/Низ или PPL, "
        "в зависимости от твоей цели, опыта и того, где тренируешься (зал или дома)\n"
        "• Рассчитываю питание по КБЖУ индивидуально — с учётом цели, веса, роста и активности\n"
        "• Веду дневник тренировок — записываю веса и повторения, сохраняю историю\n"
        "• Показываю технику упражнений с картинками\n\n"
        "📋 Как это работает:\n"
        "1. Заполняешь профиль — цель, параметры, опыт\n"
        "2. Выбираешь тариф и получаешь программу лично под тебя\n"
        "3. Тренируешься, фиксируешь результат в дневнике\n\n"
        "Нажми кнопку ниже — и поехали 👇"
    )

    if os.path.exists(WELCOME_IMAGE):
        photo = FSInputFile(WELCOME_IMAGE)
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo,
            caption=welcome_text,
            reply_markup=welcome_kb()
        )
    else:
        await bot.send_message(
            chat_id=message.chat.id,
            text=welcome_text,
            reply_markup=welcome_kb()
        )


# =========================
# Навигация
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
# Панель управления (ReplyKeyboard)
# =========================
async def open_payment_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()

    a = await get_access(message.from_user.id)

    if await is_access_active(message.from_user.id):
        text = f"✅ Доступ активен.\n{access_status_str(a)}"
        await clean_send(bot, message.chat.id, message.from_user.id, text)
    else:
        text = (
            "💳 Оплата / Доступ\n\n"
            f"{access_status_str(a)}\n\n"
            "Выбери тариф — покажу реквизиты.\n"
            "Потом пришлёшь только скрин перевода."
        )
        await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=pay_tariff_kb())
        await state.set_state(PaymentFlow.choose_tariff)

    await try_delete_user_message(bot, message)


def _profile_summary_text(u: dict) -> str:
    return (
        "⚙️ Профиль\n\n"
        f"Цель: {u.get('goal')}\n"
        f"Пол: {u.get('sex')}\n"
        f"Возраст: {u.get('age')}\n"
        f"Рост: {u.get('height')}\n"
        f"Вес: {u.get('weight')}\n"
        f"Где тренируешься: {u.get('place')}\n"
        f"Опыт: {u.get('exp')}\n"
        f"Тренировки: {u.get('freq')}×/нед\n"
        f"Еда: {u.get('meals')}×/день\n"
        f"Ограничения: {(u.get('limits') or 'нет')}"
    )


async def open_profile_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await try_delete_user_message(bot, message)
    await state.clear()

    u = await get_user(message.from_user.id)
    if await ensure_profile_ready(message.from_user.id):
        await clean_send(bot, message.chat.id, message.from_user.id, _profile_summary_text(u), reply_markup=profile_ready_kb())
        return

    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "Настроим профиль.\n\n🎯 Цель?"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_goal())


async def cb_profile_edit(callback: CallbackQuery, state: FSMContext):
    """Показываем меню выбора — что именно менять в профиле."""
    await state.clear()
    u = await get_user(callback.from_user.id)
    text = (
        "✏️ Что хочешь изменить?\n\n"
        "Выбери пункт — я задам только один вопрос и сохраню."
    )
    await clean_edit(callback, callback.from_user.id, text, reply_markup=profile_edit_field_kb(u))
    await callback.answer()


async def cb_profile_start_wizard(callback: CallbackQuery, state: FSMContext):
    """Запуск профиль-мастера из приветственного сообщения."""
    await ensure_user(callback.from_user.id, callback.from_user.username or "")
    await state.clear()

    u = await get_user(callback.from_user.id)
    # Если профиль уже заполнен — показываем его с кнопкой «Собрать программу»
    if await ensure_profile_ready(callback.from_user.id):
        text = _profile_summary_text(u) + "\n\n✅ Профиль уже заполнен!"
        await clean_edit(callback, callback.from_user.id, text,
                         reply_markup=profile_ready_kb())
        await callback.answer()
        return

    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "Настроим профиль.\n\n🎯 Цель?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_goal())
    await callback.answer()


async def open_support_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    text = "🆘 Поддержка\n\nНапиши проблему одним сообщением (можно скрин/ошибку)."
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
async def cb_build_program(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Показываем тарифы после заполнения профиля."""
    await state.clear()
    uid = callback.from_user.id

    if not await ensure_profile_ready(uid):
        await clean_edit(callback, uid,
                         "⚠️ Сначала заполни профиль полностью.",
                         reply_markup=profile_done_kb())
        await callback.answer()
        return

    text = (
        "🚀 Отлично! Профиль готов.\n\n"
        "Выбери тариф — и я сразу соберу программу тренировок под тебя:\n\n"
        f"🟢 Пробный — {TARIFFS['trial']['price']}₽\n"
        "   • 3 дня доступа\n"
        "   • Блок тренировок + ответы на вопросы\n"
        "   • Питание недоступно\n\n"
        f"🟩 Месячный — {TARIFFS['t1']['price']}₽\n"
        "   • 30 дней доступа\n"
        "   • Тренировки + питание + дневник + замеры\n"
        "   • Полный доступ ко всему\n\n"
        "👇 Выбери:"
    )
    await clean_edit(callback, uid, text, reply_markup=build_program_tariff_kb())
    await callback.answer()


async def cb_profile_field_edit(callback: CallbackQuery, state: FSMContext):
    """Обрабатываем нажатие на конкретное поле профиля для изменения."""
    field = callback.data.split("pf:", 1)[1]
    uid = callback.from_user.id
    u = await get_user(uid)

    await state.update_data(editing_field=field)

    if field == "goal":
        await state.set_state(ProfileWizard.goal)
        text = "🎯 Выбери новую цель:"
        await clean_edit(callback, uid, text, reply_markup=kb_goal())
    elif field == "sex":
        await state.set_state(ProfileWizard.sex)
        text = "👤 Выбери пол:"
        await clean_edit(callback, uid, text, reply_markup=kb_sex())
    elif field == "age":
        await state.set_state(ProfileFieldEdit.age)
        text = f"🎂 Текущий возраст: {u.get('age')}\nВведи новый:"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))
    elif field == "height":
        await state.set_state(ProfileFieldEdit.height)
        text = f"📏 Текущий рост: {u.get('height')} см\nВведи новый:"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))
    elif field == "weight":
        await state.set_state(ProfileFieldEdit.weight)
        text = f"⚖️ Текущий вес: {u.get('weight')} кг\nВведи новый:"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))
    elif field == "place":
        await state.set_state(ProfileWizard.place)
        text = "🏠 Где тренируешься?"
        await clean_edit(callback, uid, text, reply_markup=kb_place())
    elif field == "exp":
        await state.set_state(ProfileWizard.exp)
        text = "📈 Опыт тренировок?"
        await clean_edit(callback, uid, text, reply_markup=kb_exp())
    elif field == "freq":
        await state.set_state(ProfileWizard.freq)
        text = "📅 Сколько тренировок в неделю?"
        await clean_edit(callback, uid, text, reply_markup=kb_freq())
    elif field == "meals":
        await state.set_state(ProfileWizard.meals)
        text = "🍽 Сколько раз в день удобно есть?"
        await clean_edit(callback, uid, text, reply_markup=kb_meals())
    elif field == "limits":
        await state.set_state(ProfileFieldEdit.limits)
        text = f"⛔️ Текущие ограничения: {u.get('limits') or 'нет'}\nВведи новые (или «нет»):"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))

    await callback.answer()


async def _finish_field_edit(bot: Bot, chat_id: int, user_id: int):
    """После изменения одного поля — обновляем план и показываем профиль."""
    await build_plans_if_needed(user_id, force=True)
    u = await get_user(user_id)
    text = _profile_summary_text(u) + "\n\n✅ Изменение сохранено, план обновлён."
    await clean_send(bot, chat_id, user_id, text, reply_markup=profile_ready_kb())


async def profile_field_age(message: Message, state: FSMContext, bot: Bot):
    age = _parse_int_from_text(message.text or "")
    if age is None or age < 10 or age > 90:
        await message.answer("Возраст числом 🙂 Например: 23")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, age=age)
    await state.clear()
    await try_delete_user_message(bot, message)
    await _finish_field_edit(bot, message.chat.id, message.from_user.id)


async def profile_field_height(message: Message, state: FSMContext, bot: Bot):
    h = _parse_int_from_text(message.text or "")
    if h is None or h < 120 or h > 230:
        await message.answer("Рост в см 🙂 Например: 178")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, height=h)
    await state.clear()
    await try_delete_user_message(bot, message)
    await _finish_field_edit(bot, message.chat.id, message.from_user.id)


async def profile_field_weight(message: Message, state: FSMContext, bot: Bot):
    w = _parse_float_from_text(message.text or "")
    if w is None or w < 30 or w > 250:
        await message.answer("Вес в кг 🙂 Например: 72.5")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, weight=w)
    await state.clear()
    await try_delete_user_message(bot, message)
    await _finish_field_edit(bot, message.chat.id, message.from_user.id)


async def profile_field_limits(message: Message, state: FSMContext, bot: Bot):
    limits = (message.text or "").strip()
    if not limits:
        await message.answer("Напиши текстом или «нет».")
        await try_delete_user_message(bot, message)
        return
    if limits.lower() in ("нет", "нету", "никаких", "no"):
        limits = ""
    await update_user(message.from_user.id, limits=limits)
    await state.clear()
    await try_delete_user_message(bot, message)
    await _finish_field_edit(bot, message.chat.id, message.from_user.id)


async def cb_profile_back(callback: CallbackQuery, state: FSMContext):
    step = callback.data.split(":")[2]
    uid = callback.from_user.id

    if step == "goal":
        await state.set_state(ProfileWizard.goal)
        text = _profile_header(1) + "🎯 Цель?"
        await clean_edit(callback, uid, text, reply_markup=kb_goal())
    elif step == "sex":
        await state.set_state(ProfileWizard.sex)
        text = _profile_header(2) + "👤 Пол?"
        await clean_edit(callback, uid, text, reply_markup=kb_sex())
    elif step == "age":
        await state.set_state(ProfileWizard.age)
        text = _profile_header(3) + "🎂 Возраст (числом):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("sex"))
    elif step == "height":
        await state.set_state(ProfileWizard.height)
        text = _profile_header(4) + "📏 Рост в см:"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("age"))
    elif step == "weight":
        await state.set_state(ProfileWizard.weight)
        text = _profile_header(5) + "⚖️ Вес в кг:"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("height"))
    elif step == "place":
        await state.set_state(ProfileWizard.place)
        text = _profile_header(6) + "🏠 Где тренируешься?"
        await clean_edit(callback, uid, text, reply_markup=kb_place())
    elif step == "exp":
        await state.set_state(ProfileWizard.exp)
        text = _profile_header(7) + "📈 Опыт?"
        await clean_edit(callback, uid, text, reply_markup=kb_exp())
    elif step == "freq":
        await state.set_state(ProfileWizard.freq)
        text = _profile_header(8) + "📅 Сколько тренировок в неделю?"
        await clean_edit(callback, uid, text, reply_markup=kb_freq())
    elif step == "meals":
        await state.set_state(ProfileWizard.meals)
        text = _profile_header(9) + "🍽 Сколько раз в день удобно есть?"
        await clean_edit(callback, uid, text, reply_markup=kb_meals())
    elif step == "limits":
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(10) + "⛔️ Ограничения/травмы? (или «нет»):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("meals"))
    else:
        await clean_send(callback.bot, callback.message.chat.id, uid, "🏠 Меню", reply_markup=menu_main_inline_kb())

    await callback.answer()


async def cb_profile_goal(callback: CallbackQuery, state: FSMContext, bot: Bot):
    v = callback.data.split(":")[2]
    goal = {
        "mass": "масса",
        "cut": "сушка",
        "strength": "сила",
        "endurance": "выносливость",
    }.get(v, v)

    await update_user(callback.from_user.id, goal=goal)

    # Если редактируем одно поле — возвращаемся в профиль
    data = await state.get_data()
    if data.get("editing_field") == "goal":
        await state.clear()
        await _finish_field_edit(bot, callback.message.chat.id, callback.from_user.id)
        await callback.answer()
        return

    await state.set_state(ProfileWizard.sex)
    text = _profile_header(2) + "👤 Пол?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_sex())
    await callback.answer()


async def cb_profile_sex(callback: CallbackQuery, state: FSMContext, bot: Bot):
    v = callback.data.split(":")[2]
    sex = "м" if v == "m" else "ж"
    await update_user(callback.from_user.id, sex=sex)

    data = await state.get_data()
    if data.get("editing_field") == "sex":
        await state.clear()
        await _finish_field_edit(bot, callback.message.chat.id, callback.from_user.id)
        await callback.answer()
        return

    await state.set_state(ProfileWizard.age)
    text = _profile_header(3) + "🎂 Возраст (числом):"
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
        await message.answer("Возраст числом 🙂 Например: 23")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, age=age)

    await state.set_state(ProfileWizard.height)
    text = _profile_header(4) + "📏 Рост в см:"
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
    text = _profile_header(5) + "⚖️ Вес в кг:"
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


async def cb_profile_place(callback: CallbackQuery, state: FSMContext, bot: Bot):
    v = callback.data.split(":")[2]
    place = "дома" if v == "bodyweight" else "зал"
    await update_user(callback.from_user.id, place=place)

    data = await state.get_data()
    if data.get("editing_field") == "place":
        await state.clear()
        await _finish_field_edit(bot, callback.message.chat.id, callback.from_user.id)
        await callback.answer()
        return

    await state.set_state(ProfileWizard.exp)
    text = _profile_header(7) + "📈 Опыт?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_exp())
    await callback.answer()


async def cb_profile_exp(callback: CallbackQuery, state: FSMContext, bot: Bot):
    v = callback.data.split(":")[2]
    if v == "0":
        await update_user(callback.from_user.id, exp="0", freq=3)

        data = await state.get_data()
        if data.get("editing_field") in ("exp", "freq"):
            await state.clear()
            await _finish_field_edit(bot, callback.message.chat.id, callback.from_user.id)
            await callback.answer()
            return

        await state.set_state(ProfileWizard.meals)
        text = _profile_header(9) + "🍽 Сколько раз в день удобно есть?"
        await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_meals())
        await callback.answer()
        return

    exp_text = "1-2 года" if v == "mid" else "2+ года"
    await update_user(callback.from_user.id, exp=exp_text)

    data = await state.get_data()
    if data.get("editing_field") == "exp":
        await state.clear()
        await _finish_field_edit(bot, callback.message.chat.id, callback.from_user.id)
        await callback.answer()
        return

    await state.set_state(ProfileWizard.freq)
    text = _profile_header(8) + "📅 Сколько тренировок в неделю?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_freq())
    await callback.answer()


async def cb_profile_freq(callback: CallbackQuery, state: FSMContext, bot: Bot):
    f = int(callback.data.split(":")[2])
    await update_user(callback.from_user.id, freq=f)

    data = await state.get_data()
    if data.get("editing_field") == "freq":
        await state.clear()
        await _finish_field_edit(bot, callback.message.chat.id, callback.from_user.id)
        await callback.answer()
        return

    await state.set_state(ProfileWizard.meals)
    text = _profile_header(9) + "🍽 Сколько раз в день удобно есть?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_meals())
    await callback.answer()


async def cb_profile_meals(callback: CallbackQuery, state: FSMContext, bot: Bot):
    m = int(callback.data.split(":")[2])
    m = max(3, min(m, 5))
    await update_user(callback.from_user.id, meals=m)

    data = await state.get_data()
    if data.get("editing_field") == "meals":
        await state.clear()
        await _finish_field_edit(bot, callback.message.chat.id, callback.from_user.id)
        await callback.answer()
        return

    await state.set_state(ProfileWizard.limits)
    text = _profile_header(10) + "⛔️ Ограничения/травмы? (или «нет»):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("meals"))
    await callback.answer()


async def profile_limits_text(message: Message, state: FSMContext, bot: Bot):
    limits = (message.text or "").strip()
    if not limits:
        await message.answer("Напиши текстом или «нет».")
        await try_delete_user_message(bot, message)
        return
    if limits.lower() in ("нет", "нету", "никаких", "no"):
        limits = ""

    await update_user(message.from_user.id, limits=limits)
    await state.clear()

    u = await get_user(message.from_user.id)
    summary = (
        "✅ Профиль сохранён!\n\n"
        f"Цель: {u.get('goal')} • {u.get('freq')}×/нед\n"
        f"Где тренируешься: {u.get('place')}\n"
        f"Еда: {u.get('meals')}×/день\n"
        f"Ограничения: {(u.get('limits') or 'нет')}\n\n"
        "Теперь выбери тариф и я соберу твою программу 👇"
    )
    await clean_send(bot, message.chat.id, message.from_user.id, summary, reply_markup=profile_done_kb())
    await try_delete_user_message(bot, message)


# =========================
# ОПЛАТА — только скрин
# =========================
def access_status_str(a: dict) -> str:
    if not a or a.get("paid") != 1:
        return "Статус: ❌ доступа нет"
    if a.get("tariff") == "life":
        return "Статус: ✅ доступ навсегда"
    exp = a.get("expires_at")
    return f"Статус: ✅ до {exp[:10]}" if exp else "Статус: ✅ активен"


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
        "Реквизиты:\n"
        f"• Банк: {BANK_NAME}\n"
        f"• Карта: {CARD_NUMBER}\n"
        f"• Получатель: {CARD_HOLDER}\n\n"
        "Комментарий к переводу (код):\n"
        f"{code}\n\n"
        "После оплаты: «✅ Я оплатил» → пришли скрин."
    )
    await callback.message.answer(text, reply_markup=pay_inline_kb())
    await callback.answer()


async def cb_i_paid(callback: CallbackQuery, state: FSMContext):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")

    if await is_access_active(callback.from_user.id):
        await callback.message.answer("✅ Доступ уже активен.")
        await callback.answer()
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if not tariff:
        await callback.message.answer("Сначала выбери тариф в «💳 Оплата/доступ».")
        await callback.answer()
        return

    if await has_recent_pending_payment(callback.from_user.id):
        await callback.message.answer("⏳ Заявка уже на проверке.")
        await callback.answer()
        return

    await callback.message.answer("Ок. Пришли скрин перевода (как фото).")
    await state.set_state(PaymentFlow.waiting_receipt)
    await callback.answer()


async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await message.answer("Нужно именно фото/скрин 🙂")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if tariff not in TARIFFS:
        await message.answer("Тариф потерялся 😅 Открой «Оплата/доступ» и выбери заново.")
        await state.clear()
        await try_delete_user_message(bot, message)
        return

    amount = int(TARIFFS[tariff]["price"])
    receipt_file_id = message.photo[-1].file_id
    code = gen_order_code(message.from_user.id)

    payment_id = await create_payment(message.from_user.id, tariff, amount, "----", code, receipt_file_id)
    await message.answer("✅ Принял. Проверю и открою доступ.")
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
            text=f"✅ Оплата подтверждена.\nТариф: {TARIFFS[tariff]['title']}\n{access_status_str(a)}",
            reply_markup=menu_main_inline_kb()
        )
        await callback.answer("Подтверждено ✅")
    else:
        await set_payment_status(pid, "rejected")
        await bot.send_message(
            chat_id=user_id,
            text="❌ Отклонил. Проверь перевод/скрин и попробуй ещё раз (💳 Оплата/доступ)."
        )
        await callback.answer("Отклонено ❌")


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
        u["goal"], u["place"], u["exp"], int(u["freq"]),
        limits=u.get("limits") or "",
        user_id=user_id
    )

    summary, cal, p, f, c, meals = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"], meals_pref=int(u.get("meals") or 0)
    )

    nutrition_full = (
        summary
        + "\n\nЗамены (равноценные):\n"
          "• курица ↔ индейка ↔ рыба белая\n"
          "• рис ↔ гречка ↔ макароны\n"
          "• творог 0% ↔ греческий йогурт\n"
          "• банан ↔ яблоко ↔ ягоды"
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
        await clean_send(bot, chat_id, user_id, "⚠️ Сначала заполни профиль (⚙️ Профиль).")
        return

    plan_text, plan_struct = await get_workout_plan(user_id)
    if not plan_text or not plan_struct:
        await build_plans_if_needed(user_id, force=True)
        plan_text, plan_struct = await get_workout_plan(user_id)

    u = await get_user(user_id)
    kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3))

    if callback:
        await clean_edit(callback, user_id, plan_text or "🏋️ План не найден.", reply_markup=kb)
    else:
        await clean_send(bot, chat_id, user_id, plan_text or "🏋️ План не найден.", reply_markup=kb)


# =========================
# ✅ ИЗМЕНЕНИЕ 1: Кнопка «📚 Техника выполнения» вместо смайлика
# ✅ ИЗМЕНЕНИЕ 4: Прогресс-бар в заголовке дня
# =========================
def workout_day_exercises_kb(day: int, exercises: List[str], done: List[int]) -> InlineKeyboardMarkup:
    """Клавиатура упражнений дня с чекбоксами, ккал и кнопкой техники."""
    rows = []
    for idx, name in enumerate(exercises):
        is_done = idx in done
        ex_kcal = get_exercise_kcal(name)
        # Обрезаем длинные имена для кнопки
        short_name = name[:20] + "…" if len(name) > 20 else name
        if is_done:
            btn_text = f"✅ {short_name}"
        else:
            btn_text = f"⬜️ {short_name}"

        done_btn = InlineKeyboardButton(
            text=btn_text,
            callback_data=f"wex:done:{day}:{idx}"
        )
        tech_key = get_tech_key_for_exercise(name)
        if tech_key:
            tech_btn = InlineKeyboardButton(
                text="📚 Техника",
                callback_data=f"wex:tech:{day}:{tech_key}"
            )
            rows.append([done_btn, tech_btn])
        else:
            rows.append([done_btn])

    rows.append([
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"wday:stats:{day}"),
        InlineKeyboardButton(text="📋 Полная программа", callback_data="nav:workouts"),
    ])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cb_workout_day(callback: CallbackQuery, bot: Bot):
    # Обработка кнопки статистики
    parts = callback.data.split(":")
    if len(parts) == 3 and parts[1] == "stats":
        await cb_workout_stats(callback, bot)
        return

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

    day_num = int(day)
    uid = callback.from_user.id

    exercises = parse_exercises_from_day_text(day_text)
    if not exercises:
        u = await get_user(uid)
        kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3))
        await clean_edit(callback, uid, day_text, reply_markup=kb)
        await callback.answer()
        return

    done = await get_day_done_exercises(uid, day_num)
    already_done_today = await is_day_completed_today(uid, day_num)

    text = build_day_display_text(day_num, day_text, exercises, done)
    if already_done_today:
        text += "\n\n🎉 День уже засчитан сегодня! Можешь пройти снова."
    kb = workout_day_exercises_kb(day_num, exercises, done)
    await clean_edit(callback, uid, text, reply_markup=kb)
    await callback.answer()


async def cb_workout_ex_done(callback: CallbackQuery, bot: Bot):
    """Отмечаем/снимаем выполнение упражнения, обновляем прогресс и ккал."""
    parts = callback.data.split(":")
    day_num = int(parts[2])
    ex_idx = int(parts[3])
    uid = callback.from_user.id

    if not await is_access_active(uid):
        await callback.answer("Нет доступа", show_alert=True)
        return

    plan_text, plan_struct = await get_workout_plan(uid)
    if not plan_struct:
        await callback.answer("Нет плана 😅", show_alert=True)
        return

    day_text = (plan_struct.get("days") or {}).get(str(day_num))
    if not day_text:
        await callback.answer("День не найден", show_alert=True)
        return

    exercises = parse_exercises_from_day_text(day_text)
    done = await get_day_done_exercises(uid, day_num)

    if ex_idx in done:
        done.remove(ex_idx)
    else:
        done.append(ex_idx)

    await set_day_done_exercises(uid, day_num, done)

    total = len(exercises)
    done_count = len(done)
    all_done = total > 0 and done_count == total

    if all_done:
        await mark_day_completed(uid, day_num)
        await clear_day_progress(uid, day_num)
        text = build_day_display_text(day_num, day_text, exercises, list(range(total)), all_done=True)
        kb = workout_day_exercises_kb(day_num, exercises, list(range(total)))
        await clean_edit(callback, uid, text, reply_markup=kb)
        await callback.answer("🎉 День завершён!", show_alert=True)
    else:
        text = build_day_display_text(day_num, day_text, exercises, done)
        kb = workout_day_exercises_kb(day_num, exercises, done)
        await clean_edit(callback, uid, text, reply_markup=kb)
        ex_name = exercises[ex_idx] if ex_idx < len(exercises) else ""
        ex_kcal = get_exercise_kcal(ex_name)
        if ex_idx in done:
            await callback.answer(f"✅ +{ex_kcal} ккал | {done_count}/{total}")
        else:
            await callback.answer(f"↩️ Снято | {done_count}/{total}")


async def cb_workout_stats(callback: CallbackQuery, bot: Bot):
    """Статистика выполненных тренировок."""
    uid = callback.from_user.id
    async with db() as conn:
        async with conn.execute("""
            SELECT day_num, completed_date, created_at
            FROM workout_completions
            WHERE user_id=?
            ORDER BY id DESC LIMIT 30
        """, (uid,)) as cur:
            rows = await cur.fetchall()

    if not rows:
        await callback.answer("Пока нет завершённых тренировок 💪", show_alert=True)
        return

    # Группируем по неделям
    total = len(rows)
    # Считаем ккал за тренировки (примерно, из плана)
    plan_text, plan_struct = await get_workout_plan(uid)

    lines = ["📊 Статистика тренировок\n"]
    lines.append(f"Всего выполнено: {total} тренировок\n")

    # Последние 10
    lines.append("🗓 Последние тренировки:")
    for day_num, completed_date, _ in rows[:10]:
        day_label = f"День {day_num}"
        if plan_struct:
            day_text = (plan_struct.get("days") or {}).get(str(day_num), "")
            day_kind = get_day_kind_from_text(day_text)
            day_name_map = {
                "UPPER": "Верх тела", "LOWER": "Низ тела",
                "PUSH": "Толчок", "PULL": "Тяга",
                "LEGS": "Ноги", "FB": "Фулбади",
            }
            day_label = f"День {day_num} • {day_name_map.get(day_kind, 'Тренировка')}"
        lines.append(f"✅ {completed_date}  —  {day_label}")

    # Серия (streak)
    dates = sorted(set(r[1] for r in rows), reverse=True)
    streak = 0
    prev = None
    for d in dates:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            continue
        if prev is None:
            streak = 1
        elif (prev - dt).days == 1:
            streak += 1
        else:
            break
        prev = dt

    lines.append(f"\n🔥 Текущая серия: {streak} дн. подряд")

    text = "\n".join(lines)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:workouts")],
    ])
    await clean_edit(callback, uid, text, reply_markup=back_kb)
    await callback.answer()


# =========================
# ✅ ИЗМЕНЕНИЕ 1: Техника из дня — с картинкой, кнопка «Техника выполнения»
# =========================
async def cb_workout_ex_tech(callback: CallbackQuery, bot: Bot):
    """Показываем технику упражнения с картинкой (из просмотра дня)."""
    parts = callback.data.split(":")
    tech_key = parts[3]
    day_num = parts[2]

    item = TECH.get(tech_key)
    if not item:
        await callback.answer("Техника не найдена 😅", show_alert=True)
        return

    text = item["text"]
    img_path = item["img"]

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⬅️ Назад к Дню {day_num}", callback_data=f"wday:{day_num}")]
    ])

    # ✅ ИЗМЕНЕНИЕ 2: удаляем предыдущее сообщение чтобы не засорять чат
    last_id = await get_last_bot_msg_id(callback.from_user.id)
    if last_id:
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=last_id)
        except Exception:
            pass

    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        # Картинка + текст техники в caption (до 1024 символов)
        caption = text[:1020] + ("…" if len(text) > 1020 else "")
        m = await bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photo,
            caption=caption,
            reply_markup=back_kb
        )
        # Если текст длиннее — досылаем остаток
        rest = text[1020:].strip()
        if rest:
            m2 = await bot.send_message(
                chat_id=callback.message.chat.id,
                text=rest,
                reply_markup=back_kb
            )
            await set_last_bot_msg_id(callback.from_user.id, m2.message_id)
        else:
            await set_last_bot_msg_id(callback.from_user.id, m.message_id)
    else:
        # Нет картинки — просто текст
        m = await bot.send_message(
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=back_kb
        )
        await set_last_bot_msg_id(callback.from_user.id, m.message_id)

    await callback.answer()


async def open_nutrition(user_id: int, chat_id: int, bot: Bot, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        text = locked_text()
        if callback:
            await clean_edit(callback, user_id, text)
        else:
            await clean_send(bot, chat_id, user_id, text)
        return

    if not await is_full_access_active(user_id):
        text = (
            "🍽 Раздел питания доступен только по месячной подписке.\n\n"
            f"Пробный период включает только тренировки и ответы на вопросы.\n\n"
            f"Месячная подписка — {TARIFFS['t1']['price']}₽ • полный доступ 👇"
        )
        upgrade_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🟩 Месячный — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
        ])
        if callback:
            await clean_edit(callback, user_id, text, reply_markup=upgrade_kb)
        else:
            await clean_send(bot, chat_id, user_id, text, reply_markup=upgrade_kb)
        return

    if not await ensure_profile_ready(user_id):
        text = "⚠️ Сначала заполни профиль (⚙️ Профиль)."
        if callback:
            await clean_edit(callback, user_id, text)
        else:
            await clean_send(bot, chat_id, user_id, text)
        return

    u = await get_user(user_id)
    summary, _, _, _, _, _ = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"], meals_pref=int(u.get("meals") or 0)
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
    text = "📏 Замеры\n\nВыбирай замер — дату поставлю сам."
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
        "📓 Дневник\n\n"
        "Выбираешь упражнение кнопкой,\n"
        "пишешь только вес×повторы.\n"
        "Дату поставлю сам."
    )
    if callback:
        await clean_edit(callback, user_id, text, reply_markup=diary_exercises_kb())
    else:
        await clean_send(bot, chat_id, user_id, text, reply_markup=diary_exercises_kb())


# =========================
# ДНЕВНИК
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
    text = (
        f"🗓 {today}\n"
        f"✅ {exercise}\n\n"
        "Напиши подходы: весxповторы\n"
        "Пример: 60x8, 60x8, 60x7"
    )

    m = await bot.send_message(chat_id=callback.message.chat.id, text=text)
    await set_diary_prompt_msg_id(callback.from_user.id, m.message_id)
    await callback.answer()


async def diary_enter_sets(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip()
    data = await state.get_data()
    exercise = (data.get("exercise") or "").strip()
    if not exercise:
        await clean_send(bot, message.chat.id, message.from_user.id, "Сначала выбери упражнение в «Дневнике».")
        await try_delete_user_message(bot, message)
        await state.clear()
        return

    parts = [p.strip() for p in txt.split(",") if p.strip()]
    if not parts:
        await message.answer("Формат: 60x8, 60x8")
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
    msg = f"✅ Записал.\n🗓 {today}\n🏷 {exercise}\nПодходов: {len(parsed)}"
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
        await callback.message.answer("Истории пока нет 🙂")
        await callback.answer()
        return

    msg = "📜 Последние тренировки:\n\n"
    for (s, sets) in history:
        sid, session_date, title = s
        msg += f"🗓 {session_date}\n"
        cur_ex = None
        line = ""
        for ex, set_no, w, reps in sets:
            if cur_ex != ex:
                if line:
                    msg += line + "\n"
                cur_ex = ex
                line = f"• {ex}: "
            line += f"{w:g}x{reps}  "
        if line:
            msg += line + "\n"
        msg += "\n"

    await safe_send(callback.message, msg, reply_markup=simple_back_to_menu_inline_kb())
    await callback.answer()


# =========================
# ЗАМЕРЫ
# =========================
async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await state.set_state(MeasureFlow.enter_value)

    name = dict(MEASURE_TYPES).get(mtype, mtype)
    await callback.message.answer(f"Впиши «{name}» числом:")
    await callback.answer()


async def measure_value(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        val = float(txt)
    except Exception:
        await message.answer("Нужно число 🙂")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    mtype = data.get("mtype")

    await add_measure(message.from_user.id, mtype, val)
    rows = await get_last_measures(message.from_user.id, mtype, 6)

    name = dict(MEASURE_TYPES).get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    out = f"✅ {name}: {val:g}\n\nПоследние:\n{hist}"
    await clean_send(bot, message.chat.id, message.from_user.id, out, reply_markup=measures_kb())
    await state.set_state(MeasureFlow.choose_type)
    await try_delete_user_message(bot, message)


async def measures_history(callback: CallbackQuery):
    rows = await get_last_measures_any(callback.from_user.id, 30)
    if not rows:
        await callback.message.answer("Истории пока нет 🙂")
        await callback.answer()
        return

    name_map = dict(MEASURE_TYPES)
    grouped: Dict[str, List[Tuple[float, str]]] = {}
    for mtype, val, created_at in rows:
        grouped.setdefault(mtype, []).append((val, created_at))

    msg = "📜 История замеров:\n\n"
    for mtype, items in grouped.items():
        msg += f"{name_map.get(mtype, mtype)}:\n"
        for val, ts in items[:6]:
            msg += f"• {val:g} ({ts[:10]})\n"
        msg += "\n"

    await safe_send(callback.message, msg, reply_markup=measures_kb())
    await callback.answer()


# =========================
# ПИТАНИЕ: 3 варианта с картинкой
# =========================
async def cb_nutr_example(callback: CallbackQuery, bot: Bot):
    if not await is_full_access_active(callback.from_user.id):
        text = (
            "🍽 Раздел питания доступен только по месячной подписке.\n\n"
            f"Месячная подписка — {TARIFFS['t1']['price']}₽ • полный доступ 👇"
        )
        upgrade_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🟩 Месячный — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
        ])
        await clean_edit(callback, callback.from_user.id, text, reply_markup=upgrade_kb)
        await callback.answer()
        return

    if not await ensure_profile_ready(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, "⚠️ Сначала заполни профиль (⚙️ Профиль).")
        await callback.answer()
        return

    day_i = int(callback.data.split(":")[2])
    u = await get_user(callback.from_user.id)
    summary, calories, p, f, c, meals = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"], meals_pref=int(u.get("meals") or 0)
    )
    day_text = build_meal_day_text(day_i, calories, p, f, c, meals)
    await clean_edit(callback, callback.from_user.id, day_text, reply_markup=nutrition_back_kb())
    await callback.answer()


async def cb_nutr_back(callback: CallbackQuery, bot: Bot):
    await open_nutrition(callback.from_user.id, callback.message.chat.id, bot, callback=callback)
    await callback.answer()


# =========================
# ✅ ТЕХНИКИ: ХЕНДЛЕРЫ (с картинками)
# Изменение 1: картинка отправляется при показе техники
# =========================
async def cb_tech_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await clean_edit(callback, callback.from_user.id, "📚 Техники — выбери упражнение:", reply_markup=tech_kb())
    await callback.answer()


async def cb_tech_show(callback: CallbackQuery, bot: Bot):
    """Показ техники с картинкой из списка техник."""
    key = callback.data.split("tech:", 1)[1]
    item = TECH.get(key)
    if not item:
        await callback.answer("Не нашёл 😅", show_alert=True)
        return

    text = item["text"]
    img_path = item["img"]
    caption = text[:1020] + ("…" if len(text) > 1020 else "")
    rest = text[1020:].strip()

    # ✅ ИЗМЕНЕНИЕ 2: удаляем предыдущее сообщение
    last_id = await get_last_bot_msg_id(callback.from_user.id)
    if last_id:
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=last_id)
        except Exception:
            pass

    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        m = await bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photo,
            caption=caption,
            reply_markup=tech_back_kb()
        )
        await set_last_bot_msg_id(callback.from_user.id, m.message_id)
        if rest:
            m2 = await bot.send_message(
                chat_id=callback.message.chat.id,
                text=rest,
                reply_markup=tech_back_kb()
            )
            await set_last_bot_msg_id(callback.from_user.id, m2.message_id)
    else:
        m = await bot.send_message(
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=tech_back_kb()
        )
        await set_last_bot_msg_id(callback.from_user.id, m.message_id)

    await callback.answer()


# =========================
# ПОСТЫ С КАРТИНКАМИ (АДМИН)
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
    await message.answer("🗞 Посты (админ):", reply_markup=admin_posts_kb())


async def cb_post_new(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    txt = (
        "📝 Создание поста\n\n"
        "Пришли текст\n"
        "или фото/видео с подписью.\n\n"
        "Потом покажу превью."
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
    await callback.message.answer("Ок.", reply_markup=admin_posts_kb())
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
            await message.answer("Нужно текст или медиа 🙂")
            return

    post_id = await create_post_draft(ADMIN_ID, media_type, media_file_id, text)
    await state.update_data(post_id=post_id)
    await state.set_state(PostFlow.waiting_confirm)

    preview_title = f"✅ Превью (id={post_id})\n\n"
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

    post_id = int(callback.data.split(":")[2])

    post = await get_post(post_id)
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return
    if post.get("status") == "sent":
        await callback.answer("Уже отправлен", show_alert=True)
        return

    await callback.message.answer("📤 Рассылаю…")
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
    await callback.message.answer(f"✅ Готово. Отправлено: {ok} • Ошибок: {fail}", reply_markup=admin_posts_kb())
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
    await clean_send(bot, message.chat.id, message.from_user.id, "✅ Принял. Отвечу тут.")


# =========================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
def setup_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, CommandStart())

    dp.callback_query.register(cb_nav, F.data.startswith("nav:"))

    dp.callback_query.register(cb_profile_edit, F.data == "p:edit")
    dp.callback_query.register(cb_profile_start_wizard, F.data == "p:start_wizard")
    dp.callback_query.register(cb_build_program, F.data == "p:build_program")
    dp.callback_query.register(cb_profile_field_edit, F.data.startswith("pf:"))
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

    # Одиночное редактирование полей
    dp.message.register(profile_field_age, ProfileFieldEdit.age)
    dp.message.register(profile_field_height, ProfileFieldEdit.height)
    dp.message.register(profile_field_weight, ProfileFieldEdit.weight)
    dp.message.register(profile_field_limits, ProfileFieldEdit.limits)

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
    dp.callback_query.register(cb_workout_ex_done, F.data.startswith("wex:done:"))
    dp.callback_query.register(cb_workout_ex_tech, F.data.startswith("wex:tech:"))

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
        raise RuntimeError("Нужно задать BOT_TOKEN через ENV.")

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
