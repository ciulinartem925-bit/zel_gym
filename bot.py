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
from aiogram.enums import ParseMode

# =========================
# НАСТРОЙКИ (через ENV — безопасно для GitHub/Render)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_NEW_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ЮКасса — API через REST (shop_id + secret_key из личного кабинета yukassa.ru)
YUKASSA_SHOP_ID  = os.getenv("YUKASSA_SHOP_ID", "")
YUKASSA_SECRET   = os.getenv("YUKASSA_SECRET", "")

# Публичный URL бота (Render/ngrok) для return_url после оплаты
BOT_PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "https://t.me/")  # https://t.me/your_bot

DB_PATH = os.getenv("DB_PATH", "bot.db")
WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "media/welcome.jpg")

# =========================
# СЛОВАРИ МЕДИА-ФАЙЛОВ
# =========================
IMAGE_PATHS = {
    "welcome":      "media2/tech/welcome.jpg",
    "restart":      "media2/tech/restart.jpg",
    "profile":      "media2/tech/profile.jpg",
    "menu":         "media2/tech/menu.jpg",
    # ── Разделы (media/sections/) ────────────────────────────────────────────
    "workouts":     "media2/tech/workouts.jpg",
    "nutrition":    "media2/tech/nutrition.jpg",
    "diary":        "media2/tech/diary.jpg",
    "measurements": "media2/tech/measurements.jpg",
    # ────────────────────────────────────────────────────────────────────────
    "upgrade":      "media2/tech/upgrade.jpg",
    "faq":          "media2/tech/faq.jpg",
}

# Словарь JPG/PNG картинок для техник упражнений.
# Приоритет над GIF: если здесь есть путь и файл существует — отправляем картинку.
# Чтобы добавить картинку: создай файл и пропиши путь ниже — хендлеры менять не нужно.
TECH_IMAGES: Dict[str, str] = {
    # Формат: "ключ_упражнения": "media/tech/<название>.jpg"
    # Примеры (раскомментируй и добавь файл):
    # "squat":          "media/tech/squat.jpg",
    # "bench":          "media/tech/bench.jpg",
    # "deadlift":       "media/tech/deadlift.jpg",
    # "rdl_barbell":    "media/tech/rdl_barbell.jpg",
    # "pullup_wide":    "media/tech/pullup_wide.jpg",
    # "ohp_barbell":    "media/tech/ohp_barbell.jpg",
    # ── Домашние упражнения (раскомментируй и положи файл) ──────────────────
    # "pushup_wide":       "media/tech/pushup_wide.jpg",
    # "pushup_narrow":     "media/tech/pushup_narrow.jpg",
    # "pushup_elevated":   "media/tech/pushup_elevated.jpg",
    # "pike_pushup":       "media/tech/pike_pushup.jpg",
    # "wall_pushup":       "media/tech/wall_pushup.jpg",
    # "pullup_wide":       "media/tech/pullup_wide.jpg",
    # "pullup_chin":       "media/tech/pullup_chin.jpg",
    # "pullup_narrow":     "media/tech/pullup_narrow.jpg",
    # "squat_bw":          "media/tech/squat_bw.jpg",
    # "bulgarian_bw":      "media/tech/bulgarian_bw.jpg",
    # "lunge_bw":          "media/tech/lunge_bw.jpg",
    # "glute_bridge":      "media/tech/glute_bridge.jpg",
    # "plank_bw":          "media/tech/plank_bw.jpg",
    # "leg_raise_lying_bw":"media/tech/leg_raise_lying_bw.jpg",
    # "crunch_bw":         "media/tech/crunch_bw.jpg",
}

# Словарь MP4-видео для техник упражнений.
# Приоритет: TECH_VIDEOS (mp4) > TECH[key]["mp4"] > TECH_IMAGES (jpg) > TECH_GIFS > текст.
#
# Способы подключить видео к упражнению:
#   А) (рекомендуется) Добавь в TECH_VIDEOS ниже:
#        "squat": "media/tech/squat.mp4"
#   Б) Добавь поле "mp4" прямо в TECH[key]:
#        "squat": { "title": "...", "mp4": "media/tech/squat.mp4", "text": "..." }
#   В) TECH_GIFS тоже поддерживает .mp4-пути (send_video автоматически).
#
# Требования к файлу: mp4, до ~50 МБ, рекомендуется 480p, до 30 сек.
# Хендлеры менять не нужно — заработает автоматически.
TECH_VIDEOS: Dict[str, str] = {
    # ── Ноги / нижний блок ──────────────────────────────────────────────────
    # "squat":               "media/tech/squat.mp4",
    # "squat_barbell":       "media/tech/squat_barbell.mp4",
    # "squat_sumo":          "media/tech/squat_sumo.mp4",
    # "squat_bodyweight":    "media/tech/squat_bodyweight.mp4",
    # "goblet":              "media/tech/goblet.mp4",
    # "hack_squat":          "media/tech/hack_squat.mp4",
    # "bulgarian":           "media/tech/bulgarian.mp4",
    # "lunge":               "media/tech/lunge.mp4",
    # "lunge_barbell":       "media/tech/lunge_barbell.mp4",
    # "lunge_walking":       "media/tech/lunge_walking.mp4",
    # "legpress":            "media/tech/legpress.mp4",
    # "legcurl":             "media/tech/legcurl.mp4",
    # "hyperext":            "media/tech/hyperext.mp4",
    # "hinge":               "media/tech/hinge.mp4",
    # "calves":              "media/tech/calves.mp4",
    # ── Грудь / жим ─────────────────────────────────────────────────────────
    # "bench":               "media/tech/bench.mp4",
    # "bench_dumbbell":      "media/tech/bench_dumbbell.mp4",
    # "bench_machine":       "media/tech/bench_machine.mp4",
    # "incline_press_barbell":  "media/tech/incline_press_barbell.mp4",
    # "incline_press_dumbbell": "media/tech/incline_press_dumbbell.mp4",
    # "chest_fly":           "media/tech/chest_fly.mp4",
    # ── Спина / тяга ────────────────────────────────────────────────────────
    # "latpulldown_wide":    "media/tech/latpulldown_wide.mp4",
    # "latpulldown_narrow":  "media/tech/latpulldown_narrow.mp4",
    # "pullup":              "media/tech/pullup.mp4",
    # "pullup_chinup":       "media/tech/pullup_chinup.mp4",
    # "pullup_wide":         "media/tech/pullup_wide.mp4",
    # "rowtrain":            "media/tech/rowtrain.mp4",
    # "dumbbell_row":        "media/tech/dumbbell_row.mp4",
    # "barbell_row":         "media/tech/barbell_row.mp4",
    # "face_pull":           "media/tech/face_pull.mp4",
    # "rear_delt":           "media/tech/rear_delt.mp4",
    # ── Тяга / поясница ─────────────────────────────────────────────────────
    # "rdl_barbell":         "media/tech/rdl_barbell.mp4",
    # "rdl_dumbbell":        "media/tech/rdl_dumbbell.mp4",
    # "deadlift":            "media/tech/deadlift.mp4",
    # "deadlift_sumo":       "media/tech/deadlift_sumo.mp4",
    # ── Плечи ───────────────────────────────────────────────────────────────
    # "ohp_barbell":         "media/tech/ohp_barbell.mp4",
    # "ohp_dumbbell":        "media/tech/ohp_dumbbell.mp4",
    # "lateralraise":        "media/tech/lateralraise.mp4",
    # "pike_pushup":         "media/tech/pike_pushup.mp4",
    # ── Бицепс / трицепс ────────────────────────────────────────────────────
    # "biceps":              "media/tech/biceps.mp4",
    # "biceps_barbell":      "media/tech/biceps_barbell.mp4",
    # "hammer":              "media/tech/hammer.mp4",
    # "triceps":             "media/tech/triceps.mp4",
    # "triceps_oh":          "media/tech/triceps_oh.mp4",
    # "narrow_pushup":       "media/tech/narrow_pushup.mp4",
    # ── Кор / пресс ─────────────────────────────────────────────────────────
    # "core":                "media/tech/core.mp4",
    # "hanging_leg_raise":   "media/tech/hanging_leg_raise.mp4",
    # "ab_rollout":          "media/tech/ab_rollout.mp4",
    # ── Домашние упражнения (раскомментируй и положи mp4-файл) ──────────────
    # "pushup_wide":         "media/tech/pushup_wide.mp4",
    # "pushup_narrow":       "media/tech/pushup_narrow.mp4",
    # "pushup_elevated":     "media/tech/pushup_elevated.mp4",
    # "pike_pushup":         "media/tech/pike_pushup.mp4",
    # "wall_pushup":         "media/tech/wall_pushup.mp4",
    # "pullup_wide":         "media/tech/pullup_wide.mp4",
    # "pullup_chin":         "media/tech/pullup_chin.mp4",
    # "pullup_narrow":       "media/tech/pullup_narrow.mp4",
    # "squat_bw":            "media/tech/squat_bw.mp4",
    # "bulgarian_bw":        "media/tech/bulgarian_bw.mp4",
    # "lunge_bw":            "media/tech/lunge_bw.mp4",
    # "glute_bridge":        "media/tech/glute_bridge.mp4",
    # "plank_bw":            "media/tech/plank_bw.mp4",
    # "leg_raise_lying_bw":  "media/tech/leg_raise_lying_bw.mp4",
    # "crunch_bw":           "media/tech/crunch_bw.mp4",
}

TECH_GIFS = {
    # ── Ноги / нижний блок ──────────────────────────────────────────────────
    "squat_barbell":     "media/tech/squat_barbell.mp4",   # fix: был squat_barbel.gif
    "squat_sumo":        "media/tech/squat_sumo.mp4",
    "goblet":            "media/tech/goblet.mp4",
    "hack_squat":        "media/tech/hack_squat.mp4",
    "bulgarian":         "media/tech/bulgarian.mp4",
    "lunge_barbell":     "media/tech/lunge_barbell.mp4",
    "lunge_dumbbell":    "media/tech/lunge_dumbbell.mp4",
    "lunge_walking":     "media/tech/lunge_walking.mp4",
    "lunge_bw":           "media/tech/lunge_bw.mp4",
    "legpress":          "media/tech/legpress.mp4",
    "legcurl":           "media/tech/legcurl.mp4",
    "hyperext":          "media/tech/hyperext.mp4",
    # ── Грудь / жим ─────────────────────────────────────────────────────────
    "bench_dumbbell":    "media/tech/bench_dumbbell.mp4",
    "bench_machine":     "media/tech/bench_machine.mp4",
    "incline_press_barbell":  "media/tech/incline_press_barbell.mp4",
    "incline_press_dumbbell": "media/tech/incline_press_dumbbell.mp4",
    "chest_fly":         "media/tech/chest_fly.mp4",
    # ── Спина / тяга ────────────────────────────────────────────────────────
    "latpulldown_wide":  "media/tech/latpulldown_wide.mp4",
    "latpulldown_narrow": "media/tech/latpulldown_narrow.gif",
    "pullup_chinup":     "media/tech/pullup_chinup.mp4",
    "pullup_wide":       "media/tech/pullup_wide.mp4",
    "rowtrain":          "media/tech/rowtrain.mp4",
    "dumbbell_row":      "media/tech/dumbbell_row.mp4",
    "barbell_row":       "media/tech/barbell_row.mp4",
    "face_pull":         "media/tech/face_pull.mp4",
    "rear_delt":         "media/tech/rear_delt.mp4",
    # ── Тяга / поясница ─────────────────────────────────────────────────────
    "rdl_barbell":       "media/tech/rdl_barbell.mp4",
    "rdl_dumbbell":      "media/tech/rdl_dumbbell.mp4",
    "deadlift":          "media/tech/deadlift.mp4",
    "deadlift_sumo":     "media/tech/deadlift_sumo.mp4",
    "good_morning":      "media/tech/good_morning.mp4",
    # ── Плечи ───────────────────────────────────────────────────────────────
    "ohp_barbell":       "media/tech/ohp_barbell.mp4",
    "ohp_dumbbell":      "media/tech/ohp_dumbbell.mp4",
    "ohp_machine":       "media/tech/ohp_machine.mp4",
    # ── Бицепс / трицепс ────────────────────────────────────────────────────
    "biceps_barbell":    "media/tech/biceps_barbell.mp4",
    "hammer":            "media/tech/hammer.mp4",
    "triceps_oh":        "media/tech/triceps_oh.mp4",
    "narrow_pushup":     "media/tech/narrow_pushup.mp4",
    # ── Кор / пресс ─────────────────────────────────────────────────────────
    "side_plank":        "media/tech/side_plank.mp4",
    "hanging_leg_raise": "media/tech/hanging_leg_raise.mp4",
    "leg_raise_lying":   "media/tech/leg_raise_lying.mp4",
    "elbow_leg_raise":   "media/tech/elbow_leg_raise.mp4",
    "ab_crunch":         "media/tech/ab_crunch.mp4",
    "ab_rollout":        "media/tech/ab_rollout.mp4",
    # ── Разное ──────────────────────────────────────────────────────────────
    "pushup":            "media/tech/pushup.mp4",  # "row" = отжимания (так в файле)
    "pushup_elevated":   "media/tech/pushup_elevated.mp4",
    # Ноги — изолирующие
    "leg_extension":     "media/tech/leg_extension.mp4",
    "leg_adduction":     "media/tech/leg_adduction.mp4",
    "cable_kickback":    "media/tech/cable_kickback.mp4",
    # Спина
    "tbar_row":          "media/tech/tbar_row.mp4",
    "latpulldown_close": "media/tech/latpulldown_close.mp4",
    # Плечи
    "arnold_press":      "media/tech/arnold_press.mp4",
    "front_raise":       "media/tech/front_raise.mp4",
    # Руки
    "french_press":      "media/tech/french_press.mp4",
    "concentration_curl":"media/tech/concentration_curl.mp4",
    # Кор
    "bicycle_crunch":    "media/tech/bicycle_crunch.mp4",
    "russian_twist":     "media/tech/russian_twist.mp4",
    # ── Домашние упражнения (раскомментируй и положи файл) ──────────────────
    # "pushup_wide":         "media/tech/pushup_wide.mp4",
    # "pushup_narrow":       "media/tech/pushup_narrow.mp4",
    # "pushup_elevated":     "media/tech/pushup_elevated.mp4",
    # "pike_pushup":         "media/tech/pike_pushup.mp4",
    # "wall_pushup":         "media/tech/wall_pushup.mp4",
    # "pullup_chin":         "media/tech/pullup_chin.mp4",
    # "pullup_narrow":       "media/tech/pullup_narrow.mp4",
    # "squat_bw":            "media/tech/squat_bw.mp4",
    # "bulgarian_bw":        "media/tech/bulgarian_bw.mp4",
    # "lunge_bw":            "media/tech/lunge_bw.mp4",
    # "glute_bridge":        "media/tech/glute_bridge.mp4",
    # "plank_bw":            "media/tech/plank_bw.mp4",
    # "leg_raise_lying_bw":  "media/tech/leg_raise_lying_bw.mp4",
    # "crunch_bw":           "media/tech/crunch_bw.mp4",
}

# =========================
# КАНОНИЧЕСКИЕ НАЗВАНИЯ УПРАЖНЕНИЙ
# =========================
EXERCISE_NAMES = {
    # Ноги / нижний блок
    "squat_barbell":     "Присед со штангой",
    "squat_sumo":        "Присед сумо",
    "goblet":            "Гоблет-присед",
    "hack_squat":        "Хакк-присед",
    "bulgarian":         "Болгарские выпады",
    "lunge_barbell":     "Выпады со штангой",
    "lunge_dumbbell":    "Выпады с гантелями",
    "lunge_walking":     "Выпады ходьбой",
    "legpress":          "Жим ногами",
    "legcurl":           "Сгибания ног",
    "hyperext":          "Гиперэкстензия",
    # Грудь / жим
    "bench_dumbbell":    "Жим гантелей лёжа",
    "bench_machine":     "Жим в тренажёре (грудь)",
    "incline_press_barbell":  "Жим под углом (штанга)",
    "incline_press_dumbbell": "Жим под углом (гантели)",
    "chest_fly":         "Разведения на грудь",
    # Спина / тяга
    "latpulldown_wide":  "Тяга верхнего блока широким хватом",
    "latpulldown_narrow":"Тяга верхнего блока узким хватом",
    "pullup_chinup":     "Подтягивания обратным хватом",
    "pullup_wide":       "Подтягивания широким хватом",
    "rowtrain":          "Тяга горизонтального блока",
    "dumbbell_row":      "Тяга гантели",
    "barbell_row":       "Тяга штанги в наклоне",
    "face_pull":         "Face Pull",
    "rear_delt":         "Задняя дельта",
    # Тяга / поясница
    "rdl_barbell":       "Румынская тяга со штангой",
    "rdl_dumbbell":      "Румынская тяга с гантелями",
    "deadlift":          "Становая тяга",
    "deadlift_sumo":     "Становая тяга сумо",
    "good_morning":      "Good Morning",
    # Плечи
    "ohp_barbell":       "Жим штанги стоя (OHP)",
    "ohp_dumbbell":      "Жим гантелей стоя/сидя",
    "ohp_machine":       "Жим в тренажёре вверх",
    # Бицепс / трицепс
    "biceps_barbell":    "Сгибания со штангой (бицепс)",
    "hammer":            "Молотки",
    "triceps_oh":        "Разгибание трицепса из-за головы",
    "narrow_pushup":     "Отжимания узкие",
    # Кор / пресс
    "side_plank":        "Боковая планка",
    "hanging_leg_raise": "Подъёмы ног в висе",
    "leg_raise_lying":   "Подъёмы ног лёжа",
    "elbow_leg_raise":   "Подъёмы ног в упоре на локтях",
    "ab_crunch":         "Скручивания (пресс)",
    "ab_rollout":        "Ролик для пресса",
    # Разное
    "pushup_elevated":   "Отжимания с ногами на возвышении",
    # Ноги — изолирующие
    "leg_extension":     "Разгибания ног в тренажёре",
    "leg_adduction":     "Сведения ног в тренажёре",
    "cable_kickback":    "Отведения ног назад в кроссовере",
    # Грудь
    "incline_pushup":    "Отжимания с ногами на возвышении (грудь)",
    # Спина
    "tbar_row":          "Тяга Т-грифа",
    "latpulldown_close": "Тяга к груди узким нейтральным хватом",
    # Плечи
    "arnold_press":      "Арнольд-жим",
    "front_raise":       "Подъёмы перед собой",
    # Руки
    "french_press":      "Французский жим лёжа",
    "concentration_curl":"Концентрированные сгибания",
    # Кор
    "bicycle_crunch":    "Велосипед",
    "russian_twist":     "Русские скручивания",
}

# ТАРИФЫ
TARIFFS = {
    "t1":    {"title": "1 месяц",                "days": 30,   "price": 2,  "plan_regens": 3},
    "t3":    {"title": "3 месяца",               "days": 90,   "price": 3,  "plan_regens": 10},
    "life":  {"title": "Навсегда",               "days": None, "price": 1990, "plan_regens": None},
}

# Полный доступ (питание + все цели + смена программы) только на t3 и life
FULL_ACCESS_TARIFFS = {"t3", "life"}

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


class NutritionLog(StatesGroup):
    enter_calories = State()


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
    "legpress": {
        "title": "Жим ногами",
        "mp4": "media/tech/legpress.mp4",
        "text": (
            "📚 Жим ногами\n\n"
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
    # ✅ Новые техники для упражнений без покрытия
    "hanging_leg_raise": {
        "title": "Подъёмы ног в висе",
        "mp4": "media/tech/hanging_leg_raise.mp4",
        "text": (
            "📚 Подъёмы ног в висе (нижний пресс)\n\n"
            "✅ Настройка\n"
            "• Повисни на перекладине, хват чуть шире плеч.\n"
            "• Плечи активны — не вис «трупом», держи лопатки.\n\n"
            "✅ Как делать\n"
            "1) Слегка согни колени и подними ноги к груди/животу.\n"
            "2) Скручивай таз вверх в конце — это ключевое движение.\n"
            "3) Медленно опускай ноги вниз — 2–3 сек.\n"
            "4) Не раскачивайся и не помогай инерцией.\n\n"
            "⚠️ Частые ошибки\n"
            "• Ноги просто «болтаются» → не задействован нижний пресс.\n"
            "• Раскачка корпуса → снизь амплитуду, контролируй.\n\n"
            "💡 Прогрессия\n"
            "Согнутые колени → прямые ноги → уголок 90° → медленно."
        )
    },
    "leg_raise_lying": {
        "title": "Подъёмы ног лёжа",
        "mp4": "media/tech/leg_raise_lying.mp4",
        "text": (
            "📚 Подъёмы ног лёжа (нижний пресс)\n\n"
            "✅ Настройка\n"
            "• Лечь на спину, руки вдоль тела или под поясницу.\n"
            "• Поясница прижата к полу на протяжении всего движения.\n\n"
            "✅ Как делать\n"
            "1) Подними прямые ноги до 90° (или до угла с контролем).\n"
            "2) Медленно опускай — не касаясь пола, остановись в 5–10 см.\n"
            "3) Продолжай без отдыха для нижнего пресса.\n\n"
            "⚠️ Частые ошибки\n"
            "• Поясница отрывается → согни колени или уменьши амплитуду.\n"
            "• Слишком быстро → нет нагрузки на пресс.\n\n"
            "💡 Подсказка\n"
            "Чем ниже ноги к полу при опускании, тем тяжелее — найди свой уровень."
        )
    },
    "ab_crunch": {
        "title": "Скручивания (пресс)",
        "mp4": "media/tech/ab_crunch.mp4",
        "text": (
            "📚 Скручивания (верхний пресс)\n\n"
            "✅ Настройка\n"
            "• Лечь на спину, колени согнуты, стопы на полу.\n"
            "• Руки за голову (лёгкое касание) или скрещены на груди.\n\n"
            "✅ Как делать\n"
            "1) Скручивай верхнюю часть туловища, отрывая лопатки от пола.\n"
            "2) Пупок к коленям — не голова к коленям.\n"
            "3) Пауза 1 сек вверху, медленно вниз.\n\n"
            "⚠️ Частые ошибки\n"
            "• Тянешь голову руками → шея болит, пресс не работает.\n"
            "• Полный подъём (сидя) → поясница включается, пресс выключается.\n\n"
            "💡 Подсказка\n"
            "Скручивание — это короткое движение, не подъём корпуса."
        )
    },
    "ab_rollout": {
        "title": "Ролик для пресса",
        "mp4": "media/tech/ab_rollout.mp4",
        "text": (
            "📚 Ролик для пресса (ab wheel)\n\n"
            "✅ Как делать\n"
            "1) Стоя на коленях, ролик перед тобой.\n"
            "2) Медленно катись вперёд — спина прямая, пресс напряжён.\n"
            "3) Катись до предела с ровной спиной, пауза.\n"
            "4) Тяни ролик обратно прессом, не поясницей.\n\n"
            "⚠️ Частые ошибки\n"
            "• Поясница провисает → катись меньше, пока пресс не окрепнет.\n"
            "• Рывок обратно → медленнее, используй пресс.\n\n"
            "💡 Прогрессия\n"
            "Начни с короткой амплитудой. Цель — полное выкатывание с прямым телом."
        )
    },
    "side_plank": {
        "title": "Боковая планка",
        "mp4": "media/tech/side_plank.mp4",
        "text": (
            "📚 Боковая планка (косые мышцы + стабилизация)\n\n"
            "✅ Настройка\n"
            "• Упор на предплечье (локоть под плечом), тело — прямая линия сбоку.\n"
            "• Ноги прямые или одна на другой для баланса.\n\n"
            "✅ Как делать\n"
            "1) Подними таз — тело от головы до пяток одна линия.\n"
            "2) Не давай тазу проваливаться вниз или задираться.\n"
            "3) Дыши равномерно, держи заданное время.\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз провисает → сокращай время, держи ровно.\n"
            "• Плечо «тянет» → опусти его вниз от уха.\n\n"
            "💡 Прогрессия\n"
            "Статика → подъёмы ноги/руки → динамические скручивания."
        )
    },
    "elbow_leg_raise": {
        "title": "Подъёмы ног в упоре на локтях",
        "mp4": "media/tech/elbow_leg_raise.mp4",
        "text": (
            "📚 Подъёмы ног в упоре на локтях (нижний пресс)\n\n"
            "✅ Настройка\n"
            "• Упор на локти и предплечья (как в планке).\n"
            "• Спина прямая, пресс включён.\n\n"
            "✅ Как делать\n"
            "1) Поднимай колени к груди, скручивая таз.\n"
            "2) Пауза в верхней точке — сожми пресс.\n"
            "3) Медленно опускай ноги, не касаясь пола.\n\n"
            "⚠️ Частые ошибки\n"
            "• Качаешься на опоре → замедлись.\n"
            "• Спина прогибается → напряги корпус сильнее.\n\n"
            "💡 Подсказка\n"
            "Удобнее чем подъёмы в висе — хорошо для новичков."
        )
    },
    "goblet": {
        "title": "Гоблет-присед",
        "mp4": "media/tech/goblet.mp4",
        "text": (
            "📚 Гоблет-присед (ноги + корпус)\n\n"
            "✅ Настройка\n"
            "• Держи гантель/гирю у груди двумя руками.\n"
            "• Стопы чуть шире плеч, носки наружу.\n\n"
            "✅ Как делать\n"
            "1) Приседай глубоко, локти давят на колени изнутри.\n"
            "2) Спина прямая, грудь смотрит вперёд.\n"
            "3) Вставай, выталкивая пол ногами.\n\n"
            "⚠️ Частые ошибки\n"
            "• Наклон вперёд → подними вес выше, к груди.\n"
            "• Пятки отрываются → чуть шире стойка.\n\n"
            "💡 Плюс\n"
            "Отличная альтернатива обычному приседу — безопаснее для спины."
        )
    },
    "hyperext": {
        "title": "Гиперэкстензия",
        "mp4": "media/tech/hyperext.mp4",
        "text": (
            "📚 Гиперэкстензия (поясница + ягодицы)\n\n"
            "✅ Как делать\n"
            "1) Зафиксируй ноги, опустись параллельно полу.\n"
            "2) Поднимайся до прямой линии тела (не перегибайся!).\n"
            "3) Пауза вверху — сожми ягодицы.\n\n"
            "⚠️ Частые ошибки\n"
            "• Переразгибание поясницы → останавливайся на прямой линии.\n"
            "• Руки тянут шею → держи их у груди или за головой без усилия.\n\n"
            "💡 Подсказка\n"
            "Чувствуешь ягодицы больше, чем поясницу? Значит, делаешь правильно."
        )
    },
    "legcurl": {
        "title": "Сгибания ног",
        "mp4": "media/tech/legcurl.mp4",
        "text": (
            "📚 Сгибания ног (бицепс бедра)\n\n"
            "✅ Как делать\n"
            "1) Лечь на тренажёр, ось вращения — под коленями.\n"
            "2) Сгибай ноги до максимума, пауза 1 сек.\n"
            "3) Опускай медленно (2–3 сек).\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз отрывается → снизь вес.\n"
            "• Слишком быстро → нет нагрузки на бицепс бедра.\n\n"
            "💡 Подсказка\n"
            "Носки на себя — немного активирует икры, от себя — чище бицепс бедра."
        )
    },
    "rowtrain": {
        "title": "Тяга горизонтального блока",
        "mp4": "media/tech/latpulldown.mp4",
        "text": (
            "📚 Тяга горизонтального блока (середина спины)\n\n"
            "✅ Настройка\n"
            "• Сядь, ноги согнуты, спина прямая, не округлена.\n\n"
            "✅ Как делать\n"
            "1) Начни тягу с лопаток: сведи их вместе.\n"
            "2) Тяни к поясу, локти близко к телу.\n"
            "3) Пауза 1 сек в конце, затем медленно вперёд.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка корпусом → снизь вес.\n"
            "• Тянешь руками → думай «свожу лопатки».\n\n"
            "💡 Подсказка\n"
            "Нейтральный хват (ладони друг к другу) — комфортнее для плеч."
        )
    },
    "dumbbell_row": {
        "title": "Тяга гантели",
        "mp4": "media/tech/latpulldown.mp4",
        "text": (
            "📚 Тяга гантели одной рукой (спина)\n\n"
            "✅ Настройка\n"
            "• Упрись коленом и рукой в скамью, спина параллельна полу.\n\n"
            "✅ Как делать\n"
            "1) Тяни гантель к поясу, локоть вдоль тела.\n"
            "2) В верхней точке — лопатка сведена, пауза.\n"
            "3) Опускай медленно до полного растяжения.\n\n"
            "⚠️ Частые ошибки\n"
            "• Ротация корпуса → зафиксируй корпус.\n"
            "• Тянешь вверх, а не к поясу → меняй траекторию.\n\n"
            "💡 Подсказка\n"
            "Отличное упражнение для асимметрий — каждая сторона работает отдельно."
        )
    },
    "good_morning": {
        "title": "Good Morning",
        "mp4": "media/tech/good_morning.mp4",
        "text": (
            "📚 Good Morning (поясница + задняя поверхность бедра)\n\n"
            "✅ Как делать\n"
            "1) Ноги на ширине плеч, руки за головой.\n"
            "2) Небольшой сгиб в коленях, наклон вперёд с прямой спиной.\n"
            "3) Наклоняйся до параллели с полом (или до натяжения в бёдрах).\n"
            "4) Возвращайся, выталкивая таз вперёд.\n\n"
            "⚠️ Частые ошибки\n"
            "• Спина округляется → сделай амплитуду меньше.\n"
            "• Слишком тяжело → начни совсем без веса.\n\n"
            "💡 Важно\n"
            "Очень лёгкое упражнение по весу — здесь важна техника, не нагрузка."
        )
    },
    "face_pull": {
        "title": "Face Pull",
        "mp4": "media/tech/face_pull.mp4",
        "text": (
            "📚 Face Pull / Тяга к лицу (задняя дельта + ротаторы)\n\n"
            "✅ Как делать\n"
            "1) Блок/резинка на уровне лица или чуть выше.\n"
            "2) Тяни к лицу, локти выше плеч.\n"
            "3) В конце разведи руки, как будто показываешь «двойной бицепс».\n"
            "4) Медленно возвращайся.\n\n"
            "⚠️ Частые ошибки\n"
            "• Локти ниже плеч → нагрузка уходит со средней дельты.\n"
            "• Раскачка корпусом → снизь вес.\n\n"
            "💡 Подсказка\n"
            "Отличная профилактика травм плеча — делай регулярно."
        )
    },
    "rear_delt": {
        "title": "Задняя дельта",
        "mp4": "media/tech/rear_delt.mp4",
        "text": (
            "📚 Задняя дельта (гантели или тренажёр)\n\n"
            "✅ Как делать (гантели в наклоне)\n"
            "1) Наклон корпуса 45–90°, гантели свисают вниз.\n"
            "2) Разводи руки в стороны, локти слегка согнуты.\n"
            "3) В верхней точке — пауза, лопатки сведены.\n"
            "4) Медленно вниз.\n\n"
            "⚠️ Частые ошибки\n"
            "• Поднимаешь плечи к ушам → расслабь их вниз.\n"
            "• Слишком тяжело → нет контроля в задней дельте.\n\n"
            "💡 Подсказка\n"
            "Вес меньше, чем кажется нужным — задняя дельта маленькая мышца."
        )
    },
    "hammer": {
        "title": "Молотки",
        "mp4": "media/tech/hammer.mp4",
        "text": (
            "📚 Молотки (бицепс + брахиалис)\n\n"
            "✅ Как делать\n"
            "1) Держи гантели нейтральным хватом (ладони друг к другу).\n"
            "2) Сгибай до пика, локти у корпуса.\n"
            "3) Медленно опускай, полная амплитуда.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка корпусом → снизь вес.\n"
            "• Локти уезжают вперёд → зафиксируй их у бёдер.\n\n"
            "💡 Подсказка\n"
            "Молотки лучше прорабатывают брахиалис — делает руку визуально толще."
        )
    },
    "triceps_oh": {
        "title": "Разгибание трицепса из-за головы",
        "mp4": "media/tech/triceps_oh.mp4",
        "text": (
            "📚 Разгибание трицепса из-за головы (длинная головка)\n\n"
            "✅ Как делать\n"
            "1) Держи гантель двумя руками, подними над головой.\n"
            "2) Опусти за голову, локти смотрят вверх.\n"
            "3) Разгибай до прямых рук, медленно опускай.\n\n"
            "⚠️ Частые ошибки\n"
            "• Локти расходятся в стороны → держи их узко.\n"
            "• Шея напрягается → расслабь голову вперёд.\n\n"
            "💡 Подсказка\n"
            "Длинная головка трицепса лучше растягивается — делает руку «объёмнее»."
        )
    },
    "narrow_pushup": {
        "title": "Отжимания узкие (трицепс)",
        "mp4": "media/tech/narrow_pushup.mp4",
        "text": (
            "📚 Узкие отжимания\n"
            "Зачем: акцент на трицепс (все три головки) + грудь.\n\n"
            "✅ Настройка\n"
            "• Ладони под грудью, расстояние между ними 10–20 см\n"
            "• Можно треугольником (большие и указательные пальцы соприкасаются)\n"
            "• Тело — прямая линия от головы до пяток, пресс включён\n\n"
            "✅ Выполнение по шагам\n"
            "1. Вдох — медленно (2–3 сек) опускай грудь к рукам\n"
            "2. Локти идут строго назад вдоль корпуса (не в стороны!)\n"
            "3. Грудь почти касается рук — полная амплитуда\n"
            "4. Пауза 1 сек внизу — трицепс под нагрузкой\n"
            "5. Выдох — жми руками в пол, выжимай тело вверх\n"
            "6. Наверху локти не «замыкаются» полностью\n\n"
            "⚠️ Частые ошибки\n"
            "• Локти уходят в стороны → нагрузка переходит на грудь, а не трицепс\n"
            "• Таз провисает → напряги пресс и ягодицы\n"
            "• Слишком узкий хват «треугольник» без подготовки → дискомфорт в запястьях\n"
            "• Неполная амплитуда → опускайся максимально глубоко\n\n"
            "💡 Подсказки\n"
            "Чем уже хват → тем больше нагрузки на трицепс.\n"
            "Паузы внизу (2–3 сек) в разы увеличивают нагрузку на трицепс.\n"
            "Хочешь сложнее — ноги на скамью (сложнее, чем узкие от пола)."
        )
    },
    "hack_squat": {
        "title": "Хакк-присед",
        "mp4": "media/tech/hack_squat.mp4",
        "text": (
            "📚 Хакк-присед (квадрицепс)\n\n"
            "✅ Как делать\n"
            "1) В тренажёре: спина прижата к подушке, стопы на платформе.\n"
            "2) Опускай платформу до 90° в коленях или ниже.\n"
            "3) Жми вверх без «замка» в коленях.\n\n"
            "⚠️ Частые ошибки\n"
            "• Поясница отрывается от подушки → снизь амплитуду.\n"
            "• Колени внутрь → следи за траекторией.\n\n"
            "💡 Подсказка\n"
            "Стопы выше = больше нагрузки на ягодицы. Ниже = больше на квадрицепс."
        )
    },
    "bulgarian": {
        "title": "Болгарские выпады",
        "mp4": "media/tech/bulgarian.mp4",
        "text": (
            "📚 Болгарские выпады (ноги + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Задняя нога на скамье, передняя — на шаг вперёд.\n\n"
            "✅ Как делать\n"
            "1) Опускайся вертикально, заднее колено к полу.\n"
            "2) Переднее колено не выходит за носок.\n"
            "3) Выталкивайся передней ногой вверх.\n\n"
            "⚠️ Частые ошибки\n"
            "• Шаг слишком короткий → коленный сустав перегружается.\n"
            "• Корпус заваливается → держи грудь вертикально.\n\n"
            "💡 Подсказка\n"
            "Тяжелее обычных приседаний — начни без веса."
        )
    },
    "deadlift": {
        "title": "Становая тяга",
        "mp4": "media/tech/deadlift.mp4",
        "text": (
            "📚 Становая тяга\n"
            "Зачем: заднее бедро, ягодицы, весь разгибатель спины, трапеции.\n"
            "Одно из лучших упражнений для всего тела.\n\n"
            "✅ Настройка\n"
            "• Стопы на ширине бёдер, носки чуть наружу\n"
            "• Гриф над серединой стопы, касается голеней\n"
            "• Хват чуть шире бёдер (прямой или разнохват)\n"
            "• Лопатки опущены, спина прямая\n\n"
            "✅ Выполнение по шагам\n"
            "1. Вдох и напряги пресс — корпус как корсет\n"
            "2. Взгляд вперёд, не вниз\n"
            "3. Толкай пол ногами, одновременно разгибая спину\n"
            "4. Гриф идёт строго вертикально — не отводи от ног\n"
            "5. Колени и бёдра разгибаются одновременно\n"
            "6. Наверху: бёдра вперёд, не перегибайся в пояснице\n"
            "7. Опускай под контролем по той же траектории\n\n"
            "⚠️ Частые ошибки\n"
            "• Округление поясницы → СРАЗУ снизь вес, это травмоопасно\n"
            "• Гриф отходит от ног → тяни штангу к себе всё движение\n"
            "• Рывок с пола → плавный старт, набери напряжение\n"
            "• Перегиб в пояснице наверху → стой прямо, не «открывайся» назад\n\n"
            "💡 Подсказки\n"
            "Начинай с пустым грифом — ставь технику недели 2–3.\n"
            "Спина сразу округляется → попробуй румынскую тягу сначала."
        )
    },
    "barbell_row": {
        "title": "Тяга штанги в наклоне",
        "mp4": "media/tech/barbell_row.mp4",
        "text": (
            "📚 Тяга штанги в наклоне (середина спины)\n\n"
            "✅ Настройка\n"
            "• Наклон 45°, спина прямая, штанга под плечами.\n"
            "• Хват немного шире плеч (прямой или обратный).\n\n"
            "✅ Как делать\n"
            "1) Тяни штангу к поясу, начинай с лопаток.\n"
            "2) Локти идут вдоль тела назад.\n"
            "3) Пауза 1 сек — лопатки сведены.\n"
            "4) Медленно опускай до выпрямления рук.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка корпусом → снизь вес.\n"
            "• Тянешь руками → думай «локти назад».\n\n"
            "💡 Подсказка\n"
            "Одно из лучших упражнений для толщины спины."
        )
    },
    "ohp_barbell": {
        "title": "Жим штанги стоя (OHP)",
        "mp4": "media/tech/ohp_barbell.mp4",
        "text": (
            "📚 Жим штанги стоя — Overhead Press\n\n"
            "✅ Настройка\n"
            "• Хват чуть шире плеч, локти немного вперёд.\n"
            "• Пресс напряжён, ягодицы включены, ноги устойчиво.\n\n"
            "✅ Как делать\n"
            "1) Жми штангу вертикально вверх.\n"
            "2) В верхней точке голова выходит вперёд «в окно».\n"
            "3) Опускай медленно под контролем.\n\n"
            "⚠️ Частые ошибки\n"
            "• Прогиб в пояснице → напряги пресс.\n"
            "• Локти уходят назад → держи их под грифом.\n\n"
            "💡 Базовое упражнение для плеч — добавляет ширину."
        )
    },
    # ── Новые уникальные техники для вариаций ──────────────────────────────

    "squat_barbell": {
        "title": "Присед со штангой",
        "mp4": "media/tech/squat_barbell.mp4",
        "text": (
            "📚 Присед со штангой (квадрицепс + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Штанга на трапеции (низкое положение) или на верху (высокое).\n"
            "• Стопы чуть шире плеч, носки наружу 15–30°.\n"
            "• Вдох «в живот», пресс и спина в тонусе.\n\n"
            "✅ Как делать\n"
            "1) Таз назад и вниз одновременно.\n"
            "2) Колени по линии носков, не заваливай внутрь.\n"
            "3) Грудь смотрит вперёд, взгляд прямо или чуть вниз.\n"
            "4) В нижней точке — контроль, глубина до параллели или ниже.\n"
            "5) Вставай, выдох в усилии, не «замок» в коленях.\n\n"
            "⚠️ Частые ошибки\n"
            "• Штанга откатывается на шее → напряги трапецию.\n"
            "• Колени внутрь → думай «раздвигаю пол».\n"
            "• Поясница округляется → меньше глубина или улучши мобильность.\n\n"
            "💡 Безопасность\n"
            "Страховочные стойки всегда готовы. Начинай с лёгкого веса."
        )
    },
    "squat_sumo": {
        "title": "Присед сумо",
        "mp4": "media/tech/squat_sumo.mp4",
        "text": (
            "📚 Присед сумо (приводящие + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Широкая стойка: ноги значительно шире плеч.\n"
            "• Носки развёрнуты сильно наружу (45–60°).\n"
            "• Спина прямая, корпус вертикален.\n\n"
            "✅ Как делать\n"
            "1) Опускайся вниз, колени разводи по направлению носков.\n"
            "2) Таз уходит прямо вниз (не назад как в обычном приседе).\n"
            "3) Бёдра в нижней точке — параллельно полу или ниже.\n"
            "4) Вставай, выталкивая пол широко в стороны.\n\n"
            "⚠️ Частые ошибки\n"
            "• Колени заваливаются внутрь → разводи активно наружу.\n"
            "• Наклон корпуса вперёд → держи грудь выше.\n\n"
            "💡 Плюс\n"
            "Хорошо нагружает приводящие и ягодицы — отличная альтернатива обычному приседу."
        )
    },
    "bench_dumbbell": {
        "title": "Жим гантелей лёжа",
        "mp4": "media/tech/bench_dumbbell.mp4",
        "text": (
            "📚 Жим гантелей лёжа (грудь + трицепс)\n\n"
            "✅ Настройка\n"
            "• Лопатки сведены и прижаты к скамье.\n"
            "• Гантели на уровне груди, предплечья вертикальны.\n"
            "• Стопы устойчиво на полу.\n\n"
            "✅ Как делать\n"
            "1) Жми гантели вверх, слегка сводя их над грудью.\n"
            "2) Локти ~45° к корпусу (не 90° в стороны).\n"
            "3) Амплитуда: опускай ниже, чем со штангой — больше растяжение груди.\n"
            "4) Наверху — лёгкое сведение гантелей, пауза 1 сек.\n\n"
            "⚠️ Частые ошибки\n"
            "• Локти разъезжаются в стороны → плечи болят.\n"
            "• Гантели «разбегаются» вниз → контролируй траекторию.\n\n"
            "💡 Плюс гантелей\n"
            "Бо́льшая амплитуда и независимость рук — лучше для симметрии груди."
        )
    },
    "bench_machine": {
        "title": "Жим в тренажёре (грудь)",
        "mp4": "media/tech/bench_machine.mp4",
        "text": (
            "📚 Жим в тренажёре — грудь\n\n"
            "✅ Настройка\n"
            "• Настрой сиденье: рукояти на уровне середины груди.\n"
            "• Спина прижата к спинке, лопатки сведены.\n"
            "• Хват нейтральный или прямой — по конструкции тренажёра.\n\n"
            "✅ Как делать\n"
            "1) Жми вперёд до лёгкого сгибания локтей (не «замок»).\n"
            "2) Медленно возвращай рукояти к груди — полная амплитуда.\n"
            "3) Лопатки держи сведёнными на протяжении всего движения.\n\n"
            "⚠️ Частые ошибки\n"
            "• Плечи поднимаются к ушам → опусти их вниз.\n"
            "• Неполная амплитуда → теряешь растяжение груди.\n\n"
            "💡 Плюс тренажёра\n"
            "Безопаснее для суставов, идеален для изоляции и добивки."
        )
    },
    "ohp_dumbbell": {
        "title": "Жим гантелей стоя/сидя",
        "mp4": "media/tech/ohp_dumbbell.mp4",
        "text": (
            "📚 Жим гантелей над головой (плечи)\n\n"
            "✅ Настройка\n"
            "• Гантели на уровне плеч, локти под запястьями.\n"
            "• Пресс напряжён, поясница нейтральна.\n"
            "• Сидя: спина вертикально, ноги упираются в пол.\n\n"
            "✅ Как делать\n"
            "1) Жми гантели вертикально вверх, слегка сближая их.\n"
            "2) В верхней точке — руки почти прямые, без рывка.\n"
            "3) Медленно опускай к плечам, локти под 90°.\n\n"
            "⚠️ Частые ошибки\n"
            "• Прогиб в пояснице → напряги корпус или сядь.\n"
            "• Гантели уходят вперёд → держи их над плечами.\n\n"
            "💡 Плюс гантелей\n"
            "Независимость рук — работает на симметрию, меньше нагрузки на суставы."
        )
    },
    "ohp_machine": {
        "title": "Жим в тренажёре вверх",
        "mp4": "media/tech/ohp_machine.mp4",
        "text": (
            "📚 Жим в тренажёре над головой (плечи)\n\n"
            "✅ Настройка\n"
            "• Настрой сиденье: рукояти на уровне плеч.\n"
            "• Спина прижата, поясница нейтральна.\n\n"
            "✅ Как делать\n"
            "1) Жми рукояти вверх до лёгкого выпрямления рук.\n"
            "2) Медленно опускай до уровня плеч — полная амплитуда.\n"
            "3) Плечи вниз, не поднимай к ушам.\n\n"
            "⚠️ Частые ошибки\n"
            "• Спина отрывается от сиденья → снизь вес.\n"
            "• Неполный диапазон → тренажёр задаёт путь, используй его полностью.\n\n"
            "💡 Плюс тренажёра\n"
            "Идеален для новичков и добивки: стабилизация минимальна, фокус на дельтах."
        )
    },
    "rdl_barbell": {
        "title": "Румынская тяга со штангой",
        "mp4": "media/tech/rdl_barbell.mp4",
        "text": (
            "📚 Румынская тяга со штангой (задняя поверхность бедра)\n\n"
            "✅ Настройка\n"
            "• Хват чуть шире бёдер (прямой или разнохват).\n"
            "• Стопы на ширине бёдер, колени слегка согнуты.\n"
            "• Лопатки опусти и зафиксируй.\n\n"
            "✅ Как делать\n"
            "1) Таз назад, штанга скользит по ногам вниз.\n"
            "2) Спина прямая — не округляй поясницу.\n"
            "3) Опускайся до сильного натяжения в бёдрах.\n"
            "4) Возвращайся: таз вперёд, не перегибай поясницу.\n\n"
            "⚠️ Частые ошибки\n"
            "• Штанга отходит от ног → тяни её ближе, риск для спины.\n"
            "• Превращается в присед → меньше сгибай колени.\n"
            "• Поясница округляется → укороти амплитуду.\n\n"
            "💡 Безопасность\n"
            "Начинай с лёгкого веса — поясница уязвима при нарушении техники."
        )
    },
    "rdl_dumbbell": {
        "title": "Румынская тяга с гантелями",
        "mp4": "media/tech/rdl_dumbbell.mp4",
        "text": (
            "📚 Румынская тяга с гантелями (бёдра + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Гантели держи перед бёдрами, ладонями к себе или нейтрально.\n"
            "• Стопы на ширине бёдер, колени слегка согнуты.\n\n"
            "✅ Как делать\n"
            "1) Таз назад, гантели скользят вниз по ногам.\n"
            "2) Гантели держи ближе к телу, не уводи вперёд.\n"
            "3) Спина прямая, до натяжения в бёдрах.\n"
            "4) Возвращайся: таз вперёд, сжимай ягодицы наверху.\n\n"
            "⚠️ Частые ошибки\n"
            "• Гантели уходят вперёд → риск для спины, держи у ног.\n"
            "• Колени сгибаются слишком → меньше работают бёдра.\n\n"
            "💡 Плюс гантелей\n"
            "Нейтральный хват удобнее для запястий, хорошо для одностороннего варианта."
        )
    },
    "lunge_barbell": {
        "title": "Выпады со штангой",
        "mp4": "media/tech/lunge_barbell.mp4",
        "text": (
            "📚 Выпады со штангой (ноги + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Штанга на трапеции, как при приседе.\n"
            "• Стойка нейтральная, корпус прямой.\n\n"
            "✅ Как делать\n"
            "1) Шаг вперёд широкий, переднее колено не за носком.\n"
            "2) Заднее колено опускается почти до пола.\n"
            "3) Отталкивайся передней ногой назад.\n"
            "4) Меняй ноги или сделай все на одну, затем другую.\n\n"
            "⚠️ Частые ошибки\n"
            "• Корпус падает вперёд → держи штангу устойчиво на трапеции.\n"
            "• Колено внутрь → следи за выравниванием.\n\n"
            "💡 Безопасность\n"
            "Освой выпады с гантелями или без веса перед добавлением штанги."
        )
    },
    "lunge_dumbbell": {
        "title": "Выпады с гантелями",
        "mp4": "media/tech/lunge_dumbbell.mp4",
        "text": (
            "📚 Выпады с гантелями (ноги + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Гантели в опущенных руках по бокам.\n"
            "• Стойка прямая, взгляд вперёд.\n\n"
            "✅ Как делать\n"
            "1) Широкий шаг вперёд, переднее колено не за носком.\n"
            "2) Заднее колено опускается почти до пола.\n"
            "3) Оттолкнись передней ногой и вернись.\n"
            "4) Гантели держи ровно, не качайся в стороны.\n\n"
            "⚠️ Частые ошибки\n"
            "• Корпус раскачивается → напряги корпус.\n"
            "• Шаг слишком короткий → коленный сустав перегружается.\n\n"
            "💡 Удобство гантелей\n"
            "Нагрузка управляемее чем со штангой — хорошо для отработки техники."
        )
    },
    "lunge_walking": {
        "title": "Выпады ходьбой",
        "mp4": "media/tech/lunge_walking.mp4",
        "text": (
            "📚 Выпады ходьбой (динамика + координация)\n\n"
            "✅ Как делать\n"
            "1) Широкий шаг вперёд — опустился в выпад.\n"
            "2) Оттолкнулся и сразу шаг другой ногой вперёд.\n"
            "3) Двигайся вперёд плавно, без раскачки корпуса.\n"
            "4) Колено передней ноги не заходит за носок.\n\n"
            "⚠️ Частые ошибки\n"
            "• Корпус качается из стороны в сторону → медленнее.\n"
            "• Маленький шаг → коленный сустав перегружается.\n\n"
            "💡 Подсказка\n"
            "Хорошо развивает баланс и координацию. Начинай без веса."
        )
    },
    "lunge_bw": {
        "title": "Выпады (свой вес)",
        "mp4": "media/tech/lunge_bw.mp4",
        "text": (
            "📚 Выпады (ноги + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Стань прямо, ноги вместе.\n"
            "• Шаг вперёд — широкий, не узкий.\n\n"
            "✅ Как выполнять\n"
            "1) Шагни вперёд, опусти заднее колено к полу.\n"
            "2) Переднее бедро параллельно полу.\n"
            "3) Оттолкнись передней ногой, вернись в старт.\n\n"
            "⚠️ Частые ошибки\n"
            "• Переднее колено за носок — шагай шире.\n"
            "• Корпус наклоняется — держи вертикально.\n\n"
            "💡 Подсказка\n"
            "Делай попеременно обе ноги или все повторы на одну, потом другую."
        )
    },
    "pullup_chinup": {
        "title": "Подтягивания обратным хватом",
        "mp4": "media/tech/pullup_chinup.mp4",
        "text": (
            "📚 Подтягивания обратным хватом (бицепс + спина)\n\n"
            "✅ Как делать\n"
            "1) Хват снизу (ладони к себе), чуть уже плеч.\n"
            "2) Плечи вниз, лопатки включились — начало движения.\n"
            "3) Тяни грудь к перекладине, локти идут вдоль тела.\n"
            "4) Вниз медленно — 2–3 сек, полная амплитуда.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачиваешь корпус → сбавь темп.\n"
            "• Подбородок не доходит до перекладины → снизь вес/добавь резинку.\n\n"
            "💡 Vs. прямой хват\n"
            "Обратный хват больше нагружает бицепс, прямой — широчайшие. Чередуй."
        )
    },
    "pullup_wide": {
        "title": "Подтягивания широким хватом",
        "mp4": "media/tech/pullup_wide.mp4",
        "text": (
            "📚 Подтягивания широким хватом (широчайшие мышцы)\n\n"
            "✅ Как делать\n"
            "1) Хват сверху, шире плеч — примерно на ширину локтей.\n"
            "2) Плечи вниз от ушей, лопатки активированы.\n"
            "3) Тяни грудь к перекладине, локти вниз и назад.\n"
            "4) Вниз медленно — до полного растяжения широчайших.\n\n"
            "⚠️ Частые ошибки\n"
            "• Слишком широко → травма плеча, найди комфортную ширину.\n"
            "• Рывки → снижает нагрузку на спину.\n\n"
            "💡 Подсказка\n"
            "Акцент на V-образный силуэт. Вес тела сначала, потом отягощение."
        )
    },
    "latpulldown_wide": {
        "title": "Тяга верхнего блока широким хватом",
        "mp4": "media/tech/latpulldown_wide.mp4",
        "text": (
            "📚 Тяга верхнего блока широким хватом\n\n"
            "✅ Настройка\n"
            "• Хват шире плеч прямой хват сверху.\n"
            "• Грудь «вперёд», плечи опустил вниз.\n\n"
            "✅ Как делать\n"
            "1) Начни движение лопатками вниз к карманам.\n"
            "2) Тяни локти вниз и назад до верха груди.\n"
            "3) Медленно вверх — растянь широчайшие.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка корпусом → снизь вес.\n"
            "• Хват слишком широкий → неудобно для плеч, сузь до комфорта.\n\n"
            "💡 Подсказка\n"
            "Пауза 1 сек внизу — лучше чувствуешь спину."
        )
    },
    "latpulldown_narrow": {
        "title": "Тяга верхнего блока узким хватом",
        "mp4": "media/tech/latpulldown_narrow.mp4",
        "text": (
            "📚 Тяга верхнего блока узким/нейтральным хватом\n\n"
            "✅ Настройка\n"
            "• Узкая ручка (нейтральный хват, ладони друг к другу).\n"
            "• Грудь «вперёд», слегка отклонись назад.\n\n"
            "✅ Как делать\n"
            "1) Тяни ручку к груди, локти вниз вдоль тела.\n"
            "2) В нижней точке — грудь выше, лопатки сведены.\n"
            "3) Медленно вверх с полным растяжением.\n\n"
            "⚠️ Частые ошибки\n"
            "• Тянешь руками → думай «локти к рёбрам».\n"
            "• Корпус качается → снизь вес.\n\n"
            "💡 Плюс\n"
            "Нейтральный хват снижает нагрузку на локтевые суставы — удобнее для многих."
        )
    },
    "incline_press_barbell": {
        "title": "Жим под углом (штанга)",
        "mp4": "media/tech/incline_press_barbell.mp4",
        "text": (
            "📚 Жим штанги под углом (верх груди)\n\n"
            "✅ Настройка\n"
            "• Скамья 30–45°. Угол больше 45° — нагрузка уходит на плечи.\n"
            "• Лопатки сведены и прижаты, хват чуть шире плеч.\n\n"
            "✅ Как делать\n"
            "1) Опускай штангу к верху груди (ключицы) под контролем.\n"
            "2) Локти ~45° к корпусу.\n"
            "3) Жми вверх и назад к стойкам.\n\n"
            "⚠️ Частые ошибки\n"
            "• Плечи вперёд → снова сведи лопатки.\n"
            "• Слишком большой угол → переходит в жим плечами.\n\n"
            "💡 Акцент\n"
            "Лучше прорабатывает верхний пучок груди, чем горизонтальный жим."
        )
    },
    "incline_press_dumbbell": {
        "title": "Жим под углом (гантели)",
        "mp4": "media/tech/incline_press_dumbbell.mp4",
        "text": (
            "📚 Жим гантелей под углом (верх груди)\n\n"
            "✅ Настройка\n"
            "• Скамья 30–45°. Гантели на уровне груди, локти под запястьями.\n"
            "• Лопатки сведены и прижаты к скамье.\n\n"
            "✅ Как делать\n"
            "1) Жми гантели вверх, слегка сближая их над грудью.\n"
            "2) Амплитуда шире, чем со штангой — используй её.\n"
            "3) Медленно опускай с растяжением верха груди.\n\n"
            "⚠️ Частые ошибки\n"
            "• Гантели «разбегаются» → потеря контроля, снизь вес.\n"
            "• Локти 90° в стороны → нагрузка на плечи.\n\n"
            "💡 Плюс\n"
            "Большая амплитуда и независимость рук = более глубокое растяжение груди."
        )
    },
    "biceps_barbell": {
        "title": "Сгибания со штангой (бицепс)",
        "mp4": "media/tech/biceps_barbell.mp4",
        "text": (
            "📚 Сгибания со штангой — бицепс\n\n"
            "✅ Как делать\n"
            "1) Хват снизу чуть шире плеч, штанга у бёдер.\n"
            "2) Локти прижаты к корпусу — не гуляют вперёд.\n"
            "3) Сгибай до пика, не помогай корпусом.\n"
            "4) Медленно опускай — 2–3 сек, не бросай штангу.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка спиной → слишком тяжёлый вес.\n"
            "• Локти уезжают вперёд → теряешь нагрузку на бицепс.\n"
            "• Неполная амплитуда → до конца вниз, иначе бицепс не растёт.\n\n"
            "💡 Варианты хвата\n"
            "Прямой гриф — классика. EZ-гриф — меньше нагрузки на запястья."
        )
    },
    "biceps_dumbbell": {
        "title": "Сгибания с гантелями (бицепс)",
        "mp4": "media/tech/biceps_dumbbell.mp4",
        "text": (
            "📚 Сгибания с гантелями — бицепс\n\n"
            "✅ Как делать\n"
            "1) Нейтральный хват → поворачивай ладонь вверх при подъёме (супинация).\n"
            "2) Локти у корпуса, не двигаются.\n"
            "3) Поднимай до пика, сожми бицепс.\n"
            "4) Медленно вниз до полного выпрямления.\n\n"
            "⚠️ Частые ошибки\n"
            "• Нет супинации → бицепс меньше сокращается.\n"
            "• Раскачка корпуса → снизь вес.\n\n"
            "💡 Варианты\n"
            "Попеременно (одна рука) — лучше контроль. Одновременно — быстрее."
        )
    },
    "deadlift_sumo": {
        "title": "Становая тяга сумо",
        "mp4": "media/tech/deadlift_sumo.mp4",
        "text": (
            "📚 Становая тяга сумо\n\n"
            "✅ Настройка\n"
            "• Широкая стойка (ноги далеко в стороны), носки наружу.\n"
            "• Хват узкий — между коленями.\n"
            "• Бёдра низко, спина прямая, взгляд вперёд.\n\n"
            "✅ Как делать\n"
            "1) Вдох, напряги корпус, колени разводи по носкам.\n"
            "2) Толкай пол ногами, одновременно разгибай спину.\n"
            "3) Гриф ведёт вертикально вверх.\n"
            "4) Наверху — бёдра вперёд, без переразгиба.\n\n"
            "⚠️ Частые ошибки\n"
            "• Колени схлопываются внутрь → разводи активно наружу.\n"
            "• Спина округляется → снизь вес.\n\n"
            "💡 Vs. классика\n"
            "Сумо — короче амплитуда, больше квадрицепсов и приводящих."
        )
    },
    "chest_fly": {
        "title": "Разведения на грудь",
        "mp4": "media/tech/chest_fly.mp4",
        "text": (
            "📚 Разведения гантелей лёжа (грудь)\n\n"
            "✅ Настройка\n"
            "• Лежи на горизонтальной скамье, гантели над грудью.\n"
            "• Лёгкий сгиб в локтях — зафиксируй и сохрани его.\n\n"
            "✅ Как делать\n"
            "1) Опускай гантели в стороны — дугой, как обнимаешь дерево.\n"
            "2) Опускай до ощущения растяжения в груди.\n"
            "3) Своди гантели обратно над грудью той же дугой.\n\n"
            "⚠️ Частые ошибки\n"
            "• Локти сгибаются — превращается в жим → фиксируй угол.\n"
            "• Слишком тяжело → риск для плеч, возьми лёгче.\n\n"
            "💡 Подсказка\n"
            "Изолирует грудь лучше жима. Идеально после основных упражнений."
        )
    },
    # ── Отжимания с ногами на возвышении ────────────────────────────────────
    "pushup_elevated": {
        "title": "Отжимания с ногами на возвышении",
        "mp4": "media/tech/pushup_elevated.mp4",
        "text": (
            "📚 Отжимания с ногами на возвышении\n"
            "Зачем: акцент на верхнюю часть груди и передние дельты.\n\n"
            "✅ Настройка\n"
            "• Ноги на скамье или стуле, ладони на полу чуть шире плеч\n"
            "• Тело — прямая линия, таз не поднимать и не проваливать\n"
            "• Пресс и ягодицы напряжены на протяжении всего подхода\n\n"
            "✅ Выполнение по шагам\n"
            "1. Вдох — начни опускать грудь к полу (2–3 сек)\n"
            "2. Локти под углом ~45° к корпусу, не в стороны\n"
            "3. Грудь почти касается пола — без отбива\n"
            "4. Выдох — выжимай пол руками, держи тело прямым\n"
            "5. Наверху — не разгибай локти «в замок»\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз торчит вверх → опусти бёдра, напряги корпус\n"
            "• Голова смотрит вниз → взгляд чуть вперёд\n"
            "• Слишком высокое возвышение → сначала ноги на 30–40 см\n\n"
            "💡 Подсказки\n"
            "Чем выше ноги — тем сильнее акцент на верх груди и плечи.\n"
            "Контролируй опускание — это половина работы."
        )
    },
    # ── Разгибания ног в тренажёре ───────────────────────────────────────────
    "leg_extension": {
        "title": "Разгибания ног в тренажёре",
        "mp4": "media/tech/leg_extension.mp4",
        "text": (
            "📚 Разгибания ног в тренажёре\n"
            "Зачем: изолированная нагрузка на квадрицепс.\n\n"
            "✅ Настройка\n"
            "• Сядь ровно, поясница прижата к спинке\n"
            "• Валик — чуть выше щиколотки (не на подъёме стопы)\n"
            "• Угол в коленях ~90° в исходном положении\n\n"
            "✅ Выполнение по шагам\n"
            "1. Выдох — разгибай ноги до почти полного выпрямления\n"
            "2. Наверху — пауза 1 сек, сожми квадрицепс\n"
            "3. Медленно (2–3 сек) опускай вниз, не бросай вес\n"
            "4. Внизу — не касайся стопами пола между повторами\n\n"
            "⚠️ Частые ошибки\n"
            "• Рывок наверх → теряешь контроль, перегружаешь колено\n"
            "• Таз отрывается от сиденья → снизь вес\n"
            "• Слишком полное разгибание с «замком» → остановись за 5° до нуля\n\n"
            "💡 Подсказки\n"
            "Упражнение изолирующее — используй после базовых приседаний.\n"
            "Пауза наверху с сокращением — лучше чувствуешь квадрицепс."
        )
    },
    # ── Сведения ног в тренажёре ─────────────────────────────────────────────
    "leg_adduction": {
        "title": "Сведения ног в тренажёре",
        "mp4": "media/tech/leg_adduction.mp4",
        "text": (
            "📚 Сведения ног в тренажёре\n"
            "Зачем: приводящие мышцы бедра (внутренняя поверхность).\n\n"
            "✅ Настройка\n"
            "• Сядь ровно, спина прижата к спинке\n"
            "• Колени снаружи валиков, ноги раздвинуты на амплитуду тренажёра\n"
            "• Держись за рукоятки для стабильности\n\n"
            "✅ Выполнение по шагам\n"
            "1. Выдох — сводишь ноги вместе медленно и подконтрольно\n"
            "2. В конечной точке — пауза 1 сек, почувствуй сокращение\n"
            "3. Медленно разводи обратно (2–3 сек)\n"
            "4. Не позволяй весу «тянуть» ноги в стороны рывком\n\n"
            "⚠️ Частые ошибки\n"
            "• Рывок из стороны в сторону → контролируй движение\n"
            "• Слишком большой вес → нет пользы, риск травмы паха\n"
            "• Корпус вперёд → спина прямая, не сутулься\n\n"
            "💡 Подсказки\n"
            "Хорошо работает в суперсете с разведениями ног (отводящие).\n"
            "Используй умеренный вес, фокус — на ощущении в мышцах."
        )
    },
    # ── Отведения ног назад в кроссовере ─────────────────────────────────────
    "cable_kickback": {
        "title": "Отведения ног назад в кроссовере",
        "mp4": "media/tech/cable_kickback.mp4",
        "text": (
            "📚 Отведения ног назад в кроссовере\n"
            "Зачем: ягодичные мышцы, задняя поверхность бедра.\n\n"
            "✅ Настройка\n"
            "• Манжета на щиколотке, блок внизу\n"
            "• Встань боком/лицом к тренажёру, упрись в стойку\n"
            "• Корпус слегка наклони вперёд — спина прямая\n\n"
            "✅ Выполнение по шагам\n"
            "1. Выдох — отводи рабочую ногу назад и вверх\n"
            "2. Максимальная точка — нога не выше горизонтали (или чуть выше при гибкости)\n"
            "3. Пауза 1 сек — сожми ягодицу\n"
            "4. Медленно возвращай ногу вперёд, не касаясь пола\n\n"
            "⚠️ Частые ошибки\n"
            "• Поясница прогибается → держи корпус нейтральным\n"
            "• Нога идёт в сторону, а не назад → следи за траекторией\n"
            "• Рывок → работай плавно, чувствуй ягодицу\n\n"
            "💡 Подсказки\n"
            "Не обязательно высоко поднимать ногу — важнее качество.\n"
            "Можно делать стоя у стены без тренажёра (с резинкой)."
        )
    },
    # ── Тяга Т-грифа ─────────────────────────────────────────────────────────
    "tbar_row": {
        "title": "Тяга Т-грифа",
        "mp4": "media/tech/tbar_row.mp4",
        "text": (
            "📚 Тяга Т-грифа\n"
            "Зачем: широчайшие, ромбовидные, трапеции, бицепс.\n\n"
            "✅ Настройка\n"
            "• Ноги по обе стороны от грифа, колени слегка согнуты\n"
            "• Корпус наклонён 45° к полу — не горизонтально\n"
            "• Спина прямая, взгляд вперёд, лопатки слегка сведены\n\n"
            "✅ Выполнение по шагам\n"
            "1. Хват нейтральный или пронированный — чуть шире плеч\n"
            "2. Вдох — зафиксируй корпус\n"
            "3. Начни с лопаток: потяни их вниз и назад\n"
            "4. Тяни локти к рёбрам — гриф к нижней части груди\n"
            "5. Пауза 1 сек — сожми лопатки\n"
            "6. Медленно опускай вниз, полностью растягивая спину\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка корпусом → снизь вес, зафиксируй позицию\n"
            "• Тянешь руками, а не спиной → думай «локти назад»\n"
            "• Поясница округляется → уменьши наклон или вес\n\n"
            "💡 Подсказки\n"
            "Нейтральный хват (ладони друг к другу) — больше широчайших.\n"
            "Пронированный (ладони вниз) — больше трапеций."
        )
    },
    # ── Тяга к груди узким нейтральным хватом ────────────────────────────────
    "latpulldown_close": {
        "title": "Тяга к груди узким нейтральным хватом",
        "mp4": "media/tech/latpulldown_close.mp4",
        "text": (
            "📚 Тяга к груди узким нейтральным хватом\n"
            "Зачем: нижние и средние широчайшие, бицепс.\n\n"
            "✅ Настройка\n"
            "• Используй V-рукоять или нейтральную ручку\n"
            "• Хват узкий, ладони смотрят друг на друга\n"
            "• Бёдра зафиксированы под упором, грудь вперёд\n\n"
            "✅ Выполнение по шагам\n"
            "1. Вдох — лопатки вниз и назад до начала тяги\n"
            "2. Тяни локти вниз вдоль корпуса\n"
            "3. Рукоять к верхней части груди — не к подбородку\n"
            "4. Лёгкий откид назад (10–15°) допустим\n"
            "5. Пауза 1 сек — почувствуй нижние широчайшие\n"
            "6. Медленно возвращай вверх, растягивая спину\n\n"
            "⚠️ Частые ошибки\n"
            "• Сильный откид назад → это уже тяга за инерцию\n"
            "• Локти уходят в стороны → держи их ближе к корпусу\n"
            "• Плечи к ушам при возврате → контролируй опускание\n\n"
            "💡 Подсказки\n"
            "Узкий нейтральный хват — отличная альтернатива для тех,\n"
            "у кого болят запястья при широком хвате."
        )
    },
    # ── Арнольд-жим ──────────────────────────────────────────────────────────
    "arnold_press": {
        "title": "Арнольд-жим",
        "mp4": "media/tech/arnold_press.mp4",
        "text": (
            "📚 Арнольд-жим\n"
            "Зачем: все три пучка дельт + верхняя трапеция.\n\n"
            "✅ Настройка\n"
            "• Сядь на скамью со спинкой, поясница прижата\n"
            "• Старт: гантели у плеч, ладони к себе (пронация)\n"
            "• Пресс напряжён, не прогибайся\n\n"
            "✅ Выполнение по шагам\n"
            "1. Начни жать вверх — одновременно разворачивай ладони от себя\n"
            "2. В верхней точке ладони смотрят вперёд\n"
            "3. Руки почти прямые, но без «замка» в локтях\n"
            "4. Опускай вниз с обратным разворотом — ладони к себе\n"
            "5. Движение плавное, без рывков\n\n"
            "⚠️ Частые ошибки\n"
            "• Разворот только в верхней части → делай на всём пути\n"
            "• Прогиб поясницы → работай сидя со спинкой\n"
            "• Слишком тяжёлые гантели → теряется техника\n\n"
            "💡 Подсказки\n"
            "Вращение создаёт уникальный акцент на передний и средний пучок.\n"
            "Работай медленно — это не обычный жим вверх."
        )
    },
    # ── Подъёмы перед собой ───────────────────────────────────────────────────
    "front_raise": {
        "title": "Подъёмы перед собой",
        "mp4": "media/tech/front_raise.mp4",
        "text": (
            "📚 Подъёмы гантелей перед собой\n"
            "Зачем: передний пучок дельты.\n\n"
            "✅ Настройка\n"
            "• Стой ровно, гантели в опущенных руках перед бёдрами\n"
            "• Хват пронированный (ладони вниз) или нейтральный\n"
            "• Спина прямая, пресс напряжён\n\n"
            "✅ Выполнение по шагам\n"
            "1. Поднимай руку(и) вперёд до уровня плеч (не выше)\n"
            "2. Локоть слегка согнут — не прямой как кол\n"
            "3. Движение медленное — 2 сек вверх, 2 сек вниз\n"
            "4. Пауза наверху 1 сек — почувствуй сокращение переда дельты\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка корпуса → работаешь за счёт инерции, не мышц\n"
            "• Подъём выше горизонтали → нагрузка уходит на трапеции\n"
            "• Слишком тяжёлые гантели → снизь, сделай строго\n\n"
            "💡 Подсказки\n"
            "Можно делать поочерёдно или двумя руками одновременно.\n"
            "Лёгкие веса и высокое качество движения — лучше, чем тяжёлые с рывками."
        )
    },
    # ── Французский жим лёжа ─────────────────────────────────────────────────
    "french_press": {
        "title": "Французский жим лёжа",
        "mp4": "media/tech/french_press.mp4",
        "text": (
            "📚 Французский жим лёжа\n"
            "Зачем: изолированная нагрузка на трицепс (длинная головка).\n\n"
            "✅ Настройка\n"
            "• Ляг на скамью, возьми гантели или EZ-гриф\n"
            "• Руки подняты вверх, локти над грудью (не расставлены широко)\n"
            "• Плечи зафиксированы — двигаются только предплечья\n\n"
            "✅ Выполнение по шагам\n"
            "1. Вдох — медленно (2–3 сек) опускай снаряд к голове/за голову\n"
            "2. Локти не расходятся в стороны — держи их параллельно\n"
            "3. Нижняя точка — лёгкое растяжение трицепса (не рывок)\n"
            "4. Выдох — разгибай руки, жми снаряд вверх\n"
            "5. Наверху не «замыкай» локти полностью\n\n"
            "⚠️ Частые ошибки\n"
            "• Плечи двигаются → убери жим, оставь только разгибание\n"
            "• Локти широко в стороны → потеря нагрузки на трицепс\n"
            "• Слишком тяжёлый вес → травмоопасно для локтей\n\n"
            "💡 Подсказки\n"
            "EZ-гриф удобнее для запястий, чем прямой.\n"
            "Если болят локти — снизь вес и уменьши амплитуду."
        )
    },
    # ── Концентрированные сгибания ────────────────────────────────────────────
    "concentration_curl": {
        "title": "Концентрированные сгибания",
        "mp4": "media/tech/concentration_curl.mp4",
        "text": (
            "📚 Концентрированные сгибания\n"
            "Зачем: пик бицепса, максимальная изоляция.\n\n"
            "✅ Настройка\n"
            "• Сядь на скамью, ноги широко, наклони корпус вперёд\n"
            "• Локоть рабочей руки упирается в внутреннюю поверхность бедра\n"
            "• Свободная рука — на колене для поддержки\n\n"
            "✅ Выполнение по шагам\n"
            "1. Вдох — рука выпрямлена вниз, гантель под контролем\n"
            "2. Выдох — сгибай руку, поднимай гантель к плечу\n"
            "3. Наверху — разверни запястье (мизинец вверх), пауза 1 сек\n"
            "4. Медленно (2–3 сек) опускай — полное выпрямление\n\n"
            "⚠️ Частые ошибки\n"
            "• Локоть отрывается от бедра → теряешь изоляцию\n"
            "• Корпус помогает при подъёме → убери раскачку\n"
            "• Не разгибаешь полностью → теряешь амплитуду, меньше роста\n\n"
            "💡 Подсказки\n"
            "Суперсет: концентрированные + молотки — отличная пампинг-комбинация.\n"
            "Работай поочерёдно, не торопись."
        )
    },
    # ── Велосипед ────────────────────────────────────────────────────────────
    "bicycle_crunch": {
        "title": "Велосипед",
        "mp4": "media/tech/bicycle_crunch.mp4",
        "text": (
            "📚 Велосипед (кор + косые мышцы)\n"
            "Зачем: прямая и косые мышцы живота, сгибатели бедра.\n\n"
            "✅ Настройка\n"
            "• Ляг на спину, руки за головой — но не тяни шею!\n"
            "• Поясница прижата к полу на протяжении всего упражнения\n"
            "• Ноги подняты под углом ~45° к полу\n\n"
            "✅ Выполнение по шагам\n"
            "1. Одновременно: подтяни правое колено к груди + поверни левый локоть к нему\n"
            "2. Левую ногу вытяни вперёд (чем ниже — тем тяжелее)\n"
            "3. Сменяй стороны — левое колено + правый локоть\n"
            "4. Движение медленное, поворот — из корпуса, не из шеи\n\n"
            "⚠️ Частые ошибки\n"
            "• Шея тянется руками → расслабь руки, держи только для направления\n"
            "• Поясница отрывается → напряги пресс, не поднимай ноги слишком низко\n"
            "• Слишком быстро → без качества работают сгибатели, не пресс\n\n"
            "💡 Подсказки\n"
            "Медленный темп с паузой в скрутке — в разы эффективнее быстрого.\n"
            "10 медленных повторов лучше 30 быстрых."
        )
    },
    # ── Русские скручивания ───────────────────────────────────────────────────
    "russian_twist": {
        "title": "Русские скручивания",
        "mp4": "media/tech/russian_twist.mp4",
        "text": (
            "📚 Русские скручивания\n"
            "Зачем: косые мышцы живота, стабилизаторы корпуса.\n\n"
            "✅ Настройка\n"
            "• Сядь на пол, ноги согнуты — пятки на полу или приподняты (сложнее)\n"
            "• Корпус отклони назад ~45° — держи прямую спину (не горбись!)\n"
            "• Руки перед собой: соединены вместе или держат гантель/блин\n\n"
            "✅ Выполнение по шагам\n"
            "1. Поверни корпус вправо — не только руки, а всё туловище\n"
            "2. Кратковременная пауза — почувствуй напряжение косой\n"
            "3. Поверни в другую сторону через центр\n"
            "4. Движение медленное и контролируемое\n\n"
            "⚠️ Частые ошибки\n"
            "• Горбишься → держи грудь расправленной, угол фиксирован\n"
            "• Поворачиваешь только руки → скручивай корпус\n"
            "• Слишком тяжёлый блин в начале → начни без веса, потом добавляй\n\n"
            "💡 Подсказки\n"
            "Ноги на весу — сложнее и больше нагрузки на пресс.\n"
            "Добавь вес (гантель, блин, мяч) по мере роста силы."
        )
    },

    # ─── ДОМАШНИЕ УПРАЖНЕНИЯ (свой вес) ──────────────────────────────────────
    "pushup_wide": {
        "title": "Отжимания широким хватом",
        "mp4": "media/tech/pushup_wide.mp4",
        "text": (
            "📚 Отжимания широким хватом\n\n"
            "✅ Настройка\n"
            "• Руки шире плеч — акцент на грудь.\n"
            "• Тело — прямая линия от головы до пяток.\n\n"
            "✅ Как выполнять\n"
            "1) Опускайся грудью вниз, локти под углом ~45° к корпусу.\n"
            "2) Касайся грудью пола или подходи близко.\n"
            "3) Выжимай вверх до полного выпрямления рук.\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз провисает вниз — напряги пресс и ягодицы.\n"
            "• Локти 90° в стороны — снижай угол.\n\n"
            "💡 Подсказка\n"
            "Чем шире руки, тем больше нагрузки на грудь."
        )
    },
    "pushup_elevated": {
        "title": "Отжимания с ногами на возвышенности",
        "mp4": "media/tech/pushup_elevated.mp4",
        "text": (
            "📚 Отжимания с ногами на возвышенности\n\n"
            "✅ Настройка\n"
            "• Ноги на стуле/диване, руки на полу.\n"
            "• Тело — прямая наклонная линия.\n\n"
            "✅ Как выполнять\n"
            "1) Опускай грудь к полу, локти ~45° к корпусу.\n"
            "2) Удерживай тело прямым на протяжении всего движения.\n"
            "3) Выжимай вверх.\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз провисает — напряги кор.\n"
            "• Локти 90° в стороны — снижай угол.\n\n"
            "💡 Подсказка\n"
            "Нагрузка уходит на верхний пучок груди — как жим под углом."
        )
    },
    "pike_pushup": {
        "title": "Пайк отжимания",
        "mp4": "media/tech/pike_pushup.mp4",
        "text": (
            "📚 Пайк отжимания (плечи)\n\n"
            "✅ Настройка\n"
            "• Стань в позу «перевёрнутая V»: руки и ноги на полу, таз высоко.\n"
            "• Руки чуть шире плеч.\n\n"
            "✅ Как выполнять\n"
            "1) Сгибай локти, опуская голову к полу.\n"
            "2) Нос почти касается пола между руками.\n"
            "3) Выжимай вверх, выпрямляя руки.\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз опускается в горизонталь — держи V-форму.\n"
            "• Локти в стороны — они должны идти назад.\n\n"
            "💡 Подсказка\n"
            "Чем вертикальнее тело, тем больше нагрузки на плечи."
        )
    },
    "wall_pushup": {
        "title": "Отжимания у стены (плечи)",
        "mp4": "media/tech/wall_pushup.mp4",
        "text": (
            "📚 Отжимания в стойке у стены\n\n"
            "✅ Настройка\n"
            "• Встань в стойку на руках у стены.\n"
            "• Руки на ширине плеч, пальцы чуть в стороны.\n\n"
            "✅ Как выполнять\n"
            "1) Медленно сгибай локти, опуская голову к полу.\n"
            "2) Останови движение чуть не касаясь пола.\n"
            "3) Выжимай обратно.\n\n"
            "⚠️ Частые ошибки\n"
            "• Потеря равновесия — используй угол стены для опоры.\n"
            "• Слишком быстро — контролируй каждый сантиметр.\n\n"
            "💡 Подсказка\n"
            "Если стойка сложно — начни с пайк отжиманий."
        )
    },
    "pullup_wide": {
        "title": "Подтягивания широким хватом",
        "mp4": "media/tech/pullup_wide.mp4",
        "text": (
            "📚 Подтягивания широким хватом\n\n"
            "✅ Настройка\n"
            "• Хват сверху шире плеч.\n"
            "• Тело чуть отклонено назад, грудь вперёд.\n\n"
            "✅ Как выполнять\n"
            "1) Тяни локти вниз к рёбрам.\n"
            "2) Подтянись до уровня подбородка выше перекладины.\n"
            "3) Медленно опускайся до полного выпрямления рук.\n\n"
            "⚠️ Частые ошибки\n"
            "• Тянешь руками, а не спиной — думай «локти в пол».\n"
            "• Раскачка корпуса — держи тело стабильным.\n\n"
            "💡 Подсказка\n"
            "Широкий хват — максимальная нагрузка на широчайшие."
        )
    },
    "pullup_narrow": {
        "title": "Подтягивания узким хватом",
        "mp4": "media/tech/pullup_narrow.mp4",
        "text": (
            "📚 Подтягивания узким хватом\n\n"
            "✅ Настройка\n"
            "• Хват сверху уже плеч, руки почти рядом.\n"
            "• Тело прямое.\n\n"
            "✅ Как выполнять\n"
            "1) Тяни локти вниз и чуть назад.\n"
            "2) Подтянись до касания грудью перекладины.\n"
            "3) Медленно опускайся.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка — напряги пресс и ягодицы.\n"
            "• Тянешь руками — думай «лопатки вниз».\n\n"
            "💡 Подсказка\n"
            "Узкий хват — больше нагрузки на нижние широчайшие и бицепс."
        )
    },
    "squat_bw": {
        "title": "Приседания (свой вес)",
        "mp4": "media/tech/squat_bw.mp4",
        "text": (
            "📚 Приседания (собственный вес)\n\n"
            "✅ Настройка\n"
            "• Стопы на ширине плеч, носки слегка наружу.\n"
            "• Руки вперёд или за голову для баланса.\n\n"
            "✅ Как выполнять\n"
            "1) Уводи таз назад и вниз.\n"
            "2) Колени идут по линии носков.\n"
            "3) Опускайся до параллели бёдер с полом.\n"
            "4) Вставай, выталкивая пол ногами.\n\n"
            "⚠️ Частые ошибки\n"
            "• Колени внутрь — раздвигай их наружу.\n"
            "• Пятки отрываются — стопы шире.\n\n"
            "💡 Подсказка\n"
            "Держи грудь расправленной, смотри перед собой."
        )
    },
    "glute_bridge": {
        "title": "Ягодичный мост",
        "mp4": "media/tech/glute_bridge.mp4",
        "text": (
            "📚 Ягодичный мост (ягодицы + задняя поверхность бедра)\n\n"
            "✅ Настройка\n"
            "• Лягь на спину, колени согнуты, стопы на ширине бёдер.\n"
            "• Пятки под коленями.\n\n"
            "✅ Как выполнять\n"
            "1) Напряги ягодицы и подними таз вверх.\n"
            "2) В верхней точке — пауза 1–2 сек, сожми ягодицы.\n"
            "3) Медленно опускай, не касаясь пола полностью.\n\n"
            "⚠️ Частые ошибки\n"
            "• Поясница прогибается — уменьши высоту подъёма.\n"
            "• Колени разъезжаются — держи над носками.\n\n"
            "💡 Усложнение\n"
            "Одна нога на весу, или отягощение на бёдра."
        )
    },

    # ── Новые уникальные техники (разделённые дубли) ────────────────────────

    "calves_machine": {
        "title": "Подъёмы на носки в тренажёре",
        "mp4": "media/tech/calves_machine.mp4",
        "text": (
            "📚 Подъёмы на носки в тренажёре (икроножные)\n\n"
            "✅ Настройка\n"
            "• Плечи под подушками тренажёра, носки на краю платформы.\n"
            "• Пятки свободно свисают вниз — не стой на полной стопе.\n"
            "• Колени слегка согнуты, не в замок.\n\n"
            "✅ Как выполнять\n"
            "1) Опусти пятки максимально вниз — глубокое растяжение икр.\n"
            "2) Медленно поднимайся на носки как можно выше.\n"
            "3) Пауза 1–2 сек вверху — сожми икры максимально.\n"
            "4) Медленно опускай 2–3 сек — не бросай вниз.\n\n"
            "⚠️ Частые ошибки\n"
            "• Частичная амплитуда — икры не получают растяжения внизу.\n"
            "• Колени выпрямлены в замок — снимает нагрузку с икроножной.\n"
            "• Слишком тяжёлый вес — теряешь амплитуду и паузу.\n\n"
            "💡 Подсказка\n"
            "Тренажёр позволяет грузить икры серьёзным весом. "
            "Паузы вверху при каждом повторе — ключ к росту."
        )
    },
    "calves_standing": {
        "title": "Подъёмы на носки стоя",
        "mp4": "media/tech/calves_standing.mp4",
        "text": (
            "📚 Подъёмы на носки стоя (икроножные — свой вес)\n\n"
            "✅ Настройка\n"
            "• Встань носками на ступень или возвышение, пятки свисают вниз.\n"
            "• Держись за опору только для равновесия — не переноси вес на руки.\n\n"
            "✅ Как выполнять\n"
            "1) Опусти пятки ниже уровня ступени — растяжение внизу.\n"
            "2) Поднимайся на носки как можно выше.\n"
            "3) Пауза 1–2 сек вверху, сожми икры.\n"
            "4) Медленно опускай 2–3 сек.\n\n"
            "⚠️ Частые ошибки\n"
            "• Тренируешься на ровном полу — нет нижней фазы, нет растяжения.\n"
            "• Пружинишь или подпрыгиваешь — убери инерцию.\n\n"
            "💡 Прогрессия\n"
            "Обе ноги → одна нога → одна нога с гантелью в свободной руке."
        )
    },
    "bulgarian_dumbbell": {
        "title": "Болгарские выпады с гантелями",
        "mp4": "media/tech/bulgarian_dumbbell.mp4",
        "text": (
            "📚 Болгарские выпады с гантелями (квадрицепс + ягодицы)\n\n"
            "✅ Настройка\n"
            "• Задняя нога на скамье (подъём стопы), передняя — широкий шаг вперёд.\n"
            "• Гантели в опущенных руках, корпус вертикально.\n\n"
            "✅ Как выполнять\n"
            "1) Опускайся вертикально вниз, заднее колено к полу.\n"
            "2) Переднее колено над носком — не выходит за него.\n"
            "3) Выталкивайся передней ногой обратно вверх.\n"
            "4) Гантели не раскачивай — держи стабильно вдоль тела.\n\n"
            "⚠️ Частые ошибки\n"
            "• Шаг слишком короткий → колено далеко за носок.\n"
            "• Корпус заваливается вперёд — держи грудь вертикально.\n"
            "• Тянешься за весом — напряги кор, не сутулься.\n\n"
            "💡 Подсказка\n"
            "Тяжелее обычных приседаний — начни с лёгкого веса, освой баланс."
        )
    },
    "glute_bridge_single": {
        "title": "Ягодичный мост на одной ноге",
        "mp4": "media/tech/glute_bridge_single.mp4",
        "text": (
            "📚 Ягодичный мост на одной ноге (ягодицы — продвинутый)\n\n"
            "✅ Настройка\n"
            "• Лягь на спину, одна нога согнута и стоит на полу.\n"
            "• Вторая нога вытянута прямо или согнута и поднята.\n"
            "• Руки вдоль тела ладонями вниз.\n\n"
            "✅ Как выполнять\n"
            "1) Напряги ягодицу рабочей ноги ещё до подъёма.\n"
            "2) Поднимай таз до прямой линии бедро-корпус.\n"
            "3) Пауза 2 сек вверху — максимально сожми ягодицу.\n"
            "4) Медленно опускай, не расслабляясь до конца.\n"
            "5) Все повторы на одну ногу — потом меняй.\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз перекашивается в сторону — держи бёдра параллельно полу.\n"
            "• Поясница перегибается — уменьши высоту подъёма.\n\n"
            "💡 Подсказка\n"
            "Нагрузка вдвое выше обычного моста. Освой двуногий вариант сначала."
        )
    },
    "lateralraise_dumbbell": {
        "title": "Разведения гантелей в стороны",
        "mp4": "media/tech/lateralraise_dumbbell.mp4",
        "text": (
            "📚 Разведения гантелей в стороны (средняя дельта)\n\n"
            "✅ Настройка\n"
            "• Стой прямо, гантели у бёдер, лёгкий наклон вперёд 5–10°.\n"
            "• Локти слегка согнуты и зафиксированы — угол не меняется.\n\n"
            "✅ Как выполнять\n"
            "1) Поднимай гантели в стороны — локоть ведёт движение.\n"
            "2) Мизинец чуть выше большого пальца вверху (как выливаешь воду).\n"
            "3) Стоп на уровне плеч — выше не нужно.\n"
            "4) Медленно опускай 2–3 сек — не бросай вниз.\n\n"
            "⚠️ Частые ошибки\n"
            "• Раскачка корпусом — снизь вес или встань спиной к стене.\n"
            "• Тянешь запястьями — нагрузка уходит из средней дельты.\n"
            "• Поднимаешь выше плеч — трапеции берут нагрузку на себя.\n\n"
            "💡 Подсказка\n"
            "5–10 кг при идеальной технике — уже серьёзная нагрузка на дельту."
        )
    },
    "ohp_dumbbell_sitting": {
        "title": "Жим гантелей сидя",
        "mp4": "media/tech/ohp_dumbbell_sitting.mp4",
        "text": (
            "📚 Жим гантелей сидя (плечи — изолированно)\n\n"
            "✅ Настройка\n"
            "• Сядь на скамью со спинкой, спину полностью прижми.\n"
            "• Гантели на уровне плеч, локти под запястьями.\n"
            "• Ноги упираются в пол — устойчивая база.\n\n"
            "✅ Как выполнять\n"
            "1) Жми гантели вертикально вверх, слегка сближая в верхней точке.\n"
            "2) Руки почти прямые наверху — без рывка и замка.\n"
            "3) Медленно опускай до 90° в локтях.\n"
            "4) Плечи держи опущенными — не тяни к ушам.\n\n"
            "⚠️ Частые ошибки\n"
            "• Прогиб в пояснице — прижми спину к спинке скамьи.\n"
            "• Гантели уходят вперёд — держи строго над плечами.\n"
            "• Неполная амплитуда вниз — опускай до 90° каждый раз.\n\n"
            "💡 Плюс сидя\n"
            "Нет читинга корпусом — больше изоляции плеч, чем стоя."
        )
    },
    "planks_static": {
        "title": "Планка (статика)",
        "mp4": "media/tech/planks_static.mp4",
        "text": (
            "📚 Планка статическая (кор — зал)\n\n"
            "✅ Настройка\n"
            "• Упор на предплечья, локти строго под плечами.\n"
            "• Тело — прямая линия: голова, спина, ягодицы, пятки.\n"
            "• Взгляд в пол, шея нейтральна.\n\n"
            "✅ Как выполнять\n"
            "1) Напряги пресс — будто ждёшь удар в живот.\n"
            "2) Сожми ягодицы и напряги квадрицепсы одновременно.\n"
            "3) Дыши ровно: вдох носом, выдох ртом.\n"
            "4) Стоп как только форма ломается — это не тренировка кора.\n\n"
            "⚠️ Частые ошибки\n"
            "• Таз задирается вверх — облегчаешь себе, кор не работает.\n"
            "• Поясница провисает — напряги пресс, подтяни таз.\n"
            "• Задерживаешь дыхание — расслабься и дыши.\n\n"
            "💡 Прогрессия\n"
            "30 сек → 60 сек → планка с подъёмом ноги → на одной руке."
        )
    },
    "rear_delt_machine": {
        "title": "Задняя дельта в тренажёре",
        "mp4": "media/tech/rear_delt_machine.mp4",
        "text": (
            "📚 Задняя дельта в тренажёре (peck deck reverse)\n\n"
            "✅ Настройка\n"
            "• Сядь лицом к тренажёру, грудь к подушке.\n"
            "• Рукояти на уровне плеч, хват нейтральный.\n"
            "• Плечи опущены, не поднимай к ушам.\n\n"
            "✅ Как выполнять\n"
            "1) Разводи рукояти назад — движение в стороны и назад.\n"
            "2) Локти слегка согнуты и фиксированы — угол не меняется.\n"
            "3) В конечной точке лопатки сведены — пауза 1 сек.\n"
            "4) Медленно возвращай вперёд — не бросай груз.\n\n"
            "⚠️ Частые ошибки\n"
            "• Плечи поднимаются к ушам — задняя дельта отключается.\n"
            "• Меняешь угол локтей в ходе движения — нагрузка уходит.\n"
            "• Слишком тяжёлый вес — работает трапеция, не дельта.\n\n"
            "💡 Подсказка\n"
            "Задняя дельта маленькая — вес здесь всегда меньше, чем хочется взять."
        )
    },
    "crossover_chest": {
        "title": "Кроссовер",
        "mp4": "media/tech/crossover_chest.mp4",
        "text": (
            "📚 Кроссовер (изоляция груди — кабельный тренажёр)\n\n"
            "✅ Настройка\n"
            "• Блоки сверху — акцент на нижнюю грудь.\n"
            "• Блоки снизу — акцент на верхнюю грудь.\n"
            "• Встань посередине, шаг вперёд, небольшой наклон вперёд.\n\n"
            "✅ Как выполнять\n"
            "1) Руки слегка согнуты в локтях — угол фиксирован.\n"
            "2) Своди руки перед грудью дугообразным движением.\n"
            "3) В центре — руки почти касаются, пауза, сожми грудь.\n"
            "4) Медленно разводи назад — ощути растяжение в груди.\n\n"
            "⚠️ Частые ошибки\n"
            "• Сгибаешь локти в движении — превращается в жим, не изоляцию.\n"
            "• Корпус качается — снизь вес, зафиксируй положение тела.\n"
            "• Руки сильно перекрещиваются — достаточно соприкосновения.\n\n"
            "💡 Подсказка\n"
            "Угол блоков определяет акцент: верх/середина/низ груди. Экспериментируй."
        )
    },

    "cable_crunch": {
        "title": "Молитва",
        "mp4": "media/tech/cable_crunch.mp4",
        "text": (
            "📚 Молитва / Кранч на блоке (верхний пресс)\n\n"
            "✅ Настройка\n"
            "• Верхний блок с канатной рукоятью, встань на колени лицом к тренажёру.\n"
            "• Рукоять у лба или за головой, локти смотрят вниз.\n"
            "• Бёдра вертикально — не садись на пятки в ходе движения.\n\n"
            "✅ Как выполнять\n"
            "1) Вдох — зафиксируй исходное положение.\n"
            "2) Выдох — скручивай корпус вниз, тянись локтями к коленям.\n"
            "3) В нижней точке — пауза 1 сек, максимальное сокращение пресса.\n"
            "4) Медленно возвращайся в исходное — 2–3 сек, пресс держи напряжённым.\n\n"
            "⚠️ Частые ошибки\n"
            "• Сгибаешь тазобедренный сустав — садишься на пятки: нет нагрузки на пресс.\n"
            "• Тянешь руками, а не скручиваешь торс — работают руки, не пресс.\n"
            "• Слишком тяжёлый вес — теряешь контроль скручивания.\n\n"
            "💡 Подсказка\n"
            "Представь, что хочешь дотянуться лбом до колен. Бёдра неподвижны."
        )
    },
    "diamond_pushup": {
        "title": "Алмазные отжимания",
        "mp4": "media/tech/diamond_pushup.mp4",
        "text": (
            "📚 Алмазные отжимания (трицепс + грудь)\n\n"
            "✅ Настройка\n"
            "• Ладони на полу под грудью: большие и указательные пальцы образуют ромб (алмаз).\n"
            "• Тело — прямая линия, пресс и ягодицы напряжены.\n\n"
            "✅ Как выполнять\n"
            "1) Медленно опускай грудь к рукам — 2–3 сек.\n"
            "2) Локти идут строго назад вдоль корпуса — не в стороны.\n"
            "3) Грудь почти касается рук — полная амплитуда.\n"
            "4) Выжимай вверх до выпрямления рук — без замка в локтях.\n\n"
            "⚠️ Частые ошибки\n"
            "• Локти уходят в стороны — вся нагрузка уходит с трицепса.\n"
            "• Таз провисает — напряги пресс и ягодицы.\n"
            "• Дискомфорт в запястьях — попробуй на кулаках или чуть разверни пальцы.\n\n"
            "💡 Подсказка\n"
            "Самое эффективное упражнение на трицепс без инвентаря. "
            "Паузы внизу 1–2 сек резко увеличивают нагрузку."
        )
    },
}

# =========================
# ВАЛИДАЦИЯ: TECH_GIFS ↔ TECH
# Если кто-то добавит gif, но забудет написать текст техники —
# бот не упадёт, но покажет warning при старте.
# =========================
_tech_missing = [k for k in TECH_GIFS if k not in TECH]
if _tech_missing:
    import logging as _log
    _log.getLogger("trainer_bot").warning(f"TECH missing keys: {_tech_missing}")


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
        [InlineKeyboardButton(text=TECH["legpress"]["title"], callback_data="tech:legpress"),
         InlineKeyboardButton(text=TECH["hinge"]["title"], callback_data="tech:hinge")],
        [InlineKeyboardButton(text=TECH["core"]["title"], callback_data="tech:core"),
         InlineKeyboardButton(text=TECH["calves"]["title"], callback_data="tech:calves")],
        [InlineKeyboardButton(text=TECH["lunge"]["title"], callback_data="tech:lunge"),
         InlineKeyboardButton(text=TECH["hyperext"]["title"], callback_data="tech:hyperext")],
        [InlineKeyboardButton(text=TECH["face_pull"]["title"], callback_data="tech:face_pull"),
         InlineKeyboardButton(text=TECH["hammer"]["title"], callback_data="tech:hammer")],
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
            [KeyboardButton(text="🏠 Меню"), KeyboardButton(text="🆘 Поддержка")],
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
        [
            InlineKeyboardButton(text="🏋️ Мои тренировки", callback_data="nav:workouts"),
            InlineKeyboardButton(text="🍽 Моё питание", callback_data="nav:nutrition"),
        ],
        [
            InlineKeyboardButton(text="📖 Дневник", callback_data="nav:diary"),
            InlineKeyboardButton(text="📊 Замеры", callback_data="nav:measures"),
        ],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="p:edit"),
            InlineKeyboardButton(text="❓ FAQ", callback_data="nav:faq"),
        ],
        [InlineKeyboardButton(text="🔄 Сменить программу", callback_data="p:rebuild_plan")],
        [InlineKeyboardButton(text="⚙️ Тарифы / доступ", callback_data="nav:upgrade")],
    ])


def simple_back_to_menu_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


# =========================
# ✅ Тренировки: кнопки дней
# =========================
def workout_days_kb(freq: int, has_full_access: bool = False, plan_struct: dict = None):
    freq = max(MIN_DAYS, min(int(freq or 3), MAX_DAYS))
    rows = []
    btns = []
    for i in range(1, freq + 1):
        btn_text = f"📅 День {i}"
        btns.append(InlineKeyboardButton(text=btn_text, callback_data=f"wday:{i}"))
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])

    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
# ЮКасса: кнопки
# =========================
def pay_tariff_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🏆 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
        [InlineKeyboardButton(text=f"🔥 3 месяца — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
        [InlineKeyboardButton(text=f"📅 1 месяц — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
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
TOTAL_PROFILE_STEPS = 11


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
        [InlineKeyboardButton(text="✏️ Изменить профиль", callback_data="p:edit")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def profile_edit_field_kb(u: dict, regens_str: str = "") -> InlineKeyboardMarkup:
    """Меню выбора конкретного поля профиля для редактирования — 2 столбца."""
    def val(k, fallback="—"):
        v = u.get(k)
        return str(v) if v else fallback

    plan_btn_text = f"🚀 Составить новый план"
    if regens_str:
        plan_btn_text += f"  ({regens_str})"

    rows = [
        [
            InlineKeyboardButton(text=f"🎯 Цель: {val('goal')}", callback_data="pf:goal"),
            InlineKeyboardButton(text=f"👤 Пол: {val('sex')}", callback_data="pf:sex"),
        ],
        [
            InlineKeyboardButton(text=f"🎂 Возраст: {val('age')}", callback_data="pf:age"),
            InlineKeyboardButton(text=f"📏 Рост: {val('height')} см", callback_data="pf:height"),
        ],
        [
            InlineKeyboardButton(text=f"⚖️ Вес: {val('weight')} кг", callback_data="pf:weight"),
            InlineKeyboardButton(text=f"🏠 Место: {val('place')}", callback_data="pf:place"),
        ],
        [
            InlineKeyboardButton(text=f"📈 Опыт: {val('exp')}", callback_data="pf:exp"),
            InlineKeyboardButton(text=f"📅 Трен/нед: {val('freq')}", callback_data="pf:freq"),
            InlineKeyboardButton(text=f"🍽 Приёмов еды: {val('meals')}", callback_data="pf:meals"),
        ],
        [
            InlineKeyboardButton(text=f"⛔️ Ограничения", callback_data="pf:limits"),
        ],
        [InlineKeyboardButton(text=plan_btn_text, callback_data="p:do_rebuild")],
        [InlineKeyboardButton(text="🏠 Назад", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_program_tariff_kb():
    """Тарифная кнопка после заполнения профиля — ознакомление с тарифами."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Ознакомиться с тарифами", callback_data="nav:upgrade_after_profile")],
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
    return "Доступ закрыт.\nВыбери тариф — кнопка «Тарифы / доступ» в меню."


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


async def _send_with_image(
    bot: Bot, chat_id: int, user_id: int,
    text: str, image_key: str, reply_markup=None
):
    """Отправляет сообщение с фото из IMAGE_PATHS[image_key].
    Если файл не найден — отправляет обычный текст.
    Всегда удаляет предыдущее главное сообщение бота (чистый чат)."""
    path = IMAGE_PATHS.get(image_key, "")
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass
    if path and os.path.exists(path):
        try:
            photo = FSInputFile(path)
            caption = text[:1020] + ("…" if len(text) > 1020 else "")
            m = await bot.send_photo(
                chat_id=chat_id, photo=photo,
                caption=caption, reply_markup=reply_markup
            )
            await set_last_bot_msg_id(user_id, m.message_id)
            return m.message_id
        except Exception:
            pass  # fallback to text
    m = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    await set_last_bot_msg_id(user_id, m.message_id)
    return m.message_id


async def send_section(
    bot: Bot,
    chat_id: int,
    user_id: int,
    image_path: str,
    text: str,
    reply_markup=None,
    callback: Optional[CallbackQuery] = None,
):
    """Универсальная отправка раздела с картинкой из локального файла.

    Логика:
    - Если callback передан — сначала пытается edit_text (без фото, быстро).
      При наличии файла: удаляет старое сообщение и отправляет send_photo.
    - Если файл существует → send_photo(caption=text).
      Если текст > 1020 символов → фото без caption + отдельное send_message.
    - Если файла нет (или ошибка) → обычный send_message / edit_text.
    - Бот никогда не падает: все ошибки обёрнуты в try/except + warning-лог.
    """
    TG_CAPTION_LIMIT = 1020

    # ── определяем наличие файла ────────────────────────────────────────────
    has_image = bool(image_path and os.path.exists(image_path))

    # ── ветка callback: редактирование существующего сообщения ──────────────
    if callback:
        if has_image:
            # Удаляем старое сообщение, отправляем фото
            last_id = await get_last_bot_msg_id(user_id)
            if last_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=last_id)
                except Exception:
                    pass
            try:
                photo = FSInputFile(image_path)
                if len(text) <= TG_CAPTION_LIMIT:
                    m = await bot.send_photo(
                        chat_id=chat_id, photo=photo,
                        caption=text, reply_markup=reply_markup
                    )
                    await set_last_bot_msg_id(user_id, m.message_id)
                else:
                    m = await bot.send_photo(chat_id=chat_id, photo=photo)
                    m2 = await bot.send_message(
                        chat_id=chat_id, text=text, reply_markup=reply_markup
                    )
                    await set_last_bot_msg_id(user_id, m2.message_id)
                return
            except Exception as e:
                logger.warning(f"send_section: не удалось отправить фото {image_path}: {e}")
        # fallback — просто редактируем текст
        await clean_edit(callback, user_id, text, reply_markup=reply_markup)
        return

    # ── ветка без callback: отправка нового сообщения ───────────────────────
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass

    if has_image:
        try:
            photo = FSInputFile(image_path)
            if len(text) <= TG_CAPTION_LIMIT:
                m = await bot.send_photo(
                    chat_id=chat_id, photo=photo,
                    caption=text, reply_markup=reply_markup
                )
                await set_last_bot_msg_id(user_id, m.message_id)
            else:
                m = await bot.send_photo(chat_id=chat_id, photo=photo)
                m2 = await bot.send_message(
                    chat_id=chat_id, text=text, reply_markup=reply_markup
                )
                await set_last_bot_msg_id(user_id, m2.message_id)
            return
        except Exception as e:
            logger.warning(f"send_section: не удалось отправить фото {image_path}: {e}")

    # финальный fallback — только текст
    m = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    await set_last_bot_msg_id(user_id, m.message_id)


async def send_screen(
    message: Message,
    text: str,
    image_key: Optional[str] = None,
    reply_markup=None,
    edit: bool = False,
):
    """Универсальная функция отправки экрана с опциональным изображением.
    Если edit=True — пытаемся редактировать текст, иначе отправляем новое.
    Если image_key указан и файл существует — используем send_photo."""
    bot: Bot = message.bot
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    path = IMAGE_PATHS.get(image_key or "", "") if image_key else ""
    has_image = bool(path and os.path.exists(path))

    if edit and not has_image:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            await set_last_bot_msg_id(user_id, message.message_id)
            return
        except Exception:
            pass

    # Удаляем предыдущее главное сообщение
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass

    if has_image:
        try:
            photo = FSInputFile(path)
            caption = text[:1020] + ("…" if len(text) > 1020 else "")
            m = await bot.send_photo(
                chat_id=chat_id, photo=photo,
                caption=caption, reply_markup=reply_markup
            )
            await set_last_bot_msg_id(user_id, m.message_id)
            return
        except Exception:
            pass

    m = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    await set_last_bot_msg_id(user_id, m.message_id)


async def send_tech(
    bot: Bot, chat_id: int, user_id: int,
    tech_key: str, text: str, reply_markup=None
):
    """Универсальная отправка техники упражнения.

    Приоритет медиа:
      1. TECH_VIDEOS[key]  или  TECH[key]["mp4"]  — send_video (mp4)
      2. TECH_IMAGES[key]  или  TECH[key]["img"]  — send_photo (jpg/png)
      3. TECH_GIFS[key]
           • путь .mp4  → send_video
           • путь .gif  → send_animation
      4. только текст — send_message (graceful fallback)

    Текст всегда в одном сообщении с медиа — caption обрезается умно по \n.

    Как добавить видео к упражнению (3 способа):
      А) Положи файл media/tech/<key>.mp4, пропиши в TECH_VIDEOS[key] — приоритет 1.
      Б) Пропиши путь прямо в TECH[key]["mp4"] = "media/tech/..." — приоритет 1.
      В) Пропиши в TECH_GIFS[key] = "media/tech/<key>.mp4" — приоритет 3
         (если нет TECH_VIDEOS и нет TECH_IMAGES для данного ключа).
      Хендлеры менять не нужно — заработает автоматически.

    Удаляет предыдущее главное сообщение (чистый чат)."""

    # Удаляем предыдущее сообщение
    last_id = await get_last_bot_msg_id(user_id)
    if last_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass

    tech_item = TECH.get(tech_key, {})

    # ── Умная обрезка caption ────────────────────────────────────────────────
    # Telegram: caption для video/photo/animation — максимум 1024 символа.
    # Обрезаем по последнему переносу строки перед лимитом.
    LIMIT = 1020
    def make_caption(t: str) -> str:
        if len(t) <= LIMIT:
            return t
        cut = t[:LIMIT]
        last_nl = cut.rfind("\n")
        if last_nl > LIMIT // 2:
            return cut[:last_nl] + "\n…"
        return cut + "…"

    caption = make_caption(text)

    # ── Вспомогательная: отправить видео (mp4) ───────────────────────────────
    async def _try_send_video(path: str) -> bool:
        try:
            m = await bot.send_video(
                chat_id=chat_id,
                video=FSInputFile(path),
                caption=caption,
                reply_markup=reply_markup,
            )
            await set_last_bot_msg_id(user_id, m.message_id)
            return True
        except Exception as _e:
            logging.warning(f"[send_tech] send_video failed key={tech_key!r} path={path!r}: {_e}")
            return False

    # ── Вариант 1: MP4 из TECH_VIDEOS (внешний словарь — высший приоритет) ───
    video_path = TECH_VIDEOS.get(tech_key, "")
    if not video_path:
        # Проверяем поле "mp4" прямо в словаре TECH (устаревший формат хранения)
        video_path = tech_item.get("mp4", "")
    if video_path and os.path.exists(video_path):
        if await _try_send_video(video_path):
            return
        # fallback → картинка

    # ── Вариант 2: JPG/PNG из TECH_IMAGES ────────────────────────────────────
    img_path = TECH_IMAGES.get(tech_key, "")
    if not img_path:
        img_path = tech_item.get("img", "")
    if img_path and os.path.exists(img_path):
        try:
            m = await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(img_path),
                caption=caption,
                reply_markup=reply_markup,
            )
            await set_last_bot_msg_id(user_id, m.message_id)
            return
        except Exception:
            pass  # fallback → gif → text

    # ── Вариант 3: GIF/MP4 из TECH_GIFS ──────────────────────────────────────
    # Если путь оканчивается на .mp4 — отправляем как видео (send_video),
    # иначе (gif) — send_animation.
    gif_path = TECH_GIFS.get(tech_key, "")
    if gif_path and os.path.exists(gif_path):
        if gif_path.lower().endswith(".mp4"):
            # mp4 в TECH_GIFS → отправляем как видео
            if await _try_send_video(gif_path):
                return
        else:
            try:
                m = await bot.send_animation(
                    chat_id=chat_id,
                    animation=FSInputFile(gif_path),
                    caption=caption,
                    reply_markup=reply_markup,
                )
                await set_last_bot_msg_id(user_id, m.message_id)
                return
            except Exception as _e:
                logging.warning(f"[send_tech] send_animation failed key={tech_key!r}: {_e}")
                # fallback → text

    # ── Вариант 4: только текст ───────────────────────────────────────────────
    m = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    await set_last_bot_msg_id(user_id, m.message_id)


# Обратная совместимость — старое имя перенаправляет на новое
_send_tech_with_gif = send_tech


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
            ("activity", "INTEGER"),
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
            tariff_name TEXT NOT NULL DEFAULT 'Нет',
            expires_at TEXT,
            paid_at TEXT,
            plan_regens_left INTEGER DEFAULT NULL,
            remind_stage INTEGER NOT NULL DEFAULT -1
        )
        """)
        for _col, _typ in [
            ("plan_regens_left", "INTEGER DEFAULT NULL"),
            ("tariff_name",      "TEXT NOT NULL DEFAULT 'Нет'"),
            ("remind_stage",     "INTEGER NOT NULL DEFAULT -1"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE access ADD COLUMN {_col} {_typ}")
            except Exception:
                pass

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
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            completed_date TEXT,
            created_at TEXT,
            UNIQUE(user_id, completed_date)
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            log_date TEXT,
            target_kcal INTEGER,
            actual_kcal INTEGER,
            created_at TEXT,
            UNIQUE(user_id, log_date)
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            day_date TEXT,
            calories INTEGER,
            created_at TEXT,
            UNIQUE(user_id, day_date)
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
            SELECT user_id, username, goal, sex, age, height, weight, place, exp, freq, meals, limits, state, activity
            FROM users WHERE user_id=?
        """, (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        return {}
    return {
        "user_id": row[0], "username": row[1], "goal": row[2], "sex": row[3],
        "age": row[4], "height": row[5], "weight": row[6], "place": row[7],
        "exp": row[8], "freq": row[9], "meals": row[10], "limits": row[11],
        "state": row[12], "activity": row[13]
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


async def get_plan_regens(user_id: int):
    """Возвращает (regens_left, is_unlimited).
    regens_left=None => безлимит. regens_left=0 => исчерпан."""
    async with db() as conn:
        async with conn.execute(
            "SELECT plan_regens_left, tariff FROM access WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return (0, False)
    regens_left = row[0]
    tariff_code = row[1] or ""
    t = TARIFFS.get(tariff_code, {})
    base_regens = t.get("plan_regens")
    if base_regens is None:
        return (None, True)  # безлимит
    if regens_left is None:
        # Поле ещё не установлено (старые записи) — использовать базовый лимит тарифа
        return (base_regens, False)
    return (int(regens_left), False)


async def decrement_plan_regens(user_id: int):
    """Уменьшить счётчик на 1 (не уходить ниже 0)."""
    async with db() as conn:
        await conn.execute(
            "UPDATE access SET plan_regens_left = MAX(0, COALESCE(plan_regens_left, 0) - 1) WHERE user_id=?",
            (user_id,)
        )
        await conn.commit()



# =========================
# ПОДПИСКА: утилиты
# =========================
async def get_subscription(user_id: int) -> dict:
    """Единый источник правды о подписке пользователя."""
    async with db() as conn:
        async with conn.execute(
            """SELECT paid, tariff, tariff_name, expires_at, paid_at, remind_stage
               FROM access WHERE user_id=?""",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {
            "tariff": "none", "tariff_name": "Нет",
            "expires_at": None, "is_active": 0, "remind_stage": -1
        }
    paid, tariff, tariff_name, expires_at, paid_at, remind_stage = row
    is_active = 0
    if paid == 1:
        if tariff == "life":
            is_active = 1
        elif expires_at:
            try:
                if datetime.utcnow() < datetime.fromisoformat(expires_at):
                    is_active = 1
            except Exception:
                pass
    return {
        "tariff": tariff or "none",
        "tariff_name": tariff_name or "Нет",
        "expires_at": expires_at,
        "is_active": is_active,
        "remind_stage": remind_stage if remind_stage is not None else -1,
    }


def is_subscription_active(sub: dict) -> bool:
    return bool(sub.get("is_active"))


def format_tariff_line(sub: dict) -> str:
    """Строка о тарифе для главного меню."""
    tariff = sub.get("tariff", "none")
    if not is_subscription_active(sub):
        return "💳 Тариф: нет (доступ ограничен)"
    if tariff == "life":
        return "💳 Тариф: Навсегда ✅"
    tariff_name = sub.get("tariff_name") or ""
    expires_at = sub.get("expires_at")
    if expires_at:
        try:
            dt = datetime.fromisoformat(expires_at)
            return f"💳 Тариф: {tariff_name} (до {dt.strftime('%d.%m.%Y')})"
        except Exception:
            pass
    return f"💳 Тариф: {tariff_name}"


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
    regens = t.get("plan_regens")  # None = безлимит, 0 = нельзя, N = лимит
    tariff_name = t.get("title", tariff_code)

    async with db() as conn:
        await conn.execute(
            """UPDATE access
               SET paid=1, tariff=?, tariff_name=?, expires_at=?, paid_at=?,
                   plan_regens_left=?, remind_stage=-1
               WHERE user_id=?""",
            (tariff_code, tariff_name, expires_at, now_iso, regens, user_id)
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

        title = f"Тренировка {today}"  # обновляется через update_diary_session_title если известен тип дня
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
    # ── СПЕЦИФИЧНЫЕ — строго раньше общих ───────────────────────────────────

    # Присед — варианты по инвентарю (специфичные перед общим "присед")
    ("гоблет-присед", "goblet"),
    ("гоблет", "goblet"),
    ("присед с гантел", "goblet"),               # присед с гантелью/гантелями → goblet
    ("хакк-присед", "hack_squat"),
    ("хакк", "hack_squat"),
    ("болгарские выпады с гантелями", "bulgarian_dumbbell"),
    ("болгарские выпады", "bulgarian"),
    ("болгарские выпады (нога", "bulgarian"),
    ("болгар", "bulgarian"),
    ("выпады ходьбой", "lunge_walking"),          # ← уникальная: динамика
    ("выпады со штангой", "lunge_barbell"),        # ← уникальная: со штангой
    ("выпады с гантелями (ходьба)", "lunge_walking"),  # ← добавлено: вариант из пула зала
    ("выпады с гантелями", "lunge_dumbbell"),      # ← уникальная: с гантелями
    ("выпады", "lunge_bw"),                          # ← свой вес
    ("присед со штангой", "squat_barbell"),        # ← уникальная: техника со штангой
    ("присед сумо", "squat_sumo"),                 # ← уникальная: техника сумо
    ("присед с паузой", "squat_barbell"),
    ("приседания (собственный вес)", "squat_bw"),    # ← домашнее
    ("ягодичный мост на одной ноге", "glute_bridge_single"),
    ("ягодичный мост", "glute_bridge"),              # ← домашнее
    ("молитва", "cable_crunch"),
    ("кранч на блоке", "cable_crunch"),
    ("планка (статика)", "planks_static"),

    # Нижняя тяга / задняя цепь
    ("румынская тяга с гантелями", "rdl_dumbbell"),    # ← уникальная
    ("румынская тяга со штангой", "rdl_barbell"),      # ← уникальная
    ("становая тяга сумо", "deadlift_sumo"),            # ← уникальная
    ("становая тяга (техника)", "deadlift"),
    ("становая тяга (лёгкая", "deadlift"),
    ("становая тяга", "deadlift"),
    ("ягодичный мост на одной ноге", "glute_bridge_single"),
    ("гиперэкстензия с весом", "hyperext"),
    ("гиперэкстензия на полу", "hyperext"),
    ("гиперэкстензи", "hyperext"),
    ("good-morning", "good_morning"),

    # Ноги — тренажёры
    ("разгибания ног в тренажёре", "leg_extension"),     # ← уникальная
    ("разгибание ног в тренажёре", "leg_extension"),     # ← уникальная
    ("сведения ног в тренажёре", "leg_adduction"),       # ← уникальная
    ("отведения ног назад в кроссовере", "cable_kickback"), # ← уникальная
    ("кроссовер отведени", "cable_kickback"),             # ← уникальная
    ("жим ногами в тренажёре", "legpress"),
    ("жим ногами", "legpress"),
    ("жим ног", "legpress"),
    ("сгибания ног в тренажёре", "legcurl"),
    ("сгибания ног", "legcurl"),

    # Икры
    ("подъёмы на носки в тренажёре", "calves_machine"),
    ("подъёмы на носки стоя", "calves_standing"),

    # Жим лёжа — варианты по инвентарю
    ("жим штанги под углом", "incline_press_barbell"),     # ← уникальная
    ("жим гантелей под углом", "incline_press_dumbbell"),  # ← уникальная
    ("жим гантелей лёж", "bench_dumbbell"),                # ← уникальная
    ("жим в тренажёре (грудь)", "bench_machine"),          # ← уникальная
    ("жим в тренаж", "bench_machine"),
    ("разведения гантелей лёжа", "chest_fly"),             # ← уникальная
    ("кроссовер", "crossover_chest"),

    # Жим вверх — ohp_barbell строго перед ohp
    ("армейский жим", "ohp_barbell"),
    ("жим штанги стоя", "ohp_barbell"),
    ("арнольд-жим", "arnold_press"),              # ← уникальная
    ("арнольд жим", "arnold_press"),              # ← уникальная
    ("жим арнольда", "arnold_press"),             # ← уникальная
    ("жим гантелей стоя", "ohp_dumbbell"),         # ← уникальная
    ("жим гантелей сидя", "ohp_dumbbell_sitting"),          # ← уникальная
    ("жим в тренажёре вверх", "ohp_machine"),       # ← уникальная

    # Отжимания
    ("пайк отжимания", "pike_pushup"),                        # ← домашнее (плечи)
    ("отжимания в стойке у стены", "wall_pushup"),            # ← домашнее (плечи)
    ("отжимания широким хватом", "pushup_wide"),              # ← домашнее (грудь)
    ("отжимания с ногами на возвышенности", "pushup_elevated"), # ← домашнее
    ("алмазные отжимания", "diamond_pushup"),
    ("отжимания узкие (трицепс)", "narrow_pushup"),
    ("отжима узк", "narrow_pushup"),
    ("узкие отжима", "narrow_pushup"),
    ("отжимания с ногами на возвышении", "pushup_elevated"),  # ← уникальная
    ("отжимания с ногами на возвышен", "pushup_elevated"),    # ← уникальная
    ("отжимания узкие", "narrow_pushup"),         # ← точное имя из пула (без "(трицепс)")

    # Подтягивания — варианты хвата
    ("подтягивания широким хватом", "pullup_wide"),        # ← домашнее
    ("подтягивания узким хватом", "pullup_narrow"),        # ← домашнее
    ("подтягивания (широкий", "pullup_wide"),              # ← уникальная
    ("подтягивания (обратный хват)", "pullup_chinup"),     # ← уникальная
    ("верхний блок широк", "latpulldown_wide"),            # ← уникальная
    ("верхний блок узк", "latpulldown_narrow"),            # ← уникальная
    ("тяга верхнего блока широк", "latpulldown_wide"),
    ("тяга верхнего блока узк", "latpulldown_narrow"),

    # Горизонтальные тяги — barbell_row/dumbbell_row перед rowtrain
    ("тяга штанги в наклоне", "barbell_row"),
    ("тяга т-гриф", "tbar_row"),                  # ← уникальная
    ("тяга т-грифа", "tbar_row"),                  # ← уникальная
    ("тяга к груди узким", "latpulldown_close"),   # ← уникальная
    ("тяга к груди нейтральн", "latpulldown_close"), # ← уникальная
    ("тяга гантели одной рукой", "dumbbell_row"),
    ("тяга гантел", "dumbbell_row"),
    ("тяга нижнего блока", "rowtrain"),
    ("тяга горизонт", "rowtrain"),
    ("тяга в тренаж", "rowtrain"),

    # Тяга резинок — специфичные перед "тяга резинки"
    ("тяга резинки к лицу", "face_pull"),

    # Face pull / задняя дельта
    ("face pull", "face_pull"),
    ("тяга к лицу", "face_pull"),
    ("задняя дельта в тренажёре", "rear_delt_machine"),
    ("задняя дельта", "rear_delt"),
    ("разведения гантелей в наклоне", "rear_delt"),
    ("разведения гантелей в стороны", "lateralraise_dumbbell"),
    ("подъёмы перед собой", "front_raise"),              # ← уникальная
    ("подъём перед собой", "front_raise"),               # ← уникальная

    # Пресс — варианты по упражнению (специфичные перед общим "планка")
    ("ролик для пресса (ab wheel)", "ab_rollout"),
    ("ролик для пресса", "ab_rollout"),             # ← уникальная
    ("ab wheel", "ab_rollout"),
    ("боковая планка", "side_plank"),               # ← уникальная
    ("велосипед (скручивания", "ab_crunch"),
    ("велосипед", "ab_crunch"),
    ("обратные скручивания", "ab_crunch"),
    ("скручивания на блоке", "ab_crunch"),
    ("скручивания на полу", "ab_crunch"),
    ("скручивания (пресс)", "ab_crunch"),            # ← добавлено (точное название из пула)
    ("подъёмы ног в висе", "hanging_leg_raise"),    # ← уникальная
    ("подъёмы ног в упоре на локтях", "elbow_leg_raise"),  # ← уникальная
    ("подъёмы ног в упоре", "elbow_leg_raise"),
    ("подъёмы ног лёжа", "leg_raise_lying"),        # ← уникальная
    ("подъём ног лёжа", "leg_raise_lying"),
    ("подъём ног в висе", "hanging_leg_raise"),
    ("подъём ног", "leg_raise_lying"),
    ("скручива", "ab_crunch"),

    # Бицепс — варианты по инвентарю
    ("концентрированные сгибания", "concentration_curl"),  # ← уникальная
    ("сгибания штанги стоя", "biceps_barbell"),         # ← уникальная
    ("сгибания со штангой", "biceps_barbell"),          # ← уникальная
    ("сгибания гантелей стоя", "biceps_dumbbell"),      # ← уникальная (перед "сгибани")
    ("сгибания с гантелями", "biceps_dumbbell"),        # ← добавлено
    ("молотки с гантелями", "hammer"),
    ("молотки", "hammer"),

    # Трицепс — специфичные перед "разгибани"
    ("французский жим лёжа", "french_press"),            # ← уникальная
    ("французский жим", "french_press"),               # ← уникальная
]


def get_tech_key_for_exercise(name: str) -> Optional[str]:
    n = name.lower()
    for keyword, tech_key in EXERCISE_TECH_MAP:
        if keyword in n:
            return tech_key
    # Fallback — общая техника по группе мышц
    if any(x in n for x in ["ног", "ягодиц", "икр", "бедр", "приседа", "приседани", "выпад"]):
        return "squat"
    if any(x in n for x in ["грудь", "грудн", "жим"]):
        return "bench"
    if any(x in n for x in ["спин", "тяг", "блок", "подтяг"]):
        return "latpulldown"
    if any(x in n for x in ["плеч", "дельт", "жим вверх"]):
        return "ohp"
    if any(x in n for x in ["бицепс", "сгибан"]):
        return "biceps"
    if any(x in n for x in ["трицепс", "разгибан"]):
        return "triceps"
    if any(x in n for x in ["пресс", "планк", "скручив"]):
        return "core"
    if any(x in n for x in ["подъём ног", "подъёмы ног"]):
        if "вис" in n:
            return "hanging_leg_raise"
        if "упор" in n:
            return "elbow_leg_raise"
        return "leg_raise_lying"
    if any(x in n for x in ["румынск", "мертвая", "становая", "hinge", "ягодиц"]):
        return "rdl"
    return None  # нет подходящей техники — не показываем кнопку


# =========================
# Название дня по типу
# =========================
DAY_NAMES = {
    "FB-A": "Фулбади А",
    "FB-B": "Фулбади Б",
    "FB-C": "Фулбади В",
    "UPPER": "Верх тела",
    "LOWER": "Низ тела",
    "PUSH": "Грудь и Плечи",
    "PULL": "Спина и Бицепс",
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


def get_day_display_name(day_num: int, day_text: str, system: str = "") -> str:
    """Возвращает понятное название дня с учётом системы и номера."""
    t = day_text.lower()
    # Full Body — A/B/C
    if "фулбади" in t or "fullbody" in t:
        suffix = {1: "A", 2: "B", 3: "C"}.get(day_num, str(day_num))
        return f"Full Body {suffix}"
    # Верх/Низ — без "тела"
    if "верх" in t and "тела" in t:
        return "Верх"
    if "низ" in t:
        return "Низ"
    # PPL — с группами мышц
    if "толчок" in t or "push" in t:
        return "Грудь и Плечи"
    if ("тяга" in t or "pull" in t) and ("спина" in t or "бицепс" in t or "pull" in t):
        return "Спина и Бицепс"
    if "ноги" in t and "тяга" not in t:
        return "Ноги"
    return f"День {day_num}"


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


async def mark_day_completed(user_id: int, day_num: int, day_title: str = ""):
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        # Добавляем поле day_title если его нет (миграция)
        try:
            await conn.execute("ALTER TABLE workout_completions ADD COLUMN day_title TEXT DEFAULT ''")
        except Exception:
            pass
        await conn.execute("""
            INSERT INTO workout_completions (user_id, day_num, completed_date, created_at, day_title)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, day_num, today, now, day_title))
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


def build_day_display_text(day_num: int, day_text: str, exercises: List[str],
                            done: List[int], all_done: bool = False) -> str:
    """Строит текст дня тренировки с заголовком типа дня."""
    total = len(exercises)
    done_count = len(done)

    # Определяем тип дня из текста плана
    t = day_text.lower()
    # Верх тела (включая А/Б варианты для обратной совместимости)
    if "верх тела" in t or "верх а" in t or "верх б" in t or (
            "верх" in t and ("вариант а" in t or "вариант б" in t)):
        day_type = "Верх"
        day_note = ""
    elif ("низ тела" in t or ("ниж" in t and "тел" in t) or
          "низ а" in t or "низ б" in t or
          ("низ" in t and ("вариант а" in t or "вариант б" in t))):
        day_type = "Низ"
        day_note = ""
    elif "толчок" in t or ("грудь и плеч" in t):
        day_type = "Грудь и Плечи"
        day_note = "Грудь, дельты, трицепс"
    elif ("тяга" in t and "спина" in t) or ("спина и бицепс" in t):
        day_type = "Спина и Бицепс"
        day_note = "Широчайшие, ромбовидные, бицепс"
    elif "ноги" in t and "квадрицепс" in t:
        day_type = "Ноги"
        day_note = "Квадрицепс, бицепс бедра, ягодицы, икры"
    elif "фулбади" in t or "fullbody" in t:
        # Full Body — A/B/C
        suffix_map = {1: "A", 2: "B", 3: "C"}
        s = suffix_map.get(day_num, str(day_num))
        day_type = f"Full Body {s}"
        day_note = ""
    else:
        day_type = get_day_display_name(day_num, day_text) or "Тренировка"
        day_note = ""

    lines = []
    # Заголовок строго: 🏋️ День N: фокус (или 🏋️ Фулбади для fullbody)
    is_fullbody = ("фулбади" in t or "fullbody" in t)
    if is_fullbody:
        lines.append("🏋️ Фулбади")
        lines.append("📌 всё тело")
    else:
        lines.append(f"🏋️ День {day_num}: {day_type}")
        if day_note:
            lines.append(f"📌 {day_note}")
    lines.append("")
    lines.append("⚠️ Перед тренировкой разомнись 5–10 минут")
    lines.append("⏱ Отдых между подходами: ~1.5–2 мин")
    lines.append("📚 Кнопка «Техника» под каждым упражнением")
    lines.append("")

    ex_full = parse_exercises_full(day_text)
    for idx, (name, sets_reps) in enumerate(ex_full):
        is_done = idx in done
        mark = "✅" if is_done else "🔸"
        if sets_reps:
            lines.append(f"{mark} {name} {sets_reps}")
        else:
            lines.append(f"{mark} {name}")

    lines.append("")
    lines.append("🏁 После тренировки — заминка и растяжка 5–10 минут")
    lines.append("")

    bar = workout_progress_bar(done_count, total)
    if all_done:
        lines.append(f"{bar}")
        lines.append(f"✅ {done_count}/{total} упражнений выполнено")
        lines.append("")
        lines.append("🎉 ОТЛИЧНО! День засчитан!")
    else:
        lines.append(f"{bar}")
        lines.append(f"✅ {done_count}/{total} упражнений выполнено")

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
        # ══ ЗАЛ: WHITELIST упражнений для генерации ══════════════════════════
        # БАЗА — только многосуставные движения (3–4 на тренировку)
        # Каждое упражнение имеет ключ в TECH_GIFS/TECH_IMAGES

        BASE_SQUAT = [
            "Присед со штангой",              # squat_barbell ★
            "Жим ногами в тренажёре",         # legpress
            "Болгарские выпады с гантелями",  # bulgarian
            "Гоблет-присед с гантелями",      # goblet
        ]
        BASE_HINGE = [
            "Становая тяга",                  # deadlift ★
            "Становая тяга сумо",             # deadlift_sumo
            "Румынская тяга со штангой",       # rdl_barbell
            "Румынская тяга с гантелями",      # rdl_dumbbell
        ]
        BASE_HPUSH = [
            "Жим штанги лёжа",                # bench ★
            "Жим гантелей лёжа",              # bench_dumbbell
            "Жим штанги под углом",           # incline_press_barbell
            "Жим гантелей под углом",         # incline_press_dumbbell
        ]
        BASE_HPULL = [
            "Тяга штанги в наклоне",          # barbell_row ★
            "Тяга гантели одной рукой",       # dumbbell_row
            "Тяга горизонтального блока",     # rowtrain
        ]
        BASE_VPULL = [
            "Подтягивания (широкий хват)",    # pullup_wide ★
            "Подтягивания (обратный хват)",   # pullup_chinup
            "Верхний блок широким хватом",    # latpulldown_wide
            "Верхний блок узким хватом",      # latpulldown_narrow
        ]
        BASE_VPUSH = [
            "Жим штанги стоя",                # ohp_barbell ★
            "Жим гантелей стоя",              # ohp_dumbbell
        ]
        # ИЗОЛЯЦИЯ — добивочные (максимум 1–2 на тренировку)
        ISO_CHEST = [
            "Разведения гантелей лёжа",       # chest_fly
        ]
        ISO_SHOULD = [
            "Разведения гантелей в стороны",  # lateralraise
            "Face pull на блоке",             # face_pull
            "Задняя дельта в тренажёре",      # rear_delt
        ]
        ISO_BI = [
            "Сгибания гантелей стоя",         # biceps
            "Молотки с гантелями",            # hammer
        ]
        ISO_TRI = [
            "Разгибания на верхнем блоке",    # triceps
            "Французский жим с гантелями",    # triceps_oh
        ]
        ISO_LEGS = [
            "Сгибания ног в тренажёре",       # legcurl
            "Подъёмы на носки в тренажёре",   # calves
        ]
        CORE_POOL = [
            "Подъёмы ног в висе",             # hanging_leg_raise
            "Планка (статика)",               # core
            "Молитва",                        # cable_crunch
        ]

    else:
        # ══ ДОМА: WHITELIST упражнений для генерации ═════════════════════════

        BASE_SQUAT = [
            "Приседания (собственный вес)",   # squat_bw ★
            "Болгарские приседания",          # bulgarian_bw
            "Выпады",                         # lunge_bw
        ]
        BASE_HINGE = [
            "Ягодичный мост",                 # glute_bridge ★
            "Румынская тяга с гантелями",      # rdl_dumbbell
            "Гиперэкстензия на полу",         # hyperext
        ]
        BASE_HPUSH = [
            "Отжимания",                      # pushup ★
            "Отжимания широким хватом",       # pushup_wide
            "Отжимания с ногами на возвышенности",  # pushup_elevated
        ]
        BASE_HPULL = [
            "Горизонтальные подтягивания",    # pullup ★
            "Тяга гантели одной рукой",       # dumbbell_row
        ]
        BASE_VPULL = [
            "Подтягивания широким хватом",    # pullup_wide ★
            "Подтягивания обратным хватом",   # pullup_chin
            "Подтягивания узким хватом",      # pullup_narrow
        ]
        BASE_VPUSH = [
            "Жим гантелей сидя",              # ohp_dumbbell ★
        ]
        ISO_CHEST = [
            "Отжимания узким хватом",         # pushup_narrow
            "Алмазные отжимания",             # diamond_pushup
        ]
        ISO_SHOULD = [
            "Разведения гантелей в стороны",  # lateralraise
            "Тяга резинки к лицу",            # face_pull
        ]
        ISO_BI = [
            "Сгибания гантелей стоя",         # biceps
            "Молотки с гантелями",            # hammer
        ]
        ISO_TRI = [
            "Отжимания узкие",                # narrow_pushup
            "Разгибание гантели из-за головы",# triceps_oh
        ]
        ISO_LEGS = [
            "Ягодичный мост на одной ноге",   # hinge
            "Подъёмы на носки стоя",          # calves
        ]
        CORE_POOL = [
            "Планка",                         # plank_bw
            "Подъём ног лёжа",                # leg_raise_lying_bw
            "Скручивания",                    # crunch_bw
        ]

    # Фильтры ограничений
    if tags["elbow"]:
        ISO_TRI = [x for x in ISO_TRI if "француз" not in x.lower()
                   and "разгибани" not in x.lower()]
    if tags["knee"]:
        BASE_SQUAT = [x for x in BASE_SQUAT
                      if "выпад" not in x.lower() and "болгар" not in x.lower()]
    if tags["back"]:
        BASE_HINGE = [x for x in BASE_HINGE
                      if "румын" not in x.lower() and "становая" not in x.lower()]

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

    def pick1(pool: List[str], avoid: List[str], exclude: List[str] = None) -> str:
        """Выбрать одно упражнение из пула, избегая ключевых слов и уже выбранных."""
        ex = exclude or []
        safe = [x for x in pool
                if not any(k in x.lower() for k in avoid)
                and x not in ex]
        if not safe:
            safe = [x for x in pool if x not in ex]
        return rnd.choice(safe) if safe else (rnd.choice(pool) if pool else "—")

    def day_block(kind: str) -> List[str]:
        lines = [f"RIR: {rir}", ""]
        used: List[str] = []  # уже выбранные упражнения в этот день

        # ── ФУЛБАДИ А: ноги + горизонтальный жим/тяга ───────────────────────
        if kind == "FB-A":
            squat = pick1(BASE_SQUAT, avoid_keys, used); used.append(squat)
            hinge = pick1(BASE_HINGE, avoid_keys, used); used.append(hinge)
            hpush = pick1(BASE_HPUSH, avoid_keys, used); used.append(hpush)
            vpull = pick1(BASE_VPULL, avoid_keys, used); used.append(vpull)
            # 1 изоляция: ноги или плечи
            iso1 = pick1(ISO_LEGS if rnd.random() < 0.6 else ISO_SHOULD, avoid_keys, used)
            used.append(iso1)
            lines += ["База:",
                      f"• {fmt(squat, base_sets, reps_base)}",
                      f"• {fmt(hinge, base_sets, reps_base)}",
                      f"• {fmt(hpush, base_sets, reps_base)}",
                      f"• {fmt(vpull, base_sets, reps_base)}", "",
                      "Изоляция:",
                      f"• {fmt(iso1, iso_sets, reps_iso)}"]
            if not is_novice:
                core = pick1(CORE_POOL, avoid_keys, used)
                lines.append(f"• {fmt(core, '2', '30–60 сек')}")
            return lines

        # ── ФУЛБАДИ Б: спина + жим вверх ────────────────────────────────────
        if kind == "FB-B":
            squat = pick1(BASE_SQUAT, avoid_keys, used); used.append(squat)
            vpull = pick1(BASE_VPULL, avoid_keys, used); used.append(vpull)
            hpull = pick1(BASE_HPULL, avoid_keys, used); used.append(hpull)
            vpush = (pick1(BASE_VPUSH, avoid_keys, used)
                     if not tags["shoulder"]
                     else pick1(BASE_HPUSH, avoid_keys, used))
            used.append(vpush)
            # 1 изоляция: бицепс
            iso1 = pick1(ISO_BI, avoid_keys, used); used.append(iso1)
            lines += ["База:",
                      f"• {fmt(squat, base_sets, reps_base)}",
                      f"• {fmt(vpull, base_sets, reps_base)}",
                      f"• {fmt(hpull, base_sets, reps_base)}",
                      f"• {fmt(vpush, base_sets, reps_base)}", "",
                      "Изоляция:",
                      f"• {fmt(iso1, iso_sets, reps_iso)}"]
            if not is_novice:
                core = pick1(CORE_POOL, avoid_keys, used)
                lines.append(f"• {fmt(core, '2', '30–60 сек')}")
            return lines

        # ── ФУЛБАДИ В: грудь + задняя цепь ──────────────────────────────────
        if kind == "FB-C":
            squat = pick1(BASE_SQUAT, avoid_keys, used); used.append(squat)
            hpush = pick1(BASE_HPUSH, avoid_keys, used); used.append(hpush)
            hpull = pick1(BASE_HPULL, avoid_keys, used); used.append(hpull)
            hinge = pick1(BASE_HINGE, avoid_keys, used); used.append(hinge)
            # 1-2 изоляции: грудь + трицепс (или только трицепс)
            iso1_pool = ISO_CHEST if is_gym else ISO_TRI
            iso1 = pick1(iso1_pool, avoid_keys, used); used.append(iso1)
            lines += ["База:",
                      f"• {fmt(squat, base_sets, reps_base)}",
                      f"• {fmt(hpush, base_sets, reps_base)}",
                      f"• {fmt(hpull, base_sets, reps_base)}",
                      f"• {fmt(hinge, base_sets, reps_base)}", "",
                      "Изоляция:",
                      f"• {fmt(iso1, iso_sets, reps_iso)}"]
            if not is_novice and not tags["elbow"]:
                iso2 = pick1(ISO_TRI, avoid_keys, used)
                lines.append(f"• {fmt(iso2, iso_sets, reps_iso)}")
            return lines

        # ── ВЕРХ/НИЗ: UPPER ──────────────────────────────────────────────────
        if kind == "UPPER":
            hpush = pick1(BASE_HPUSH, avoid_keys, used); used.append(hpush)
            vpull = pick1(BASE_VPULL, avoid_keys, used); used.append(vpull)
            hpull = pick1(BASE_HPULL, avoid_keys, used); used.append(hpull)
            lines.append("База:")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            lines.append(f"• {fmt(vpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            if not tags["shoulder"]:
                vpush = pick1(BASE_VPUSH, avoid_keys, used); used.append(vpush)
                lines.append(f"• {fmt(vpush, base_sets, reps_base)}")
            lines += ["", "Изоляция:"]
            iso1 = pick1(ISO_BI, avoid_keys, used); used.append(iso1)
            lines.append(f"• {fmt(iso1, iso_sets, reps_iso)}")
            if not tags["elbow"]:
                iso2 = pick1(ISO_TRI, avoid_keys, used)
                lines.append(f"• {fmt(iso2, iso_sets, reps_iso)}")
            return lines

        # ── ВЕРХ/НИЗ: LOWER ──────────────────────────────────────────────────
        if kind == "LOWER":
            squat = pick1(BASE_SQUAT, avoid_keys, used); used.append(squat)
            hinge = pick1(BASE_HINGE, avoid_keys, used); used.append(hinge)
            lines.append("База:")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            pool2 = [x for x in BASE_SQUAT if x != squat]
            if pool2 and not tags["knee"]:
                sq2 = pick1(pool2, avoid_keys, used); used.append(sq2)
                lines.append(f"• {fmt(sq2, base_sets, reps_base)}")
            lines += ["", "Изоляция:"]
            iso1 = pick1(ISO_LEGS, avoid_keys, used); used.append(iso1)
            reps_iso1 = "15–20" if "носки" in iso1.lower() else reps_iso
            lines.append(f"• {fmt(iso1, iso_sets, reps_iso1)}")
            core = pick1(CORE_POOL, avoid_keys, used)
            lines.append(f"• {fmt(core, '2', '40–60 сек')}")
            return lines

        # ── PPL: PUSH ────────────────────────────────────────────────────────
        if kind == "PUSH":
            hpush = pick1(BASE_HPUSH, avoid_keys, used); used.append(hpush)
            lines.append("База:")
            lines.append(f"• {fmt(hpush, base_sets, reps_base)}")
            if not tags["shoulder"]:
                vpush = pick1(BASE_VPUSH, avoid_keys, used); used.append(vpush)
                lines.append(f"• {fmt(vpush, base_sets, reps_base)}")
            pool2 = [x for x in BASE_HPUSH if x != hpush]
            if pool2:
                hp2 = pick1(pool2, avoid_keys, used); used.append(hp2)
                lines.append(f"• {fmt(hp2, base_sets, reps_base)}")
            lines += ["", "Изоляция:"]
            iso_c = pick1(ISO_CHEST if is_gym else ISO_TRI, avoid_keys, used)
            used.append(iso_c)
            lines.append(f"• {fmt(iso_c, iso_sets, reps_iso)}")
            if not tags["elbow"]:
                iso_s = pick1(ISO_SHOULD, avoid_keys, used)
                lines.append(f"• {fmt(iso_s, iso_sets, reps_iso)}")
            return lines

        # ── PPL: PULL ────────────────────────────────────────────────────────
        if kind == "PULL":
            vpull = pick1(BASE_VPULL, avoid_keys, used); used.append(vpull)
            hpull = pick1(BASE_HPULL, avoid_keys, used); used.append(hpull)
            lines.append("База:")
            lines.append(f"• {fmt(vpull, base_sets, reps_base)}")
            lines.append(f"• {fmt(hpull, base_sets, reps_base)}")
            pool2 = [x for x in BASE_HPULL if x != hpull]
            if pool2:
                hp2 = pick1(pool2, avoid_keys, used); used.append(hp2)
                lines.append(f"• {fmt(hp2, base_sets, reps_base)}")
            lines += ["", "Изоляция:"]
            iso1 = pick1(ISO_BI, avoid_keys, used); used.append(iso1)
            lines.append(f"• {fmt(iso1, iso_sets, reps_iso)}")
            iso2 = pick1(ISO_SHOULD, avoid_keys, used)
            lines.append(f"• {fmt(iso2, iso_sets, reps_iso)}")
            return lines

        # ── PPL: LEGS ────────────────────────────────────────────────────────
        if kind == "LEGS":
            squat = pick1(BASE_SQUAT, avoid_keys, used); used.append(squat)
            hinge = pick1(BASE_HINGE, avoid_keys, used); used.append(hinge)
            pool2 = [x for x in BASE_SQUAT if x != squat]
            lines.append("База:")
            lines.append(f"• {fmt(squat, base_sets, reps_base)}")
            lines.append(f"• {fmt(hinge, base_sets, reps_base)}")
            if pool2 and not tags["knee"]:
                sq2 = pick1(pool2, avoid_keys, used); used.append(sq2)
                lines.append(f"• {fmt(sq2, base_sets, reps_base)}")
            lines += ["", "Изоляция:"]
            for ex in ISO_LEGS:
                if ex not in used and not (tags["knee"] and any(k in ex.lower() for k in avoid_knee)):
                    reps_ex = "15–20" if "носки" in ex.lower() else reps_iso
                    lines.append(f"• {fmt(ex, iso_sets, reps_ex)}")
                    used.append(ex)
            core = pick1(CORE_POOL, avoid_keys, used)
            lines.append(f"• {fmt(core, '2', '30–60 сек')}")
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

    # Описания систем для заголовков
    KIND_TITLES = {
        "FB-A": ("Фулбади А", "Всё тело: акцент на ноги и толчок"),
        "FB-B": ("Фулбади Б", "Всё тело: акцент на спину и тягу"),
        "FB-C": ("Фулбади В", "Всё тело: комплексный день"),
        "UPPER": ("Верх тела", "Грудь, спина, плечи, руки"),
        "LOWER": ("Низ тела", "Квадрицепс, бицепс бедра, ягодицы, икры"),
        "PUSH": ("Толчок — Грудь и Плечи", "Грудь, передние/средние дельты, трицепс"),
        "PULL": ("Тяга — Спина и Бицепс", "Широчайшие, ромбовидные, бицепс, задняя дельта"),
        "LEGS": ("Ноги", "Квадрицепс, бицепс бедра, ягодицы, икры"),
    }

    UPPER_LOWER_SCHEDULE = {
        1: "Понедельник",
        2: "Вторник",
        3: "Четверг",
        4: "Пятница",
    }

    PPL_SCHEDULE = {
        1: "Понедельник",
        2: "Вторник",
        3: "Среда",
        4: "Пятница",
        5: "Суббота",
    }

    days: Dict[str, str] = {}
    for d in range(1, f + 1):
        kind = template[d - 1]
        kind_title, kind_desc = KIND_TITLES.get(kind, (system, ""))

        if system == "Верх/Низ":
            day_sched = UPPER_LOWER_SCHEDULE.get(d, f"День {d}")
            header = (
                f"День {d} — {kind_title}\n"
                f"📅 {day_sched}\n"
                f"💡 {kind_desc}\n"
                f"⏱ ~50–70 мин\n\n"
            )
        elif system.startswith("PPL"):
            day_sched = PPL_SCHEDULE.get(d, f"День {d}")
            header = (
                f"День {d} — {kind_title}\n"
                f"📅 {day_sched}\n"
                f"💡 {kind_desc}\n"
                f"⏱ ~50–70 мин\n\n"
            )
        else:
            header = (
                f"День {d} — {kind_title}\n"
                f"💡 {kind_desc}\n"
                f"⏱ ~45–60 мин\n\n"
            )

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
# ✅ ПИТАНИЕ
# =========================

FOOD_DB = {
    # Крупы и углеводы (данные выверены по calorizator.ru — та же база, что использует FatSecret)
    "oats":      {"name": "Овсянка (сухая)",                "kcal": 366, "p": 12.5, "f": 6.2,  "c": 61.0},  # calorizator.ru: Овсяные хлопья Геркулес
    "rice":      {"name": "Рис белый (сухой)",              "kcal": 344, "p": 6.7,  "f": 0.7,  "c": 78.9},  # calorizator.ru: Рис белый (сухой)
    "buckwheat": {"name": "Гречка (сухая)",                 "kcal": 313, "p": 12.6, "f": 3.3,  "c": 62.1},  # calorizator.ru: Гречневая крупа ядрица
    "pasta":     {"name": "Макароны (сухие)",               "kcal": 337, "p": 10.4, "f": 1.1,  "c": 69.7},  # calorizator.ru: Макароны высшего сорта
    "bread_rye": {"name": "Хлеб ржаной",                   "kcal": 217, "p": 5.9,  "f": 1.1,  "c": 44.5},  # calorizator.ru: Хлеб ржаной формовой
    "potato":    {"name": "Картофель",                      "kcal": 79,  "p": 2.0,  "f": 0.1,  "c": 17.5},  # без изменений (данные точные)
    "veg":       {"name": "Овощи (огурец/помидор/капуста)", "kcal": 25,  "p": 1.2,  "f": 0.2,  "c": 4.5},   # без изменений (среднее значение)
    # Белковые продукты
    "chicken":   {"name": "Куриная грудка (варёная)",       "kcal": 157, "p": 32.1, "f": 3.2,  "c": 0.0},   # calorizator.ru: Куриная грудка варёная
    "chicken_thigh": {"name": "Куриное бедро (без кожи)",   "kcal": 202, "p": 26.0, "f": 10.9, "c": 0.0},   # без изменений
    "turkey":    {"name": "Индейка (варёная)",              "kcal": 181, "p": 28.6, "f": 7.4,  "c": 0.0},   # без изменений
    "fish":      {"name": "Рыба белая (минтай/хек)",        "kcal": 79,  "p": 17.6, "f": 1.0,  "c": 0.0},   # calorizator.ru: Минтай отварной
    "tuna_can":  {"name": "Тунец консервированный",         "kcal": 96,  "p": 21.0, "f": 1.2,  "c": 0.0},   # calorizator.ru: Тунец в собственном соку
    "eggs":      {"name": "Яйца куриные",                   "kcal": 157, "p": 12.7, "f": 11.5, "c": 0.7},   # calorizator.ru: Яйцо куриное варёное
    "curd_2":    {"name": "Творог 2–5%",                    "kcal": 103, "p": 18.0, "f": 2.0,  "c": 3.3},   # calorizator.ru: Творог 2% — без изменений
    "kefir":     {"name": "Кефир 1%",                       "kcal": 40,  "p": 2.8,  "f": 1.0,  "c": 4.0},   # calorizator.ru: Кефир 1%
    "milk":      {"name": "Молоко 2.5%",                    "kcal": 52,  "p": 2.8,  "f": 2.5,  "c": 4.7},   # calorizator.ru: Молоко 2.5%
    # Жиры
    "oil_sunfl": {"name": "Масло подсолнечное",             "kcal": 899, "p": 0.0,  "f": 99.9, "c": 0.0},   # calorizator.ru: Масло подсолнечное рафинированное
    "oil_olive": {"name": "Масло оливковое",                "kcal": 898, "p": 0.0,  "f": 99.8, "c": 0.0},   # calorizator.ru: Масло оливковое
    # Фрукты
    "banana":    {"name": "Банан",                          "kcal": 89,  "p": 1.1,  "f": 0.3,  "c": 22.8},  # calorizator.ru: Банан
    "apple":     {"name": "Яблоко",                         "kcal": 52,  "p": 0.4,  "f": 0.2,  "c": 13.8},  # calorizator.ru: Яблоко свежее
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
    """
    Подгоняет граммовки к целевым КБЖУ.
    Ключевое отличие от старой версии: scale_all_by_key() масштабирует
    ВСЕ вхождения продукта во всех приёмах пропорционально — это важно,
    потому что белок (курица, яйца и т.д.) встречается в обеде, ужине и перекусе
    одновременно, и двигать только одно вхождение недостаточно.
    """
    import copy

    present_keys: set = set()
    for meal in day_meals:
        for k, _ in meal:
            present_keys.add(k)

    protein_keys = ["chicken", "chicken_thigh", "turkey", "fish", "tuna_can", "eggs", "curd_2", "kefir", "milk"]
    carb_keys    = ["rice", "buckwheat", "oats", "pasta", "bread_rye", "potato", "banana", "apple"]
    fat_keys     = ["oil_sunfl", "oil_olive"]

    # Лимиты зависят от цели: при высоких углеводах крупы должны быть больше
    target_carbs = target.get("c", 200)
    carb_scale = max(1.0, target_carbs / 200)   # при У=475 → scale=2.375

    LIMITS: Dict[str, Tuple[float, float]] = {
        "rice":          (30, int(150 * carb_scale)), "buckwheat":    (30, int(150 * carb_scale)),
        "oats":          (30, int(120 * carb_scale)), "pasta":        (30, int(150 * carb_scale)),
        "bread_rye":     (20, int(80  * carb_scale)), "potato":       (60, int(300 * carb_scale)),
        "banana":        (60, int(150 * carb_scale)), "apple":        (60, int(150 * carb_scale)),
        "chicken":       (40, 280), "chicken_thigh": (40, 280), "turkey":      (40, 280),
        "fish":          (50, 320), "tuna_can":      (40, 240), "eggs":        (55, 330),
        "curd_2":        (80, 350), "kefir":         (80, 400), "milk":        (80, 400),
        "oil_sunfl":     (5, 35),  "oil_olive":     (5, 35),
    }

    def clamp_meals():
        for mi in range(len(day_meals)):
            for ii in range(len(day_meals[mi])):
                k, g = day_meals[mi][ii]
                lo, hi = LIMITS.get(k, (0, 9999))
                day_meals[mi][ii] = (k, max(lo, min(hi, g)))

    clamp_meals()

    def score_fn(meals_state):
        t = _totals_of_day(meals_state)
        dP = t["p"] - target["p"]
        dF = t["f"] - target["f"]
        dC = t["c"] - target["c"]
        dK = t["kcal"] - target["kcal"]
        return 3.0 * abs(dP) + 4.0 * abs(dF) + 2.0 * abs(dC) + 0.5 * abs(dK)

    def scale_all_by_key(key: str, factor: float):
        """Умножает граммовку продукта во ВСЕХ приёмах на factor (с учётом лимитов)."""
        lo, hi = LIMITS.get(key, (0, 9999))
        for mi in range(len(day_meals)):
            for ii in range(len(day_meals[mi])):
                k, g = day_meals[mi][ii]
                if k == key:
                    new_g = max(lo, min(hi, round(g * factor / 5) * 5))
                    day_meals[mi][ii] = (k, new_g)

    def add_all_by_key(key: str, delta: float):
        """Добавляет delta граммов к продукту во ВСЕХ приёмах (с учётом лимитов)."""
        lo, hi = LIMITS.get(key, (0, 9999))
        for mi in range(len(day_meals)):
            for ii in range(len(day_meals[mi])):
                k, g = day_meals[mi][ii]
                if k == key:
                    day_meals[mi][ii] = (k, max(lo, min(hi, g + delta)))

    active_proteins = [k for k in protein_keys if k in present_keys]
    active_carbs    = [k for k in carb_keys if k in present_keys]
    active_fats     = [k for k in fat_keys if k in present_keys]

    best_meals = copy.deepcopy(day_meals)
    best_score = score_fn(day_meals)

    # --- Фаза 1: подгоняем белок (3 прохода масштабирования) ---
    for _ in range(30):
        t = _totals_of_day(day_meals)
        dP = t["p"] - target["p"]
        if abs(dP) <= 3:
            break
        cur_p = t["p"]
        if cur_p > 1:
            factor = max(0.2, min(4.0, target["p"] / cur_p))
            for pk in active_proteins:
                scale_all_by_key(pk, factor)

    # --- Фаза 2: подгоняем жир ---
    for _ in range(10):
        t = _totals_of_day(day_meals)
        dF = t["f"] - target["f"]
        if abs(dF) <= 3:
            break
        if active_fats:
            delta = max(5, min(15, int(abs(dF) / 90 * 100 / 5) * 5))
            for fk in active_fats:
                add_all_by_key(fk, -delta if dF > 0 else delta)

    # --- Фаза 3: подгоняем углеводы ---
    for _ in range(30):
        t = _totals_of_day(day_meals)
        dC = t["c"] - target["c"]
        if abs(dC) <= 5:
            break
        cur_c = t["c"]
        if cur_c > 1:
            factor = max(0.2, min(4.0, target["c"] / cur_c))
            for ck in active_carbs:
                scale_all_by_key(ck, factor)

    # --- Фаза 4: повторная коррекция белка (углеводы могли добавить белок через молочку) ---
    for _ in range(20):
        t = _totals_of_day(day_meals)
        dP = t["p"] - target["p"]
        if abs(dP) <= 5:
            break
        cur_p = t["p"]
        if cur_p > 1:
            factor = max(0.2, min(4.0, target["p"] / cur_p))
            for pk in active_proteins:
                scale_all_by_key(pk, factor)

    sc = score_fn(day_meals)
    if sc < best_score:
        best_meals = copy.deepcopy(day_meals)
        best_score = sc

    # --- Фаза 5: финальные итерации доводки ---
    for _ in range(40):
        t = _totals_of_day(day_meals)
        dP = t["p"] - target["p"]
        dF = t["f"] - target["f"]
        dC = t["c"] - target["c"]
        dK = t["kcal"] - target["kcal"]

        if abs(dP) <= 5 and abs(dF) <= 4 and abs(dC) <= 10 and abs(dK) <= 40:
            break

        penalties = sorted([
            (abs(dP) / max(target["p"], 1), "p", dP),
            (abs(dF) / max(target["f"], 1), "f", dF),
            (abs(dC) / max(target["c"], 1), "c", dC),
        ], key=lambda x: -x[0])

        _, worst_macro, worst_delta = penalties[0]
        adjusted = False

        if worst_macro == "p" and active_proteins:
            cur_p = t["p"]
            if cur_p > 1:
                factor = max(0.2, min(4.0, target["p"] / cur_p))
                for pk in active_proteins:
                    scale_all_by_key(pk, factor)
                adjusted = True

        elif worst_macro == "f" and active_fats:
            delta = max(5, min(15, int(abs(worst_delta) / 90 * 100 / 5) * 5))
            for fk in active_fats:
                add_all_by_key(fk, -delta if worst_delta > 0 else delta)
            adjusted = True

        elif worst_macro == "c" and active_carbs:
            cur_c = t["c"]
            if cur_c > 1:
                factor = max(0.2, min(4.0, target["c"] / cur_c))
                for ck in active_carbs:
                    scale_all_by_key(ck, factor)
                adjusted = True

        else:
            if active_carbs and abs(dK) > 40:
                add_all_by_key(active_carbs[0], -5 if dK > 0 else 5)
                adjusted = True

        if not adjusted:
            break

        sc = score_fn(day_meals)
        if sc < best_score:
            best_meals = copy.deepcopy(day_meals)
            best_score = sc

    day_meals[:] = best_meals
    clamp_meals()

    # --- Финальная докрутка калорий по 5г ---
    cycle_keys = active_carbs + active_proteins
    idx = 0
    for _ in range(120):
        t = _totals_of_day(day_meals)
        dK = t["kcal"] - target["kcal"]
        if abs(dK) <= 30:
            break
        if not cycle_keys:
            break
        add_all_by_key(cycle_keys[idx % len(cycle_keys)], -5 if dK > 0 else 5)
        idx += 1
    clamp_meals()

    return _totals_of_day(day_meals)


def _build_day_variant(variant: int, meals: int) -> List[List[Tuple[str, float]]]:
    """
    Бюджетные шаблоны. Продукты: овсянка, рис/гречка/макароны, яйца, курица,
    творог, кефир, молоко, масло подсолнечное, хлеб, банан, яблоко, рыба, тунец.
    Порции: крупы 60–120г сухими, белок 120–200г, масло 10–15г.
    """
    meals = max(3, min(int(meals or 3), 5))

    if variant == 1:
        # Классика: овсянка + курица + рис
        day = [
            [("oats", 80.0), ("milk", 200.0), ("banana", 100.0)],
            [("rice", 100.0), ("chicken", 180.0), ("veg", 200.0), ("oil_sunfl", 10.0)],
            [("rice", 90.0), ("chicken", 160.0), ("veg", 150.0)],
        ]
        if meals >= 4:
            day.append([("curd_2", 200.0), ("apple", 120.0)])
        if meals >= 5:
            day.append([("kefir", 200.0), ("bread_rye", 40.0)])
        return day

    if variant == 2:
        # Яйца + гречка + куриное бедро
        day = [
            [("eggs", 180.0), ("bread_rye", 60.0), ("veg", 100.0)],
            [("buckwheat", 100.0), ("chicken_thigh", 180.0), ("veg", 200.0), ("oil_sunfl", 10.0)],
            [("buckwheat", 80.0), ("chicken_thigh", 150.0), ("veg", 150.0)],
        ]
        if meals >= 4:
            day.append([("curd_2", 200.0), ("banana", 100.0)])
        if meals >= 5:
            day.append([("kefir", 200.0)])
        return day

    if variant == 3:
        # Тунец + макароны + творог
        day = [
            [("oats", 80.0), ("kefir", 200.0), ("apple", 120.0)],
            [("pasta", 100.0), ("tuna_can", 180.0), ("veg", 200.0), ("oil_sunfl", 10.0)],
            [("pasta", 80.0), ("chicken", 160.0), ("veg", 150.0)],
        ]
        if meals >= 4:
            day.append([("curd_2", 200.0), ("banana", 100.0)])
        if meals >= 5:
            day.append([("milk", 200.0), ("bread_rye", 40.0)])
        return day

    if variant == 4:
        # Рыба + гречка — мало готовки
        day = [
            [("oats", 80.0), ("milk", 200.0), ("banana", 100.0)],
            [("buckwheat", 100.0), ("fish", 200.0), ("veg", 200.0), ("oil_sunfl", 10.0)],
            [("buckwheat", 80.0), ("fish", 160.0), ("veg", 150.0)],
        ]
        if meals >= 4:
            day.append([("curd_2", 200.0), ("apple", 120.0), ("bread_rye", 40.0)])
        if meals >= 5:
            day.append([("kefir", 250.0), ("bread_rye", 40.0)])
        return day

    # Вариант 5: яйца + курица + хлеб (максимально просто)
    day = [
        [("eggs", 180.0), ("bread_rye", 60.0)],
        [("rice", 100.0), ("chicken", 180.0), ("veg", 200.0), ("oil_sunfl", 10.0)],
        [("rice", 90.0), ("chicken", 150.0), ("veg", 150.0)],
    ]
    if meals >= 4:
        day.append([("curd_2", 200.0), ("banana", 100.0)])
    if meals >= 5:
        day.append([("kefir", 200.0), ("apple", 120.0)])
    return day


def build_meal_day_text(day_i: int, calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> str:
    target = {"kcal": float(calories), "p": float(protein_g), "f": float(fat_g), "c": float(carbs_g)}
    day_meals = _build_day_variant(day_i, meals)

    # --- Предварительное масштабирование шаблона ---
    # Масштабируем шаблон так чтобы суммарный белок уже был близок к цели.
    # Это даёт алгоритму _adjust_to_target гораздо лучшую стартовую позицию.
    pre_t = _totals_of_day(day_meals)
    if pre_t["p"] > 1 and protein_g > 0:
        pre_factor = max(0.3, min(3.0, float(protein_g) / pre_t["p"]))
        # Масштабируем все граммовки кроме масла и овощей
        NON_SCALE = {"veg", "oil_sunfl", "oil_olive"}
        for mi in range(len(day_meals)):
            for ii in range(len(day_meals[mi])):
                k, g = day_meals[mi][ii]
                if k not in NON_SCALE:
                    day_meals[mi][ii] = (k, max(10.0, round(g * pre_factor / 5) * 5))

    tot = _adjust_to_target(day_meals, target)

    final_k = int(round(tot["kcal"]))
    final_p = int(round(tot["p"]))
    final_f = int(round(tot["f"]))
    final_c = int(round(tot["c"]))

    dk = final_k - calories
    dp = final_p - protein_g
    df = final_f - fat_g
    dc = final_c - carbs_g

    def signed(v):
        return f"+{v}" if v >= 0 else str(v)

    meal_names = ["🌅 Завтрак", "🌞 Обед", "🌆 Ужин", "🥗 Перекус 1", "🍎 Перекус 2"]

    lines = [f"📅 Пример {day_i}  (цель: {calories} ккал | Б {protein_g}г Ж {fat_g}г У {carbs_g}г)", ""]
    for mi, m in enumerate(day_meals, start=1):
        m_filtered = [(k, g) for k, g in m if g > 0.5]
        if not m_filtered:
            continue
        mt = _sum_nutr(m_filtered)
        meal_name = meal_names[mi - 1] if mi <= len(meal_names) else f"Приём {mi}"
        lines.append(f"{meal_name}")
        lines.append(f"({int(round(mt['kcal']))} ккал | Б {int(round(mt['p']))}г Ж {int(round(mt['f']))}г У {int(round(mt['c']))}г)")
        for k, g in m_filtered:
            if k == "eggs":
                est = max(1, int(round(g / 60.0)))
                lines.append(f"• {FOOD_DB[k]['name']} — {est} шт (~{int(g)}г)")
            elif k in ("oil_sunfl", "oil_olive"):
                lines.append(f"• {FOOD_DB[k]['name']} — {int(round(g))} г (1 ст.л. ≈ 10г)")
            else:
                lines.append(f"• {FOOD_DB[k]['name']} — {int(round(g))} г")
        lines.append("")

    lines.append(f"✅ Итог дня: {final_k} ккал ({signed(dk)} от цели {calories})")
    lines.append(f"   Б: {final_p}г ({signed(dp)})  Ж: {final_f}г ({signed(df)})  У: {final_c}г ({signed(dc)})")
    lines.append("")
    lines.append("⚠️ Примечания:")
    lines.append("   • Крупы и макароны — вес СУХИМИ (до варки)")
    lines.append("   • Рис варёный ×3, гречка ×2.5 от сухого")
    lines.append("   • Мясо и рыба — вес ГОТОВЫМИ (варёными)")
    lines.append("   • Яйцо среднее ≈ 55–65 г")
    return "\n".join(lines)


def nutrition_examples_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Вариант 1 (классика)", callback_data="nutr:ex:1"),
            InlineKeyboardButton(text="Вариант 2 (яйца + гречка)", callback_data="nutr:ex:2"),
        ],
        [
            InlineKeyboardButton(text="Вариант 3 (тунец + паста)", callback_data="nutr:ex:3"),
            InlineKeyboardButton(text="Вариант 4 (рыба + гречка)", callback_data="nutr:ex:4"),
        ],
        [InlineKeyboardButton(text="Вариант 5 (яйца + рис)", callback_data="nutr:ex:5")],
        [InlineKeyboardButton(text="💡 Фишки в питании", callback_data="nutr:tips")],
        [InlineKeyboardButton(text="🛒 Моя корзина", callback_data="nutr:basket")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def nutrition_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к рациону", callback_data="nutr:back")],
        [InlineKeyboardButton(text="🛒 Моя корзина", callback_data="nutr:basket")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


def generate_nutrition_summary(goal: str, sex: str, age: int, height: int, weight: float, exp: str,
                             freq: int = 3, place: str = "свой вес", meals_pref: Optional[int] = None) -> Tuple[str, int, int, int, int, int]:
    calories = calc_calories(height, weight, age, sex, goal, freq=freq, place=place)
    p, f, c = calc_macros(calories, weight, goal)
    meals = int(meals_pref or 0) if meals_pref else suggest_meals_count(calories)
    meals = max(3, min(meals, 5))

    p_kcal = p * 4
    f_kcal = f * 9
    c_kcal = c * 4
    total_check = p_kcal + f_kcal + c_kcal

    # Распределение по приёмам (30/40/30)
    b_kcal = int(round(calories * 0.30))
    l_kcal = int(round(calories * 0.40))
    d_kcal = calories - b_kcal - l_kcal

    # Пропорциональное распределение макросов по приёмам
    def split_macro(total, pct): return int(round(total * pct))

    b_p, b_f, b_c = split_macro(p, 0.30), split_macro(f, 0.30), split_macro(c, 0.30)
    l_p, l_f, l_c = split_macro(p, 0.40), split_macro(f, 0.40), split_macro(c, 0.40)
    d_p = p - b_p - l_p
    d_f = f - b_f - l_f
    d_c = c - b_c - l_c

    summary = (
        "🍽 Питание — твой расчёт\n\n"
        f"⚡️ Калории: {calories} ккал/день\n"
        f"💪 Белок: {p} г\n"
        f"🥑 Жиры: {f} г\n"
        f"🍚 Углеводы: {c} г\n"
        f"🍽 Приёмов пищи: {meals}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌅 Завтрак — {b_kcal} ккал (30%)\n"
        f"   Б: {b_p} г  |  Ж: {b_f} г  |  У: {b_c} г\n\n"
        f"☀️ Обед — {l_kcal} ккал (40%)\n"
        f"   Б: {l_p} г  |  Ж: {l_f} г  |  У: {l_c} г\n\n"
        f"🌙 Ужин — {d_kcal} ккал (30%)\n"
        f"   Б: {d_p} г  |  Ж: {d_f} г  |  У: {d_c} г\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выбери пример рациона 👇\n"
        "(Все варианты подходят под твои цифры)"
    )
    return summary, calories, p, f, c, meals


# =========================
# МЕНЮ / START
# =========================
FAQ_QUESTIONS = {
    "faq:weight": {
        "q": "⚖️ Почему не меняется вес?",
        "a": (
            "⚖️ Вес стоит — разбираем по шагам\n\n"
            "Короткий ответ: ты ешь столько же, сколько тратишь.\n\n"
            "─── Почему это происходит ───\n\n"
            "1. Скрытые калории\n"
            "Соусы, масло, орехи, перекусы — всё считается.\n"
            "Пример: 1 ст.л. масла = 90 ккал. 2 горсти орехов = 300 ккал.\n"
            "За день «незаметных» калорий набегает 400–700.\n\n"
            "2. Неправильное взвешивание\n"
            "Норма: утром, после туалета, натощак, без одежды.\n"
            "Смотри среднее за 7 дней — не на конкретный день.\n"
            "Колебания 0.5–2 кг за день — это вода, не жир.\n\n"
            "3. Мало белка → теряешь мышцы, а не жир\n"
            "Нужно 1.6–2 г/кг. При 70 кг → 112–140 г в день.\n"
            "Источники дёшево: яйца, творог, куриное бедро, тунец.\n\n"
            "─── Что делать прямо сейчас ───\n\n"
            "• Неделю записывай ВСЁ что ешь (можно в FatSecret)\n"
            "• Взвешивайся 3–4 раза в неделю утром — смотри среднее\n"
            "• Вес стоит 2 недели → убери 150–200 ккал в день\n"
            "• Подожди ещё неделю — если нет результата, убирай ещё 100\n\n"
            "Пример: норма 2200 ккал → ешь 1950–2000, жди 14 дней."
        )
    },
    "faq:muscle": {
        "q": "💪 Почему нет роста мышц?",
        "a": (
            "💪 Мышцы не растут — ищем причину\n\n"
            "Нужно три вещи одновременно: нагрузка + еда + сон.\n\n"
            "─── 4 частые причины ───\n\n"
            "1. Нет прогрессии нагрузки\n"
            "Делаешь одни и те же веса 2–3 месяца? Тело адаптировалось.\n"
            "Что делать: раз в 1–2 недели добавляй +2.5 кг или +1 повтор.\n"
            "Пример: жим 60 кг × 8 → через 2 недели 60 кг × 9 → 62.5 кг × 7.\n\n"
            "2. Мало белка\n"
            "На массу нужно 1.8–2.2 г/кг.\n"
            "При 75 кг → 135–165 г белка в день.\n"
            "Дешево: творог 200г (35г Б) + куриное бедро 200г (54г Б) + 3 яйца (18г Б) = 107г за день.\n\n"
            "3. Мало калорий\n"
            "На дефиците мышцы не растут.\n"
            "Для набора нужен профицит +200–300 ккал/день.\n"
            "Как добавить: +1 ст.л. масла, +100г крупы, кефир к ужину.\n\n"
            "4. Мало сна\n"
            "Меньше 7 часов → гормон роста выделяется хуже.\n"
            "Попробуй 2 недели по 8 часов — разница заметна.\n\n"
            "─── Совет новичку ───\n"
            "Первые 3–6 месяцев мышцы растут быстро даже без идеального питания.\n"
            "Главное — регулярность и прогрессия."
        )
    },
    "faq:plateau": {
        "q": "📉 Почему прекратился прогресс (плато)?",
        "a": (
            "📉 Плато — как выйти\n\n"
            "Тело привыкло к нагрузке. Нормально через 6–10 недель.\n\n"
            "─── Признаки плато ───\n"
            "• 3+ недели без роста рабочего веса\n"
            "• Тренировки ощущаются лёгкими\n"
            "• Вес тела не меняется при дефиците\n\n"
            "─── Три шага выхода ───\n\n"
            "1. Смени программу\n"
            "Нажми «Составить новый план» → другие упражнения = новый стимул.\n\n"
            "2. Разгрузочная неделя\n"
            "Снизь веса на 30%, работай только на технику.\n"
            "Через неделю обычно идёт прорыв.\n\n"
            "3. Проверь питание\n"
            "Незаметно выросли калории? Неделю записывай всё.\n"
            "Часто находишь лишние 200–400 ккал.\n\n"
            "─── Примеры смены нагрузки ───\n\n"
            "Присед 80 кг × 5 стоит 4 недели?\n"
            "→ Убери до 70 кг, добавь паузу 2 сек внизу.\n"
            "→ Через 2 недели 82.5 кг пойдут легче.\n\n"
            "Жим 60 кг × 8 стоит?\n"
            "→ Попробуй жим гантелей 25 кг — другая амплитуда.\n\n"
            "Плато 2–3 недели — норма.\n"
            "Больше месяца — меняй подход кардинально."
        )
    },
    "faq:diet": {
        "q": "🍽 Как правильно питаться для результата?",
        "a": (
            "🍽 Питание — что реально работает\n\n"
            "Два правила дают 90% результата:\n"
            "1. Попадай в калории.\n"
            "2. Ешь достаточно белка.\n\n"
            "─── Калории ───\n\n"
            "Похудение: дефицит 300–400 ккал от нормы.\n"
            "Набор: профицит 200–300 ккал.\n"
            "Норма — твои цифры в разделе «Питание».\n\n"
            "─── Белок ───\n\n"
            "1.8–2 г/кг. При 70 кг → 126–140 г в день.\n"
            "Дёшево и просто:\n"
            "• Яйца 3 шт. утром = 18 г Б\n"
            "• Куриное бедро 200 г обед = 54 г Б\n"
            "• Творог 200 г вечер = 35 г Б\n"
            "Итого: ~107 г Б без особых усилий.\n\n"
            "─── Что не надо делать ───\n\n"
            "• Убирать жиры совсем — нужно минимум 0.7 г/кг.\n"
            "  (Меньше жиров → падают гормоны.)\n"
            "• Убирать углеводы — они дают энергию для тренировок.\n"
            "• Есть «чисто» 100% времени — главное стабильность.\n\n"
            "─── Первый шаг прямо сейчас ───\n\n"
            "Добавь белковый продукт к каждому приёму пищи.\n"
            "Это одно изменение уже даёт результат за 3–4 недели.\n\n"
            "Открой «Питание» → там твои персональные КБЖУ и 5 готовых примеров."
        )
    },
    "faq:motivation": {
        "q": "😔 Как не бросить и сохранить мотивацию?",
        "a": (
            "😔 Мотивация пропала — что делать\n\n"
            "Мотивацию ждать бесполезно. Делай, когда не хочется.\n"
            "Привычка строится не на желании, а на повторении.\n\n"
            "─── Три шага ───\n\n"
            "1. Снизь требования к себе\n"
            "Не можешь час — иди на 30 минут.\n"
            "Не можешь 3 раза в неделю — ходи 2.\n"
            "Главное — не прерывать цепочку полностью.\n\n"
            "2. Записывай результаты\n"
            "Дневник тренировок: рост в цифрах виден через 3–4 недели.\n"
            "Как записывать: упражнение → кг × повторы.\n"
            "Пример: «Жим: 60 кг × 8 (прошлый раз: 60 × 7) ✅»\n"
            "Фото тела раз в 4 недели: весы врут, фото — нет.\n\n"
            "3. Пропустил тренировку?\n"
            "Ничего страшного. Просто иди на следующую.\n"
            "Одна-две пропущенных тренировки не ломают результат.\n\n"
            "─── Реальные сроки ───\n\n"
            "Видимые изменения: 8–12 недель при регулярной работе.\n"
            "Силовой рост: уже через 3–4 недели веса начнут расти.\n"
            "Не месяц, но и не годы."
        )
    },
    "faq:sleep": {
        "q": "😴 Почему важен сон и восстановление?",
        "a": (
            "😴 Сон — самый дешёвый инструмент роста\n\n"
            "Мышцы растут не во время тренировки, а после — во сне.\n"
            "Тренировка = стимул. Сон = когда идёт рост.\n\n"
            "─── Факты ───\n\n"
            "• 6 часов: мышечное восстановление замедляется, кортизол растёт\n"
            "• 7–9 часов: оптимум для прогресса\n"
            "• Хронический недосып: кортизол высокий → жир копится, мышцы уходят\n\n"
            "── Признаки плохого восстановления ──\n\n"
            "• Веса стоят несколько недель\n"
            "• Быстро устаёшь на тренировке\n"
            "• Раздражительность, плохое настроение утром\n"
            "• Частые простуды\n\n"
            "─── Что сделать прямо сейчас ───\n\n"
            "1. Ложись в одно и то же время (±30 мин)\n"
            "2. Убери телефон за 30 мин до сна\n"
            "3. Прохладная комната (18–20°C): температура тела падает → засыпание быстрее\n"
            "4. Если тренируешься 4–5 раз в неделю: 1 полный день отдыха обязателен\n\n"
            "Попробуй 2 недели с 8 часами сна —\n"
            "почти всегда веса начинают расти снова."
        )
    },
    "faq:technique": {
        "q": "🏋️ Почему так важна техника?",
        "a": (
            "🏋️ Техника — зачем тратить на неё время\n\n"
            "Плохая техника = либо травма, либо нагрузка идёт не туда.\n\n"
            "─── Что происходит без техники ───\n\n"
            "• Присед с круглой спиной → нагрузка на поясницу, не ноги\n"
            "• Тяга блока «руками» → работает бицепс, а не спина\n"
            "• Жим с широко разведёнными локтями → болят плечи\n\n"
            "─── Как освоить технику быстро ───\n\n"
            "1. Первые 2–4 недели — работай с 50–60% от максимального веса\n"
            "2. Нажимай «Техника» под каждым упражнением в боте\n"
            "3. Снимай себя на видео — ошибки сразу видны сбоку\n"
            "4. Освоил технику → добавляй +2.5 кг каждые 1–2 недели\n\n"
            "─── Как понять, что техника ок ───\n\n"
            "• Чувствуешь ту мышцу, которую тренируешь\n"
            "• Нет боли в суставах после тренировки\n"
            "• Веса растут при той же технике\n\n"
            "─── Правило новичка ───\n\n"
            "Чисто с лёгким весом > криво с тяжёлым.\n"
            "Хорошая техника сейчас = годы без травм и стабильный прогресс."
        )
    },
    "faq:cardio": {
        "q": "🏃 Нужно ли кардио?",
        "a": (
            "🏃 Кардио — нужно или нет\n\n"
            "Не обязательно, но полезно. Зависит от цели.\n\n"
            "─── Для похудения ───\n\n"
            "Помогает создать дефицит без урезания еды.\n"
            "Формат: 2–3 раза в неделю по 25–35 мин в отдельные дни.\n"
            "Что выбрать: ходьба быстрым шагом, велосипед, эллипс.\n"
            "ЧСС: 60–70% от максимума (220 минус возраст).\n"
            "Пример для 30 лет: (220-30) × 0.65 = 124 уд/мин.\n\n"
            "─── Для набора массы ───\n\n"
            "Много кардио = тело жжёт мышцы.\n"
            "Максимум: 1–2 раза по 20 мин, только для здоровья сердца.\n\n"
            "─── Для новичка ───\n\n"
            "1. Первые 2–3 месяца — только силовые.\n"
            "   Привыкни к нагрузке, освой технику.\n"
            "2. Потом добавляй кардио по желанию.\n\n"
            "─ Кардио ускоряет восстановление ─\n\n"
            "Прогулка 20–30 мин на следующий день после тяжёлой тренировки\n"
            "улучшает кровоток и снижает боли в мышцах."
        )
    },
}


def faq_kb():
    rows = [
        [
            InlineKeyboardButton(text="⚖️ Вес не меняется", callback_data="faq:weight"),
            InlineKeyboardButton(text="💪 Мышцы не растут", callback_data="faq:muscle"),
        ],
        [
            InlineKeyboardButton(text="📉 Прогресс встал", callback_data="faq:plateau"),
            InlineKeyboardButton(text="🍽 Как питаться", callback_data="faq:diet"),
        ],
        [
            InlineKeyboardButton(text="😔 Нет мотивации", callback_data="faq:motivation"),
            InlineKeyboardButton(text="😴 Сон и отдых", callback_data="faq:sleep"),
        ],
        [
            InlineKeyboardButton(text="🏋️ Зачем техника", callback_data="faq:technique"),
            InlineKeyboardButton(text="🏃 Нужно ли кардио", callback_data="faq:cardio"),
        ],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faq_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Все вопросы", callback_data="nav:faq")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])


async def open_faq(user_id: int, chat_id: int, bot: Bot, callback: Optional[CallbackQuery] = None):
    text = (
        "❓ Частые вопросы\n\n"
        "Разбираем причины, по которым нет результата.\n"
        "Выбери тему — дам конкретный ответ без воды."
    )
    if callback:
        await clean_edit(callback, user_id, text, reply_markup=faq_kb())
    else:
        await _send_with_image(bot, chat_id, user_id, text, "faq", reply_markup=faq_kb())


async def cb_faq_question(callback: CallbackQuery, bot: Bot):
    key = callback.data  # faq:weight, faq:muscle etc
    item = FAQ_QUESTIONS.get(key)
    if not item:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    await clean_edit(callback, callback.from_user.id, item["a"], reply_markup=faq_back_kb())
    await callback.answer()


async def show_main_menu(bot: Bot, chat_id: int, user_id: int):
    u = await get_user(user_id)
    sub = await get_subscription(user_id)
    tariff_line = format_tariff_line(sub)

    freq = int(u.get("freq") or 3)
    goal_key = u.get("goal") or ""
    goal_label = GOAL_DISPLAY.get(goal_key, goal_key or "не указана")

    workouts_done = await get_workouts_done_last_7_days(user_id)

    current_weight = await get_current_weight(user_id)
    weight_line = f"{current_weight} кг" if current_weight else "не записан"

    # Квадратики по числу дней тренировок
    def squares(done: int, total: int) -> str:
        done = min(done, total)
        return "■" * done + "□" * (total - done)

    w_done_str = squares(workouts_done, freq)
    w_check = " ✅" if workouts_done >= freq else ""

    w_pct = min(int(round(workouts_done / max(freq, 1) * 100)), 100)

    text = (
        f"🎯 Цель: {goal_label}\n\n"
        f"📍 Текущая позиция:\n\n"
        f"Вес: {weight_line}\n"
        f"Тренировки: {workouts_done}/{freq}  {w_done_str}{w_check}\n\n"
        f"{tariff_line}\n\n"
        f"Неделя тренировок закрыта на {w_pct}%."
    )
    await _send_with_image(bot, chat_id, user_id, text, "menu", reply_markup=menu_main_inline_kb())


def welcome_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заполнить профиль", callback_data="p:start_wizard")],
    ])


async def cmd_start(message: Message, bot: Bot):
    uid = message.from_user.id
    await ensure_user(uid, message.from_user.username or "")
    await try_delete_user_message(bot, message)

    # Проверяем: профиль заполнен, но подписки нет
    u = await get_user(uid)
    profile_complete = bool(
        u.get("goal") and u.get("sex") and u.get("height")
        and u.get("weight") and u.get("freq")
    )
    subscription_active = await is_access_active(uid)

    if profile_complete and not subscription_active:
        await bot.send_message(
            chat_id=message.chat.id,
            text="✅ Я на месте. Кнопки снизу 👇",
            reply_markup=control_reply_kb()
        )
        spec_text = (
            "👋 Ты уже заполнил профиль — осталось открыть доступ.\n\n"
            "Что умеет бот:\n\n"
            "✅ составит программу под твою цель\n"
            "✅ покажет технику упражнений\n"
            "✅ поможет отслеживать прогресс\n\n"
            "Чтобы продолжить, выбери доступ 👇"
        )
        await _send_with_image(
            bot, message.chat.id, uid,
            spec_text, "restart", reply_markup=build_program_tariff_kb()
        )
        return

    await bot.send_message(
        chat_id=message.chat.id,
        text="✅ Я на месте. Кнопки снизу 👇",
        reply_markup=control_reply_kb()
    )

    welcome_text = (
        "<b>👋 Привет! Я твой персональный тренер.</b>\n\n"
        "Что умею:\n\n"
        "— Составляю программу под твою цель, уровень и место (зал / дома)\n"
        "— Считаю КБЖУ индивидуально — с учётом веса, цели и активности\n"
        "— Даю готовый план питания\n"
        "— Веду дневник тренировок: веса, повторения, история\n"
        "— Показываю технику с картинкой — кнопка под каждым упражнением\n"
        "— Отвечаю на частые вопросы: плато, питание, мотивация\n\n"
        "Как начать:\n\n"
        "1. Жми на «📋 Заполнить профиль» и пройди короткий опрос\n"
        "2. Выбери тариф — получи готовый план под свою цель\n\n"
        "Поехали!"
    )

    await _send_with_image(
        bot, message.chat.id, uid,
        welcome_text, "welcome", reply_markup=welcome_kb()
    )


async def open_upgrade(user_id: int, chat_id: int, bot: Bot, callback: Optional[CallbackQuery] = None, source: str = ""):
    text = (
        "💳 <b>Тарифы — выбери свой уровень</b>\n\n"
        "Программа тренировок, техника выполнения, план питания и дневник — всё в одном боте. "
        "Бот подстраивается под твой уровень, цель и место тренировок.\n\n"

        f"🔵 <b>1 месяц — {TARIFFS['t1']['price']}₽</b>\n\n"
        "Попробуй и почувствуй разницу:\n\n"
        "• Персональный план тренировок (зал или дома)\n"
        "• Техника каждого упражнения — видео/картинка прямо в тренировке\n"
        "• Дневник тренировок: веса, повторы, история по дням\n"
        "• Замеры тела и отслеживание прогресса\n"
        "• Обновление программы: 3 раза\n"
        "• Поддержка и FAQ\n\n"

        f"🟡 <b>3 месяца — {TARIFFS['t3']['price']}₽</b> ⭐ Рекомендуем\n\n"
        "Именно 3 месяца нужны, чтобы увидеть реальный результат:\n\n"
        "• Всё из тарифа «1 месяц»\n"
        "• <b>Питание: расчёт КБЖУ + готовый рацион на каждый день</b>\n"
        "• Обновление программы: 10 раз\n"
        "• Выгоднее, чем 3 раза по «1 месяцу»\n\n"

        f"🟢 <b>Навсегда — {TARIFFS['life']['price']}₽</b>\n\n"
        "Один раз — пользуйся сколько угодно:\n\n"
        "• Полный доступ ко всем функциям без ограничений\n"
        "• Тренировки + питание + дневник + замеры + техники\n"
        "• Обновление программы: безлимит\n"
        "• Никаких повторных списаний\n\n"

        "⚠️ <i>Питание (расчёт КБЖУ и готовый рацион) доступно только на тарифах «3 месяца» и «Навсегда».</i>\n\n"
        "👇 Выбери тариф и начни прямо сейчас:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔵 1 месяц — {TARIFFS['t1']['price']}₽", callback_data="tariff:t1")],
        [InlineKeyboardButton(text=f"🟡 3 месяца — {TARIFFS['t3']['price']}₽ ⭐", callback_data="tariff:t3")],
        [InlineKeyboardButton(text=f"🟢 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
        [InlineKeyboardButton(
            text="⬅️ Назад" if source == "after_profile" else "🏠 Меню",
            callback_data="nav:back_to_program_tariff" if source == "after_profile" else "nav:menu"
        )],
    ])

    if callback:
        await clean_edit(callback, user_id, text, reply_markup=kb)
    else:
        await _send_with_image(bot, chat_id, user_id, text, "upgrade", reply_markup=kb)


# =========================
# PAYWALL
# =========================
PAYWALL_SECTIONS = {"workouts", "nutrition", "diary", "measures"}


async def show_paywall(callback, back_to: str = "nav:menu") -> None:
    """Показывает экран оплаты вместо заблокированного раздела."""
    uid = callback.from_user.id
    text = (
        "🔒 <b>Раздел доступен после оплаты.</b>\n\n"
        "Выбери тариф, чтобы получить доступ к тренировкам, питанию, дневнику и замерам."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Перейти к оплате", callback_data="nav:upgrade")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_to)],
    ])
    await clean_edit(callback, uid, text, reply_markup=kb)
    await callback.answer()


# =========================
# Навигация
# =========================
async def cb_nav(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await ensure_user(callback.from_user.id, callback.from_user.username or "")
    await state.clear()

    key = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    chat_id = callback.message.chat.id

    # ── Пэйволл: закрытые разделы требуют активной подписки ────────────────────
    if key in PAYWALL_SECTIONS:
        sub = await get_subscription(uid)
        if not is_subscription_active(sub):
            await show_paywall(callback, back_to="nav:menu")
            return

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
    elif key == "faq":
        await open_faq(user_id=uid, chat_id=chat_id, bot=bot, callback=callback)
    elif key == "upgrade":
        await open_upgrade(user_id=uid, chat_id=chat_id, bot=bot, callback=callback)
    elif key == "upgrade_after_profile":
        await open_upgrade(user_id=uid, chat_id=chat_id, bot=bot, callback=callback, source="after_profile")
    elif key in ("back_to_program_tariff", "back_to_profile_done"):
        # Возврат на экран «Профиль готов!» из тарифов или пробного
        await _show_profile_done_screen(callback, uid)
    else:
        await show_main_menu(bot, chat_id, uid)

    await callback.answer()


# =========================
# Панель управления (ReplyKeyboard)
# =========================
async def open_payment_from_reply(message: Message, state: FSMContext, bot: Bot):
    await ensure_user(message.from_user.id, message.from_user.username or "")
    await state.clear()

    uid = message.from_user.id
    a = await get_access(uid)

    if await is_access_active(uid):
        tariff = a.get("tariff", "")
        if tariff in FULL_ACCESS_TARIFFS:
            text = f"У тебя полный доступ.\n{access_status_str(a)}"
            await clean_send(bot, message.chat.id, uid, text)
        else:
            # Базовый тариф (t1) — показываем линейку тарифов
            await open_upgrade(uid, message.chat.id, bot)
    else:
        # Нет доступа — сразу показываем линейку тарифов
        await open_upgrade(uid, message.chat.id, bot)

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
        await _send_with_image(bot, message.chat.id, message.from_user.id, _profile_summary_text(u), "profile", reply_markup=profile_ready_kb())
        return

    await state.set_state(ProfileWizard.goal)
    text = _profile_header(1) + "Настроим профиль.\n\n🎯 Цель?"
    await _send_with_image(bot, message.chat.id, message.from_user.id, text, "profile", reply_markup=kb_goal())


async def cb_profile_edit(callback: CallbackQuery, state: FSMContext):
    """Показываем меню выбора — что именно менять в профиле."""
    await state.clear()
    uid = callback.from_user.id

    # ── Пэйволл: редактирование профиля только с активной подпиской
    sub = await get_subscription(uid)
    if not is_subscription_active(sub):
        await show_paywall(callback, back_to="nav:menu")
        return

    u = await get_user(uid)
    regens_left, is_unlimited = await get_plan_regens(uid)

    # Если лимит исчерпан — показываем заглушку с предложением апгрейда
    if not is_unlimited and regens_left is not None and int(regens_left) <= 0:
        a = await get_access(uid)
        tariff_name = TARIFFS.get(a.get("tariff", ""), {}).get("title", "текущий")
        await clean_edit(callback, uid,
            f"⚠️ Лимит смены плана исчерпан.\n\n"
            f"Твой тариф: {tariff_name}\n"
            "Чтобы менять план чаще — обнови подписку 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Тарифы", callback_data="nav:upgrade")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:menu")],
            ])
        )
        await callback.answer()
        return

    if is_unlimited:
        regens_str = "безлимит"
    elif regens_left is not None:
        regens_str = f"осталось: {regens_left}"
    else:
        regens_str = ""

    text = (
        "Редактирование профиля\n\n"
        "Выбери параметр — введи новое значение.\n"
        "Когда всё готово — нажми «Составить новый план»."
    )
    if regens_str:
        text += f"\n\n🔄 Смен плана: {regens_str}"
    await clean_edit(callback, uid, text, reply_markup=profile_edit_field_kb(u, regens_str))
    await callback.answer()


async def _do_rebuild_plan(callback: CallbackQuery, bot: Bot):
    """Внутренняя функция: пересобирает план тренировок. Вызывается после редактирования профиля."""
    uid = callback.from_user.id

    if not await is_access_active(uid):
        await callback.answer("🔒 Нужен активный тариф.", show_alert=True)
        return

    if not await ensure_profile_ready(uid):
        await clean_edit(callback, uid, "⚠️ Сначала заполни профиль полностью.")
        await callback.answer()
        return

    regens_left, is_unlimited = await get_plan_regens(uid)
    if not is_unlimited and regens_left is not None and int(regens_left) <= 0:
        a = await get_access(uid)
        tariff_name = TARIFFS.get(a.get("tariff", ""), {}).get("title", "текущий")
        await clean_edit(callback, uid,
            f"⚠️ Лимит обновлений плана исчерпан.\n\nТариф: {tariff_name}\nЧтобы обновлять план чаще — перейди в «Оплата».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплата", callback_data="nav:upgrade")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:menu")],
            ])
        )
        await callback.answer()
        return

    await callback.answer("🔄 Составляю новую программу…")

    import random as _rnd
    shift = _rnd.randint(1, 9999)
    varied_id = uid + shift * 100

    u = await get_user(uid)
    intro, plan_struct = generate_workout_plan(
        u["goal"], u["place"], u["exp"], int(u["freq"] or 3),
        limits=u.get("limits") or "",
        user_id=varied_id
    )
    await save_workout_plan(uid, intro, dumps_plan(plan_struct))

    if not is_unlimited:
        await decrement_plan_regens(uid)

    async with db() as conn:
        await conn.execute("DELETE FROM workout_day_progress WHERE user_id=?", (uid,))
        await conn.commit()

    regens_after, is_unlim_after = await get_plan_regens(uid)
    if is_unlim_after:
        regens_str = "Безлимит"
    elif regens_after is not None:
        regens_str = f"Осталось обновлений: {regens_after}"
    else:
        regens_str = ""

    full_access = await is_full_access_active(uid)
    kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3), has_full_access=full_access, plan_struct=plan_struct)
    suffix = f"\n\n{regens_str}" if regens_str else ""
    await clean_edit(callback, uid,
        intro + "\n\n✅ Профиль обновлён ✅ Программа пересобрана!" + suffix,
        reply_markup=kb
    )


async def cb_rebuild_plan(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Кнопка «Сменить программу» — ведёт в редактирование профиля.
    Пересборка плана произойдёт после нажатия «Составить новый план» в профиле."""
    uid = callback.from_user.id

    # ── Пэйволл: смена программы только с активной подпиской
    sub = await get_subscription(uid)
    if not is_subscription_active(sub):
        await show_paywall(callback, back_to="nav:menu")
        return

    # Перенаправляем прямо в редактирование профиля
    await cb_profile_edit(callback, state)


async def cb_do_rebuild(callback: CallbackQuery, bot: Bot):
    """Кнопка «Составить новый план» в профиле — пересборка плана с текущими данными."""
    await _do_rebuild_plan(callback, bot)


async def cb_profile_start_wizard(callback: CallbackQuery, state: FSMContext):
    """Запуск профиль-мастера из приветственного сообщения."""
    await ensure_user(callback.from_user.id, callback.from_user.username or "")
    await state.clear()

    u = await get_user(callback.from_user.id)
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
    text = "Поддержка\n\nНапиши проблему — одним сообщением.\nМожно приложить скриншот."
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
async def _show_profile_done_screen(callback: CallbackQuery, uid: int) -> None:
    """Единый рендер экрана 'Профиль готов!' с выбором тарифа."""
    text = (
        "🚀 Профиль готов!\n\n"
        "Теперь выбери подходящий тариф и начни тренировки 👇"
    )
    await clean_edit(callback, uid, text, reply_markup=build_program_tariff_kb())


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

    await _show_profile_done_screen(callback, uid)
    await callback.answer()


async def cb_profile_field_edit(callback: CallbackQuery, state: FSMContext):
    """Обрабатываем нажатие на конкретное поле профиля для изменения."""
    field = callback.data.split("pf:", 1)[1]
    uid = callback.from_user.id
    u = await get_user(uid)

    await state.update_data(editing_field=field)

    if field == "goal":
        await state.set_state(ProfileWizard.goal)
        text = "Выбери новую цель:"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💪 Масса", callback_data="p:goal:mass"),
             InlineKeyboardButton(text="🔥 Сушка", callback_data="p:goal:cut")],
            [InlineKeyboardButton(text="🏋️ Сила", callback_data="p:goal:strength"),
             InlineKeyboardButton(text="🏃 Выносливость", callback_data="p:goal:endurance")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")],
        ])
        await clean_edit(callback, uid, text, reply_markup=kb)
    elif field == "sex":
        await state.set_state(ProfileWizard.sex)
        text = "Выбери пол:"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👨 Мужчина", callback_data="p:sex:m"),
             InlineKeyboardButton(text="👩 Женщина", callback_data="p:sex:f")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")],
        ])
        await clean_edit(callback, uid, text, reply_markup=kb)
    elif field == "age":
        await state.set_state(ProfileFieldEdit.age)
        text = f"Текущий возраст: {u.get('age')}\nВведи новый:"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))
    elif field == "height":
        await state.set_state(ProfileFieldEdit.height)
        text = f"Текущий рост: {u.get('height')} см\nВведи новый:"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))
    elif field == "weight":
        await state.set_state(ProfileFieldEdit.weight)
        text = f"Текущий вес: {u.get('weight')} кг\nВведи новый:"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))
    elif field == "place":
        await state.set_state(ProfileWizard.place)
        text = "Где тренируешься?"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Дома", callback_data="p:place:bodyweight"),
             InlineKeyboardButton(text="🏋️ В зале", callback_data="p:place:gym")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")],
        ])
        await clean_edit(callback, uid, text, reply_markup=kb)
    elif field == "exp":
        await state.set_state(ProfileWizard.exp)
        text = "Опыт тренировок?"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="0 (новичок)", callback_data="p:exp:0")],
            [InlineKeyboardButton(text="1–2 года", callback_data="p:exp:mid"),
             InlineKeyboardButton(text="2+ года", callback_data="p:exp:adv")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")],
        ])
        await clean_edit(callback, uid, text, reply_markup=kb)
    elif field == "freq":
        await state.set_state(ProfileWizard.freq)
        text = "Сколько тренировок в неделю?"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="3×/нед", callback_data="p:freq:3"),
             InlineKeyboardButton(text="4×/нед", callback_data="p:freq:4")],
            [InlineKeyboardButton(text="5×/нед", callback_data="p:freq:5")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")],
        ])
        await clean_edit(callback, uid, text, reply_markup=kb)
    elif field == "meals":
        await state.set_state(ProfileWizard.meals)
        text = "Сколько раз в день удобно есть?"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="3 раза", callback_data="p:meals:3"),
             InlineKeyboardButton(text="4 раза", callback_data="p:meals:4")],
            [InlineKeyboardButton(text="5 раз", callback_data="p:meals:5")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")],
        ])
        await clean_edit(callback, uid, text, reply_markup=kb)
    elif field == "limits":
        await state.set_state(ProfileFieldEdit.limits)
        text = f"Текущие ограничения: {u.get('limits') or 'нет'}\nВведи новые (или «нет»):"
        await clean_edit(callback, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="p:edit")]
        ]))
    await callback.answer()


async def _finish_field_edit(bot: Bot, chat_id: int, user_id: int):
    """После изменения одного поля — показываем профиль без пересборки плана.
    Пересборка происходит только при нажатии 'Составить новый план'."""
    u = await get_user(user_id)
    regens_left, is_unlimited = await get_plan_regens(user_id)
    if is_unlimited:
        regens_str = "безлимит"
    elif regens_left is not None:
        regens_str = f"осталось: {regens_left}"
    else:
        regens_str = ""
    text = _profile_summary_text(u) + "\n\nПараметр сохранён.\nНажми «Составить новый план», чтобы обновить программу."
    if regens_str:
        text += f"\n🔄 Смен плана: {regens_str}"
    await clean_send(bot, chat_id, user_id, text, reply_markup=profile_edit_field_kb(u, regens_str))


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
        text = _profile_header(9) + "📅 Сколько тренировок в неделю?"
        await clean_edit(callback, uid, text, reply_markup=kb_freq())
    elif step == "meals":
        await state.set_state(ProfileWizard.meals)
        text = _profile_header(10) + "🍽 Сколько раз в день удобно есть?"
        await clean_edit(callback, uid, text, reply_markup=kb_meals())
    elif step == "limits":
        await state.set_state(ProfileWizard.limits)
        text = _profile_header(11) + "⛔️ Ограничения/травмы? (или «нет»):"
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

        # Новичок — freq уже 3, пропускаем выбор частоты, идём на meals
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
    text = _profile_header(10) + "🍽 Сколько раз в день удобно есть?"
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
    text = _profile_header(11) + "⛔️ Ограничения/травмы? (или «нет»):"
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
# ✅ ОПЛАТА ЧЕРЕЗ ЮКАССА REST API
# =========================
def access_status_str(a: dict) -> str:
    if not a or a.get("paid") != 1:
        return "Статус: ❌ доступа нет"
    if a.get("tariff") == "life":
        return "Статус: ✅ доступ навсегда"
    exp = a.get("expires_at")
    return f"Статус: ✅ до {exp[:10]}" if exp else "Статус: ✅ активен"


async def yukassa_create_payment(tariff_code: str, user_id: int):
    """
    Создаём платёж через ЮКасса REST API.
    Возвращает (data_dict, error_str). При успехе error_str == None.
    """
    import uuid
    import base64
    import aiohttp

    t = TARIFFS[tariff_code]
    amount_str = f"{t['price']:.2f}"
    idempotence_key = str(uuid.uuid4())

    credentials = base64.b64encode(
        f"{YUKASSA_SHOP_ID}:{YUKASSA_SECRET}".encode()
    ).decode()

    return_url = BOT_PUBLIC_URL if BOT_PUBLIC_URL else "https://t.me/"

    payload = {
        "amount": {"value": amount_str, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": f"Tarif {tariff_code} user_id={user_id}",
        "metadata": {"tariff": tariff_code, "user_id": str(user_id)},
    }

    try:
        logger.info(f"YooKassa: creating payment user={user_id} tariff={tariff_code} amount={amount_str}")
        logger.info(f"YooKassa: shop_id={YUKASSA_SHOP_ID[:4]}**** return_url={return_url}")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.yookassa.ru/v3/payments",
                json=payload,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Idempotence-Key": idempotence_key,
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    logger.info(f"YooKassa: payment created id={data.get('id')}")
                    return data, None
                else:
                    err_code = data.get("code", "?")
                    err_desc = data.get("description", "?")
                    err_msg = f"HTTP {resp.status} | code={err_code} | {err_desc}"
                    logger.error(f"YooKassa API error: {err_msg} | full={data}")
                    return None, err_msg
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        logger.error(f"YooKassa request failed: {err_msg}")
        return None, err_msg


async def yukassa_get_payment(payment_id: str) -> Optional[dict]:
    """Получаем статус платежа из ЮКасса."""
    import base64
    import aiohttp

    credentials = base64.b64encode(
        f"{YUKASSA_SHOP_ID}:{YUKASSA_SECRET}".encode()
    ).decode()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.yookassa.ru/v3/payments/{payment_id}",
                headers={"Authorization": f"Basic {credentials}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
    except Exception as e:
        logger.error(f"YooKassa get_payment failed: {e}")
        return None


async def save_yukassa_payment_id(payment_db_id: int, yukassa_id: str):
    """Сохраняем ЮКасса payment_id в колонку receipt_file_id."""
    async with db() as conn:
        await conn.execute(
            "UPDATE payments SET receipt_file_id=? WHERE id=?",
            (yukassa_id, payment_db_id)
        )
        await conn.commit()


async def cb_tariff(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Создаём платёж через ЮКасса REST API и отправляем кнопку со ссылкой.
    Пользователь оплачивает на странице ЮКасса, бот проверяет статус.
    """
    parts = callback.data.split(":")
    tariff_code = parts[1]
    # from_profile=True если пришли с экрана "Профиль готов!" — кнопка Назад ведёт туда же
    from_profile = len(parts) > 2 and parts[2] == "from_profile"
    back_cb = "nav:back_to_profile_done" if from_profile else "nav:upgrade"
    back_label = "⬅️ Назад" if from_profile else "⬅️ Назад к тарифам"

    if tariff_code not in TARIFFS:
        await callback.answer("Не понял тариф 😅", show_alert=True)
        return

    t = TARIFFS[tariff_code]
    uid = callback.from_user.id

    if not YUKASSA_SHOP_ID or not YUKASSA_SECRET:
        await callback.message.answer(
            "⚠️ Оплата временно недоступна.\n"
            "Свяжитесь с поддержкой."
        )
        await callback.answer()
        return

    # Показываем сообщение "создаём платёж"
    await callback.answer("⏳ Создаю ссылку на оплату…")

    # Создаём платёж в ЮКасса
    yk_data, yk_err = await yukassa_create_payment(tariff_code, uid)

    if not yk_data:
        # Показываем детали ошибки чтобы можно было диагностировать
        err_detail = f"\n\n🔍 Детали: {yk_err}" if yk_err else ""
        # Уведомляем админа об ошибке
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ Ошибка создания платежа\nuser={uid} tariff={tariff_code}\n{yk_err}"
                )
            except Exception:
                pass
        await clean_edit(callback, uid,
            f"❌ Не удалось создать счёт на оплату.{err_detail}\n\n"
            "Напиши в поддержку или попробуй позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=back_label, callback_data=back_cb)]
            ])
        )
        return

    yk_payment_id = yk_data.get("id", "")
    confirmation_url = (yk_data.get("confirmation") or {}).get("confirmation_url", "")

    if not confirmation_url:
        await clean_edit(callback, uid,
            "❌ Не получил ссылку на оплату.\nПопробуй позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=back_label, callback_data=back_cb)]
            ])
        )
        return

    # Записываем в БД для последующей проверки
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        cur = await conn.execute("""
            INSERT INTO payments (user_id, tariff, amount, last4, code, status, receipt_file_id, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        """, (uid, tariff_code, t["price"], "yukassa", yk_payment_id, yk_payment_id, now))
        await conn.commit()
        payment_db_id = cur.lastrowid

    # Текст сообщения
    desc = (
        "Тренировки + питание + дневник + замеры"
        if tariff_code in FULL_ACCESS_TARIFFS
        else "Тренировки + дневник + замеры"
    )
    days_str = f"{t['days']} дн." if t["days"] else "без ограничений"

    text = (
        f"Оплата: {t['title']}\n\n"
        f"Сумма: {t['price']}₽\n"
        f"Включает: {desc}\n"
        f"Срок: {days_str}\n\n"
        "Нажми кнопку ниже — откроется страница оплаты.\n"
        "После оплаты вернись в бот: доступ активируется автоматически."
    )

    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Перейти к оплате", url=confirmation_url)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_pay:{yk_payment_id}:{tariff_code}")],
        [InlineKeyboardButton(text=back_label, callback_data=back_cb)],
    ])

    await clean_edit(callback, uid, text, reply_markup=pay_kb)


async def cb_check_payment(callback: CallbackQuery, bot: Bot):
    """
    Пользователь нажал «Я оплатил» — проверяем статус в ЮКасса API.
    """
    parts = callback.data.split(":")
    yk_payment_id = parts[1]
    tariff_code = parts[2] if len(parts) > 2 else ""
    uid = callback.from_user.id

    await callback.answer("🔍 Проверяю оплату…")

    yk_data = await yukassa_get_payment(yk_payment_id)

    if not yk_data:
        await callback.message.answer(
            "❌ Не удалось проверить статус.\nПопробуй через минуту или напиши в поддержку."
        )
        return

    status = yk_data.get("status", "")
    metadata = yk_data.get("metadata") or {}

    if not tariff_code:
        tariff_code = metadata.get("tariff", "")

    if status == "succeeded":
        # Оплата прошла — выдаём доступ
        if tariff_code and tariff_code in TARIFFS:
            await set_paid_tariff(uid, tariff_code)
            # Обновляем статус в БД
            async with db() as conn:
                await conn.execute(
                    "UPDATE payments SET status='approved' WHERE receipt_file_id=? AND user_id=?",
                    (yk_payment_id, uid)
                )
                await conn.commit()

            t = TARIFFS[tariff_code]
            a = await get_access(uid)

            await clean_edit(callback, uid,
                f"✅ Оплата подтверждена!\n"
                f"Тариф: {t['title']}\n"
                f"{access_status_str(a)}\n\n"
                "Теперь иди тренироваться 💪",
                reply_markup=menu_main_inline_kb()
            )

            # Уведомляем админа
            if ADMIN_ID:
                try:
                    await bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"💰 Оплата подтверждена (ЮКасса)\n"
                            f"user_id: {uid}\n"
                            f"tariff: {tariff_code} ({t['title']})\n"
                            f"amount: {t['price']}₽\n"
                            f"yukassa_id: {yk_payment_id}"
                        )
                    )
                except Exception:
                    pass
        else:
            await clean_edit(callback, uid,
                "✅ Оплата прошла! Свяжитесь с поддержкой для активации.",
                reply_markup=menu_main_inline_kb()
            )

    elif status == "pending":
        await callback.message.answer(
            "⏳ Платёж ещё обрабатывается.\n"
            "Подожди 1–2 минуты и нажми «Я оплатил» снова."
        )
    elif status in ("canceled", "cancelled"):
        await callback.message.answer(
            "❌ Платёж отменён.\n"
            "Нажми «⬅️ Назад» и попробуй снова."
        )
    else:
        await callback.message.answer(
            f"⚠️ Статус платежа: {status}\n"
            "Если деньги списались — напиши в поддержку."
        )


async def admin_actions(callback: CallbackQuery, bot: Bot):
    """Ручное одобрение/отклонение (для случаев без ЮКасса)."""
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


async def get_workouts_done_last_7_days(user_id: int) -> int:
    """Считает уникальные дни тренировок за последние 7 дней (включая сегодня)."""
    since = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    async with db() as conn:
        async with conn.execute("""
            SELECT COUNT(DISTINCT completed_date) FROM workout_completions
            WHERE user_id=? AND completed_date >= ? AND completed_date <= ?
        """, (user_id, since, today)) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def mark_nutrition_day(user_id: int):
    """Отмечает сегодня как закрытый день питания (идемпотентно)."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO nutrition_completions (user_id, completed_date, created_at) VALUES (?,?,?)",
                (user_id, today, now)
            )
            await conn.commit()
        except Exception:
            pass


async def get_nutrition_done_last_7_days(user_id: int) -> int:
    """Считает закрытые дни питания за последние 7 дней."""
    since = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    async with db() as conn:
        async with conn.execute("""
            SELECT COUNT(DISTINCT completed_date) FROM nutrition_completions
            WHERE user_id=? AND completed_date >= ? AND completed_date <= ?
        """, (user_id, since, today)) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def get_latest_weight(user_id: int) -> Optional[float]:
    """Возвращает последнее значение веса тела из замеров."""
    async with db() as conn:
        async with conn.execute("""
            SELECT value FROM measurements
            WHERE user_id=? AND mtype='weight'
            ORDER BY id DESC LIMIT 1
        """, (user_id,)) as cur:
            row = await cur.fetchone()
    return float(row[0]) if row else None


async def get_current_weight(user_id: int) -> Optional[float]:
    """Текущий вес: сначала из замеров, иначе из профиля users.weight."""
    mw = await get_latest_weight(user_id)
    if mw is not None:
        return mw
    u = await get_user(user_id)
    w = u.get("weight")
    return float(w) if w else None


async def log_nutrition_day(user_id: int, target_kcal: int, actual_kcal: int):
    """Сохраняет фактическое потребление ккал за сегодня."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        try:
            await conn.execute(
                """INSERT INTO nutrition_daily (user_id, log_date, target_kcal, actual_kcal, created_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id, log_date) DO UPDATE SET
                       actual_kcal=excluded.actual_kcal, created_at=excluded.created_at""",
                (user_id, today, target_kcal, actual_kcal, now)
            )
            await conn.commit()
        except Exception:
            pass


async def get_nutrition_today(user_id: int) -> Optional[dict]:
    """Возвращает запись питания за сегодня (или None)."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with db() as conn:
        async with conn.execute(
            "SELECT target_kcal, actual_kcal FROM nutrition_daily WHERE user_id=? AND log_date=?",
            (user_id, today)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    target, actual = int(row[0]), int(row[1])
    deviation = actual - target
    closed = abs(deviation) <= 100
    return {"target": target, "actual": actual, "deviation": deviation, "closed": closed}


async def get_nutrition_week_stats(user_id: int) -> dict:
    """Возвращает статистику питания за последние 7 дней."""
    since = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    async with db() as conn:
        async with conn.execute("""
            SELECT target_kcal, actual_kcal FROM nutrition_daily
            WHERE user_id=? AND log_date >= ? AND log_date <= ?
        """, (user_id, since, today)) as cur:
            rows = await cur.fetchall()
    if not rows:
        return {"closed": 0, "total": 0, "avg_deviation": 0}
    closed = sum(1 for t, a in rows if abs(a - t) <= 100)
    avg_dev = int(sum(abs(a - t) for t, a in rows) / len(rows))
    return {"closed": closed, "total": len(rows), "avg_deviation": avg_dev}


async def save_nutrition_log(user_id: int, calories: int):
    """Сохраняет введённые пользователем калории за сегодня."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.utcnow().isoformat()
    async with db() as conn:
        await conn.execute(
            """INSERT INTO nutrition_logs (user_id, day_date, calories, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id, day_date) DO UPDATE SET
                   calories=excluded.calories, created_at=excluded.created_at""",
            (user_id, today, calories, now)
        )
        await conn.commit()


async def get_nutrition_log_today(user_id: int) -> Optional[int]:
    """Возвращает калории из nutrition_logs за сегодня."""
    today = datetime.now().strftime("%Y-%m-%d")
    async with db() as conn:
        async with conn.execute(
            "SELECT calories FROM nutrition_logs WHERE user_id=? AND day_date=?",
            (user_id, today)
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else None


GOAL_DISPLAY = {
    "mass": "Набор мышечной массы",
    "cut": "Похудение / сушка",
    "strength": "Рост силы",
    "endurance": "Выносливость",
    "keep": "Поддержание формы",
}


async def get_week_progress(user_id: int, freq: int) -> str:
    """Блок прогресса недели: выполнено/осталось."""
    today = datetime.now().date()
    # Начало текущей недели (понедельник)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    async with db() as conn:
        async with conn.execute("""
            SELECT completed_date FROM workout_completions
            WHERE user_id=? AND completed_date >= ? AND completed_date <= ?
            ORDER BY completed_date
        """, (user_id, week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d"))) as cur:
            rows = await cur.fetchall()

    # Уникальные дни (один день = одна тренировка)
    done_dates = list(set(r[0] for r in rows))
    done_count = len(done_dates)
    remaining = max(0, freq - done_count)

    # Строим визуальную строку дней недели
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    done_set = set(done_dates)
    day_cells = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        mark = "✅" if d.strftime("%Y-%m-%d") in done_set else "⬜"
        day_cells.append(f"{mark} {day_names[i]}")
    # Только рабочие дни по freq (показываем первые freq дней или всю неделю)
    row_str = "  ".join(day_cells)

    lines = [
        "📊 Прогресс недели",
        "",
        f"Выполнено: {done_count}/{freq}",
        f"Осталось: {remaining}",
        "",
        row_str,
    ]
    return "\n".join(lines)


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
    freq = int(u.get("freq") or plan_struct.get("freq") or 3)
    full_access = await is_full_access_active(user_id)
    kb = workout_days_kb(freq, has_full_access=full_access, plan_struct=plan_struct)

    week_progress = await get_week_progress(user_id, freq)
    display_text = (plan_text or "🏋️ План не найден.") + "\n\n" + week_progress

    if callback:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["workouts"], display_text, reply_markup=kb,
            callback=callback,
        )
    else:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["workouts"], display_text, reply_markup=kb,
        )


# =========================
# ✅ Клавиатура дня тренировки — только управление и техники
# убраны кнопки «Статистика» и «Меню»
# =========================
def workout_day_exercises_kb(day: int, exercises: List[str], done: List[int]) -> InlineKeyboardMarkup:
    """Клавиатура упражнений дня с чекбоксами и кнопкой техники."""
    rows = []
    for idx, name in enumerate(exercises):
        is_done = idx in done
        short_name = name[:22] + "…" if len(name) > 22 else name
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

    # Только кнопка «Назад к программе»
    rows.append([InlineKeyboardButton(text="📋 Программа", callback_data="nav:workouts")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cb_workout_day(callback: CallbackQuery, bot: Bot):
    # Обработка кнопки статистики (из главного экрана тренировок)
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
        full_access = await is_full_access_active(uid)
        kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3), has_full_access=full_access, plan_struct=plan_struct)
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
    """Отмечаем/снимаем выполнение упражнения."""
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
        day_title = get_day_display_name(day_num, day_text)
        await mark_day_completed(uid, day_num, day_title)
        await clear_day_progress(uid, day_num)
        text = build_day_display_text(day_num, day_text, exercises, list(range(total)), all_done=True)
        kb = workout_day_exercises_kb(day_num, exercises, list(range(total)))
        await clean_edit(callback, uid, text, reply_markup=kb)
        await callback.answer("🎉 День завершён!", show_alert=True)
    else:
        text = build_day_display_text(day_num, day_text, exercises, done)
        kb = workout_day_exercises_kb(day_num, exercises, done)
        await clean_edit(callback, uid, text, reply_markup=kb)
        await callback.answer(f"{'✅' if ex_idx in done else '↩️'} {done_count}/{total}")


async def cb_workout_stats(callback: CallbackQuery, bot: Bot):
    """Статистика тренировок: дни по порядку + закрытие недели."""
    uid = callback.from_user.id
    async with db() as conn:
        try:
            async with conn.execute("""
                SELECT day_num, completed_date, created_at, day_title
                FROM workout_completions
                WHERE user_id=?
                ORDER BY completed_date ASC, day_num ASC, id ASC
                LIMIT 60
            """, (uid,)) as cur:
                rows = await cur.fetchall()
        except Exception:
            async with conn.execute("""
                SELECT day_num, completed_date, created_at
                FROM workout_completions
                WHERE user_id=?
                ORDER BY completed_date ASC, day_num ASC, id ASC
                LIMIT 60
            """, (uid,)) as cur:
                raw = await cur.fetchall()
            rows = [(r[0], r[1], r[2], "") for r in raw]

    if not rows:
        await callback.answer("Пока нет завершённых тренировок 💪", show_alert=True)
        return

    total = len(rows)
    plan_text, plan_struct = await get_workout_plan(uid)
    u = await get_user(uid)
    freq = int(u.get("freq") or 3)

    lines = ["\U0001f4ca Статистика тренировок\n"]
    lines.append(f"Всего выполнено: {total} тренировок\n")

    # Последние 10 — от старых к новым (rows уже в порядке ASC)
    recent = rows[-10:]
    lines.append("🗓 Последние тренировки:")
    for row in recent:
        day_num = row[0]
        completed_date = row[1]
        saved_title = row[3] if len(row) > 3 else ""
        if saved_title:
            day_label = f"День {day_num} • {saved_title}"
        elif plan_struct:
            day_text_str = (plan_struct.get("days") or {}).get(str(day_num), "")
            day_name = get_day_display_name(day_num, day_text_str)
            day_label = f"День {day_num} • {day_name}"
        else:
            day_label = f"День {day_num}"
        lines.append(f"✅ {completed_date}  —  {day_label}")

    # Серия (streak) по всем датам
    all_dates = sorted(set(r[1] for r in rows), reverse=True)
    streak = 0
    prev = None
    for d in all_dates:
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

    lines.append(f"\n\U0001f525 Текущая серия: {streak} дн. подряд")

    # Закрытие недели
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    ws_str = week_start.strftime("%Y-%m-%d")
    we_str = week_end.strftime("%Y-%m-%d")
    done_this_week = len(set(
        r[1] for r in rows if ws_str <= r[1] <= we_str
    ))
    lines.append("")
    if done_this_week >= freq:
        lines.append(f"✅ Неделя закрыта: выполнено {done_this_week}/{freq}")
    else:
        lines.append(f"📌 Осталось: {freq - done_this_week} тренировок на этой неделе")

    text = "\n".join(lines)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к тренировкам", callback_data="nav:workouts")],
    ])
    await clean_edit(callback, uid, text, reply_markup=back_kb)
    await callback.answer()


async def cb_workout_ex_tech(callback: CallbackQuery, bot: Bot):
    """Показываем технику конкретного упражнения из просмотра дня тренировки.
    callback_data: wex:tech:{day_num}:{tech_key}
    Использует GIF из TECH_GIFS, если файл существует.
    """
    parts = callback.data.split(":")
    tech_key = parts[3] if len(parts) > 3 else ""
    day_num  = parts[2] if len(parts) > 2 else "1"

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⬅️ Назад к тренировке (День {day_num})",
            callback_data=f"wday:{day_num}"
        )]
    ])

    item = TECH.get(tech_key)
    if not item:
        logging.warning(f"[cb_workout_ex_tech] missing tech_key={tech_key!r} — техника не найдена в TECH")
        await clean_edit(
            callback,
            callback.from_user.id,
            (
                "⚠️ Техника для этого упражнения пока не добавлена.\n\n"
                "Напиши в поддержку — добавим."
            ),
            reply_markup=back_kb,
        )
        await callback.answer()
        return

    text = item["text"]
    # Используем GIF (не картинку)
    await _send_tech_with_gif(
        bot, callback.message.chat.id, callback.from_user.id,
        tech_key, text, reply_markup=back_kb
    )
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
            "Питание\n\n"
            "Раздел «Питание» доступен на тарифах 3 месяца и Навсегда.\n\n"
            "Почему именно эти тарифы?\n"
            "90 дней — минимальный срок, чтобы увидеть реальный результат от питания.\n"
            "Месяц — слишком мало для стабильного изменения состава тела.\n\n"
            "Выбери тариф:"
        )
        upgrade_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔥 3 месяца — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
            [InlineKeyboardButton(text=f"🏆 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
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
    summary, calories, _, _, _, _ = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"], meals_pref=int(u.get("meals") or 0)
    )

    full_text = summary

    if callback:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["nutrition"], full_text, reply_markup=nutrition_examples_kb(),
            callback=callback,
        )
    else:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["nutrition"], full_text, reply_markup=nutrition_examples_kb(),
        )


async def open_measures(user_id: int, chat_id: int, bot: Bot, state: FSMContext, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        if callback:
            await clean_edit(callback, user_id, locked_text())
        else:
            await clean_send(bot, chat_id, user_id, locked_text())
        return

    await state.set_state(MeasureFlow.choose_type)
    text = "Замеры\n\nВыбери параметр — дату поставлю сам."
    if callback:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["measurements"], text, reply_markup=measures_kb(),
            callback=callback,
        )
    else:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["measurements"], text, reply_markup=measures_kb(),
        )


async def open_diary(user_id: int, chat_id: int, bot: Bot, state: FSMContext, callback: Optional[CallbackQuery] = None):
    if not await is_access_active(user_id):
        if callback:
            await clean_edit(callback, user_id, locked_text())
        else:
            await clean_send(bot, chat_id, user_id, locked_text())
        return

    await state.set_state(DiaryFlow.choosing_exercise)
    text = (
        "Дневник тренировок\n\n"
        "Как вести:\n"
        "— Выбери упражнение кнопкой\n"
        "— Введи подходы: вес × повторения\n"
        "   Пример: 60x8, 62x8, 65x6\n"
        "— Дата ставится автоматически\n\n"
        "Почему не все упражнения?\n"
        "Дневник содержит базовые упражнения — жим, присед, тягу, подтягивания.\n"
        "Прогресс в базе отражает общий прогресс точнее всего.\n\n"
        "Записывай каждую тренировку — через 4–8 недель увидишь рост в цифрах."
    )
    if callback:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["diary"], text, reply_markup=diary_exercises_kb(),
            callback=callback,
        )
    else:
        await send_section(
            bot, chat_id, user_id,
            IMAGE_PATHS["diary"], text, reply_markup=diary_exercises_kb(),
        )


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
    text = f"Замеры\n\n\U0001f4dd {name}\n\nВведи значение числом:"
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к замерам", callback_data="nav:measures")]
    ])
    await clean_edit(callback, callback.from_user.id, text, reply_markup=back_kb)
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
    uid = message.from_user.id

    await add_measure(uid, mtype, val)
    await try_delete_user_message(bot, message)

    rows = await get_last_measures(uid, mtype, 6)
    name = dict(MEASURE_TYPES).get(mtype, mtype)
    hist = "\n".join([f"• {r[0]:g} ({r[1][:10]})" for r in rows])
    out = (
        f"Замеры\n\n"
        f"✅ {name} записан: {val:g}\n\n"
        f"Последние {name}:\n{hist}\n\n"
        "Выбери следующий параметр:"
    )
    await state.set_state(MeasureFlow.choose_type)
    # Редактируем последнее сообщение бота — без создания нового
    await clean_send(bot, message.chat.id, uid, out, reply_markup=measures_kb())


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
# ПИТАНИЕ: 3 варианта
# =========================
def build_shopping_basket(goal: str, calories: int, protein_g: int, fat_g: int, carbs_g: int, meals: int) -> str:
    """Генерирует список продуктов на неделю под цель пользователя."""
    g = (goal or "").lower()
    is_cut = "суш" in g
    is_mass = "мас" in g

    # Базовые недельные количества (на 7 дней)
    # Белок: основной источник
    chicken_week = int(round(protein_g * 0.45 * 7 / 0.31))   # 45% белка из курицы
    fish_week    = int(round(protein_g * 0.25 * 7 / 0.18))   # 25% из рыбы
    eggs_week    = max(7, int(round(protein_g * 0.15 * 7 / 0.127 / 60)))  # ~15% из яиц (шт)
    curd_week    = int(round(protein_g * 0.15 * 7 / 0.18))   # ~15% из творога

    # Углеводы
    rice_week    = int(round(carbs_g * 0.35 * 7 / 0.76))
    buckwheat_w  = int(round(carbs_g * 0.25 * 7 / 0.57))
    oats_week    = int(round(carbs_g * 0.15 * 7 / 0.60))
    potato_week  = int(round(carbs_g * 0.10 * 7 / 0.17))
    fruit_week   = int(round(carbs_g * 0.15 * 7 / 0.17))   # фрукты ~17% углеводов

    # Жиры
    oil_week     = int(round(fat_g * 0.4 * 7 / 1.0))         # масло 40% жиров
    nuts_week    = int(round(fat_g * 0.3 * 7 / 0.5))         # орехи ~50% жиров

    # Овощи — всегда
    veg_week     = 1400  # 200г в день × 7

    lines = [
        f"🛒 Корзина на неделю",
        f"Цель: {goal} | {calories} ккал/день",
        f"Б {protein_g}г / Ж {fat_g}г / У {carbs_g}г",
        "",
        "🥩 БЕЛОК:",
        f"• Куриная грудка — {chicken_week} г (~{chicken_week//1000:.1f} кг)" if chicken_week >= 1000 else f"• Куриная грудка — {chicken_week} г",
        f"• Рыба белая (треска/минтай) — {fish_week} г" + (" (~{:.1f} кг)".format(fish_week/1000) if fish_week >= 1000 else ""),
        f"• Яйца — {eggs_week} шт",
        f"• Творог 0–5% — {curd_week} г",
    ]

    if is_mass:
        beef_week = int(round(protein_g * 0.10 * 7 / 0.25))
        lines.append(f"• Говядина (варёная) — {beef_week} г  (для разнообразия)")

    lines += [
        "",
        "🍚 УГЛЕВОДЫ:",
        f"• Рис (сухой) — {rice_week} г",
        f"• Гречка (сухая) — {buckwheat_w} г",
        f"• Овсянка (сухая) — {oats_week} г",
        f"• Картофель — {potato_week} г" if not is_cut else f"• Картофель — {potato_week//2} г  (на сушке меньше)",
        f"• Фрукты (яблоко/банан/ягоды) — ~{fruit_week} г/нед",
        "",
        "🥦 ОВОЩИ:",
        f"• Овощи (огурец, помидор, капуста) — ~{veg_week} г/нед",
        "  (покупай любые свежие или замороженные)",
        "",
        "🫒 ЖИРЫ:",
        f"• Масло оливковое/подсолнечное — {oil_week} г  (~{oil_week//10} ст.л.)",
        f"• Орехи (миндаль/грецкий) — {nuts_week} г",
    ]

    if not is_cut:
        lines += [
            f"• Греческий йогурт 2% — 500–700 г",
        ]

    lines += [
        "",
        "💡 Замены:",
        "  курица ↔ индейка ↔ рыба",
        "  рис ↔ гречка ↔ макароны",
        "  творог ↔ греческий йогурт",
        "",
        "⚠️ Это примерный список. Корректируй под",
        "   свой вкус и доступность продуктов.",
    ]

    return "\n".join(lines)


async def cb_nutr_example(callback: CallbackQuery, bot: Bot):
    if not await is_full_access_active(callback.from_user.id):
        text = (
            "🍽 Питание открывается на тарифах 3 месяца и Навсегда.\n\n"
            f"Выбери тариф 👇"
        )
        upgrade_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔥 3 месяца — {TARIFFS['t3']['price']}₽", callback_data="tariff:t3")],
            [InlineKeyboardButton(text=f"🏆 Навсегда — {TARIFFS['life']['price']}₽", callback_data="tariff:life")],
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

    # Вычисляем фактические ккал из текста варианта
    target_obj = {"kcal": float(calories), "p": float(p), "f": float(f), "c": float(c)}
    day_meals_tmp = _build_day_variant(day_i, meals)
    tot = _adjust_to_target(day_meals_tmp, target_obj)
    actual_kcal = int(round(tot["kcal"]))

    # Записываем день в nutrition_daily и nutrition_completions
    await log_nutrition_day(callback.from_user.id, calories, actual_kcal)
    await mark_nutrition_day(callback.from_user.id)

    await clean_edit(callback, callback.from_user.id, day_text, reply_markup=nutrition_back_kb())
    await callback.answer()


async def cb_nutr_tips(callback: CallbackQuery, bot: Bot):
    """Фишки в питании — структурированный гайд."""
    text = (
        "💡 Фишки в питании\n\n"
        "────────────────────\n"
        "1. Как быстро добрать калории\n"
        "────────────────────\n"
        "• Добавь масло к крупе — 1 ст. л. подсолнечного = +90 ккал.\n"
        "• Хлеб ржаной к обеду — 2 ломтика (~120 ккал, просто и дёшево).\n"
        "• Кефир или молоко вместо воды к еде — стакан 200 мл = +80–110 ккал.\n"
        "• Банан между приёмами — 1 шт. = ~90 ккал, сытно и быстро.\n"
        "• Шейк: молоко 300 мл + банан + овсянка 40 г = ~400 ккал за 3 мин.\n"
        "• Паста с яйцами: гречка / макароны + 2 яйца = белок + углеводы.\n"
        "• Увеличь порцию крупы на 20–30 г — это ещё +60–100 ккал без лишней готовки.\n\n"
        "────────────────────\n"
        "2. Как добрать белок\n"
        "────────────────────\n"
        "• Творог 2–5% на ночь — 200 г = ~35 г белка. Самый дешёвый источник.\n"
        "• Кефир 1% — 300 мл = ~10 г белка, но выпить легко.\n"
        "• Яйца — 2 яйца = ~12 г белка, и везде доступны.\n"
        "• Тунец консервированный — 1 банка 185 г = ~40 г белка, без готовки.\n"
        "• Куриное бедро вместо грудки — чуть жирнее, зато дешевле и сочнее.\n\n"
        "────────────────────\n"
        "3. Если еда не лезет\n"
        "────────────────────\n"
        "• Ешь чаще, но меньше — 4–5 раз по 300–400 ккал легче, чем 3 раза по 700.\n"
        "• Пей калории: молоко, кефир, коктейли. Жидкость не «давит» так, как еда.\n"
        "• Убери воду за 30 мин до еды — желудок не будет переполнен.\n"
        "• Приготовь сразу на день — когда еда уже готова, съесть проще.\n"
        "• Калорийная каша: овсянка на молоке + масло + банан = ~500 ккал за один завтрак.\n\n"
        "────────────────────\n"
        "4. Примеры быстрых доборов\n"
        "────────────────────\n"
        "→ +200 ккал легко:\n"
        "   2 ломтика хлеба (50 г) + 1 ч.л. масла = ~210 ккал\n\n"
        "→ +300 ккал и 25г белка:\n"
        "   Творог 200г + банан = ~290 ккал, 35г белка\n\n"
        "→ +400 ккал быстрый шейк:\n"
        "   Молоко 300мл + банан + овсянка 40г = ~410 ккал, 15г белка"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к питанию", callback_data="nutr:back")],
        [InlineKeyboardButton(text="🛒 Моя корзина", callback_data="nutr:basket")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])
    await clean_edit(callback, callback.from_user.id, text, reply_markup=kb)
    await callback.answer()


async def cb_nutr_back(callback: CallbackQuery, bot: Bot):
    await open_nutrition(callback.from_user.id, callback.message.chat.id, bot, callback=callback)
    await callback.answer()


async def cb_nutr_basket(callback: CallbackQuery, bot: Bot):
    """Корзина продуктов на неделю."""
    if not await is_full_access_active(callback.from_user.id):
        await callback.answer("🔒 Доступно на тарифах 3 месяца и Навсегда.", show_alert=True)
        return

    if not await ensure_profile_ready(callback.from_user.id):
        await clean_edit(callback, callback.from_user.id, "⚠️ Сначала заполни профиль.")
        await callback.answer()
        return

    u = await get_user(callback.from_user.id)
    _, calories, p, f, c, meals = generate_nutrition_summary(
        u["goal"], u["sex"], int(u["age"]), int(u["height"]), float(u["weight"]), u["exp"],
        freq=int(u["freq"]), place=u["place"], meals_pref=int(u.get("meals") or 0)
    )

    basket_text = build_shopping_basket(u["goal"], calories, p, f, c, meals)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к рациону", callback_data="nutr:back")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="nav:menu")],
    ])
    await clean_edit(callback, callback.from_user.id, basket_text, reply_markup=back_kb)
    await callback.answer()


# =========================
# ✅ ТЕХНИКИ: ХЕНДЛЕРЫ (с GIF-анимациями)
# =========================
async def cb_tech_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await clean_edit(callback, callback.from_user.id, "📚 Техники — выбери упражнение:", reply_markup=tech_kb())
    await callback.answer()


async def cb_tech_show(callback: CallbackQuery, bot: Bot):
    """Показ техники с GIF-анимацией из TECH_GIFS (если файл есть), иначе — текст."""
    key = callback.data.split("tech:", 1)[1]
    item = TECH.get(key)
    if not item:
        await callback.answer("Не нашёл 😅", show_alert=True)
        return

    text = item["text"]
    await _send_tech_with_gif(
        bot, callback.message.chat.id, callback.from_user.id,
        key, text, reply_markup=tech_back_kb()
    )
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
# ✅ СМЕНА ПРОГРАММЫ ТРЕНИРОВОК (для full access)
# =========================
async def cb_workout_rebuild(callback: CallbackQuery, bot: Bot):
    """Пересобирает план тренировок (только для месячной подписки и выше)."""
    uid = callback.from_user.id
    if not await is_full_access_active(uid):
        await callback.answer(
            "🔒 Смена программы доступна на тарифах 3 месяца и Навсегда.",
            show_alert=True
        )
        return

    await callback.answer("🔄 Пересобираю программу…")

    # Меняем seed (сдвигаем на случайное число) чтобы получить другой рандом
    u = await get_user(uid)
    import random as _rnd
    shift = _rnd.randint(1, 9999)
    # Сохраняем сдвиг в поле state (временно), чтобы gen функция получила другой seed
    original_id = uid
    # Генерируем с изменённым user_id (seed) для разнообразия
    varied_id = uid + shift * 100

    intro, plan_struct = generate_workout_plan(
        u["goal"], u["place"], u["exp"], int(u["freq"] or 3),
        limits=u.get("limits") or "",
        user_id=varied_id
    )
    await save_workout_plan(uid, intro, dumps_plan(plan_struct))

    # Сбрасываем прогресс дней
    async with db() as conn:
        await conn.execute("DELETE FROM workout_day_progress WHERE user_id=?", (uid,))
        await conn.commit()

    full_access = await is_full_access_active(uid)
    kb = workout_days_kb(int(u.get("freq") or plan_struct.get("freq") or 3), has_full_access=full_access, plan_struct=plan_struct)
    await clean_edit(callback, uid,
        intro + "\n\n✅ Программа обновлена! Прогресс дней сброшен.",
        reply_markup=kb
    )


# =========================
# ДИАГНОСТИКА ОПЛАТЫ (АДМИН)
# =========================
async def cmd_testpay(message: Message, bot: Bot):
    """Команда /testpay — проверяет соединение с ЮКасса API (только для админа)."""
    if message.from_user.id != ADMIN_ID:
        return

    lines = ["🔍 Диагностика ЮКасса\n"]
    lines.append(f"YUKASSA_SHOP_ID: {'✅ ' + YUKASSA_SHOP_ID[:4] + '****' if YUKASSA_SHOP_ID else '❌ не задан'}")
    lines.append(f"YUKASSA_SECRET: {'✅ задан' if YUKASSA_SECRET else '❌ не задан'}")
    lines.append(f"BOT_PUBLIC_URL: {BOT_PUBLIC_URL or '❌ не задан'}\n")

    if not YUKASSA_SHOP_ID or not YUKASSA_SECRET:
        lines.append("❌ Переменные окружения не заданы!")
        await message.answer("\n".join(lines))
        return

    lines.append("⏳ Пробую создать тестовый платёж (1₽)...")
    await message.answer("\n".join(lines))

    yk_data, yk_err = await yukassa_create_payment("t1", message.from_user.id)

    if yk_data:
        pay_id = yk_data.get("id", "?")
        conf_url = (yk_data.get("confirmation") or {}).get("confirmation_url", "?")
        await message.answer(
            f"✅ Успешно! Платёж создан.\n"
            f"ID: {pay_id}\n"
            f"URL: {conf_url[:60]}..."
        )
    else:
        await message.answer(
            f"❌ Ошибка создания платежа:\n{yk_err}\n\n"
            "Проверь:\n"
            "1. YUKASSA_SHOP_ID и YUKASSA_SECRET правильные\n"
            "2. Магазин активирован в yukassa.ru\n"
            "3. IP сервера не заблокирован ЮКасса"
        )


# =========================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
def setup_handlers(dp: Dispatcher):
    from aiogram.types import PreCheckoutQuery

    dp.message.register(cmd_start, CommandStart())

    dp.callback_query.register(cb_nav, F.data.startswith("nav:"))

    dp.callback_query.register(cb_profile_edit, F.data == "p:edit")
    dp.callback_query.register(cb_rebuild_plan, F.data == "p:rebuild_plan")
    dp.callback_query.register(cb_do_rebuild, F.data == "p:do_rebuild")
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

    # ЮКасса REST API
    dp.callback_query.register(cb_tariff, F.data.startswith("tariff:"))
    dp.callback_query.register(cb_check_payment, F.data.startswith("check_pay:"))

    # Ручное одобрение (запасной вариант)
    dp.callback_query.register(admin_actions, F.data.startswith("admin_approve:") | F.data.startswith("admin_reject:"))

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
    dp.callback_query.register(cb_nutr_basket, F.data == "nutr:basket")
    dp.callback_query.register(cb_nutr_tips, F.data == "nutr:tips")

    dp.callback_query.register(cb_faq_question, F.data.startswith("faq:"))

    dp.callback_query.register(cb_workout_day, F.data.startswith("wday:"))
    dp.callback_query.register(cb_workout_ex_done, F.data.startswith("wex:done:"))
    dp.callback_query.register(cb_workout_ex_tech, F.data.startswith("wex:tech:"))
    dp.callback_query.register(cb_workout_rebuild, F.data == "workout:rebuild")

    dp.message.register(cmd_testpay, Command("testpay"))
    dp.message.register(cmd_posts, Command("posts"))
    dp.callback_query.register(cb_post_new, F.data == "post:new")
    dp.callback_query.register(cb_post_cancel, F.data == "post:cancel")
    dp.callback_query.register(cb_post_send, F.data.startswith("post:send:"))
    dp.message.register(post_waiting_content, PostFlow.waiting_content)

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

# =========================
# НАПОМИНАНИЯ ОБ ОКОНЧАНИИ ПОДПИСКИ
# =========================
async def _check_and_remind_subscriptions(bot: Bot):
    """Проверяем подписки и рассылаем уведомления при необходимости."""
    today = datetime.utcnow().date()
    async with db() as conn:
        async with conn.execute(
            """SELECT user_id, tariff, tariff_name, expires_at, remind_stage
               FROM access WHERE paid=1 AND expires_at IS NOT NULL"""
        ) as cur:
            rows = await cur.fetchall()

    for row in rows:
        user_id, tariff, tariff_name, expires_at, remind_stage = row
        remind_stage = remind_stage if remind_stage is not None else -1
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            exp_date = exp_dt.date()
        except Exception:
            continue

        days_left = (exp_date - today).days

        # Подписка истекла — деактивируем
        if days_left < 0:
            async with db() as conn:
                await conn.execute(
                    "UPDATE access SET paid=0 WHERE user_id=?", (user_id,)
                )
                await conn.commit()
            continue

        # Определяем нужную стадию напоминания
        send_stage = None
        if days_left == 3 and remind_stage < 3:
            send_stage = 3
        elif days_left == 1 and remind_stage < 1:
            send_stage = 1
        elif days_left == 0 and remind_stage < 0:
            send_stage = 0

        if send_stage is None:
            continue

        if days_left == 0:
            days_str = "сегодня"
        elif days_left == 1:
            days_str = "завтра"
        else:
            days_str = f"через {days_left} дн."

        text = (
            f"\u23f3 Подписка скоро закончится: осталось {days_left} дн.\n\n"
            "Чтобы не потерять доступ к плану и статистике — продли подписку \U0001f447"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="\U0001f4b3 Продлить подписку",
                callback_data="nav:upgrade"
            )]
        ])
        try:
            await bot.send_message(user_id, text, reply_markup=kb)
            async with db() as conn:
                await conn.execute(
                    "UPDATE access SET remind_stage=? WHERE user_id=?",
                    (send_stage, user_id)
                )
                await conn.commit()
        except Exception:
            pass  # пользователь заблокировал бота


async def subscription_reminder_loop(bot: Bot):
    """Фоновая задача: раз в 12 часов проверяет подписки и шлёт уведомления."""
    logger.info("subscription_reminder_loop started")
    while True:
        try:
            await _check_and_remind_subscriptions(bot)
        except Exception:
            logger.exception("subscription_reminder_loop error")
        await asyncio.sleep(12 * 3600)


async def main():
    if "PASTE_NEW_TOKEN_HERE" in BOT_TOKEN or not BOT_TOKEN or BOT_TOKEN == "0":
        raise RuntimeError("Нужно задать BOT_TOKEN через ENV.")

    if ADMIN_ID == 0:
        logger.warning("ADMIN_ID не задан. Уведомления об оплатах не будут отправляться.")

    if not YUKASSA_SHOP_ID or not YUKASSA_SECRET:
        logger.warning("YUKASSA_SHOP_ID или YUKASSA_SECRET не заданы. Оплата через ЮКасса не будет работать.")

    await init_db()

    bot = Bot(
    token=BOT_TOKEN,
    parse_mode=ParseMode.HTML
)
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
        subscription_reminder_loop(bot),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

