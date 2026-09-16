import os
import re
import json
import asyncio
from datetime import datetime, date, timedelta, time
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode

TOKEN = "8703800816:AAH5c8PSbXalv_1HmJ7gx8quwCCKnxKRyrk"

MAIN_API_URL = "https://rozklad.nubip.edu.ua/api/public/schedule/VETM/3-10"
ELECTIVE_API_URL = "https://rozklad.nubip.edu.ua/api/public/schedule/ADDT/0-8"
SUBSCRIBERS_FILE = "subscribers.json"

# Пари по 80 хв (1 год 20 хв), перерви 20 хв
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

def get_keyboard(chat_id: int):
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

# ----------------- ОБРОБКА ДАНИХ ТА API -----------------

def clean_html(raw_html: str) -> str:
    cleared = re.sub(r'<br\s*/?>', '\n   ▫️ ', raw_html)
    cleared = re.sub(r'<.*?>', '', cleared)
    return cleared.strip()

def get_week_parity(target_date: date) -> str:
    week_num = target_date.isocalendar()[1]
    return "even" if week_num % 2 == 0 else "odd"

async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"Помилка запиту до {url}: {e}")
    return {}

async def get_schedule_for_date(target_date: date) -> tuple[str, list]:
    day_idx = target_date.weekday()
    if day_idx in (5, 6):
        return f"📅 <b>{WEEKDAYS_MAP[day_idx][1]}</b> ({target_date.strftime('%d.%m.%Y')}):\n\nВихідний день! Пар немає 🎉", []

    slug, day_title = WEEKDAYS_MAP[day_idx]
    parity = get_week_parity(target_date)
    parity_ua = "Чисельник" if parity == "odd" else "Знаменник"

    async with aiohttp.ClientSession() as session:
        main_data = await fetch_json(session, MAIN_API_URL)
        elective_data = await fetch_json(session, ELECTIVE_API_URL)

    main_lessons = main_data.get("days", {}).get(slug, {}).get("lessons", [])
    elective_lessons = elective_data.get("days", {}).get(slug, {}).get("lessons", [])

    valid_lessons = []
    for l in main_lessons:
        on_week = l.get("onWeek", "all")
        if on_week == "all" or on_week == parity:
            slot = l.get("timeSlot")
            subject = l.get("subject", "").strip()

            if "вибором" in subject.lower() or "посилання" in subject.lower():
                electives_matched = [
                    clean_html(e.get("subject", ""))
                    for e in elective_lessons
                    if e.get("timeSlot") == slot and (e.get("onWeek") in ("all", parity))
                ]
                if electives_matched:
                    subject = "<b>Вибіркова дисципліна:</b>\n   ▫️ " + "\n   ▫️ ".join(electives_matched)

            valid_lessons.append({
                "timeSlot": slot,
                "subject": subject
            })

    valid_lessons.sort(key=lambda x: x["timeSlot"])

    if not valid_lessons:
        return f"📅 <b>{day_title}</b> ({target_date.strftime('%d.%m.%Y')}) — <i>{parity_ua}</i>\n\nПар немає 🏖", []

    text_lines = [f"📅 <b>{day_title}</b> ({target_date.strftime('%d.%m.%Y')}) — <i>{parity_ua}</i>\n"]
    for l in valid_lessons:
        slot = l["timeSlot"]
        time_str = BELL_TIMES.get(slot, {}).get("str", "Час не визначено")
        text_lines.append(f"<b>{slot} пара</b> 🕒 <code>{time_str}</code>\n▫️ {l['subject']}\n")

    return "\n".join(text_lines), valid_lessons

async def get_schedule_by_type(target_parity: str) -> str:
    parity_ua = "Чисельник" if target_parity == "odd" else "Знаменник"
    
    async with aiohttp.ClientSession() as session:
        main_data = await fetch_json(session, MAIN_API_URL)
        elective_data = await fetch_json(session, ELECTIVE_API_URL)

    days_dict = main_data.get("days", {})
    elective_dict = elective_data.get("days", {})
    output = [f"📚 <b>Повний розклад: {parity_ua}</b>\n"]

    for i in range(5):
        slug, title = WEEKDAYS_MAP[i]
        day_lessons = days_dict.get(slug, {}).get("lessons", [])
        day_electives = elective_dict.get(slug, {}).get("lessons", [])

        valid = []
        for l in day_lessons:
            on_week = l.get("onWeek", "all")
            if on_week in ("all", target_parity):
                slot = l.get("timeSlot")
                subject = l.get("subject", "").strip()

                if "вибором" in subject.lower() or "посилання" in subject.lower():
                    matched = [
                        clean_html(e.get("subject", ""))
                        for e in day_electives
                        if e.get("timeSlot") == slot and e.get("onWeek") in ("all", target_parity)
                    ]
                    if matched:
                        subject = "<b>Вибіркова дисципліна:</b>\n   ▫️ " + "\n   ▫️ ".join(matched)

                valid.append({"timeSlot": slot, "subject": subject})

        valid.sort(key=lambda x: x["timeSlot"])
        output.append(f"▫️ <b>{title}</b>:")
        if valid:
            for item in valid:
                t = BELL_TIMES.get(item['timeSlot'], {}).get('str', '')
                output.append(f"  <b>{item['timeSlot']} пара</b> ({t}):\n  {item['subject']}")
        else:
            output.append("  Пар немає")
        output.append("")

    return "\n".join(output)

# ----------------- ХЕНДЛЕРИ КОМАНД ТА КНОПОК -----------------

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "👋 Вітаю! Я бот розкладу для групи <b>ВЕТМ 3-10</b>.\n\nОберіть потрібну дію:",
        reply_markup=get_keyboard(msg.chat.id),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text == "📍 На сьогодні")
async def today_schedule(msg: types.Message):
    text, _ = await get_schedule_for_date(datetime.now().date())
    await msg.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "➡️ На завтра")
async def tomorrow_schedule(msg: types.Message):
    tomorrow = datetime.now().date() + timedelta(days=1)
    text, _ = await get_schedule_for_date(tomorrow)
    await msg.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📅 Чисельник")
async def numerator_schedule(msg: types.Message):
    text = await get_schedule_by_type("odd")
    await msg.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📅 Знаменник")
async def denominator_schedule(msg: types.Message):
    text = await get_schedule_by_type("even")
    await msg.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text.in_(["🔔 Увімкнути сповіщення", "🔕 Вимкнути сповіщення"]))
async def toggle_subscription(msg: types.Message):
    chat_id = msg.chat.id
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)
        await msg.answer("🔕 Сповіщення <b>вимкнено</b>.", reply_markup=get_keyboard(chat_id), parse_mode=ParseMode.HTML)
    else:
        subscribers.add(chat_id)
        save_subscribers(subscribers)
        await msg.answer(
            "🔔 Сповіщення <b>увімкнено</b>!\n\n"
            "• Розклад на наступний день о 20:00 (у неділю–четвер)\n"
            "• Нагадування за 20 хвилин до початку кожної пари",
            reply_markup=get_keyboard(chat_id),
            parse_mode=ParseMode.HTML
        )

# ----------------- ФОНОВА СЛУЖБА СПОВІЩЕНЬ -----------------

async def notifier_loop():
    notified_slots_today = set()
    evening_notified_date = None

    while True:
        try:
            now = datetime.now()
            today = now.date()
            current_time = now.time()

            # 1. Вечірній розклад на завтра о 20:00 (крім п'ятниці 4 та суботи 5)
            if current_time.hour == 20 and current_time.minute == 0:
                if evening_notified_date != today:
                    if today.weekday() not in (4, 5):
                        tomorrow = today + timedelta(days=1)
                        text, lessons = await get_schedule_for_date(tomorrow)
                        if lessons:
                            msg_text = f"📢 <b>Розклад на завтра:</b>\n\n{text}"
                            for chat_id in list(subscribers):
                                try:
                                    await bot.send_message(chat_id, msg_text, parse_mode=ParseMode.HTML)
                                except Exception:
                                    pass
                    evening_notified_date = today

            # Очищення відміток пар опівночі
            if current_time.hour == 0 and current_time.minute == 1:
                notified_slots_today.clear()

            # 2. Сповіщення за 20 хвилин до початку пари (пн–пт)
            if today.weekday() not in (5, 6):
                _, lessons = await get_schedule_for_date(today)
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
                                f"⏳ <b>Через 20 хвилин пара!</b>\n\n"
                                f"<b>{slot} пара</b> (<code>{time_str}</code>)\n"
                                f"▫️ {l['subject']}"
                            )
                            for chat_id in list(subscribers):
                                try:
                                    await bot.send_message(chat_id, alert_text, parse_mode=ParseMode.HTML)
                                except Exception:
                                    pass
                            notified_slots_today.add(slot)

        except Exception as e:
            print(f"Помилка у циклі сповіщень: {e}")

        await asyncio.sleep(25)

# ----------------- ГОЛОВНИЙ ЗАПУСК -----------------

async def main():
    asyncio.create_task(notifier_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
