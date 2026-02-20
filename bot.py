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
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove,
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
# FSM
# =========================
class PaymentFlow(StatesGroup):
    choose_tariff = State()
    waiting_amount = State()
    waiting_last4 = State()
    waiting_receipt = State()


class DiaryFlow(StatesGroup):
    choose_day = State()
    enter_title = State()
    enter_sets = State()


class DiaryQuickFlow(StatesGroup):
    choose_exercise = State()
    enter_weight = State()


class MeasureFlow(StatesGroup):
    choose_type = State()
    enter_value = State()


class FAQFlow(StatesGroup):
    ask = State()


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


# =========================
# ✅ ТЕХНИКИ ВЫПОЛНЕНИЯ (картинка + МАКСИМАЛЬНО ПОНЯТНО)
# =========================
TECH = {
    "squat": {
        "title": "Присед",
        "img": "media/tech/squat.jpg",
        "text": (
            "📚 ПРИСЕД (штанга/гоблет)\n\n"
            "🎯 Цель: ноги + ягодицы, стабильная спина.\n\n"
            "✅ Настройка (до первого повтора):\n"
            "1) Стопы: ширина плеч (можно чуть шире), носки 15–30° наружу.\n"
            "2) Опора: вес на середине стопы, пятки НЕ отрывай.\n"
            "3) Корпус: вдох животом → напряги пресс (как будто тебя сейчас толкнут).\n"
            "4) Спина: нейтральная (не округляй поясницу).\n\n"
            "✅ Движение (каждый повтор):\n"
            "1) Начало: таз чуть назад + колени вперед одновременно.\n"
            "2) Колени: идут туда же, куда носки (не заваливай внутрь).\n"
            "3) Глубина: опускайся до комфортной, где спина остаётся ровной.\n"
            "4) Вверх: толкай пол серединой стопы, колени не своди.\n"
            "5) Выдох: после прохождения самого тяжёлого участка.\n\n"
            "⏱ Темп (просто и работает):\n"
            "• вниз 2 секунды, пауза 0–1 сек, вверх уверенно.\n\n"
            "❌ Частые ошибки:\n"
            "• пятки отрываются\n"
            "• колени заваливаются внутрь\n"
            "• круглая поясница внизу\n"
            "• «падаешь» вниз без контроля\n\n"
            "🟢 Подсказка:\n"
            "Если колени «гуляют» — думай: «раздвигаю пол стопами».\n"
        )
    },
    "bench": {
        "title": "Жим лёжа",
        "img": "media/tech/bench.jpg",
        "text": (
            "📚 ЖИМ ЛЁЖА (штанга/гантели)\n\n"
            "🎯 Цель: грудь + трицепс, безопасные плечи.\n\n"
            "✅ Настройка:\n"
            "1) Ляг: глаза примерно под грифом.\n"
            "2) Лопатки: сведи и опусти вниз (как будто зажимаешь карандаш).\n"
            "3) Грудь «вверх», поясница с лёгким прогибом, но таз НЕ отрывай.\n"
            "4) Ноги: уверенно упираются в пол, пятки можно прижать.\n"
            "5) Хват: чуть шире плеч. Запястье над локтем.\n\n"
            "✅ Повтор:\n"
            "1) Снимай гриф на прямых руках.\n"
            "2) Опускай на нижнюю часть груди/линию сосков.\n"
            "3) Локти: примерно 30–60° от корпуса (не 90° в стороны).\n"
            "4) Жми вверх и чуть назад к стойкам.\n\n"
            "❌ Ошибки:\n"
            "• плечи «вылезают» вперёд (лопатки не держишь)\n"
            "• локти слишком широко\n"
            "• отрыв таза\n"
            "• запястья ломаются назад\n\n"
            "🟢 Подсказка:\n"
            "Думай: «ломаю гриф» (как будто крутишь его наружу) — плечи стабильнее.\n"
        )
    },
    "row": {
        "title": "Тяга (гребля)",
        "img": "media/tech/row.jpg",
        "text": (
            "📚 ТЯГА ГОРИЗОНТАЛЬНАЯ (блок/гантель)\n\n"
            "🎯 Цель: середина спины, лопатки, широчайшие.\n\n"
            "✅ Настройка:\n"
            "1) Корпус ровный, грудь слегка вперёд.\n"
            "2) Плечи вниз (не поднимай к ушам).\n"
            "3) Живот напряжён.\n\n"
            "✅ Повтор:\n"
            "1) Начни с лопатки: сначала слегка сведи лопатки.\n"
            "2) Затем тяни локти назад.\n"
            "3) В конце — короткая пауза 0.5 сек, почувствуй спину.\n"
            "4) Возврат медленно: дай лопаткам разойтись, но без «провала» корпуса.\n\n"
            "❌ Ошибки:\n"
            "• тянешь плечами вверх\n"
            "• рывок корпусом\n"
            "• слишком короткая амплитуда\n\n"
            "🟢 Подсказка:\n"
            "Думай: «локоть в карман» — так включается спина, а не бицепс.\n"
        )
    },
    "latpulldown": {
        "title": "Верхний блок",
        "img": "media/tech/latpulldown.jpg",
        "text": (
            "📚 ВЕРХНИЙ БЛОК (тяга к груди)\n\n"
            "🎯 Цель: широчайшие, верх спины.\n\n"
            "✅ Настройка:\n"
            "1) Сядь плотно, зафиксируй бедра под валиками.\n"
            "2) Корпус слегка отклонён назад (10–20°), но не раскачивайся.\n"
            "3) Плечи вниз.\n\n"
            "✅ Повтор:\n"
            "1) Сначала опусти лопатки вниз.\n"
            "2) Тяни гриф к верхней части груди.\n"
            "3) Локти веди вниз и назад.\n"
            "4) Возвращай вверх медленно, сохраняя контроль.\n\n"
            "❌ Ошибки:\n"
            "• тянешь за голову\n"
            "• раскачка корпусом\n"
            "• плечи поднимаются к ушам\n\n"
            "🟢 Подсказка:\n"
            "Если чувствуешь только руки — уменьшай вес и делай паузу внизу 1 сек.\n"
        )
    },
    "pullup": {
        "title": "Подтягивания",
        "img": "media/tech/pullup.jpg",
        "text": (
            "📚 ПОДТЯГИВАНИЯ\n\n"
            "🎯 Цель: широчайшие, верх спины, бицепс.\n\n"
            "✅ Настройка:\n"
            "1) Хват: чуть шире плеч (как удобно без боли).\n"
            "2) Корпус: лёгкое напряжение пресса, ноги можно скрестить.\n\n"
            "✅ Повтор (самое понятное):\n"
            "1) Старт: «лопатки вниз» (как будто хочешь опустить плечи).\n"
            "2) Потом тяни локти к рёбрам.\n"
            "3) Вверху грудь стремится к перекладине.\n"
            "4) Вниз опускайся контролируемо до прямых рук.\n\n"
            "❌ Ошибки:\n"
            "• раскачка/киппинг\n"
            "• подбородок вверх, спина не работает\n"
            "• не опускаешься полностью вниз\n\n"
            "🟢 Подсказка:\n"
            "Если тяжело — делай негативы: запрыгнул вверх → 3–5 сек медленно вниз.\n"
        )
    },
    "rdl": {
        "title": "Румынская тяга",
        "img": "media/tech/rdl.jpg",
        "text": (
            "📚 РУМЫНСКАЯ ТЯГА (RDL)\n\n"
            "🎯 Цель: задняя поверхность бедра + ягодицы, сильная поясница без перегруза.\n\n"
            "✅ Настройка:\n"
            "1) Ноги на ширине таза.\n"
            "2) Колени чуть согнуты и почти не меняют угол.\n"
            "3) Спина ровная, пресс напряжён.\n"
            "4) Гриф/гантели держи близко к ногам.\n\n"
            "✅ Повтор:\n"
            "1) Двигайся тазом назад (как будто закрываешь дверь попой).\n"
            "2) Гриф скользит вдоль бёдер/голеней.\n"
            "3) Опускайся до момента, когда спина ровная и тянет заднюю поверхность.\n"
            "4) Вверх — сжимай ягодицы, возвращай таз вперёд.\n\n"
            "❌ Ошибки:\n"
            "• округляешь спину\n"
            "• приседание вместо наклона (колени сильно уходят вперёд)\n"
            "• гриф далеко от ног\n\n"
            "🟢 Подсказка:\n"
            "Если не чувствуешь заднюю поверхность — уменьши вес и делай паузу внизу 1 сек.\n"
        )
    },
    "ohp": {
        "title": "Жим вверх",
        "img": "media/tech/ohp.jpg",
        "text": (
            "📚 ЖИМ СТОЯ (OHP)\n\n"
            "🎯 Цель: плечи + трицепс, сильный корпус.\n\n"
            "✅ Настройка:\n"
            "1) Стойка: ноги на ширине плеч.\n"
            "2) Пресс и ягодицы напряжены (чтобы не прогибаться).\n"
            "3) Гриф на верхней груди, локти чуть вперед.\n\n"
            "✅ Повтор:\n"
            "1) Жми гриф вверх по прямой линии.\n"
            "2) Чтобы пройти лицо: чуть уводишь голову назад, потом «ныряешь» под гриф.\n"
            "3) Вверху руки почти прямые, плечи не поднимай к ушам.\n\n"
            "❌ Ошибки:\n"
            "• сильный прогиб в пояснице\n"
            "• жим «вперед» (гриф улетает)\n"
            "• плечи к ушам\n\n"
            "🟢 Подсказка:\n"
            "Думай: «ребра вниз» — сразу убирает лишний прогиб.\n"
        )
    },
    "lateralraise": {
        "title": "Разведения в стороны",
        "img": "media/tech/lateralraise.jpg",
        "text": (
            "📚 РАЗВЕДЕНИЯ В СТОРОНЫ (дельты)\n\n"
            "🎯 Цель: средняя дельта (ширина плеч).\n\n"
            "✅ Настройка:\n"
            "1) Лёгкий наклон корпуса вперед 5–10°.\n"
            "2) Плечи опущены.\n"
            "3) Локоть чуть согнут.\n\n"
            "✅ Повтор:\n"
            "1) Поднимай гантели до уровня плеч.\n"
            "2) Локоть чуть выше кисти.\n"
            "3) Вниз медленно, без броска.\n\n"
            "❌ Ошибки:\n"
            "• рывок корпусом\n"
            "• поднимаешь слишком высоко (включается трапеция)\n"
            "• плечи к ушам\n\n"
            "🟢 Подсказка:\n"
            "Если трапеция забирает — снизь вес и делай паузу 1 сек вверху.\n"
        )
    },
    "biceps": {
        "title": "Бицепс сгибания",
        "img": "media/tech/biceps.jpg",
        "text": (
            "📚 СГИБАНИЯ НА БИЦЕПС\n\n"
            "🎯 Цель: бицепс без читинга.\n\n"
            "✅ Настройка:\n"
            "1) Локти прижаты к корпусу и НЕ уезжают вперед.\n"
            "2) Плечи вниз.\n\n"
            "✅ Повтор:\n"
            "1) Поднимай, не раскачивая корпус.\n"
            "2) Вверху — короткая пауза 0.5 сек.\n"
            "3) Опускай медленно 2–3 сек.\n\n"
            "❌ Ошибки:\n"
            "• раскачка спиной\n"
            "• локти уходят вперед\n"
            "• бросаешь вниз\n\n"
            "🟢 Подсказка:\n"
            "Лучше меньше вес — но чисто. Бицепс любит контроль.\n"
        )
    },
    "triceps": {
        "title": "Трицепс на блоке",
        "img": "media/tech/triceps.jpg",
        "text": (
            "📚 ТРИЦЕПС НА БЛОКЕ (разгибания)\n\n"
            "🎯 Цель: трицепс, плотное разгибание.\n\n"
            "✅ Настройка:\n"
            "1) Локти прижаты к корпусу.\n"
            "2) Корпус чуть наклонен, пресс напряжен.\n\n"
            "✅ Повтор:\n"
            "1) Разогни руки до конца.\n"
            "2) Внизу пауза 0.5 сек.\n"
            "3) Вверх — контролируемо, не поднимай локти.\n\n"
            "❌ Ошибки:\n"
            "• локти «гуляют»\n"
            "• включаешь плечи\n"
            "• половина амплитуды\n\n"
            "🟢 Подсказка:\n"
            "Если чувствуешь плечо — снизь вес и фиксируй локти сильнее.\n"
        )
    },
    "legpress": {
        "title": "Жим ногами",
        "img": "media/tech/legpress.jpg",
        "text": (
            "📚 ЖИМ НОГАМИ\n\n"
            "🎯 Цель: ноги, безопаснее чем присед при правильной технике.\n\n"
            "✅ Настройка:\n"
            "1) Стопы на платформе на ширине плеч.\n"
            "2) Поясница и таз прижаты к спинке.\n\n"
            "✅ Повтор:\n"
            "1) Опускай платформу до комфортной глубины.\n"
            "2) Колени идут по направлению носков.\n"
            "3) Выжимай через пятки/середину стопы.\n\n"
            "❌ Ошибки:\n"
            "• отрыв таза/поясницы (опасно)\n"
            "• колени заваливаются внутрь\n"
            "• слишком глубокая амплитуда ценой спины\n\n"
            "🟢 Подсказка:\n"
            "Главное правило: спина не отрывается. Если отрывается — уменьши глубину.\n"
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

        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:workouts")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tech_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="tech:list")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


# =========================
# UI: МЕНЮ (сообщение + кнопки)
# =========================
def menu_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Мои тренировки", callback_data="menu:workouts")],
        [InlineKeyboardButton(text="🍽 Моё питание", callback_data="menu:nutrition")],
        [InlineKeyboardButton(text="📏 Замеры", callback_data="menu:measures")],
        [InlineKeyboardButton(text="📓 Дневник", callback_data="menu:diary")],
        [InlineKeyboardButton(text="⚙️ Панель управления", callback_data="menu:control")],
    ])


# ✅ ПАНЕЛЬ УПРАВЛЕНИЯ ТЕПЕРЬ ВНИЗУ (клавиатура)
def control_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Оплата/доступ")],
            [KeyboardButton(text="⚙️ Профиль")],
            [KeyboardButton(text="🆘 Поддержка")],
            [KeyboardButton(text="🏠 Меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие…"
    )


def remove_reply_kb():
    return ReplyKeyboardRemove()


def workouts_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Техники выполнения упражнений", callback_data="tech:list")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


def simple_back_to_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


# =========================
# Оплата: кнопки
# =========================
def pay_tariff_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟩 1 месяц — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
        [InlineKeyboardButton(text=f"🟦 3 месяца — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
        [InlineKeyboardButton(text=f"🟨 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


def pay_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="pay_i_paid")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


def admin_review_kb(payment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_approve:{payment_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject:{payment_id}")],
    ])


# =========================
# Профиль: «приятное заполнение» + шкала
# =========================
TOTAL_PROFILE_STEPS = 8

def _bar(step: int, total: int = TOTAL_PROFILE_STEPS) -> str:
    step = max(0, min(step, total))
    done = "⬛" * step
    left = "⬜" * (total - step)
    return f"▭{done}{left}▭"

def _profile_header(step: int) -> str:
    return f"🧩 Заполнение профиля {step}/{TOTAL_PROFILE_STEPS}\n{_bar(step)}\n"

def kb_goal():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💪 Масса", callback_data="p:goal:mass"),
         InlineKeyboardButton(text="🔥 Сушка", callback_data="p:goal:cut")],
        [InlineKeyboardButton(text="🧩 Форма", callback_data="p:goal:fit")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def kb_sex():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Мужчина", callback_data="p:sex:m"),
         InlineKeyboardButton(text="👩 Женщина", callback_data="p:sex:f")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:goal")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def kb_age():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="18–25", callback_data="p:age:21"),
         InlineKeyboardButton(text="26–35", callback_data="p:age:30")],
        [InlineKeyboardButton(text="36–45", callback_data="p:age:40"),
         InlineKeyboardButton(text="46–55", callback_data="p:age:50")],
        [InlineKeyboardButton(text="56+", callback_data="p:age:60")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:sex")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def kb_height():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="150–160", callback_data="p:h:155"),
         InlineKeyboardButton(text="161–170", callback_data="p:h:166")],
        [InlineKeyboardButton(text="171–180", callback_data="p:h:176"),
         InlineKeyboardButton(text="181–190", callback_data="p:h:186")],
        [InlineKeyboardButton(text="191+", callback_data="p:h:195")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:age")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def kb_weight():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="30–50", callback_data="p:w:45"),
         InlineKeyboardButton(text="50–60", callback_data="p:w:55")],
        [InlineKeyboardButton(text="60–80", callback_data="p:w:70"),
         InlineKeyboardButton(text="80–100", callback_data="p:w:90")],
        [InlineKeyboardButton(text="100–120", callback_data="p:w:110"),
         InlineKeyboardButton(text="120+", callback_data="p:w:125")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:height")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def kb_place():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Дом", callback_data="p:place:home"),
         InlineKeyboardButton(text="🏋️ Зал", callback_data="p:place:gym")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:weight")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def kb_exp():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="0 (новичок)", callback_data="p:exp:0")],
        [InlineKeyboardButton(text="1–2 года", callback_data="p:exp:mid"),
         InlineKeyboardButton(text="2+ года", callback_data="p:exp:adv")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:place")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def kb_freq():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3×/нед", callback_data="p:freq:3"),
         InlineKeyboardButton(text="4×/нед", callback_data="p:freq:4")],
        [InlineKeyboardButton(text="5×/нед", callback_data="p:freq:5")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:back:exp")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


# =========================
# УТИЛИТЫ
# =========================
def gen_order_code(user_id: int) -> str:
    rnd = random.randint(100, 999)
    return f"TG{str(user_id)[-3:]}{rnd}"


def locked_text() -> str:
    return "🔒 Раздел доступен после оплаты.\nОткрой: ⚙️ Панель управления → 💳 Оплата/доступ"


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


# =========================
# ✅ АНТИ-ЗАСОРЕНИЕ ЧАТА: храним последнее сообщение бота и заменяем его
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


async def clean_send(bot: Bot, chat_id: int, user_id: int, text: str, reply_markup=None, reply_kb=None):
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass

    # reply_kb — это ReplyKeyboardMarkup (нижняя клавиатура)
    m = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup or reply_kb)
    await set_last_bot_msg_id(user_id, m.message_id)
    return m.message_id


async def clean_send_photo(bot: Bot, chat_id: int, user_id: int, photo, caption: str = "", reply_markup=None):
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass
    m = await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption, reply_markup=reply_markup)
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


async def find_or_create_quick_session(user_id: int, session_date: str) -> int:
    """
    Один «быстрый дневник» на день: чтобы не плодить кучу сессий.
    """
    async with db() as conn:
        async with conn.execute("""
            SELECT id FROM diary_sessions
            WHERE user_id=? AND session_date=? AND title='Быстрая запись'
            ORDER BY id DESC LIMIT 1
        """, (user_id, session_date)) as cur:
            row = await cur.fetchone()
        if row:
            return int(row[0])

    return await create_diary_session(user_id, session_date, "Быстрая запись")


async def next_set_no(session_id: int, exercise: str) -> int:
    async with db() as conn:
        async with conn.execute("""
            SELECT COALESCE(MAX(set_no), 0) FROM diary_sets
            WHERE session_id=? AND exercise=?
        """, (session_id, exercise)) as cur:
            row = await cur.fetchone()
    mx = int(row[0] or 0)
    return mx + 1


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
# ТРЕНИРОВКИ (база + изоляция)
# =========================
def _pick(rnd: random.Random, items: List[str]) -> str:
    items = [x for x in items if x]
    if not items:
        return "—"
    return rnd.choice(items)


def generate_workout_plan(goal: str, place: str, exp: str, freq: int, user_id: int = 0) -> str:
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym") or ("gym" in pl)
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
        "2) Потом добавляй вес (+2.5–5%) и снова работай в диапазоне\n"
        "3) Если техника ломается — вес не повышай\n"
        "4) Если усталость копится 7–10 дней — сделай неделю легче (-20–30% объёма)\n\n"
        + "\n".join(days)
    )


# =========================
# ПИТАНИЕ (однотипное, простое) + ✅ 3 КНОПКИ, 3 ПРИМЕРА (день 1/2/3)
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

def build_3day_meal_plan_days(calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> List[str]:
    days_out = []
    for day_i in range(1, 4):
        day_meals, tot = _build_day_items(meals, calories, protein_g, fat_g, carbs_g)

        lines = [f"📅 Пример питания №{day_i}", ""]
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
        days_out.append("\n".join(lines))
    return days_out

def generate_nutrition_intro_and_days(goal: str, sex: str, age: int, height: int, weight: float, exp: str, freq: int = 3, place: str = "дом"):
    calories = calc_calories(height, weight, age, sex, goal, freq=freq, place=place)
    p, f, c = calc_macros(calories, weight, goal)
    meals = suggest_meals_count(calories)

    intro = (
        "🍽 Моё питание\n\n"
        f"Цель: {goal}\n"
        f"Калории: ~{calories} ккал/день\n"
        f"БЖУ (ориентир): Б {p}г / Ж {f}г / У {c}г\n"
        f"Приёмов пищи: {meals}\n\n"
        "Нажми на пример ниже — открою готовый вариант на день.\n"
        "⚠️ Крупы в плане указаны в СУХОМ виде. Масло считай всегда."
    )
    days = build_3day_meal_plan_days(calories, p, f, c, meals)
    return intro, days


def nutrition_examples_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥣 Пример 1", callback_data="nutr:ex:1")],
        [InlineKeyboardButton(text="🍗 Пример 2", callback_data="nutr:ex:2")],
        [InlineKeyboardButton(text="🍚 Пример 3", callback_data="nutr:ex:3")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])

def nutrition_example_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к примерам", callback_data="nutr:menu")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])


# =========================
# МЕНЮ / START
# =========================
async def show_main_menu(bot: Bot, chat_id: int, user_id: int):
    text = "📌 Меню\n\nВыбирай раздел ниже 👇"
    await clean_send(bot, chat_id, user_id, text, reply_markup=menu_main_kb(), reply_kb=remove_reply_kb())


async def cmd_start(message: Message, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await show_main_menu(bot, message.chat.id, message.from_user.id)


async def cb_menu(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")
    await state.clear()

    key = callback.data.split(":", 1)[1]

    if key == "main":
        await show_main_menu(bot, callback.message.chat.id, callback.from_user.id)
    elif key == "control":
        # ✅ панель управления внизу (reply keyboard)
        await clean_send(
            bot, callback.message.chat.id, callback.from_user.id,
            "⚙️ Панель управления\n\nВыбери действие на клавиатуре снизу 👇",
            reply_kb=control_reply_kb()
        )
    elif key == "workouts":
        await open_workouts_cb(callback, bot)
    elif key == "nutrition":
        await open_nutrition_cb(callback, bot)
    elif key == "measures":
        await open_measures_cb(callback, state, bot)
    elif key == "diary":
        await open_diary_cb(callback, state, bot)
    else:
        await show_main_menu(bot, callback.message.chat.id, callback.from_user.id)

    await callback.answer()


# ✅ обработка кнопок снизу (панель управления)
async def ctrl_text_buttons(message: Message, state: FSMContext):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    t = (message.text or "").strip()

    if t == "💳 Оплата/доступ":
        # чтобы не плодить — заменяем сообщение бота
        fake_cb = _fake_callback_from_message(message)
        await state.clear()
        await open_payment_cb(fake_cb, state)
        return

    if t == "⚙️ Профиль":
        fake_cb = _fake_callback_from_message(message)
        await state.clear()
        await start_profile_wizard(fake_cb, state)
        return

    if t == "🆘 Поддержка":
        fake_cb = _fake_callback_from_message(message)
        await state.clear()
        await open_support_cb(fake_cb)
        return

    if t == "🏠 Меню":
        await state.clear()
        await show_main_menu(message.bot, message.chat.id, message.from_user.id)
        return


def _fake_callback_from_message(message: Message) -> CallbackQuery:
    """
    Хак: некоторые функции у нас ожидают CallbackQuery (редактирование/clean_edit).
    Для сообщений из reply keyboard сделаем «псевдо-callback» объект с нужными полями.
    """
    class _FakeCb:
        def __init__(self, msg: Message):
            self.message = msg
            self.from_user = msg.from_user
            self.bot = msg.bot
            self.data = ""
        async def answer(self, *args, **kwargs):
            return
    return _FakeCb(message)  # type: ignore


# =========================
# ПРОФИЛЬ-МАСТЕР: одно сообщение редактируется + шкала
# =========================
async def start_profile_wizard(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileWizard.goal)

    text = _profile_header(1) + "🎯 Выбери цель:"
    # если пришли из reply-кнопок, edit может не сработать — clean_send снесёт прошлое сообщение бота
    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, text, reply_markup=kb_goal())


async def cb_profile_back(callback: CallbackQuery, state: FSMContext):
    step = callback.data.split(":")[2]
    uid = callback.from_user.id

    if step == "goal":
        await state.set_state(ProfileWizard.goal)
        text = _profile_header(1) + "🎯 Выбери цель:"
        await clean_edit(callback, uid, text, reply_markup=kb_goal())
    elif step == "sex":
        await state.set_state(ProfileWizard.sex)
        text = _profile_header(2) + "👤 Выбери пол:"
        await clean_edit(callback, uid, text, reply_markup=kb_sex())
    elif step == "age":
        await state.set_state(ProfileWizard.age)
        text = _profile_header(3) + "🎂 Выбери возраст:"
        await clean_edit(callback, uid, text, reply_markup=kb_age())
    elif step == "height":
        await state.set_state(ProfileWizard.height)
        text = _profile_header(4) + "📏 Выбери рост (см):"
        await clean_edit(callback, uid, text, reply_markup=kb_height())
    elif step == "weight":
        await state.set_state(ProfileWizard.weight)
        text = _profile_header(5) + "⚖️ Выбери вес (кг):"
        await clean_edit(callback, uid, text, reply_markup=kb_weight())
    elif step == "place":
        await state.set_state(ProfileWizard.place)
        text = _profile_header(6) + "🏠 Где тренируешься?"
        await clean_edit(callback, uid, text, reply_markup=kb_place())
    elif step == "exp":
        await state.set_state(ProfileWizard.exp)
        text = _profile_header(7) + "📈 Выбери опыт:"
        await clean_edit(callback, uid, text, reply_markup=kb_exp())
    else:
        await show_main_menu(callback.bot, callback.message.chat.id, uid)

    await callback.answer()


async def cb_profile_goal(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    goal = {"mass": "масса", "cut": "сушка", "fit": "форма"}.get(v, v)
    await update_user(callback.from_user.id, goal=goal)

    await state.set_state(ProfileWizard.sex)
    text = _profile_header(2) + "👤 Выбери пол:"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_sex())
    await callback.answer()


async def cb_profile_sex(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    sex = "м" if v == "m" else "ж"
    await update_user(callback.from_user.id, sex=sex)

    await state.set_state(ProfileWizard.age)
    text = _profile_header(3) + "🎂 Выбери возраст:"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_age())
    await callback.answer()


async def cb_profile_age(callback: CallbackQuery, state: FSMContext):
    age = int(callback.data.split(":")[2])
    await update_user(callback.from_user.id, age=age)

    await state.set_state(ProfileWizard.height)
    text = _profile_header(4) + "📏 Выбери рост (см):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_height())
    await callback.answer()


async def cb_profile_height(callback: CallbackQuery, state: FSMContext):
    h = int(callback.data.split(":")[2])
    await update_user(callback.from_user.id, height=h)

    await state.set_state(ProfileWizard.weight)
    text = _profile_header(5) + "⚖️ Выбери вес (кг):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_weight())
    await callback.answer()


async def cb_profile_weight(callback: CallbackQuery, state: FSMContext):
    w = float(callback.data.split(":")[2])
    await update_user(callback.from_user.id, weight=w)

    await state.set_state(ProfileWizard.place)
    text = _profile_header(6) + "🏠 Где тренируешься?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_place())
    await callback.answer()


async def cb_profile_place(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    place = "дом" if v == "home" else "зал"
    await update_user(callback.from_user.id, place=place)

    await state.set_state(ProfileWizard.exp)
    text = _profile_header(7) + "📈 Выбери опыт:"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_exp())
    await callback.answer()


async def cb_profile_exp(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]

    if v == "0":
        await update_user(callback.from_user.id, exp="0", freq=3)
        await state.clear()
        text = (
            _profile_header(8) +
            "✅ Профиль заполнен!\n\n"
            "Новичку поставил частоту 3×/нед.\n"
            "Теперь можно открыть меню и пользоваться разделами."
        )
        await clean_edit(callback, callback.from_user.id, text, reply_markup=menu_main_kb())
        await callback.answer()
        return

    exp_text = "1-2 года" if v == "mid" else "2+ года"
    await update_user(callback.from_user.id, exp=exp_text)

    await state.set_state(ProfileWizard.freq)
    text = _profile_header(8) + "📅 Сколько тренировок в неделю удобно?"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_freq())
    await callback.answer()


async def cb_profile_freq(callback: CallbackQuery, state: FSMContext):
    f = int(callback.data.split(":")[2])
    await update_user(callback.from_user.id, freq=f)
    await state.clear()

    u = await get_user(callback.from_user.id)
    summary = (
        _profile_header(8) +
        "✅ Профиль сохранён!\n\n"
        f"Цель: {u.get('goal')}\n"
        f"Пол: {u.get('sex')}\n"
        f"Возраст: {u.get('age')}\n"
        f"Рост: {u.get('height')}\n"
        f"Вес: {u.get('weight')}\n"
        f"Где: {u.get('place')}\n"
        f"Опыт: {u.get('exp')}\n"
        f"Частота: {u.get('freq')}×/нед\n\n"
        "Можно переходить к оплате или сразу в меню."
    )
    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, summary, reply_markup=menu_main_kb())
    await callback.answer()


# =========================
# ОПЛАТА
# =========================
def access_status_str(a: dict) -> str:
    if not a or a.get("paid") != 1:
        return "Статус: ❌ нет доступа"
    if a.get("tariff") == "life":
        return "Статус: ✅ доступ активен (НАВСЕГДА)"
    exp = a.get("expires_at")
    return f"Статус: ✅ доступ активен до {exp[:10]}" if exp else "Статус: ✅ доступ активен"


async def open_payment_cb(callback: CallbackQuery, state: FSMContext):
    a = await get_access(callback.from_user.id)

    if await is_access_active(callback.from_user.id):
        await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id,
                        f"✅ Доступ активен.\n{access_status_str(a)}",
                        reply_markup=menu_main_kb())
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
    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, text, reply_markup=pay_tariff_kb())
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
    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, text, reply_markup=pay_inline_kb())
    await callback.answer()


async def cb_i_paid(callback: CallbackQuery, state: FSMContext):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")

    if await is_access_active(callback.from_user.id):
        await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, "✅ У тебя уже активный доступ.", reply_markup=menu_main_kb())
        await callback.answer()
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if not tariff:
        await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, "Сначала выбери тариф в «💳 Оплата/доступ».", reply_markup=menu_main_kb())
        await callback.answer()
        return

    if await has_recent_pending_payment(callback.from_user.id):
        await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, "⏳ У тебя уже есть активная заявка (до 2 часов).", reply_markup=menu_main_kb())
        await callback.answer()
        return

    await clean_send(
        callback.bot, callback.message.chat.id, callback.from_user.id,
        f"Введи сумму, которую перевёл.\nОжидаемая сумма для тарифа «{TARIFFS[tariff]['title']}»: {TARIFFS[tariff]['price']}₽"
    )
    await state.set_state(PaymentFlow.waiting_amount)
    await callback.answer()


async def pay_amount(message: Message, state: FSMContext):
    txt = re.sub(r"[^\d]", "", message.text or "")
    if not txt:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Сумму числом, например 1150")
        return
    await state.update_data(amount=int(txt))
    await clean_send(message.bot, message.chat.id, message.from_user.id, "Введи последние 4 цифры карты отправителя (или 0000):")
    await state.set_state(PaymentFlow.waiting_last4)


async def pay_last4(message: Message, state: FSMContext):
    txt = re.sub(r"[^\d]", "", message.text or "")
    if len(txt) != 4:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Нужно ровно 4 цифры. Например 1234 (или 0000)")
        return
    await state.update_data(last4=txt)
    await clean_send(message.bot, message.chat.id, message.from_user.id, "Отправь чек/скрин оплаты как фото:")
    await state.set_state(PaymentFlow.waiting_receipt)


async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Нужно фото/скрин чека. Отправь как фото.")
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if tariff not in TARIFFS:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Не вижу выбранный тариф. Открой «Оплата/доступ» и выбери тариф заново.", reply_markup=menu_main_kb())
        await state.clear()
        return

    amount = int(data.get("amount", 0))
    last4 = data.get("last4", "0000")
    receipt_file_id = message.photo[-1].file_id
    code = gen_order_code(message.from_user.id)

    payment_id = await create_payment(message.from_user.id, tariff, amount, last4, code, receipt_file_id)
    await clean_send(message.bot, message.chat.id, message.from_user.id, "✅ Заявка отправлена. Как подтвержу — доступ откроется.", reply_markup=menu_main_kb())

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
                "Открой меню и пользуйся разделами 👇"
            ),
            reply_markup=menu_main_kb()
        )
        await callback.answer("Подтверждено ✅")
    else:
        await set_payment_status(pid, "rejected")
        await bot.send_message(
            chat_id=user_id,
            text="❌ Оплата отклонена. Проверь сумму/чек/комментарий и попробуй снова: ⚙️ Панель управления → 💳 Оплата/доступ"
        )
        await callback.answer("Отклонено ❌")


# =========================
# РАЗДЕЛЫ: ТРЕНИРОВКИ / ПИТАНИЕ / ЗАМЕРЫ / ДНЕВНИК
# =========================
async def ensure_profile_ready(user_id: int) -> bool:
    u = await get_user(user_id)
    need = ["goal", "sex", "age", "height", "weight", "place", "exp", "freq"]
    return not any(not u.get(k) for k in need)

async def build_plans_if_needed(user_id: int):
    u = await get_user(user_id)
    workout = generate_workout_plan(
        u["goal"], u["place"], u["exp"], int(u["freq"]),
        user_id=user_id
    )
    # питание сохраняем “как раньше” (общий текст), но выдаём через кнопки примеры
    intro, days = generate_nutrition_intro_and_days(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )
    await save_workout_plan(user_id, workout)
    await save_nutrition_plan(user_id, intro + "\n\n" + "\n\n".join(days))


async def open_workouts_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id

    if not await is_access_active(uid):
        await clean_edit(callback, uid, locked_text(), reply_markup=menu_main_kb())
        return

    if not await ensure_profile_ready(uid):
        await clean_edit(
            callback, uid,
            "⚠️ Сначала заполни профиль.\n⚙️ Панель управления → Профиль",
            reply_markup=menu_main_kb()
        )
        return

    plan = await get_workout_plan(uid)
    if not plan:
        await build_plans_if_needed(uid)
        plan = await get_workout_plan(uid)

    head = "🏋️ Мои тренировки\n\n"
    txt = head + (plan or "План пока не найден.")
    if len(txt) <= TG_SAFE_MSG_LEN:
        await clean_edit(callback, uid, txt, reply_markup=workouts_kb())
    else:
        await clean_send(bot, callback.message.chat.id, uid, head, reply_markup=workouts_kb())
        await safe_send(callback.message, plan or "")
    return


# ✅ ПИТАНИЕ: при нажатии — только текст сверху + 3 кнопки с примерами
async def open_nutrition_cb(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id

    if not await is_access_active(uid):
        await clean_edit(callback, uid, locked_text(), reply_markup=menu_main_kb())
        return

    if not await ensure_profile_ready(uid):
        await clean_edit(
            callback, uid,
            "⚠️ Сначала заполни профиль.\n⚙️ Панель управления → Профиль",
            reply_markup=menu_main_kb()
        )
        return

    u = await get_user(uid)
    intro, _days = generate_nutrition_intro_and_days(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )

    await clean_edit(callback, uid, intro, reply_markup=nutrition_examples_kb())


async def cb_nutrition_menu(callback: CallbackQuery):
    uid = callback.from_user.id
    u = await get_user(uid)
    intro, _days = generate_nutrition_intro_and_days(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )
    await clean_edit(callback, uid, intro, reply_markup=nutrition_examples_kb())
    await callback.answer()


async def cb_nutrition_example(callback: CallbackQuery):
    uid = callback.from_user.id
    idx = int(callback.data.split(":")[2])  # 1..3

    u = await get_user(uid)
    intro, days = generate_nutrition_intro_and_days(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )
    idx = max(1, min(idx, 3))
    text = days[idx - 1].strip()

    # показываем пример в том же сообщении
    await clean_edit(callback, uid, text, reply_markup=nutrition_example_back_kb())
    await callback.answer()


async def open_measures_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    if not await is_access_active(uid):
        await clean_edit(callback, uid, locked_text(), reply_markup=menu_main_kb())
        return

    await state.set_state(MeasureFlow.choose_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Вес (кг)", callback_data="mtype:weight")],
        [InlineKeyboardButton(text="📏 Талия (см)", callback_data="mtype:waist")],
        [InlineKeyboardButton(text="💪 Рука (см)", callback_data="mtype:arm")],
        [InlineKeyboardButton(text="胸 Грудь (см)", callback_data="mtype:chest")],
        [InlineKeyboardButton(text="🦵 Бедро (см)", callback_data="mtype:thigh")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])
    await clean_edit(callback, uid, "📏 Замеры\n\nВыбери, что записать:", reply_markup=kb)


async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await state.set_state(MeasureFlow.enter_value)

    name = {"weight": "Вес (кг)", "waist": "Талия (см)", "arm": "Рука (см)", "chest": "Грудь (см)", "thigh": "Бедро (см)"}.get(mtype, mtype)
    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, f"Введи значение для «{name}» числом (например 72.5):")
    await callback.answer()


async def measure_value(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        val = float(txt)
    except Exception:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Нужно число. Например 72.5")
        return

    data = await state.get_data()
    mtype = data.get("mtype")

    await add_measure(message.from_user.id, mtype, val)
    rows = await get_last_measures(message.from_user.id, mtype, 6)

    name = {"weight": "Вес", "waist": "Талия", "arm": "Рука", "chest": "Грудь", "thigh": "Бедро"}.get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    await clean_send(message.bot, message.chat.id, message.from_user.id, f"✅ Записал: {name} = {val:g}\n\nПоследние:\n{hist}", reply_markup=menu_main_kb())
    await state.clear()


# =========================
# ✅ ДНЕВНИК: кнопки базовых упражнений → дата сама → вводишь только вес
# =========================
BASE_EXERCISES = [
    ("Присед", "dq:Присед"),
    ("Жим лёжа", "dq:Жим лёжа"),
    ("Румынская тяга", "dq:Румынская тяга"),
    ("Тяга горизонтальная", "dq:Тяга горизонтальная"),
    ("Верхний блок", "dq:Верхний блок"),
    ("Подтягивания", "dq:Подтягивания"),
    ("Жим вверх", "dq:Жим вверх"),
    ("Жим ногами", "dq:Жим ногами"),
    ("Выпады", "dq:Выпады"),
    ("Отжимания", "dq:Отжимания"),
]

def diary_quick_kb():
    rows = []
    # по 2 в ряд
    for i in range(0, len(BASE_EXERCISES), 2):
        a = BASE_EXERCISES[i]
        b = BASE_EXERCISES[i+1] if i+1 < len(BASE_EXERCISES) else None
        if b:
            rows.append([InlineKeyboardButton(text=a[0], callback_data=a[1]),
                         InlineKeyboardButton(text=b[0], callback_data=b[1])])
        else:
            rows.append([InlineKeyboardButton(text=a[0], callback_data=a[1])])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:diary")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_diary_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    if not await is_access_active(uid):
        await clean_edit(callback, uid, locked_text(), reply_markup=menu_main_kb())
        return

    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Быстрая запись (только вес)", callback_data="d:quick")],
        [InlineKeyboardButton(text="➕ Записать тренировку (вручную)", callback_data="d:new")],
        [InlineKeyboardButton(text="📜 История (последние 10)", callback_data="d:history")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
    ])
    await clean_edit(callback, uid, "📓 Дневник\n\nВыбери действие:", reply_markup=kb)


async def diary_quick_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(DiaryQuickFlow.choose_exercise)
    await clean_edit(callback, callback.from_user.id, "⚡ Быстрая запись\n\nВыбери упражнение:", reply_markup=diary_quick_kb())
    await callback.answer()


async def diary_quick_choose(callback: CallbackQuery, state: FSMContext):
    # callback.data = dq:<exercise>
    ex = callback.data.split("dq:", 1)[1].strip()
    await state.update_data(exercise=ex)
    await state.set_state(DiaryQuickFlow.enter_weight)

    today = datetime.now().strftime("%Y-%m-%d")
    await clean_send(
        callback.bot, callback.message.chat.id, callback.from_user.id,
        f"🗓 Дата: {today}\n🏋️ Упражнение: {ex}\n\nВведи только вес (например 80 или 27.5):"
    )
    await callback.answer()


async def diary_quick_weight(message: Message, state: FSMContext):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        w = float(txt)
    except Exception:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Нужно число (например 80 или 27.5).")
        return

    data = await state.get_data()
    ex = (data.get("exercise") or "").strip()
    if not ex:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Не вижу упражнение. Открой дневник заново.", reply_markup=menu_main_kb())
        await state.clear()
        return

    today = datetime.now().strftime("%Y-%m-%d")
    session_id = await find_or_create_quick_session(message.from_user.id, today)
    sn = await next_set_no(session_id, ex)

    # reps = 0 (ты просил вводить только вес)
    await add_set(session_id, ex, sn, w, 0)

    await clean_send(
        message.bot, message.chat.id, message.from_user.id,
        f"✅ Записал: {ex} — подход {sn}: {w:g} кг\n\nХочешь ещё? Выбери упражнение кнопкой ниже 👇",
        reply_markup=diary_quick_kb()
    )
    await state.set_state(DiaryQuickFlow.choose_exercise)


async def diary_new(callback: CallbackQuery, state: FSMContext):
    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, "Введи дату (YYYY-MM-DD) или напиши: сегодня")
    await state.set_state(DiaryFlow.choose_day)
    await callback.answer()


async def diary_choose_day(message: Message, state: FSMContext):
    t = (message.text or "").strip().lower()
    if t == "сегодня":
        d = datetime.now().strftime("%Y-%m-%d")
    else:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            await clean_send(message.bot, message.chat.id, message.from_user.id, "Формат даты: 2026-02-13 или слово 'сегодня'")
            return
        d = t
    await state.update_data(session_date=d)
    await clean_send(message.bot, message.chat.id, message.from_user.id, "Название тренировки? (например: День 1 / Push / Ноги)")
    await state.set_state(DiaryFlow.enter_title)


async def diary_enter_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Напиши название (например: День 1)")
        return
    data = await state.get_data()
    session_id = await create_diary_session(message.from_user.id, data["session_date"], title)
    await state.update_data(session_id=session_id)

    await clean_send(
        message.bot, message.chat.id, message.from_user.id,
        "Теперь вводи упражнения по одному.\n"
        "Формат: Упражнение: 60x8, 60x8, 60x7\n"
        "Когда закончишь — напиши: стоп"
    )
    await state.set_state(DiaryFlow.enter_sets)


async def diary_enter_sets(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() == "стоп":
        await clean_send(message.bot, message.chat.id, message.from_user.id, "✅ Тренировка записана.", reply_markup=menu_main_kb())
        await state.clear()
        return

    if ":" not in txt:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Нужен формат: Упражнение: 60x8, 60x8")
        return

    ex, sets_str = [x.strip() for x in txt.split(":", 1)]
    parts = [p.strip() for p in sets_str.split(",") if p.strip()]
    if not ex or not parts:
        await clean_send(message.bot, message.chat.id, message.from_user.id, "Заполни и название, и подходы. Пример: Жим: 60x8, 60x8")
        return

    data = await state.get_data()
    session_id = data["session_id"]

    for i, p in enumerate(parts, start=1):
        m = re.match(r"^(\d+(\.\d+)?)\s*[xх]\s*(\d+)$", p.lower())
        if not m:
            await clean_send(message.bot, message.chat.id, message.from_user.id, f"Не понял подход: '{p}'. Пример: 60x8")
            return
        w = float(m.group(1))
        r = int(m.group(3))
        await add_set(session_id, ex, i, w, r)

    await clean_send(message.bot, message.chat.id, message.from_user.id, f"✅ Записал: {ex} ({len(parts)} подходов). Следующее упражнение или 'стоп'.")


async def diary_history(callback: CallbackQuery):
    history = await get_diary_history(callback.from_user.id, 10)
    if not history:
        await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, "Истории пока нет. Нажми «➕ Записать тренировку»", reply_markup=menu_main_kb())
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
            # reps может быть 0 (из быстрого дневника)
            if reps and int(reps) > 0:
                line += f"{w:g}x{int(reps)}  "
            else:
                line += f"{w:g}кг  "
        if line:
            msg += line + "\n"
        msg += "\n"

    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, msg, reply_markup=simple_back_to_menu_kb())
    await callback.answer()


# =========================
# ПОДДЕРЖКА
# =========================
async def open_support_cb(callback: CallbackQuery):
    text = (
        "🆘 Поддержка\n\n"
        "Напиши проблему одним сообщением — я перешлю админу.\n"
        "Если есть ошибка в консоли/логах — пришли текст."
    )
    await clean_send(callback.bot, callback.message.chat.id, callback.from_user.id, text, reply_markup=menu_main_kb())


async def forward_to_admin(message: Message, bot: Bot):
    if message.from_user.id == ADMIN_ID:
        return
    if not message.text or message.text.startswith("/"):
        return
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📩 Поддержка от @{message.from_user.username or 'no_username'} (id={message.from_user.id}):\n\n{message.text}"
    )


# =========================
# ✅ ТЕХНИКИ: ХЕНДЛЕРЫ (с картинками) + анти-засорение
# =========================
async def cb_tech_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await clean_edit(callback, callback.from_user.id, "📚 Техники выполнения — выбери упражнение:", reply_markup=tech_kb())
    await callback.answer()


async def cb_tech_show(callback: CallbackQuery, bot: Bot):
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
        # вместо message.answer_photo (которая плодит) — удалим прошлое сообщение бота и отправим новое
        await clean_send_photo(bot, callback.message.chat.id, callback.from_user.id, photo=photo, caption=caption, reply_markup=tech_back_kb())
        if rest:
            await clean_send(bot, callback.message.chat.id, callback.from_user.id, rest, reply_markup=tech_back_kb())
    else:
        await clean_send(bot, callback.message.chat.id, callback.from_user.id, text, reply_markup=tech_back_kb())

    await callback.answer()


# =========================
# ✅ НОВОЕ: ПОСТЫ С КАРТИНКАМИ (АДМИН) — оставил как было
# =========================
def admin_posts_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать пост", callback_data="post:new")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:main")],
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
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
def setup_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, CommandStart())

    # меню
    dp.callback_query.register(cb_menu, F.data.startswith("menu:"))

    # reply-кнопки панели управления (внизу)
    dp.message.register(ctrl_text_buttons, F.text.in_({"💳 Оплата/доступ", "⚙️ Профиль", "🆘 Поддержка", "🏠 Меню"}))

    # профиль мастер
    dp.callback_query.register(cb_profile_back, F.data.startswith("p:back:"))
    dp.callback_query.register(cb_profile_goal, F.data.startswith("p:goal:"))
    dp.callback_query.register(cb_profile_sex, F.data.startswith("p:sex:"))
    dp.callback_query.register(cb_profile_age, F.data.startswith("p:age:"))
    dp.callback_query.register(cb_profile_height, F.data.startswith("p:h:"))
    dp.callback_query.register(cb_profile_weight, F.data.startswith("p:w:"))
    dp.callback_query.register(cb_profile_place, F.data.startswith("p:place:"))
    dp.callback_query.register(cb_profile_exp, F.data.startswith("p:exp:"))
    dp.callback_query.register(cb_profile_freq, F.data.startswith("p:freq:"))

    # оплата
    dp.callback_query.register(cb_tariff, F.data.startswith("tariff:"))
    dp.callback_query.register(cb_i_paid, F.data == "pay_i_paid")
    dp.callback_query.register(admin_actions, F.data.startswith("admin_approve:") | F.data.startswith("admin_reject:"))
    dp.message.register(pay_amount, PaymentFlow.waiting_amount)
    dp.message.register(pay_last4, PaymentFlow.waiting_last4)
    dp.message.register(pay_receipt, PaymentFlow.waiting_receipt)

    # дневник
    dp.callback_query.register(diary_quick_start, F.data == "d:quick")
    dp.callback_query.register(diary_quick_choose, F.data.startswith("dq:"))
    dp.message.register(diary_quick_weight, DiaryQuickFlow.enter_weight)

    dp.callback_query.register(diary_new, F.data == "d:new")
    dp.callback_query.register(diary_history, F.data == "d:history")
    dp.message.register(diary_choose_day, DiaryFlow.choose_day)
    dp.message.register(diary_enter_title, DiaryFlow.enter_title)
    dp.message.register(diary_enter_sets, DiaryFlow.enter_sets)

    # замеры
    dp.callback_query.register(cb_measure_type, F.data.startswith("mtype:"))
    dp.message.register(measure_value, MeasureFlow.enter_value)

    # техники
    dp.callback_query.register(cb_tech_list, F.data == "tech:list")
    dp.callback_query.register(cb_tech_show, F.data.startswith("tech:"))

    # питание (3 кнопки)
    dp.callback_query.register(cb_nutrition_menu, F.data == "nutr:menu")
    dp.callback_query.register(cb_nutrition_example, F.data.startswith("nutr:ex:"))

    # админ посты
    dp.message.register(cmd_posts, Command("posts"))
    dp.callback_query.register(cb_post_new, F.data == "post:new")
    dp.callback_query.register(cb_post_cancel, F.data == "post:cancel")
    dp.callback_query.register(cb_post_send, F.data.startswith("post:send:"))
    dp.message.register(post_waiting_content, PostFlow.waiting_content)

    # поддержка (любой текст пользователи -> админу)
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
