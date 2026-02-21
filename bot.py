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
    choosing_exercise = State()
    enter_sets = State()


class MeasureFlow(StatesGroup):
    choose_type = State()
    enter_value = State()
    history_pick_type = State()


class PostFlow(StatesGroup):
    waiting_content = State()
    waiting_confirm = State()


class ProfileWizard(StatesGroup):
    goal = State()
    sex = State()
    age = State()      # ввод текстом
    height = State()   # ввод текстом
    weight = State()   # ввод текстом
    place = State()
    exp = State()
    freq = State()
    limits = State()   # ограничения (текст)


# =========================
# ✅ ТЕХНИКИ ВЫПОЛНЕНИЯ (картинка + текст) — СДЕЛАНО ПОДРОБНЕЕ И ПОНЯТНЕЕ
# =========================
TECH = {
    "squat": {
        "title": "Присед",
        "img": "media/tech/squat.jpg",
        "text": (
            "📚 Присед со штангой\n\n"
            "🎯 Что качает: квадрицепс, ягодицы, стабилизация корпуса.\n\n"
            "✅ Настройка (до снятия штанги):\n"
            "1) Гриф на трапециях (высокая постановка) или чуть ниже (низкая) — выбери, где стабильнее.\n"
            "2) Лопатки СВЕДИ и «прижми» гриф спиной — чтобы он не гулял.\n"
            "3) Стопы: ширина плеч/чуть шире, носки слегка наружу.\n"
            "4) Вдох животом → напряги пресс и поясницу (корпус «камень»).\n\n"
            "⬇️ Опускание:\n"
            "1) Начни одновременно: таз чуть назад + колени по линии носков.\n"
            "2) Дави стопой в пол: пятка + основание большого + малого пальца (треножник).\n"
            "3) Спина ровная, грудь не падает, шея нейтрально.\n"
            "4) Глубина: до комфортной, где таз не «подкручивается» и спина остаётся ровной.\n\n"
            "⬆️ Подъём:\n"
            "1) Отталкивайся серединой стопы, НЕ уходи на носки.\n"
            "2) Колени продолжают идти по носкам (не заваливай внутрь).\n"
            "3) Вставай без рывка, корпус жёсткий.\n\n"
            "🫁 Дыхание:\n"
            "• Вдох перед опусканием → держишь пресс → выдох после прохождения самой тяжёлой точки.\n\n"
            "❌ Частые ошибки:\n"
            "• колени внутрь\n"
            "• пятки отрываются\n"
            "• спина округляется\n"
            "• слишком быстрый низ/рывок вверх\n\n"
            "🧠 Короткие подсказки:\n"
            "• «Колени по носкам»\n"
            "• «Пресс камень»\n"
            "• «Штанга над серединой стопы»"
        )
    },
    "bench": {
        "title": "Жим лёжа",
        "img": "media/tech/bench.jpg",
        "text": (
            "📚 Жим лёжа\n\n"
            "🎯 Что качает: грудь, трицепс, передняя дельта.\n\n"
            "✅ Настройка:\n"
            "1) Глаза под грифом.\n"
            "2) Лопатки СВЕДИ и ОПУСТИ вниз (как будто прячешь их в карманы).\n"
            "3) Грудь чуть вверх, поясница с лёгким прогибом — таз НЕ отрывается.\n"
            "4) Ноги жёстко в пол — создают стабильность.\n\n"
            "🤲 Хват:\n"
            "• Обычно чуть шире плеч.\n"
            "• Запястья ровные: гриф ближе к основанию ладони.\n\n"
            "⬇️ Опускание:\n"
            "1) Веди гриф к нижней части груди (примерно линия сосков/чуть ниже).\n"
            "2) Локти под углом ~45° к корпусу (не разводи в стороны).\n\n"
            "⬆️ Жим:\n"
            "1) Жми «вверх и чуть назад» к стойкам.\n"
            "2) Лопатки НЕ теряй — они должны быть сведены весь подход.\n\n"
            "🫁 Дыхание:\n"
            "• Вдох перед опусканием → задержка → выдох после середины подъёма.\n\n"
            "❌ Ошибки:\n"
            "• локти слишком в стороны\n"
            "• ягодицы отрываются\n"
            "• плечи выезжают вперёд (лопатки разъезжаются)\n"
            "• гриф гуляет по траектории\n\n"
            "🧠 Подсказки:\n"
            "• «Лопатки в карманы»\n"
            "• «Локти 45°»\n"
            "• «Ноги — опора»"
        )
    },
    "row": {
        "title": "Тяга (гребля)",
        "img": "media/tech/row.jpg",
        "text": (
            "📚 Тяга горизонтального блока / гребля\n\n"
            "🎯 Что качает: широчайшие, середина спины, ромбовидные.\n\n"
            "✅ Настройка:\n"
            "1) Сядь устойчиво, стопы упираются.\n"
            "2) Спина ровная, грудь чуть вперёд.\n"
            "3) Плечи вниз — не поднимай к ушам.\n\n"
            "➡️ Тяга:\n"
            "1) Начни лопатками: слегка сведи/опусти.\n"
            "2) Затем тяни локти назад вдоль корпуса.\n"
            "3) В конце 0.5–1 сек пауза — почувствуй спину.\n\n"
            "⬅️ Возврат:\n"
            "1) Руки вперёд плавно.\n"
            "2) Корпус не падает, не раскачивайся.\n\n"
            "❌ Ошибки:\n"
            "• раскачка корпусом\n"
            "• плечи к ушам\n"
            "• тянешь руками без включения спины\n\n"
            "🧠 Подсказки:\n"
            "• «Локти назад, не кисти»\n"
            "• «Плечи вниз»\n"
            "• «Без рывков»"
        )
    },
    "latpulldown": {
        "title": "Верхний блок",
        "img": "media/tech/latpulldown.jpg",
        "text": (
            "📚 Тяга верхнего блока\n\n"
            "🎯 Что качает: широчайшие, верх спины, бицепс как помощник.\n\n"
            "✅ Настройка:\n"
            "1) Зафиксируй бедра под валиками.\n"
            "2) Хват чуть шире плеч.\n"
            "3) Грудь вверх, плечи вниз.\n\n"
            "⬇️ Тяга:\n"
            "1) Сначала «опусти лопатки» (как мини-подтягивание).\n"
            "2) Потом веди локти вниз и назад.\n"
            "3) Гриф тянем к верхней части груди.\n\n"
            "⬆️ Возврат:\n"
            "• Поднимаем плавно, не бросаем вес.\n\n"
            "❌ Ошибки:\n"
            "• тяга за голову\n"
            "• сильная раскачка\n"
            "• плечи поднимаются к ушам\n\n"
            "🧠 Подсказки:\n"
            "• «Гриф к груди»\n"
            "• «Локти вниз»\n"
            "• «Плечи вниз»"
        )
    },
    "pullup": {
        "title": "Подтягивания",
        "img": "media/tech/pullup.jpg",
        "text": (
            "📚 Подтягивания\n\n"
            "🎯 Что качает: широчайшие, бицепс, мышцы лопаток.\n\n"
            "✅ Старт:\n"
            "1) Хват на ширине плеч/чуть шире.\n"
            "2) Корпус напряжён, без раскачки.\n"
            "3) Первое движение — лопатки вниз и к позвоночнику.\n\n"
            "⬆️ Подъём:\n"
            "1) Тяни локти к рёбрам.\n"
            "2) Подбородок проходит перекладину без «клюва» шеей.\n\n"
            "⬇️ Опускание:\n"
            "• Медленно вниз до почти прямых рук (контроль важнее).\n\n"
            "❌ Ошибки:\n"
            "• раскачка/киппинг\n"
            "• плечи к ушам\n"
            "• «пол-амплитуды» всегда\n\n"
            "🧠 Подсказки:\n"
            "• «Лопатки вниз»\n"
            "• «Локти к рёбрам»\n"
            "• «Контроль вниз»"
        )
    },
    "rdl": {
        "title": "Румынская тяга",
        "img": "media/tech/rdl.jpg",
        "text": (
            "📚 Румынская тяга\n\n"
            "🎯 Что качает: ягодицы, задняя поверхность бедра, стабилизация спины.\n\n"
            "✅ Настройка:\n"
            "1) Стопы на ширине таза.\n"
            "2) Колени чуть согнуты и почти фиксированы.\n"
            "3) Пресс напряжён, спина ровная.\n\n"
            "⬇️ Наклон:\n"
            "1) Уводи таз назад (как будто закрываешь дверь попой).\n"
            "2) Гриф скользит близко по ногам.\n"
            "3) Опускайся до натяжения задней поверхности бедра (обычно до середины голени).\n\n"
            "⬆️ Подъём:\n"
            "1) Толкай таз вперёд, сжимай ягодицы.\n"
            "2) Не перегибайся в пояснице вверху.\n\n"
            "❌ Ошибки:\n"
            "• округление поясницы\n"
            "• гриф далеко от ног\n"
            "• превращение в присед (слишком сгибаешь колени)\n\n"
            "🧠 Подсказки:\n"
            "• «Таз назад»\n"
            "• «Гриф по ногам»\n"
            "• «Спина ровная»"
        )
    },
    "ohp": {
        "title": "Жим вверх",
        "img": "media/tech/ohp.jpg",
        "text": (
            "📚 Жим штанги/гантелей вверх\n\n"
            "🎯 Что качает: дельты, трицепс, верх груди (частично).\n\n"
            "✅ Настройка:\n"
            "1) Стопы на ширине плеч.\n"
            "2) Пресс и ягодицы напряжены (чтобы не прогибаться).\n"
            "3) Локти чуть впереди грифа.\n\n"
            "⬆️ Жим:\n"
            "1) Жми вверх по линии лица: чуть убери голову назад, чтобы гриф прошёл.\n"
            "2) Вверху — голова «в окно» (под гриф), корпус ровный.\n\n"
            "❌ Ошибки:\n"
            "• сильный прогиб в пояснице\n"
            "• гриф уходит далеко вперёд\n"
            "• локти разлетаются в стороны\n\n"
            "🧠 Подсказки:\n"
            "• «Рёбра вниз»\n"
            "• «Пресс камень»\n"
            "• «Гриф близко к лицу»"
        )
    },
    "lateralraise": {
        "title": "Разведения в стороны",
        "img": "media/tech/lateralraise.jpg",
        "text": (
            "📚 Разведения гантелей в стороны\n\n"
            "🎯 Что качает: средняя дельта (ширина плеч).\n\n"
            "✅ Техника:\n"
            "1) Локоть слегка согнут.\n"
            "2) Плечи опущены, шея расслаблена.\n"
            "3) Поднимай локоть чуть выше кисти, до уровня плеч.\n"
            "4) Вниз медленно, без броска.\n\n"
            "❌ Ошибки:\n"
            "• махи корпусом\n"
            "• подъём слишком высоко с потерей контроля\n"
            "• плечи к ушам\n\n"
            "🧠 Подсказки:\n"
            "• «Локоть ведёт»\n"
            "• «Плечи вниз»\n"
            "• «Контроль вниз»"
        )
    },
    "biceps": {
        "title": "Бицепс сгибания",
        "img": "media/tech/biceps.jpg",
        "text": (
            "📚 Сгибания на бицепс\n\n"
            "🎯 Что качает: бицепс (основная), предплечья (вторично).\n\n"
            "✅ Техника:\n"
            "1) Локти прижаты к корпусу и почти не двигаются.\n"
            "2) Поднимай вес контролируемо, без раскачки.\n"
            "3) Вверху — короткая пауза.\n"
            "4) Опускай медленно 2–3 секунды.\n\n"
            "❌ Ошибки:\n"
            "• читинг корпусом\n"
            "• локти улетают вперед\n"
            "• слишком большой вес\n\n"
            "🧠 Подсказки:\n"
            "• «Локти на месте»\n"
            "• «Медленно вниз»"
        )
    },
    "triceps": {
        "title": "Трицепс на блоке",
        "img": "media/tech/triceps.jpg",
        "text": (
            "📚 Разгибания на трицепс (блок)\n\n"
            "🎯 Что качает: трицепс.\n\n"
            "✅ Техника:\n"
            "1) Локти прижаты к корпусу.\n"
            "2) Разгибай до конца без рывка.\n"
            "3) Внизу пауза 0.5 сек.\n"
            "4) Вверх медленно, локти не поднимай.\n\n"
            "❌ Ошибки:\n"
            "• локти расходятся\n"
            "• корпус раскачивается\n"
            "• плечи уходят вперед\n\n"
            "🧠 Подсказки:\n"
            "• «Локти прижаты»\n"
            "• «Пауза внизу»"
        )
    },
    "legpress": {
        "title": "Жим ногами",
        "img": "media/tech/legpress.jpg",
        "text": (
            "📚 Жим ногами\n\n"
            "🎯 Что качает: квадрицепс, ягодицы (в зависимости от постановки стоп).\n\n"
            "✅ Настройка:\n"
            "1) Поясница прижата к спинке.\n"
            "2) Стопы обычно чуть шире плеч.\n"
            "3) Колени идут по направлению носков.\n\n"
            "⬇️ Опускание:\n"
            "1) До комфортной глубины.\n"
            "2) Поясницу не отрывай.\n\n"
            "⬆️ Жим:\n"
            "1) Жми через пятки/середину стопы.\n"
            "2) Не «вбивай» колени в полный замок резко.\n\n"
            "❌ Ошибки:\n"
            "• отрыв поясницы\n"
            "• колени внутрь\n"
            "• короткая амплитуда всегда\n\n"
            "🧠 Подсказки:\n"
            "• «Поясница прижата»\n"
            "• «Колени по носкам»"
        )
    },
}


def tech_list_kb():
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

        # ✅ чтобы можно было вернуться обратно к тренировкам
        [InlineKeyboardButton(text="⬅️ Назад к тренировкам", callback_data="tech:back_workouts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tech_back_kb():
    # ✅ оставляем только одну кнопку как ты попросил
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="tech:list")],
    ])


# =========================
# ✅ ПАНЕЛЬ УПРАВЛЕНИЯ (ReplyKeyboard) — ВЕРНУЛИ
# =========================
def control_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚙️ Профиль"), KeyboardButton(text="🏠 Меню")],
            [KeyboardButton(text="💳 Оплата/доступ"), KeyboardButton(text="🆘 Поддержка")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие снизу 👇"
    )


# =========================
# Inline: меню разделов (без панели управления)
# =========================
def menu_main_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️ Мои тренировки", callback_data="nav:workouts")],
        [InlineKeyboardButton(text="🍽 Моё питание", callback_data="nav:nutrition")],
        [InlineKeyboardButton(text="📏 Замеры", callback_data="nav:measures")],
        [InlineKeyboardButton(text="📓 Дневник", callback_data="nav:diary")],
    ])


def workouts_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Техники выполнения упражнений", callback_data="tech:list")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def simple_back_to_menu_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
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
# Профиль: шкала "■■■□□ 100%" + ввод текстом
# =========================
TOTAL_PROFILE_STEPS = 9  # goal, sex, age, height, weight, place, exp, freq, limits


def _bar(step: int, total: int = TOTAL_PROFILE_STEPS) -> str:
    step = max(0, min(step, total))
    done = "■" * step
    left = "□" * (total - step)
    pct = int(round(step / total * 100)) if total else 0
    return f"{done}{left} {pct}%"


def _profile_header(step: int) -> str:
    return f"🧩 Заполнение профиля {step}/{TOTAL_PROFILE_STEPS}\n{_bar(step)}\n\n"


def profile_done_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️🍽 Перейти к тренировкам и питанию", callback_data="nav:menu")]
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


def kb_back_menu(back_to: str):
    # back_to: goal/sex/age/height/weight/place/exp/freq
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:back:{back_to}")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def kb_place():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Дом", callback_data="p:place:home"),
         InlineKeyboardButton(text="🏋️ Зал", callback_data="p:place:gym")],
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


# =========================
# УТИЛИТЫ
# =========================
def gen_order_code(user_id: int) -> str:
    rnd = random.randint(100, 999)
    return f"TG{str(user_id)[-3:]}{rnd}"


def locked_text() -> str:
    return "🔒 Раздел доступен после оплаты.\nНажми снизу: 💳 Оплата/доступ"


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
            SELECT user_id, username, goal, sex, age, height, weight, place, exp, freq, limits
            FROM users WHERE user_id=?
        """, (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return {}
    return {
        "user_id": row[0], "username": row[1], "goal": row[2], "sex": row[3],
        "age": row[4], "height": row[5], "weight": row[6], "place": row[7],
        "exp": row[8], "freq": row[9], "limits": row[10]
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
# ТРЕНИРОВКИ — формат как ты просил + учет ограничений
# =========================
def _pick(rnd: random.Random, items: List[str]) -> str:
    items = [x for x in items if x]
    if not items:
        return "—"
    return rnd.choice(items)


def _limits_flags(limits: str) -> dict:
    t = (limits or "").lower()
    return {
        "back": ("спин" in t) or ("поясн" in t),
        "knee": ("колен" in t) or ("мениск" in t),
        "shoulder": ("плеч" in t) or ("ротатор" in t) or ("сустав" in t),
        "elbow": ("локт" in t),
    }


def generate_workout_plan(goal: str, place: str, exp: str, freq: int, limits: str = "", user_id: int = 0) -> str:
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym") or ("gym" in pl)
    where = "ЗАЛ" if is_gym else "ДОМ"

    lvl = exp_level(exp)
    seed = (user_id or 0) + int(datetime.utcnow().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    flags = _limits_flags(limits)

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

    # ✅ фильтры под ограничения (простые и понятные)
    if flags["back"]:
        legs_base = [x for x in legs_base if "Румын" not in x and "тяга" not in x.lower()]
        core = [x for x in core if "Подъёмы ног" not in x]
        if is_gym:
            legs_base = legs_base + ["Разгибания ног (лёгко)"]
        else:
            legs_base = legs_base + ["Ягодичный мост"]
    if flags["knee"]:
        legs_base = [x for x in legs_base if "Присед" not in x and "Жим ногами" not in x]
        legs_iso = [x for x in legs_iso if "Разгибания" not in x]
        legs_base = legs_base + (["Ягодичный мост", "Румынская тяга (лёгкая)"] if is_gym else ["Ягодичный мост"])
    if flags["shoulder"]:
        push_base = [x for x in push_base if "Жим" not in x] + ["Отжимания (комфортно)"]
        shoulders_iso = [x for x in shoulders_iso if "Разведения" not in x] + ["Тяга каната к лицу (лёгко)"]

    reps_base = "6–10" if lvl != "novice" else "8–12"
    reps_iso = "10–15"
    base_sets = "3–4" if lvl != "novice" else "3"
    iso_sets = "3"

    f = int(freq or 3)
    f = max(3, min(f, 5))

    g = (goal or "").lower()
    if "суш" in g:
        note = "Сушка: держи 1–2 повтора в запасе (RIR 1–2), отказ редко."
    elif "мас" in g:
        note = "Масса: прогрессируй по повторам/весу, отказ редко, техника важнее."
    else:
        note = "Форма: прогрессируй плавно, без постоянного отказа."

    # ✅ формат как ты просил: "День X" + список буллетов
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

        bullets = [
            f"• {push} — {base_sets}×{reps_base}",
            f"• {pull} — {base_sets}×{reps_base}",
            f"• {legs} — {base_sets}×{reps_base}",
            f"• {sh} — {iso_sets}×{reps_iso}",
            f"• {bi} — {iso_sets}×{reps_iso}",
            f"• {tri} — {iso_sets}×{reps_iso}",
        ]
        if f >= 4:
            bullets.append(f"• {lg} — {iso_sets}×{reps_iso}")
        if f >= 5:
            bullets.append(f"• {cr} — {iso_sets}×12–20")

        day_text = f"День {d + 1}\n\n" + "\n".join(bullets) + "\n\n"
        days.append(day_text)

    limits_line = f"Ограничения: {limits.strip()}" if (limits or "").strip() else "Ограничения: нет"
    return (
        f"🏋️ ТРЕНИРОВКИ ({where}) — {f}×/нед\n\n"
        f"Цель: {goal}\n"
        f"{limits_line}\n"
        f"{note}\n\n"
        "📌 Прогрессия:\n"
        "1) Доводи подходы до верхней границы повторов\n"
        "2) Потом добавляй вес (+2.5–5%) и снова работай в диапазоне\n"
        "3) Если техника ломается — вес не повышай\n"
        "4) Если усталость копится 7–10 дней — сделай неделю легче (-20–30% объёма)\n\n"
        + "".join(days)
    )


# =========================
# ПИТАНИЕ (однотипное, простое) + ✅ 3 кнопки примеров
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

def build_meal_day_text(day_i: int, calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> str:
    day_meals, tot = _build_day_items(meals, calories, protein_g, fat_g, carbs_g)

    lines = [f"📅 Пример {day_i}", ""]
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
    return "\n".join(lines)

def nutrition_examples_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥣 Пример 1", callback_data="nutr:ex:1")],
        [InlineKeyboardButton(text="🍗 Пример 2", callback_data="nutr:ex:2")],
        [InlineKeyboardButton(text="🍚 Пример 3", callback_data="nutr:ex:3")],
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
        f"Калории: ~{calories} ккал/день\n"
        f"БЖУ (ориентир): Б {p}г / Ж {f}г / У {c}г\n"
        f"Приёмов пищи: {meals}\n\n"
        "Нажми на пример ниже — открою готовый вариант на день.\n"
        "⚠️ Крупы в плане указаны в СУХОМ виде. Масло считай всегда."
    )
    return summary, calories, p, f, c, meals


# =========================
# МЕНЮ / START (✅ приветственный текст + управление снизу)
# =========================
async def show_main_menu(bot: Bot, chat_id: int, user_id: int):
    text = (
        "👋 Привет! Я твой «Зелёный тренер».\n\n"
        "Я помогу:\n"
        "• составить тренировки под твою цель\n"
        "• дать простое питание (без запар)\n"
        "• вести дневник тренировок и замеры, чтобы видеть прогресс\n\n"
        "Выбирай раздел ниже 👇\n\n"
        "ℹ️ Управление (оплата/профиль/поддержка) — на клавиатуре снизу."
    )
    await clean_send(bot, chat_id, user_id, text, reply_markup=menu_main_inline_kb())


async def cmd_start(message: Message, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")

    # ✅ тихо ставим ReplyKeyboard и удаляем сообщение, чтобы не было лишней строки
    tmp = await bot.send_message(
        chat_id=message.chat.id,
        text=".",
        reply_markup=control_reply_kb()
    )
    try:
        await bot.delete_message(message.chat.id, tmp.message_id)
    except Exception:
        pass

    await show_main_menu(bot, message.chat.id, message.from_user.id)
    await try_delete_user_message(bot, message)


# =========================
# ✅ Навигация по inline (меню/разделы)
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
# ✅ Панель управления — ReplyKeyboard
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
            "Выбери тариф:\n"
            f"• 1 месяц — {TARIFFS['t1']['price']}₽\n"
            f"• 3 месяца — {TARIFFS['t3']['price']}₽\n"
            f"• навсегда — {TARIFFS['life']['price']}₽\n\n"
            "После выбора я покажу реквизиты и код для комментария."
        )
        await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=pay_tariff_kb())
        await state.set_state(PaymentFlow.choose_tariff)

    await try_delete_user_message(bot, message)


async def open_profile_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await try_delete_user_message(bot, message)

    await state.clear()
    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "🎯 Выбери цель:"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_goal())


async def open_support_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    text = (
        "🆘 Поддержка\n\n"
        "Напиши проблему одним сообщением — я перешлю админу.\n"
        "Если есть ошибка в консоли/логах — пришли текст."
    )
    await clean_send(bot, message.chat.id, message.from_user.id, text)
    await try_delete_user_message(bot, message)


async def open_menu_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()
    await show_main_menu(bot, message.chat.id, message.from_user.id)
    await try_delete_user_message(bot, message)


# =========================
# ПРОФИЛЬ-МАСТЕР: одно сообщение редактируется + шкала
# =========================
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
        text = _profile_header(3) + "🎂 Введи возраст (числом):"
        await clean_edit(callback, uid, text, reply_markup=kb_back_menu("sex"))
    elif step == "height":
        await state.set_state(ProfileWizard.height)
        text = _profile_header(4) + "📏 Введи рост в см (числом):"
        await clean_edit(callback, uid, text, reply_markup=kb_back_menu("age"))
    elif step == "weight":
        await state.set_state(ProfileWizard.weight)
        text = _profile_header(5) + "⚖️ Введи вес в кг (числом):"
        await clean_edit(callback, uid, text, reply_markup=kb_back_menu("height"))
    elif step == "place":
        await state.set_state(ProfileWizard.place)
        text = _profile_header(6) + "🏠 Где тренируешься?"
        await clean_edit(callback, uid, text, reply_markup=kb_place())
    elif step == "exp":
        await state.set_state(ProfileWizard.exp)
        text = _profile_header(7) + "📈 Выбери опыт:"
        await clean_edit(callback, uid, text, reply_markup=kb_exp())
    elif step == "freq":
        await state.set_state(ProfileWizard.freq)
        text = _profile_header(8) + "📅 Сколько тренировок в неделю удобно?"
        await clean_edit(callback, uid, text, reply_markup=kb_freq())
    elif step == "limits":
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(9) + "🚫 Есть какие-то ограничения/травмы? Напиши (или 'нет'):"
        await clean_edit(callback, uid, text, reply_markup=kb_back_menu("freq"))
    else:
        await clean_send(callback.bot, callback.message.chat.id, uid, "🏠 Меню", reply_markup=menu_main_inline_kb())

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
    text = _profile_header(3) + "🎂 Введи возраст (числом):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_back_menu("sex"))
    await callback.answer()


def _parse_int_from_text(t: str) -> Optional[int]:
    t = (t or "").strip()
    m = re.search(r"\d+", t)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _parse_float_from_text(t: str) -> Optional[float]:
    t = (t or "").strip().replace(",", ".")
    m = re.search(r"\d+(\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


async def profile_age_input(message: Message, state: FSMContext, bot: Bot):
    age = _parse_int_from_text(message.text or "")
    if not age or age < 10 or age > 90:
        await message.answer("Возраст числом (например 21).")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, age=age)

    await state.set_state(ProfileWizard.height)
    # обновляем «мастер»-сообщение через clean_send (чтобы чат не засорялся)
    text = _profile_header(4) + "📏 Введи рост в см (числом):"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_back_menu("age"))
    await try_delete_user_message(bot, message)


async def profile_height_input(message: Message, state: FSMContext, bot: Bot):
    h = _parse_int_from_text(message.text or "")
    if not h or h < 120 or h > 230:
        await message.answer("Рост в см (например 176).")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, height=h)

    await state.set_state(ProfileWizard.weight)
    text = _profile_header(5) + "⚖️ Введи вес в кг (числом):"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_back_menu("height"))
    await try_delete_user_message(bot, message)


async def profile_weight_input(message: Message, state: FSMContext, bot: Bot):
    w = _parse_float_from_text(message.text or "")
    if not w or w < 30 or w > 250:
        await message.answer("Вес в кг (например 72.5).")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, weight=float(w))

    await state.set_state(ProfileWizard.place)
    text = _profile_header(6) + "🏠 Где тренируешься?"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_place())
    await try_delete_user_message(bot, message)


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
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(9) + "🚫 Есть какие-то ограничения/травмы? Напиши (или 'нет'):"
        await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_back_menu("freq"))
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

    await state.set_state(ProfileWizard.limits)
    text = _profile_header(9) + "🚫 Есть какие-то ограничения/травмы? Напиши (или 'нет'):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_back_menu("freq"))
    await callback.answer()


async def profile_limits_input(message: Message, state: FSMContext, bot: Bot):
    limits = (message.text or "").strip()
    if not limits:
        await message.answer("Напиши ограничения текстом или 'нет'.")
        await try_delete_user_message(bot, message)
        return

    if limits.lower() in ("нет", "no", "none", "-"):
        limits = "нет"

    await update_user(message.from_user.id, limits=limits)
    await state.clear()

    u = await get_user(message.from_user.id)
    summary = (
        _profile_header(9) +
        "✅ Профиль сохранён!\n\n"
        f"Цель: {u.get('goal')}\n"
        f"Пол: {u.get('sex')}\n"
        f"Возраст: {u.get('age')}\n"
        f"Рост: {u.get('height')}\n"
        f"Вес: {u.get('weight')}\n"
        f"Где: {u.get('place')}\n"
        f"Опыт: {u.get('exp')}\n"
        f"Частота: {u.get('freq')}×/нед\n"
        f"Ограничения: {u.get('limits')}\n\n"
        "Теперь можно открыть питание/тренировки."
    )
    await clean_send(bot, message.chat.id, message.from_user.id, summary, reply_markup=profile_done_kb())
    await try_delete_user_message(bot, message)


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
        await callback.message.answer("Сначала выбери тариф в «💳 Оплата/доступ».")
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


async def pay_amount(message: Message, state: FSMContext, bot: Bot):
    txt = re.sub(r"[^\d]", "", message.text or "")
    if not txt:
        await message.answer("Сумму числом, например 1150")
        await try_delete_user_message(bot, message)
        return
    await state.update_data(amount=int(txt))
    await message.answer("Введи последние 4 цифры карты отправителя (или 0000):")
    await state.set_state(PaymentFlow.waiting_last4)
    await try_delete_user_message(bot, message)


async def pay_last4(message: Message, state: FSMContext, bot: Bot):
    txt = re.sub(r"[^\d]", "", message.text or "")
    if len(txt) != 4:
        await message.answer("Нужно ровно 4 цифры. Например 1234 (или 0000)")
        await try_delete_user_message(bot, message)
        return
    await state.update_data(last4=txt)
    await message.answer("Отправь чек/скрин оплаты как фото:")
    await state.set_state(PaymentFlow.waiting_receipt)
    await try_delete_user_message(bot, message)


async def pay_receipt(message: Message, state: FSMContext, bot: Bot):
    if not message.photo:
        await message.answer("Нужно фото/скрин чека. Отправь как фото.")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    tariff = data.get("tariff")
    if tariff not in TARIFFS:
        await message.answer("Не вижу выбранный тариф. Открой «Оплата/доступ» и выбери тариф заново.")
        await state.clear()
        await try_delete_user_message(bot, message)
        return

    amount = int(data.get("amount", 0))
    last4 = data.get("last4", "0000")
    receipt_file_id = message.photo[-1].file_id
    code = gen_order_code(message.from_user.id)

    payment_id = await create_payment(message.from_user.id, tariff, amount, last4, code, receipt_file_id)
    await message.answer("✅ Заявка отправлена. Как подтвержу — доступ откроется.")
    await try_delete_user_message(bot, message)

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
            reply_markup=menu_main_inline_kb()
        )
        await callback.answer("Подтверждено ✅")
    else:
        await set_payment_status(pid, "rejected")
        await bot.send_message(
            chat_id=user_id,
            text="❌ Оплата отклонена. Проверь сумму/чек/комментарий и попробуй снова (кнопка снизу: 💳 Оплата/доступ)."
        )
        await callback.answer("Отклонено ❌")


# =========================
# РАЗДЕЛЫ: ТРЕНИРОВКИ / ПИТАНИЕ / ЗАМЕРЫ / ДНЕВНИК
# =========================
async def ensure_profile_ready(user_id: int) -> bool:
    u = await get_user(user_id)
    need = ["goal", "sex", "age", "height", "weight", "place", "exp", "freq", "limits"]
    return not any(u.get(k) is None or u.get(k) == "" for k in need)

async def build_plans_if_needed(user_id: int):
    u = await get_user(user_id)

    workout = generate_workout_plan(
        u["goal"], u["place"], u["exp"], int(u["freq"]),
        limits=u.get("limits") or "",
        user_id=user_id
    )
    summary, cal, p, f, c, meals = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"]
    )
    nutrition_full = (
        summary
        + "\n\n🔁 Простые замены:\n"
          "• курица ↔ индейка ↔ рыба\n"
          "• рис ↔ гречка ↔ макароны\n"
          "• творог ↔ йогурт/кефир\n"
    )

    await save_workout_plan(user_id, workout)
    await save_nutrition_plan(user_id, nutrition_full)

def diary_exercises_kb():
    # ✅ оставили только то, что реально удобно отслеживать для прогресса
    # ✅ без смайликов у упражнений
    # ✅ 2 столбца
    rows = [
        [InlineKeyboardButton(text="Жим лёжа", callback_data="d:ex:Жим лёжа"),
         InlineKeyboardButton(text="Присед", callback_data="d:ex:Присед")],
        [InlineKeyboardButton(text="Тяга (гребля)", callback_data="d:ex:Тяга (гребля)"),
         InlineKeyboardButton(text="Верхний блок", callback_data="d:ex:Верхний блок")],
        [InlineKeyboardButton(text="Подтягивания", callback_data="d:ex:Подтягивания"),
         InlineKeyboardButton(text="Румынская тяга", callback_data="d:ex:Румынская тяга")],
        [InlineKeyboardButton(text="Жим вверх", callback_data="d:ex:Жим вверх"),
         InlineKeyboardButton(text="Жим ногами", callback_data="d:ex:Жим ногами")],
        [InlineKeyboardButton(text="📜 История", callback_data="d:history")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def measures_kb():
    # ✅ добавили «дневник/историю» как в дневнике тренировок
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Вес (кг)", callback_data="mtype:weight"),
         InlineKeyboardButton(text="📏 Талия (см)", callback_data="mtype:waist")],
        [InlineKeyboardButton(text="💪 Рука (см)", callback_data="mtype:arm"),
         InlineKeyboardButton(text="胸 Грудь (см)", callback_data="mtype:chest")],
        [InlineKeyboardButton(text="🦵 Бедро (см)", callback_data="mtype:thigh")],
        [InlineKeyboardButton(text="📜 История замеров", callback_data="m:history")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])

def measures_history_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вес", callback_data="m:hist:weight"),
         InlineKeyboardButton(text="Талия", callback_data="m:hist:waist")],
        [InlineKeyboardButton(text="Рука", callback_data="m:hist:arm"),
         InlineKeyboardButton(text="Грудь", callback_data="m:hist:chest")],
        [InlineKeyboardButton(text="Бедро", callback_data="m:hist:thigh")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:measures")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])

async def open_workouts(user_id: int, chat_id: int, bot: Bot, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        await clean_send(bot, chat_id, user_id, locked_text())
        return

    if not await ensure_profile_ready(user_id):
        await clean_send(bot, chat_id, user_id, "⚠️ Сначала заполни профиль (кнопка снизу: ⚙️ Профиль).")
        return

    plan = await get_workout_plan(user_id)
    if not plan:
        await build_plans_if_needed(user_id)
        plan = await get_workout_plan(user_id)

    head = "🏋️ Мои тренировки\n\n"
    txt = head + (plan or "План пока не найден.")
    if callback:
        if len(txt) <= TG_SAFE_MSG_LEN:
            await clean_edit(callback, user_id, txt, reply_markup=workouts_inline_kb())
        else:
            await clean_send(bot, chat_id, user_id, head, reply_markup=workouts_inline_kb())
            await bot.send_message(chat_id, plan or "")
    else:
        await clean_send(bot, chat_id, user_id, txt, reply_markup=workouts_inline_kb())

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
    text = "📏 Замеры\n\nВыбери, что записать (дата ставится автоматически):"
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
        "Выбери упражнение кнопкой.\n"
        "Дата проставится автоматически — тебе останется ввести только вес и повторы."
    )
    if callback:
        await clean_edit(callback, user_id, text, reply_markup=diary_exercises_kb())
    else:
        await clean_send(bot, chat_id, user_id, text, reply_markup=diary_exercises_kb())

# =========================
# ✅ ДНЕВНИК: выбор упражнения → ввод только вес/повторы
# =========================
async def diary_pick_ex(callback: CallbackQuery, state: FSMContext, bot: Bot):
    exercise = callback.data.split("d:ex:", 1)[1].strip()
    await state.update_data(exercise=exercise)
    await state.set_state(DiaryFlow.enter_sets)

    today = datetime.now().strftime("%Y-%m-%d")
    text = (
        f"🗓 {today}\n"
        f"✅ Упражнение: {exercise}\n\n"
        "Введи подходы (только вес и повторы):\n"
        "Пример: 60x8, 60x8, 60x7\n\n"
        "Можно и один подход: 80x6"
    )
    await clean_edit(callback, callback.from_user.id, text, reply_markup=diary_exercises_kb())
    await callback.answer()

async def diary_enter_sets(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip()
    data = await state.get_data()
    exercise = (data.get("exercise") or "").strip()
    if not exercise:
        await clean_send(bot, message.chat.id, message.from_user.id, "Сначала выбери упражнение кнопкой в «Дневнике».")
        await try_delete_user_message(bot, message)
        await state.clear()
        return

    parts = [p.strip() for p in txt.split(",") if p.strip()]
    if not parts:
        await message.answer("Формат: 60x8, 60x8 (весxповторы)")
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
        "Можешь выбрать следующее упражнение кнопкой ниже."
    )
    await clean_send(bot, message.chat.id, message.from_user.id, msg, reply_markup=diary_exercises_kb())
    await try_delete_user_message(bot, message)
    await state.set_state(DiaryFlow.choosing_exercise)

async def diary_history(callback: CallbackQuery):
    history = await get_diary_history(callback.from_user.id, 10)
    if not history:
        await callback.message.answer("Истории пока нет. Выбери упражнение и введи вес/повторы.")
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
# ✅ ЗАМЕРЫ: как дневник (выбор → ввод → история)
# =========================
async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await state.set_state(MeasureFlow.enter_value)

    name = {"weight": "Вес (кг)", "waist": "Талия (см)", "arm": "Рука (см)", "chest": "Грудь (см)", "thigh": "Бедро (см)"}.get(mtype, mtype)
    await callback.message.answer(f"Введи значение для «{name}» числом (например 72.5):")
    await callback.answer()


async def measure_value(message: Message, state: FSMContext, bot: Bot):
    txt = (message.text or "").strip().replace(",", ".")
    try:
        val = float(txt)
    except Exception:
        await message.answer("Нужно число. Например 72.5")
        await try_delete_user_message(bot, message)
        return

    data = await state.get_data()
    mtype = data.get("mtype")

    await add_measure(message.from_user.id, mtype, val)
    rows = await get_last_measures(message.from_user.id, mtype, 6)

    name = {"weight": "Вес", "waist": "Талия", "arm": "Рука", "chest": "Грудь", "thigh": "Бедро"}.get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    out = f"✅ Записал: {name} = {val:g}\n\nПоследние:\n{hist}\n\nМожешь выбрать другой замер в «📏 Замеры»."
    await clean_send(bot, message.chat.id, message.from_user.id, out, reply_markup=measures_kb())
    await state.set_state(MeasureFlow.choose_type)
    await try_delete_user_message(bot, message)


async def measures_history_open(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MeasureFlow.history_pick_type)
    await callback.message.answer("📜 История замеров — выбери тип:", reply_markup=measures_history_kb())
    await callback.answer()


async def measures_history_pick(callback: CallbackQuery):
    mtype = callback.data.split(":")[2]
    rows = await get_last_measures(callback.from_user.id, mtype, 15)
    name = {"weight": "Вес", "waist": "Талия", "arm": "Рука", "chest": "Грудь", "thigh": "Бедро"}.get(mtype, mtype)
    if not rows:
        await callback.message.answer(f"Истории по «{name}» пока нет.", reply_markup=measures_history_kb())
        await callback.answer()
        return
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    await callback.message.answer(f"📜 {name} — последние записи:\n\n{hist}", reply_markup=measures_history_kb())
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
# ✅ ТЕХНИКИ: ХЕНДЛЕРЫ (с картинками) + возврат к тренировкам
# =========================
async def cb_tech_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await clean_edit(callback, callback.from_user.id, "📚 Техники выполнения — выбери упражнение:", reply_markup=tech_list_kb())
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
        await callback.message.answer_photo(photo=photo, caption=caption, reply_markup=tech_back_kb())
        if rest:
            await callback.message.answer(rest, reply_markup=tech_back_kb())
    else:
        await callback.message.answer(text, reply_markup=tech_back_kb())

    await callback.answer()


async def cb_tech_back_workouts(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await open_workouts(callback.from_user.id, callback.message.chat.id, bot, callback=callback)
    await callback.answer()


# =========================
# ✅ НОВОЕ: ПОСТЫ С КАРТИНКАМИ (АДМИН)
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
# ПОДДЕРЖКА: любой текст пользователей -> админу (и удаляем у пользователя)
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
    await clean_send(bot, message.chat.id, message.from_user.id, "✅ Отправил в поддержку. Я отвечу здесь, как админ отреагирует.")


# =========================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
def setup_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, CommandStart())

    dp.callback_query.register(cb_nav, F.data.startswith("nav:"))

    dp.callback_query.register(cb_profile_back, F.data.startswith("p:back:"))
    dp.callback_query.register(cb_profile_goal, F.data.startswith("p:goal:"))
    dp.callback_query.register(cb_profile_sex, F.data.startswith("p:sex:"))
    dp.callback_query.register(cb_profile_place, F.data.startswith("p:place:"))
    dp.callback_query.register(cb_profile_exp, F.data.startswith("p:exp:"))
    dp.callback_query.register(cb_profile_freq, F.data.startswith("p:freq:"))

    # ✅ ввод текстом
    dp.message.register(profile_age_input, ProfileWizard.age)
    dp.message.register(profile_height_input, ProfileWizard.height)
    dp.message.register(profile_weight_input, ProfileWizard.weight)
    dp.message.register(profile_limits_input, ProfileWizard.limits)

    dp.callback_query.register(cb_tariff, F.data.startswith("tariff:"))
    dp.callback_query.register(cb_i_paid, F.data == "pay_i_paid")
    dp.callback_query.register(admin_actions, F.data.startswith("admin_approve:") | F.data.startswith("admin_reject:"))
    dp.message.register(pay_amount, PaymentFlow.waiting_amount)
    dp.message.register(pay_last4, PaymentFlow.waiting_last4)
    dp.message.register(pay_receipt, PaymentFlow.waiting_receipt)

    dp.callback_query.register(cb_measure_type, F.data.startswith("mtype:"))
    dp.message.register(measure_value, MeasureFlow.enter_value)
    dp.callback_query.register(measures_history_open, F.data == "m:history")
    dp.callback_query.register(measures_history_pick, F.data.startswith("m:hist:"))

    dp.callback_query.register(diary_pick_ex, F.data.startswith("d:ex:"))
    dp.callback_query.register(diary_history, F.data == "d:history")
    dp.message.register(diary_enter_sets, DiaryFlow.enter_sets)

    dp.callback_query.register(cb_tech_list, F.data == "tech:list")
    dp.callback_query.register(cb_tech_back_workouts, F.data == "tech:back_workouts")
    dp.callback_query.register(cb_tech_show, F.data.startswith("tech:"))

    dp.callback_query.register(cb_nutr_example, F.data.startswith("nutr:ex:"))
    dp.callback_query.register(cb_nutr_back, F.data == "nutr:back")

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
