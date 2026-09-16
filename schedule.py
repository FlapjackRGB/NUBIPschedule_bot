import os
import re
import json
import asyncio
from datetime import datetime, date, timedelta, time
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)

TOKEN = ("8703800816:AAH5c8PSbXalv_1HmJ7gx8quwCCKnxKRyrk")

MAIN_API_URL = "https://rozklad.nubip.edu.ua/api/public/schedule/VETM/3-10"
ELECTIVE_API_URL = "https://rozklad.nubip.edu.ua/api/public/schedule/ADDT/0-8"
SUBSCRIBERS_FILE = "subscribers.json"

# Пари по 80 хв (1:20), перерви по 20 хв
BELL_TIMES = {
    1: {"start": time(8, 30),  "end": time(9, 50),  "str": "08:30 – 09:50"},
    2: {"start": time(10, 10), "end": time(11, 30), "str": "10:10 – 11:30"},
    3: {"start": time(11, 50), "end": time(13, 10), "str": "11:50 – 13:10"},
    4: {"start": time(13, 30), "end": time(14, 50), "str": "13:30 – 14:50"},
    5: {"start": time(15, 10), "end": time(16, 30), "str": "15:10 – 16:30"},
    6: {"start": time(16, 50), "end": time(18, 10), "str": "16:50 – 18:10"},
    7: {"start": time(18, 30), "end": time(19, 50), "str": "18:30 – 19:50"},
}

WEEKDAYS_MAP = {
    0: ("monday", "Понеділок"),
    1: ("tuesday", "Вівторок"),
    2: ("wednesday", "Середа"),
    3: ("thursday", "Четвер"),
    4: ("friday", "П'ятниця"),
    5: ("saturday", "Субота"),
    6: ("sunday", "Неділя")
}

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ----------------- ПІДПИСНИКИ СПОВІЩЕНЬ -----------------

def load_subscribers() -> set:
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_subscribers(subs: set):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(subs), f)

subscribers = load_subscribers()

def get_main_keyboard(chat_id: int):
    is_subbed = chat_id in subscribers
    sub_text = "🔕 Вимкнути сповіщення" if is_subbed else "🔔 Увімкнути сповіщення"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 На сьогодні"), KeyboardButton(text="➡️ На завтра")],
            [KeyboardButton(text="📅 Чисельник"), KeyboardButton(text="📅 Знаменник")],
            [KeyboardButton(text=sub_text)]
        ],
        resize_keyboard=True
    )

def get_inline_parity_keyboard(current_mode: str, day_slug: str):
    btn_odd = "🔘 Чисельник" if current_mode == "odd" else "Чисельник"
    btn_even = "🔘 Знаменник" if current_mode == "even" else "Знаменник"
    btn_all = "🔘 Обидва" if current_mode == "all" else "Обидва"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_odd, callback_data=f"switch:{day_slug}:odd"),
                InlineKeyboardButton(text=btn_even, callback_data=f"switch:{day_slug}:even"),
                InlineKeyboardButton(text=btn_all, callback_data=f"switch:{day_slug}:all")
            ]
        ]
    )

# ----------------- ПАРСИНГ ТА ЗАПИТИ ДО API -----------------

def parse_electives(raw_html: str) -> list[str]:
    items = re.split(r'<br\s*/?>', raw_html)
    cleaned = []
    for item in items:
        text = re.sub(r'<.*?>', '', item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned

def get_week_parity(target_date: date) -> str:
    week_num = target_date.isocalendar()[1]
    return "even" if week_num % 2 == 0 else "odd"

async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"Помилка запиту до API ({url}): {e}")
    return {}

async def build_schedule_text(group_name: str, day_slug: str, day_title: str, mode: str) -> tuple[str, list]:
    parity_str = "Чисельник" if mode == "odd" else ("Знаменник" if mode == "even" else "Обидва")

    async with aiohttp.ClientSession() as session:
        main_data = await fetch_json(session, MAIN_API_URL)
        elective_data = await fetch_json(session, ELECTIVE_API_URL)

    real_group_name = main_data.get("name", group_name)
    main_lessons = main_data.get("days", {}).get(day_slug, {}).get("lessons", [])
    elective_lessons = elective_data.get("days", {}).get(day_slug, {}).get("lessons", [])

    valid_lessons = []
    for l in main_lessons:
        on_week = l.get("onWeek", "all")
        if mode == "all" or on_week in ("all", mode):
            slot = l.get("timeSlot")
            subject = l.get("subject", "").strip()

            if "вибором" in subject.lower() or "посилання" in subject.lower() or "http" in subject.lower():
                matched = []
                for e in elective_lessons:
                    if e.get("timeSlot") == slot and (mode == "all" or e.get("onWeek") in ("all", mode)):
                        matched.extend(parse_electives(e.get("subject", "")))

                if matched:
                    subject = "Дисципліни за вибором:\n" + "\n".join(f"     • {sub}" for sub in matched)
                else:
                    subject = "Дисципліни за вибором студента"

            valid_lessons.append({"timeSlot": slot, "subject": subject})

    valid_lessons.sort(key=lambda x: x["timeSlot"])

    lines = [
        f"📚 {real_group_name}",
        f"Тиждень: {parity_str}",
        "_________________________________\n",
        f"🗓️ {day_title}:\n"
    ]

    if not valid_lessons:
        lines.append("  ◽ Пар немає\n")
    else:
        for item in valid_lessons:
            slot = item["timeSlot"]
            time_str = BELL_TIMES.get(slot, {}).get("str", "Час не визначено")
            lines.append(f"{slot} пара 🕒 {time_str}")
            lines.append(f"  ◽ {item['subject']}\n")

    return "\n".join(lines).strip(), valid_lessons

# ----------------- ХЕНДЛЕРИ КОРИСТУВАЧА -----------------

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "👋 Вітаю! Я бот розкладу для групи ВЕТМ 3-10.\nОберіть потрібний пункт у меню нижче:",
        reply_markup=get_main_keyboard(msg.chat.id)
    )

@dp.message(F.text == "📍 На сьогодні")
async def today_schedule(msg: types.Message):
    today = datetime.now().date()
    day_idx = today.weekday()
    if day_idx in (5, 6):
        await msg.answer(f"🗓️ {WEEKDAYS_MAP[day_idx][1]}:\n\n  ◽ Вихідний день! Пар немає 🎉")
        return

    slug, day_title = WEEKDAYS_MAP[day_idx]
    parity = get_week_parity(today)
    text, _ = await build_schedule_text("Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, parity)
    await msg.answer(text, reply_markup=get_inline_parity_keyboard(parity, slug))

@dp.message(F.text == "➡️ На завтра")
async def tomorrow_schedule(msg: types.Message):
    tomorrow = datetime.now().date() + timedelta(days=1)
    day_idx = tomorrow.weekday()
    if day_idx in (5, 6):
        await msg.answer(f"🗓️ {WEEKDAYS_MAP[day_idx][1]}:\n\n  ◽ Вихідний день! Пар немає 🎉")
        return

    slug, day_title = WEEKDAYS_MAP[day_idx]
    parity = get_week_parity(tomorrow)
    text, _ = await build_schedule_text("Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, parity)
    await msg.answer(text, reply_markup=get_inline_parity_keyboard(parity, slug))

@dp.message(F.text == "📅 Чисельник")
async def numerator_full(msg: types.Message):
    separator = "_________________________________\n"
    output = ["📚 Ветеринарна медицина ВМ-2023010 с.т.\nТиждень: Чисельник\n" + separator]
    for i in range(5):
        slug, title = WEEKDAYS_MAP[i]
        _, lessons = await build_schedule_text("", slug, title, "odd")
        output.append(f"🗓️ {title}:\n")
        if lessons:
            for l in lessons:
                t = BELL_TIMES.get(l['timeSlot'], {}).get('str', '')
                output.append(f"{l['timeSlot']} пара 🕒 {t}\n  ◽ {l['subject']}\n")
        else:
            output.append("  ◽ Пар немає\n")
        if i < 4:
            output.append(separator)
    await msg.answer("\n".join(output))

@dp.message(F.text == "📅 Знаменник")
async def denominator_full(msg: types.Message):
    separator = "_________________________________\n"
    output = ["📚 Ветеринарна медицина ВМ-2023010 с.т.\nТиждень: Знаменник\n" + separator]
    for i in range(5):
        slug, title = WEEKDAYS_MAP[i]
        _, lessons = await build_schedule_text("", slug, title, "even")
        output.append(f"🗓️ {title}:\n")
        if lessons:
            for l in lessons:
                t = BELL_TIMES.get(l['timeSlot'], {}).get('str', '')
                output.append(f"{l['timeSlot']} пара 🕒 {t}\n  ◽ {l['subject']}\n")
        else:
            output.append("  ◽ Пар немає\n")
        if i < 4:
            output.append(separator)
    await msg.answer("\n".join(output))

@dp.callback_query(F.data.startswith("switch:"))
async def switch_inline_mode(call: CallbackQuery):
    _, slug, mode = call.data.split(":")
    day_title = next(title for s, title in WEEKDAYS_MAP.values() if s == slug)
    text, _ = await build_schedule_text("Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, mode)

    try:
        await call.message.edit_text(text, reply_markup=get_inline_parity_keyboard(mode, slug))
    except Exception:
        pass
    await call.answer()

@dp.message(F.text.in_(["🔔 Увімкнути сповіщення", "🔕 Вимкнути сповіщення"]))
async def toggle_subscription(msg: types.Message):
    chat_id = msg.chat.id
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)
        await msg.answer("🔕 Сповіщення вимкнено.", reply_markup=get_main_keyboard(chat_id))
    else:
        subscribers.add(chat_id)
        save_subscribers(subscribers)
        await msg.answer(
            "🔔 Сповіщення увімкнено!\n\n"
            "• Розклад на завтра о 20:00 (у неділю–четвер)\n"
            "• Нагадування за 20 хвилин до початку кожної пари",
            reply_markup=get_main_keyboard(chat_id)
        )

# ----------------- СИСТЕМА СПОВІЩЕНЬ -----------------

async def notifier_loop():
    notified_slots_today = set()
    evening_notified_date = None

    while True:
        try:
            now = datetime.now()
            today = now.date()
            current_time = now.time()

            # 1. Вечірній розклад на завтра о 20:00
            if current_time.hour == 20 and current_time.minute == 0:
                if evening_notified_date != today:
                    # Якщо сьогодні п'ятниця (4) або субота (5), завтра вихідний — не спамимо
                    if today.weekday() not in (4, 5):
                        tomorrow = today + timedelta(days=1)
                        slug, day_title = WEEKDAYS_MAP[tomorrow.weekday()]
                        parity = get_week_parity(tomorrow)
                        text, lessons = await build_schedule_text(
                            "Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, parity
                        )
                        if lessons:
                            msg_text = f"📢 Розклад на завтра:\n\n{text}"
                            for chat_id in list(subscribers):
                                try:
                                    await bot.send_message(
                                        chat_id, msg_text, reply_markup=get_inline_parity_keyboard(parity, slug)
                                    )
                                except Exception:
                                    pass
                    evening_notified_date = today

            # Очищення відміток відправлених пар опівночі
            if current_time.hour == 0 and current_time.minute == 1:
                notified_slots_today.clear()

            # 2. Сповіщення за 20 хвилин до початку кожної пари (пн–пт)
            if today.weekday() not in (5, 6):
                slug, day_title = WEEKDAYS_MAP[today.weekday()]
                parity = get_week_parity(today)
                _, lessons = await build_schedule_text("", slug, day_title, parity)

                for l in lessons:
                    slot = l["timeSlot"]
                    if slot in notified_slots_today:
                        continue

                    start_time = BELL_TIMES.get(slot, {}).get("start")
                    if start_time:
                        lesson_dt = datetime.combine(today, start_time)
                        diff = (lesson_dt - now).total_seconds()

                        # Вікно спрацювання: за 19–20 хвилин до початку
                        if 1140 <= diff <= 1260:
                            time_str = BELL_TIMES[slot]["str"]
                            alert_text = (
                                f"⏳ Через 20 хвилин пара!\n\n"
                                f"{slot} пара 🕒 {time_str}\n"
                                f"  ◽ {l['subject']}"
                            )
                            for chat_id in list(subscribers):
                                try:
                                    await bot.send_message(chat_id, alert_text)
                                except Exception:
                                    pass
                            notified_slots_today.add(slot)

        except Exception as e:
            print(f"Помилка у циклі сповіщень: {e}")

        await asyncio.sleep(25)

# ----------------- ТОЧКА ВХОДУ -----------------

async def main():
    asyncio.create_task(notifier_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
