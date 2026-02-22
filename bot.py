from aiohttp import web

import asyncio
import logging
import os
import random
import re
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
    limits = State()       # текстом


# =========================
# ✅ ТЕХНИКИ ВЫПОЛНЕНИЯ (картинка + текст)
# =========================
TECH = {
    "squat": {
        "title": "Присед",
        "img": "media/tech/squat.jpg",
        "text": (
            "📚 Присед со штангой\n\n"
            "🎯 Цель: квадрицепс/ягодицы, сила ног.\n\n"
            "✅ Подготовка:\n"
            "1) Стопы: ширина плеч (или чуть шире), носки слегка наружу.\n"
            "2) Гриф: на трапециях (высоко) или ниже (низко) — как комфортнее.\n"
            "3) Корпус: грудь вверх, лопатки сведены, шея нейтрально.\n"
            "4) Опора: «треножник» стопы — пятка + основание большого + малого пальца.\n\n"
            "⬇️ Спуск:\n"
            "1) Сначала вдох животом → напряги пресс.\n"
            "2) Таз чуть назад, колени идут по линии носков.\n"
            "3) Спина ровная, не «складывайся» в пояснице.\n"
            "4) Глубина: до той, где спина остаётся ровной, а стопа не теряет опору.\n\n"
            "⬆️ Подъём:\n"
            "1) Толкайся серединой стопы/пяткой, не уходи на носки.\n"
            "2) Колени по линии носков, не заваливай внутрь.\n"
            "3) Корпус жёсткий, без рывков.\n\n"
            "🫁 Дыхание:\n"
            "• вдох перед опусканием → держишь пресс → выдох после «тяжёлой точки».\n\n"
            "❌ Частые ошибки:\n"
            "• колени внутрь • пятки отрываются • спина округляется • слишком быстрый спуск\n\n"
            "✅ Подсказки:\n"
            "• «Колени по носкам» • «Пресс — камень» • «Гриф над серединой стопы»"
        )
    },
    "bench": {
        "title": "Жим лёжа",
        "img": "media/tech/bench.jpg",
        "text": (
            "📚 Жим лёжа\n\n"
            "🎯 Цель: грудь/трицепс/передняя дельта.\n\n"
            "✅ Настройка:\n"
            "1) Глаза под грифом. Хват чуть шире плеч.\n"
            "2) Лопатки: сведи и опусти вниз (как будто «в карманы»).\n"
            "3) Ноги: уверенно в пол, лёгкое давление ногами (стабильность).\n"
            "4) Допустим лёгкий прогиб в спине, но ягодицы НЕ отрывай от лавки.\n\n"
            "⬇️ Опускание:\n"
            "1) Контроль: опускай к нижней части груди.\n"
            "2) Локти примерно 30–60° к корпусу (в среднем ~45°).\n"
            "3) Запястья ровные, не «ломай» кисть назад.\n\n"
            "⬆️ Жим:\n"
            "1) Жми вверх и слегка назад в сторону стоек (естественная траектория).\n"
            "2) Лопатки не теряй — плечи не «вылезают» вперёд.\n\n"
            "❌ Ошибки:\n"
            "• локти в стороны 90° • ягодицы отрываются • плечи вперёд • кривые запястья\n\n"
            "✅ Подсказки:\n"
            "• «Лопатки в карманы» • «Локти 45°» • «Ноги — опора»"
        )
    },

    # ✅ ИЗМЕНЕНИЕ: вместо "Тяга (гребля)" теперь "Отжимания"
    "row": {
        "title": "Отжимания",
        "img": "media/tech/pushup.jpg",  # положи фото сюда (или переименуй путь под свой файл)
        "text": (
            "📚 Отжимания\n\n"
            "🎯 Цель: грудь/трицепс/передняя дельта, контроль корпуса.\n\n"
            "✅ Исходное положение:\n"
            "1) Ладони под плечами (или чуть шире), пальцы «раскрыты».\n"
            "2) Корпус прямой: голова–плечи–таз–пятки в одной линии.\n"
            "3) Пресс и ягодицы напряжены — чтобы не проваливаться в пояснице.\n\n"
            "⬇️ Опускание:\n"
            "1) Локти веди под углом ~30–60° к корпусу (не разводи в стороны).\n"
            "2) Опускайся до касания грудью пола/до комфортной глубины.\n"
            "3) Плечи не поднимай к ушам.\n\n"
            "⬆️ Подъём:\n"
            "1) Толкай пол от себя, сохраняя корпус жёстким.\n"
            "2) Вверху не «провисай» — держи пресс.\n\n"
            "🫁 Дыхание:\n"
            "• вдох вниз → выдох вверх.\n\n"
            "✅ Варианты (если тяжело/легко):\n"
            "• легче: отжимания от лавки/опоры\n"
            "• сложнее: ноги на опоре / пауза внизу / медленный негатив\n\n"
            "❌ Ошибки:\n"
            "• провал поясницы • локти строго в стороны • голова вниз • неполная амплитуда всегда\n\n"
            "✅ Подсказки:\n"
            "• «Корпус — доска» • «Локти под 45°» • «Грудь к полу, не голова»"
        )
    },
    "latpulldown": {
        "title": "Верхний блок",
        "img": "media/tech/latpulldown.jpg",
        "text": (
            "📚 Тяга верхнего блока\n\n"
            "🎯 Цель: широчайшие, спина.\n\n"
            "✅ Настройка:\n"
            "1) Бёдра под валиками, стопы уверенно.\n"
            "2) Грудь вверх, спина ровная, плечи опусти вниз.\n"
            "3) Хват чуть шире плеч (или средний — как комфортнее).\n\n"
            "⬇️ Тяга:\n"
            "1) Стартуй лопатками: «опусти плечи вниз».\n"
            "2) Затем тяни гриф к верхней части груди.\n"
            "3) Локти веди вниз и назад, не «вперёд».\n"
            "4) Внизу короткая пауза 0.5–1 сек — почувствуй спину.\n\n"
            "⬆️ Возврат:\n"
            "1) Поднимай вес контролируемо, не бросай.\n"
            "2) Вверху не поднимай плечи к ушам.\n\n"
            "❌ Ошибки:\n"
            "• тяга за голову • раскачка корпусом • плечи вверх • тяга только руками\n\n"
            "✅ Подсказки:\n"
            "• «Плечи вниз» • «Локти вниз» • «Контроль вверх»"
        )
    },
    "pullup": {
        "title": "Подтягивания",
        "img": "media/tech/pullup.jpg",
        "text": (
            "📚 Подтягивания\n\n"
            "🎯 Цель: широчайшие/верх спины/бицепс.\n\n"
            "✅ Старт:\n"
            "1) Хват чуть шире плеч или средний.\n"
            "2) Корпус напряжён: пресс, ягодицы.\n"
            "3) Сделай «полуподтягивание лопатками»: плечи вниз/лопатки к позвоночнику.\n\n"
            "⬆️ Подъём:\n"
            "1) Тяни локти к рёбрам, а не подбородок к перекладине.\n"
            "2) Грудь стремится к перекладине, без рывков.\n\n"
            "⬇️ Спуск:\n"
            "1) Опускайся плавно 2–3 секунды.\n"
            "2) Внизу полностью контролируй плечи (не «проваливайся»).\n\n"
            "✅ Если тяжело:\n"
            "• резинка / гравитрон / негативы (медленно вниз)\n\n"
            "❌ Ошибки:\n"
            "• раскачка • плечи к ушам • резкие рывки • всегда короткая амплитуда\n\n"
            "✅ Подсказки:\n"
            "• «Лопатки вниз» • «Локти к рёбрам» • «Контроль вниз»"
        )
    },
    "rdl": {
        "title": "Румынская тяга",
        "img": "media/tech/rdl.jpg",
        "text": (
            "📚 Румынская тяга\n\n"
            "🎯 Цель: ягодицы/задняя поверхность бедра, сильный «задний» контур.\n\n"
            "✅ Настройка:\n"
            "1) Стопы на ширине таза/плеч, гриф в руках.\n"
            "2) Колени слегка согнуты и почти не меняются.\n"
            "3) Спина ровная, грудь «вперёд-вверх», плечи вниз.\n\n"
            "⬇️ Наклон:\n"
            "1) Главное движение — таз назад (как будто закрываешь дверцу попой).\n"
            "2) Гриф скользит близко к ногам.\n"
            "3) Опускайся до сильного натяжения в задней поверхности бедра.\n"
            "4) Поясница нейтральна — без округления.\n\n"
            "⬆️ Подъём:\n"
            "1) Поднимайся ягодицами: таз вперёд, корпус остаётся жёстким.\n"
            "2) Вверху не «переразгибайся» в пояснице.\n\n"
            "❌ Ошибки:\n"
            "• округление спины • гриф далеко от ног • превращение в присед • рывки\n\n"
            "✅ Подсказки:\n"
            "• «Таз назад» • «Гриф по ногам» • «Спина ровная»"
        )
    },
    "ohp": {
        "title": "Жим вверх",
        "img": "media/tech/ohp.jpg",
        "text": (
            "📚 Жим штанги/гантелей вверх стоя\n\n"
            "🎯 Цель: дельты/трицепс, сила плеч.\n\n"
            "✅ Настройка:\n"
            "1) Стойка устойчивая, стопы примерно на ширине плеч.\n"
            "2) Пресс и ягодицы напряжены (чтобы не прогибаться в пояснице).\n"
            "3) Гриф у ключиц/подбородка, локти немного впереди грифа.\n\n"
            "⬆️ Жим:\n"
            "1) Жми по траектории «вверх и чуть назад».\n"
            "2) Гриф идёт близко к лицу: голова чуть назад → затем «в окно» (голова вперёд под гриф).\n\n"
            "⬇️ Опускание:\n"
            "1) Контролируй вниз, не бросай.\n"
            "2) Локти не разваливай в стороны слишком сильно.\n\n"
            "❌ Ошибки:\n"
            "• прогиб в пояснице • гриф уходит далеко вперёд • плечи к ушам\n\n"
            "✅ Подсказки:\n"
            "• «Рёбра вниз» • «Пресс камень» • «Гриф близко»"
        )
    },
    "lateralraise": {
        "title": "Разведения в стороны",
        "img": "media/tech/lateralraise.jpg",
        "text": (
            "📚 Разведения гантелей в стороны\n\n"
            "🎯 Цель: средняя дельта (ширина плеч).\n\n"
            "✅ Техника:\n"
            "1) Плечи вниз, шея расслаблена.\n"
            "2) Локоть слегка согнут и фиксирован.\n"
            "3) Поднимай до уровня плеч (чуть ниже/до комфортного).\n"
            "4) Вниз опускай медленно 2–3 секунды.\n\n"
            "✅ Чувство мышц:\n"
            "• локоть ведёт движение (обычно локоть чуть выше кисти).\n\n"
            "❌ Ошибки:\n"
            "• махи корпусом • слишком высокий подъём • плечи к ушам • рывки\n\n"
            "✅ Подсказки:\n"
            "• «Локоть ведёт» • «Плечи вниз» • «Контроль вниз»"
        )
    },
    "biceps": {
        "title": "Бицепс сгибания",
        "img": "media/tech/biceps.jpg",
        "text": (
            "📚 Сгибания на бицепс\n\n"
            "🎯 Цель: бицепс, сила/объём рук.\n\n"
            "✅ Техника:\n"
            "1) Локти прижаты к корпусу и почти не двигаются.\n"
            "2) Поднимай без раскачки, корпус ровный.\n"
            "3) Вверху короткая пауза, почувствуй сокращение.\n"
            "4) Опускай 2–3 секунды (контроль важнее веса).\n\n"
            "❌ Ошибки:\n"
            "• читинг корпусом • локти улетают вперёд • слишком большой вес\n\n"
            "✅ Подсказки:\n"
            "• «Локти на месте» • «Вниз медленно»"
        )
    },
    "triceps": {
        "title": "Трицепс на блоке",
        "img": "media/tech/triceps.jpg",
        "text": (
            "📚 Разгибания на трицепс (блок)\n\n"
            "🎯 Цель: трицепс, форма/сила рук.\n\n"
            "✅ Техника:\n"
            "1) Локти прижаты к корпусу и не «разъезжаются».\n"
            "2) Разгибай до конца, но без боли в локте.\n"
            "3) Внизу пауза 0.5 сек.\n"
            "4) Вверх возвращай медленно, не бросай.\n\n"
            "❌ Ошибки:\n"
            "• локти расходятся • раскачка корпусом • плечи уходят вперёд\n\n"
            "✅ Подсказки:\n"
            "• «Локти прижаты» • «Пауза внизу»"
        )
    },
    "legpress": {
        "title": "Жим ногами",
        "img": "media/tech/legpress.jpg",
        "text": (
            "📚 Жим ногами\n\n"
            "🎯 Цель: квадрицепс/ягодицы, безопасная нагрузка на ноги.\n\n"
            "✅ Настройка:\n"
            "1) Поясница и таз прижаты к спинке.\n"
            "2) Стопы на платформе: средняя постановка — самый универсальный вариант.\n"
            "3) Колени смотрят по линии носков.\n\n"
            "⬇️ Опускание:\n"
            "1) Опускай до комфортной глубины, где поясница не отрывается.\n"
            "2) Контроль, без «провала».\n\n"
            "⬆️ Выжим:\n"
            "1) Жми через пятки/середину стопы.\n"
            "2) Не выпрямляй колени «в замок».\n\n"
            "❌ Ошибки:\n"
            "• отрыв поясницы • колени внутрь • слишком короткая амплитуда\n\n"
            "✅ Подсказки:\n"
            "• «Поясница прижата» • «Колени по носкам»"
        )
    },
}


def tech_kb():
    rows = [
        [InlineKeyboardButton(text=TECH["squat"]["title"], callback_data="tech:squat"),
         InlineKeyboardButton(text=TECH["bench"]["title"], callback_data="tech:bench")],

        # ✅ кнопка "Отжимания" оставлена в прежнем ключе "row"
        [InlineKeyboardButton(text=TECH["row"]["title"], callback_data="tech:row"),
         InlineKeyboardButton(text=TECH["latpulldown"]["title"], callback_data="tech:latpulldown")],

        [InlineKeyboardButton(text=TECH["pullup"]["title"], callback_data="tech:pullup"),
         InlineKeyboardButton(text=TECH["ohp"]["title"], callback_data="tech:ohp")],

        [InlineKeyboardButton(text=TECH["rdl"]["title"], callback_data="tech:rdl"),
         InlineKeyboardButton(text=TECH["lateralraise"]["title"], callback_data="tech:lateralraise")],

        [InlineKeyboardButton(text=TECH["biceps"]["title"], callback_data="tech:biceps"),
         InlineKeyboardButton(text=TECH["triceps"]["title"], callback_data="tech:triceps")],

        [InlineKeyboardButton(text=TECH["legpress"]["title"], callback_data="tech:legpress")],

        # (оставил как было) кнопка назад к тренировкам в списке техник
        [InlineKeyboardButton(text="⬅️ Назад к тренировкам", callback_data="nav:workouts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ✅ ИЗМЕНЕНИЕ: под техникой оставляем только "Назад к списку"
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
        input_field_placeholder="Выбери действие снизу 👇"
    )


# =========================
# Inline: меню разделов
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
# ✅ Профиль: прогресс "■■■■■■■□□□ 100%" + возраст/рост/вес текстом + ограничения
# =========================
TOTAL_PROFILE_STEPS = 9


def _bar(step: int, total: int = TOTAL_PROFILE_STEPS, width: int = 10) -> str:
    step = max(0, min(step, total))
    pct = int(round(step / total * 100)) if total else 0
    filled = int(round(pct / 100 * width))
    filled = max(0, min(filled, width))
    return f"{'■'*filled}{'□'*(width-filled)} {pct}%"


def _profile_header(step: int) -> str:
    return f"🧩 Заполнение профиля {step}/{TOTAL_PROFILE_STEPS}\n{_bar(step)}\n\n"


def profile_done_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏋️🍽 Перейти к тренировкам и питанию", callback_data="nav:menu")]
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


def kb_text_step(back_to: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:back:{back_to}")],
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
# ✅ ТРЕНИРОВКИ (индивидуально + по твоим правилам)
# =========================
def _limits_tags(limits: str) -> Dict[str, bool]:
    t = (limits or "").lower()
    return {
        "knee": any(x in t for x in ["колен", "колено", "мениск", "связк"]),
        "back": any(x in t for x in ["спин", "поясниц", "грыж", "протруз", "сколиоз"]),
        "shoulder": any(x in t for x in ["плеч", "лопат", "ротатор"]),
        "elbow": any(x in t for x in ["локт", "тендинит"]),
    }


def _pick_with_avoid(rnd: random.Random, items: List[str], avoid_keys: List[str]) -> str:
    safe, risky = [], []
    for it in items:
        it_low = (it or "").lower()
        if any(k in it_low for k in avoid_keys):
            risky.append(it)
        else:
            safe.append(it)
    if safe:
        return rnd.choice(safe)
    if risky:
        return rnd.choice(risky)
    return "—"


def _fmt_day(day_no: int, lines: List[str]) -> str:
    out = [f"День {day_no}", ""]
    for ln in lines:
        out.append(f"• {ln}")
    out.append("")
    return "\n".join(out)


def generate_workout_plan(goal: str, place: str, exp: str, freq: int, limits: str, user_id: int = 0) -> str:
    """
    ✅ ЛОГИКА ПО ТВОЕМУ ТЗ:
    - Разминка 5–10 мин (упоминание в начале)
    - Для роста/формы:
      База: 3–4 упражнения, 3–4 подхода, 6–10 повторений, отдых 2–3 мин, RIR 1–2
      Изоляция: 2–3 упражнения, 2–4 подхода, 8–15 повторений, отдых 60–90 сек
    - Для сушки:
      База: 3–4 упражнения, 3–4 подхода, 5–8 повторений, отдых 2–3 мин, RIR 1–2
      Изоляция: 2–3 упражнения, 2–4 подхода, 8–15 повторений, отдых 60–90 сек
      + напоминание про кардио/шаги
    - Формат дней: без слов "БАЗА/ИЗОЛЯЦИЯ", просто список "• ..."
    """
    pl = (place or "").lower()
    is_gym = ("зал" in pl) or (pl == "gym") or ("gym" in pl)
    where = "ЗАЛ" if is_gym else "ДОМ"

    lvl = exp_level(exp)
    seed = (user_id or 0) + int(datetime.utcnow().strftime("%Y%m%d"))
    rnd = random.Random(seed)

    tags = _limits_tags(limits)

    avoid_knee = ["присед", "жим ног", "выпад", "болгар", "разгиб"]
    avoid_back = ["тяга", "станов", "наклон", "гребл", "румын"]
    avoid_shoulder = ["жим вверх", "жим лёжа", "жим в тренаж", "отжим"]
    avoid_elbow = ["разгиб", "француз", "трицепс", "сгибан"]

    g = (goal or "").lower()
    is_cut = ("суш" in g)

    # повторы для базы по цели
    reps_base = "5–8" if is_cut else "6–10"
    if lvl == "novice" and not is_cut:
        reps_base = "8–12"

    base_sets = "3–4" if lvl != "novice" else "3"
    iso_sets = "3" if lvl != "novice" else "2–3"
    reps_iso = "8–15"  # по твоему ТЗ

    f = int(freq or 3)
    f = max(3, min(f, 5))

    def choose_base_push():
        if is_gym:
            base = [
                "Жим лёжа (штанга)",
                "Жим гантелей лёжа",
                "Жим в тренажёре",
                "Отжимания",
            ]
        else:
            base = [
                "Отжимания",
                "Отжимания с упором ног",
                "Жим гантелей лёжа",
            ]
        if tags["shoulder"]:
            base += ["Жим в тренажёре (лёгко)", "Сведения в тренажёре/на блоке"]
        return _pick_with_avoid(rnd, base, avoid_shoulder if tags["shoulder"] else [])

    def choose_base_pull():
        if is_gym:
            base = [
                "Верхний блок",
                "Подтягивания",
                "Тяга горизонтального блока",
                "Тяга гантели одной рукой",
            ]
        else:
            base = [
                "Подтягивания",
                "Тяга гантели одной рукой",
                "Тяга резинки к поясу",
            ]
        if tags["back"]:
            base += ["Верхний блок (лёгко, без раскачки)", "Тяга к груди сидя (лёгко)"]
        return _pick_with_avoid(rnd, base, avoid_back if tags["back"] else [])

    def choose_base_legs():
        if is_gym:
            base = [
                "Присед со штангой",
                "Жим ногами",
                "Гоблет-присед",
                "Румынская тяга (лёгкая)",
            ]
        else:
            base = [
                "Приседания",
                "Болгарские выпады",
                "Ягодичный мост",
                "Гоблет-присед",
            ]
        if tags["knee"]:
            base += ["Ягодичный мост", "Сгибания ног (если есть тренажёр)"]
        if tags["back"]:
            base += ["Жим ногами (лёгко)", "Ягодичный мост"]
        avoid = []
        if tags["knee"]:
            avoid += avoid_knee
        if tags["back"]:
            avoid += avoid_back
        return _pick_with_avoid(rnd, base, avoid)

    def choose_base_extra(day_index: int):
        # 4-е базовое упражнение (по желанию): чередуем по дням
        # для новичка — обычно не нужно, поэтому даём только если не novice и/или если 4-5 тренировок
        if lvl == "novice" and f <= 3:
            return None

        options = []
        if is_gym:
            options += ["Жим вверх", "Подтягивания", "Верхний блок", "Жим ногами"]
        else:
            options += ["Отжимания", "Подтягивания", "Ягодичный мост"]

        # учитываем ограничения
        avoid = []
        if tags["shoulder"]:
            avoid += avoid_shoulder
        if tags["back"]:
            avoid += avoid_back
        if tags["knee"]:
            avoid += avoid_knee
        pick = _pick_with_avoid(rnd, options, avoid)

        # чтобы не повторялось слишком часто — иногда пропускаем
        if f == 3 and day_index % 2 == 0:
            return None
        return pick

    # изоляции: 2–3 упражнения
    shoulders_iso = ["Face pull (канат)", "Разведения в стороны (гантели)"]
    biceps_iso = ["Сгибания на блоке", "Сгибания гантелей"]
    triceps_iso = ["Разгибания на блоке", "Разгибания одной рукой (лёгко)", "Отжимания узкие"]
    legs_iso = ["Икры стоя/сидя", "Сгибания ног", "Разгибания ног"]

    if tags["elbow"]:
        biceps_iso += ["Молотки (лёгко)"]
        triceps_iso = ["Разгибания на блоке (лёгко)", "Разгибания одной рукой (лёгко)"]

    if tags["knee"]:
        legs_iso = ["Икры стоя/сидя", "Сгибания ног", "Ягодичный мост"]

    iso_avoid = []
    if tags["shoulder"]:
        iso_avoid += avoid_shoulder
    if tags["elbow"]:
        iso_avoid += avoid_elbow
    if tags["knee"]:
        iso_avoid += avoid_knee

    def fmt_base(name: str) -> str:
        return f"{name} — {base_sets}×{reps_base}"

    def fmt_iso(name: str) -> str:
        return f"{name} — {iso_sets}×{reps_iso}"

    limits_line = (limits or "").strip() or "нет"

    header_note = (
        "✅ Как строится тренировка\n"
        "Разминка — 5–10 мин: лёгкое кардио + разминочные подходы.\n"
        f"База — 3–4 упражнения, {base_sets} подхода, {reps_base} повторений, отдых 2–3 мин, 1–2 повтора в запасе.\n"
        f"Изоляция — 2–3 упражнения, 2–4 подхода, {reps_iso} повторений, отдых 60–90 сек.\n"
    )
    if is_cut:
        header_note += (
            "\n🔥 Сушка (важно):\n"
            "• старайся сохранять тяжёлые веса (сила падает — мышца уходит)\n"
            "• общий объём можно снизить на 10–20%\n"
            "• кардио: 2–4 раза/нед 20–40 мин ИЛИ 8–12 тыс шагов/день\n"
            "• не убивай себя отказами\n"
        )
    else:
        header_note += (
            "\n💪 Рост мышц (важно):\n"
            "• 10–20 подходов в неделю на мышцу\n"
            "• тренировать мышцу 2 раза в неделю\n"
            "• прогрессировать в весах или повторах\n"
        )

    prog = (
        "\n📌 Прогрессия:\n"
        "• Доводи подходы до верхней границы повторов\n"
        "• Потом добавляй вес (+2.5–5%) и снова работай в диапазоне\n"
        "• Если техника ломается — вес не повышай\n"
        "• Если усталость копится 7–10 дней — неделя легче (-20–30% объёма)\n"
    )

    days_text = []
    for d in range(1, f + 1):
        base_push = choose_base_push()
        base_pull = choose_base_pull()
        base_legs = choose_base_legs()
        base_extra = choose_base_extra(d)

        # изоляции: 2–3
        iso1 = _pick_with_avoid(rnd, shoulders_iso, iso_avoid)
        iso2 = _pick_with_avoid(rnd, biceps_iso, iso_avoid)
        iso3 = _pick_with_avoid(rnd, triceps_iso, iso_avoid)
        iso4 = _pick_with_avoid(rnd, legs_iso, iso_avoid)

        # собираем список: база 3–4, изоляция 2–3
        lines = [
            fmt_base(base_push),
            fmt_base(base_pull),
            fmt_base(base_legs),
        ]
        if base_extra:
            lines.append(fmt_base(base_extra))

        # для сушки обычно не раздуваем изоляцию — пусть будет 2–3, без лишнего
        if is_cut:
            lines += [fmt_iso(iso1), fmt_iso(iso2)]
            if f >= 4:
                lines.append(fmt_iso(iso4))
        else:
            lines += [fmt_iso(iso1), fmt_iso(iso2)]
            # 3-я изоляция чаще при 4–5 тренировках или если уровень не новичок
            if f >= 4 or lvl != "novice":
                lines.append(fmt_iso(iso3))
            if f >= 5:
                lines.append(fmt_iso(iso4))

        days_text.append(_fmt_day(d, lines))

    return (
        f"🏋️ Мои тренировки ({where}) — {f}×/нед\n\n"
        f"Цель: {goal}\n"
        f"Ограничения: {limits_line}\n\n"
        + header_note
        + prog
        + "\n"
        + "\n".join(days_text)
    )


# =========================
# ПИТАНИЕ (как было у тебя)
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
# МЕНЮ / START
# =========================
async def show_main_menu(bot: Bot, chat_id: int, user_id: int):
    text = (
        "👋 Привет! Я твой «Зелёный тренер».\n\n"
        "Я помогу:\n"
        "• составить тренировки под твою цель и возможности\n"
        "• дать простое питание (без запар)\n"
        "• вести дневник тренировок и замеры, чтобы видеть прогресс\n\n"
        "Выбирай раздел ниже 👇\n\n"
        "ℹ️ Управление (оплата/профиль/поддержка) всегда находится на клавиатуре снизу."
    )
    await clean_send(bot, chat_id, user_id, text, reply_markup=menu_main_inline_kb())


async def cmd_start(message: Message, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await bot.send_message(
        chat_id=message.chat.id,
        text="✅ Панель управления закреплена снизу.",
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


def _profile_summary_text(u: dict) -> str:
    return (
        "⚙️ Твой профиль\n\n"
        f"Цель: {u.get('goal')}\n"
        f"Пол: {u.get('sex')}\n"
        f"Возраст: {u.get('age')}\n"
        f"Рост: {u.get('height')}\n"
        f"Вес: {u.get('weight')}\n"
        f"Где: {u.get('place')}\n"
        f"Опыт: {u.get('exp')}\n"
        f"Частота: {u.get('freq')}×/нед\n"
        f"Ограничения: {(u.get('limits') or 'нет')}"
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
    text = _profile_header(1) + "🎯 Выбери цель:"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_goal())


async def cb_profile_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "🎯 Выбери цель:"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_goal())
    await callback.answer()


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
# ПРОФИЛЬ-МАСТЕР (часть кнопками, часть текстом)
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
        text = _profile_header(3) + "🎂 Напиши возраст числом (например 23):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("sex"))
    elif step == "height":
        await state.set_state(ProfileWizard.height)
        text = _profile_header(4) + "📏 Напиши рост в см (например 178):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("age"))
    elif step == "weight":
        await state.set_state(ProfileWizard.weight)
        text = _profile_header(5) + "⚖️ Напиши вес в кг (например 72.5):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("height"))
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
        text = _profile_header(9) + "⛔️ Есть какие-то ограничения? Напиши текстом (или «нет»):"
        await clean_edit(callback, uid, text, reply_markup=kb_text_step("freq"))
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
    text = _profile_header(3) + "🎂 Напиши возраст числом (например 23):"
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
        await message.answer("Возраст числом, например 23")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, age=age)

    await state.set_state(ProfileWizard.height)
    text = _profile_header(4) + "📏 Напиши рост в см (например 178):"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_text_step("age"))
    await try_delete_user_message(bot, message)


async def profile_height_text(message: Message, state: FSMContext, bot: Bot):
    h = _parse_int_from_text(message.text or "")
    if h is None or h < 120 or h > 230:
        await message.answer("Рост в см, например 178")
        await try_delete_user_message(bot, message)
        return
    await update_user(message.from_user.id, height=h)

    await state.set_state(ProfileWizard.weight)
    text = _profile_header(5) + "⚖️ Напиши вес в кг (например 72.5):"
    await clean_send(bot, message.chat.id, message.from_user.id, text, reply_markup=kb_text_step("height"))
    await try_delete_user_message(bot, message)


async def profile_weight_text(message: Message, state: FSMContext, bot: Bot):
    w = _parse_float_from_text(message.text or "")
    if w is None or w < 30 or w > 250:
        await message.answer("Вес в кг, например 72.5")
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
    text = _profile_header(7) + "📈 Выбери опыт:"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_exp())
    await callback.answer()


async def cb_profile_exp(callback: CallbackQuery, state: FSMContext):
    v = callback.data.split(":")[2]
    if v == "0":
        await update_user(callback.from_user.id, exp="0", freq=3)
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(9) + "⛔️ Есть какие-то ограничения? Напиши текстом (или «нет»):"
        await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("freq"))
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
    text = _profile_header(9) + "⛔️ Есть какие-то ограничения? Напиши текстом (или «нет»):"
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb_text_step("freq"))
    await callback.answer()


async def profile_limits_text(message: Message, state: FSMContext, bot: Bot):
    limits = (message.text or "").strip()
    if not limits:
        await message.answer("Напиши ограничения текстом или «нет».")
        await try_delete_user_message(bot, message)
        return
    if limits.lower() in ("нет", "нету", "никаких", "no"):
        limits = ""

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
        f"Ограничения: {(u.get('limits') or 'нет')}\n\n"
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
# РАЗДЕЛЫ
# =========================
async def ensure_profile_ready(user_id: int) -> bool:
    u = await get_user(user_id)
    need = ["goal", "sex", "age", "height", "weight", "place", "exp", "freq"]
    if any(not u.get(k) for k in need):
        return False
    return True


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


# ✅ ДНЕВНИК: ключевые упражнения (заменили "Тяга (гребля)" -> "Отжимания")
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
    text = "📏 Замеры (дневник)\n\nВыбери замер кнопкой — дата поставится автоматически."
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
        "Отслеживаем только ключевые упражнения — так прогресс виден лучше.\n"
        "Выбери упражнение кнопкой.\n"
        "Дата проставится автоматически — тебе останется ввести только вес и повторы."
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
        "Выбери следующее упражнение кнопкой ниже."
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
# ✅ ЗАМЕРЫ (дневник)
# =========================
async def cb_measure_type(callback: CallbackQuery, state: FSMContext):
    mtype = callback.data.split(":")[1]
    await state.update_data(mtype=mtype)
    await state.set_state(MeasureFlow.enter_value)

    name = dict(MEASURE_TYPES).get(mtype, mtype)
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

    name = dict(MEASURE_TYPES).get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    out = f"✅ Записал: {name} = {val:g}\n\nПоследние:\n{hist}\n\nВыбери следующий замер ниже."
    await clean_send(bot, message.chat.id, message.from_user.id, out, reply_markup=measures_kb())
    await state.set_state(MeasureFlow.choose_type)
    await try_delete_user_message(bot, message)


async def measures_history(callback: CallbackQuery):
    rows = await get_last_measures_any(callback.from_user.id, 30)
    if not rows:
        await callback.message.answer("Истории замеров пока нет. Запиши первый замер.")
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
    await clean_send(bot, message.chat.id, message.from_user.id, "✅ Отправил в поддержку. Я отвечу здесь, как админ отреагирует.")


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

    dp.message.register(profile_age_text, ProfileWizard.age)
    dp.message.register(profile_height_text, ProfileWizard.height)
    dp.message.register(profile_weight_text, ProfileWizard.weight)
    dp.message.register(profile_limits_text, ProfileWizard.limits)

    dp.callback_query.register(cb_tariff, F.data.startswith("tariff:"))
    dp.callback_query.register(cb_i_paid, F.data == "pay_i_paid")
    dp.callback_query.register(admin_actions, F.data.startswith("admin_approve:") | F.data.startswith("admin_reject:"))
    dp.message.register(pay_amount, PaymentFlow.waiting_amount)
    dp.message.register(pay_last4, PaymentFlow.waiting_last4)
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
