import os
import re
import json
import asyncio
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    ReplyKeyboardRemove
)

TOKEN = ("8703800816:AAH5c8PSbXalv_1HmJ7gx8quwCCKnxKRyrk")

# Фіксація точного часового поясу Києва
KYIV_TZ = ZoneInfo("Europe/Kyiv")

MAIN_API_URL = "https://rozklad.nubip.edu.ua/api/public/schedule/VETM/3-10"
ELECTIVE_API_URL = "https://rozklad.nubip.edu.ua/api/public/schedule/ADDT/0-8"
SITE_URL = "https://rozklad.nubip.edu.ua/"
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

CACHED_PARITY = {"parity": None, "timestamp": 0}

# ----------------- ПІДПИСНИКИ (ПІДТРИМКА ГІЛОК/ФОРУМІВ) -----------------

def load_subscribers() -> list[dict]:
    """Завантажує підписників: {chat_id: int, thread_id: int|None}"""
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                sub_list = []
                for item in data:
                    if isinstance(item, int):
                        sub_list.append({"chat_id": item, "thread_id": None})
                    elif isinstance(item, dict) and "chat_id" in item:
                        sub_list.append(item)
                return sub_list
        except Exception:
            return []
    return []

def save_subscribers(subs: list[dict]):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)

subscribers = load_subscribers()

def is_subscribed(chat_id: int, thread_id: int | None) -> bool:
    return any(s["chat_id"] == chat_id and s.get("thread_id") == thread_id for s in subscribers)

def get_main_keyboard(chat_id: int, thread_id: int | None = None):
    is_sub = is_subscribed(chat_id, thread_id)
    sub_text = "🔕 Вимкнути сповіщення" if is_sub else "🔔 Увімкнути сповіщення"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 На сьогодні"), KeyboardButton(text="➡️ На завтра")],
            [KeyboardButton(text="📅 Розклад")],
            [KeyboardButton(text=sub_text)]
        ],
        resize_keyboard=True
    )

def get_inline_day_keyboard(current_mode: str, day_slug: str):
    btn_odd = "🔘 Чисельник" if current_mode == "odd" else "Чисельник"
    btn_even = "🔘 Знаменник" if current_mode == "even" else "Знаменник"
    btn_all = "🔘 Обидва" if current_mode == "all" else "Обидва"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_odd, callback_data=f"switch_day:{day_slug}:odd"),
                InlineKeyboardButton(text=btn_even, callback_data=f"switch_day:{day_slug}:even"),
                InlineKeyboardButton(text=btn_all, callback_data=f"switch_day:{day_slug}:all")
            ]
        ]
    )

def get_inline_week_keyboard(current_mode: str):
    btn_odd = "🔘 Чисельник" if current_mode == "odd" else "Чисельник"
    btn_even = "🔘 Знаменник" if current_mode == "even" else "Знаменник"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_odd, callback_data="switch_week:odd"),
                InlineKeyboardButton(text=btn_even, callback_data="switch_week:even")
            ]
        ]
    )

# ----------------- ВИЗНАЧЕННЯ ТИЖНЯ ТА ПАРСИНГ -----------------

def get_fallback_parity(target_date: date) -> str:
    year = target_date.year
    sem_start = date(year, 9, 1) if target_date.month >= 8 else date(year, 2, 1)
    monday = sem_start - timedelta(days=sem_start.weekday())
    week_num = ((target_date - monday).days // 7) + 1
    return "odd" if week_num % 2 != 0 else "even"

async def fetch_site_week_parity(session: aiohttp.ClientSession) -> str:
    now_ts = asyncio.get_event_loop().time()
    if CACHED_PARITY["parity"] and (now_ts - CACHED_PARITY["timestamp"] < 1800):
        return CACHED_PARITY["parity"]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        async with session.get(SITE_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                html = await resp.text()

                patterns_odd = [
                    r'(?:active|selected|current)[^>]*>[^<]*чисельник',
                    r'class="[^"]*(?:active|selected)[^"]*"[^>]*>[^<]*чисельник'
                ]
                patterns_even = [
                    r'(?:active|selected|current)[^>]*>[^<]*знаменник',
                    r'class="[^"]*(?:active|selected)[^"]*"[^>]*>[^<]*знаменник'
                ]

                for p in patterns_even:
                    if re.search(p, html, re.IGNORECASE | re.DOTALL):
                        CACHED_PARITY["parity"] = "even"
                        CACHED_PARITY["timestamp"] = now_ts
                        return "even"

                for p in patterns_odd:
                    if re.search(p, html, re.IGNORECASE | re.DOTALL):
                        CACHED_PARITY["parity"] = "odd"
                        CACHED_PARITY["timestamp"] = now_ts
                        return "odd"

                lower_html = html.lower()
                pos_odd = lower_html.find("чисельник")
                pos_even = lower_html.find("знаменник")
                if pos_odd != -1 and (pos_even == -1 or pos_odd < pos_even):
                    CACHED_PARITY["parity"] = "odd"
                    CACHED_PARITY["timestamp"] = now_ts
                    return "odd"
                elif pos_even != -1:
                    CACHED_PARITY["parity"] = "even"
                    CACHED_PARITY["timestamp"] = now_ts
                    return "even"
    except Exception as e:
        print(f"Помилка парсингу тижня з сайту: {e}")

    now_kyiv = datetime.now(KYIV_TZ).date()
    fallback = get_fallback_parity(now_kyiv)
    CACHED_PARITY["parity"] = fallback
    CACHED_PARITY["timestamp"] = now_ts
    return fallback

def parse_electives(raw_html: str) -> list[str]:
    items = re.split(r'<br\s*/?>', raw_html)
    cleaned = []
    for item in items:
        text = re.sub(r'<.*?>', '', item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned

def parity_name(parity_code: str) -> str:
    return "Чисельник" if parity_code == "odd" else "Знаменник"

async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"Помилка запиту до API ({url}): {e}")
    return {}

async def build_schedule_text(group_name: str, day_slug: str, day_title: str, mode: str) -> tuple[str, list]:
    async with aiohttp.ClientSession() as session:
        current_parity = await fetch_site_week_parity(session)
        main_data = await fetch_json(session, MAIN_API_URL)
        elective_data = await fetch_json(session, ELECTIVE_API_URL)

    current_parity_str = parity_name(current_parity)
    selected_parity_str = "Чисельник" if mode == "odd" else ("Знаменник" if mode == "even" else "Обидва")

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
        f"Поточний тиждень: {current_parity_str}",
        f"Відображено: {selected_parity_str}",
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

async def build_full_week_text(mode: str) -> str:
    separator = "_________________________________\n"

    async with aiohttp.ClientSession() as session:
        current_parity = await fetch_site_week_parity(session)
        main_data = await fetch_json(session, MAIN_API_URL)
        elective_data = await fetch_json(session, ELECTIVE_API_URL)

    current_parity_str = parity_name(current_parity)
    selected_parity_str = parity_name(mode)
    real_group_name = main_data.get("name", "Ветеринарна медицина ВМ-2023010 с.т.")
    days_dict = main_data.get("days", {})
    elective_dict = elective_data.get("days", {})

    output = [
        f"📚 {real_group_name}",
        f"Поточний тиждень: {current_parity_str}",
        f"Відображено: {selected_parity_str}",
        separator
    ]

    for i in range(5):
        slug, title = WEEKDAYS_MAP[i]
        day_lessons = days_dict.get(slug, {}).get("lessons", [])
        day_electives = elective_dict.get(slug, {}).get("lessons", [])

        valid = []
        for l in day_lessons:
            on_week = l.get("onWeek", "all")
            if on_week in ("all", mode):
                slot = l.get("timeSlot")
                subject = l.get("subject", "").strip()

                if "вибором" in subject.lower() or "посилання" in subject.lower() or "http" in subject.lower():
                    matched = []
                    for e in day_electives:
                        if e.get("timeSlot") == slot and e.get("onWeek") in ("all", mode):
                            matched.extend(parse_electives(e.get("subject", "")))
                    if matched:
                        subject = "Дисципліни за вибором:\n" + "\n".join(f"     • {sub}" for sub in matched)
                    else:
                        subject = "Дисципліни за вибором студента"

                valid.append({"timeSlot": slot, "subject": subject})

        valid.sort(key=lambda x: x["timeSlot"])
        output.append(f"🗓️ {title}:\n")
        if valid:
            for item in valid:
                t = BELL_TIMES.get(item['timeSlot'], {}).get('str', '')
                output.append(f"{item['timeSlot']} пара 🕒 {t}\n  ◽ {item['subject']}\n")
        else:
            output.append("  ◽ Пар немає\n")

        if i < 4:
            output.append(separator)

    return "\n".join(output)

# ----------------- ХЕНДЛЕРИ КОРИСТУВАЧА -----------------

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    # У групі або топіку прибираємо нижню клавіатуру, щоб не захаращувати екран
    if msg.chat.type in ("group", "supergroup"):
        await msg.answer(
            "👋 Бот розкладу для ВЕТМ 3-10 підключено до групи.\n\n"
            "Доступні команди:\n"
            "• /today — розклад на сьогодні\n"
            "• /tomorrow — на завтра\n"
            "• /schedule — на весь тиждень\n"
            "• /subscribe — увімкнути/вимкнути автосповіщення в цій гілці (лише для адмінів)",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        # В особистих повідомленнях клавіатура відображається
        await msg.answer(
            "👋 Вітаю! Я бот розкладу для групи ВЕТМ 3-10.\n"
            "Оберіть потрібний пункт у меню або скористайтеся командами:\n"
            "• /today — на сьогодні\n"
            "• /tomorrow — на завтра\n"
            "• /schedule — повний тиждень\n"
            "• /subscribe — налаштування сповіщень",
            reply_markup=get_main_keyboard(msg.chat.id, msg.message_thread_id)
        )

@dp.message(Command("today"))
@dp.message(F.text == "📍 На сьогодні")
async def today_schedule(msg: types.Message):
    today = datetime.now(KYIV_TZ).date()
    async with aiohttp.ClientSession() as session:
        current_parity = await fetch_site_week_parity(session)
    current_parity_str = parity_name(current_parity)
    day_idx = today.weekday()

    if day_idx in (5, 6):
        await msg.answer(
            f"📚 Ветеринарна медицина ВМ-2023010 с.т.\n"
            f"Поточний тиждень: {current_parity_str}\n"
            f"_________________________________\n\n"
            f"🗓️ {WEEKDAYS_MAP[day_idx][1]}:\n\n  ◽ Вихідний день! Пар немає 🎉"
        )
        return

    slug, day_title = WEEKDAYS_MAP[day_idx]
    text, _ = await build_schedule_text("Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, current_parity)
    await msg.answer(text, reply_markup=get_inline_day_keyboard(current_parity, slug))

@dp.message(Command("tomorrow"))
@dp.message(F.text == "➡️ На завтра")
async def tomorrow_schedule(msg: types.Message):
    now_date = datetime.now(KYIV_TZ).date()
    tomorrow = now_date + timedelta(days=1)
    day_idx = tomorrow.weekday()

    async with aiohttp.ClientSession() as session:
        current_parity = await fetch_site_week_parity(session)
    current_parity_str = parity_name(current_parity)

    if day_idx in (5, 6):
        await msg.answer(
            f"📚 Ветеринарна медицина ВМ-2023010 с.т.\n"
            f"Поточний тиждень: {current_parity_str}\n"
            f"_________________________________\n\n"
            f"🗓️ {WEEKDAYS_MAP[day_idx][1]}:\n\n  ◽ Вихідний день! Пар немає 🎉"
        )
        return

    tomorrow_parity = ("even" if current_parity == "odd" else "odd") if tomorrow.weekday() == 0 else current_parity

    slug, day_title = WEEKDAYS_MAP[day_idx]
    text, _ = await build_schedule_text("Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, tomorrow_parity)
    await msg.answer(text, reply_markup=get_inline_day_keyboard(tomorrow_parity, slug))

@dp.message(Command("schedule"))
@dp.message(F.text == "📅 Розклад")
async def full_schedule(msg: types.Message):
    async with aiohttp.ClientSession() as session:
        current_parity = await fetch_site_week_parity(session)
    text = await build_full_week_text(current_parity)
    await msg.answer(text, reply_markup=get_inline_week_keyboard(current_parity))

@dp.callback_query(F.data.startswith("switch_day:"))
async def switch_inline_day(call: CallbackQuery):
    _, slug, mode = call.data.split(":")
    day_title = next(title for s, title in WEEKDAYS_MAP.values() if s == slug)
    text, _ = await build_schedule_text("Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, mode)

    try:
        await call.message.edit_text(text, reply_markup=get_inline_day_keyboard(mode, slug))
    except Exception:
        pass
    await call.answer()

@dp.callback_query(F.data.startswith("switch_week:"))
async def switch_inline_week(call: CallbackQuery):
    _, mode = call.data.split(":")
    text = await build_full_week_text(mode)

    try:
        await call.message.edit_text(text, reply_markup=get_inline_week_keyboard(mode))
    except Exception:
        pass
    await call.answer()

@dp.message(Command("subscribe"))
@dp.message(F.text.in_(["🔔 Увімкнути сповіщення", "🔕 Вимкнути сповіщення"]))
async def toggle_subscription(msg: types.Message):
    global subscribers
    chat_id = msg.chat.id
    thread_id = msg.message_thread_id

    # Захист: у групі керувати сповіщеннями може тільки адміністратор або творець чату
    if msg.chat.type in ("group", "supergroup"):
        member = await msg.chat.get_member(msg.from_user.id)
        if member.status not in ("creator", "administrator"):
            await msg.answer("⚠️ Тільки адміністратор групи може змінювати налаштування сповіщень!")
            return

    if is_subscribed(chat_id, thread_id):
        subscribers = [s for s in subscribers if not (s["chat_id"] == chat_id and s.get("thread_id") == thread_id)]
        save_subscribers(subscribers)
        await msg.answer("🔕 Сповіщення <b>вимкнено</b> для цього чату/гілки.", parse_mode="HTML")
    else:
        subscribers.append({"chat_id": chat_id, "thread_id": thread_id})
        save_subscribers(subscribers)
        place = f"цій закритій гілці (ID: {thread_id})" if thread_id else "цьому чаті"
        await msg.answer(
            f"🔔 Сповіщення <b>увімкнено</b> у {place}!\n\n"
            "• Розклад на завтра о 20:00 (у неділю–четвер)\n"
            "• Нагадування за 20 хвилин до початку кожної пари",
            parse_mode="HTML"
        )

# ----------------- СИСТЕМА СПОВІЩЕНЬ -----------------

async def notifier_loop():
    notified_slots_today = set()
    evening_notified_date = None

    while True:
        try:
            now = datetime.now(KYIV_TZ)
            today = now.date()
            current_time = now.time()

            async with aiohttp.ClientSession() as session:
                current_parity = await fetch_site_week_parity(session)

            # 1. Вечірнє повідомлення на завтра о 20:00 за Києвом (крім пт і сб)
            if current_time.hour == 20 and current_time.minute == 0:
                if evening_notified_date != today:
                    if today.weekday() not in (4, 5):
                        tomorrow = today + timedelta(days=1)
                        slug, day_title = WEEKDAYS_MAP[tomorrow.weekday()]
                        tomorrow_parity = ("even" if current_parity == "odd" else "odd") if tomorrow.weekday() == 0 else current_parity
                        text, lessons = await build_schedule_text(
                            "Ветеринарна медицина ВМ-2023010 с.т.", slug, day_title, tomorrow_parity
                        )
                        if lessons:
                            msg_text = f"📢 Розклад на завтра:\n\n{text}"
                            for sub in list(subscribers):
                                try:
                                    await bot.send_message(
                                        chat_id=sub["chat_id"],
                                        text=msg_text,
                                        message_thread_id=sub.get("thread_id"),
                                        reply_markup=get_inline_day_keyboard(tomorrow_parity, slug)
                                    )
                                except Exception as e:
                                    print(f"Помилка відправки в {sub}: {e}")
                    evening_notified_date = today

            # Очищення списку відправлених пар опівночі за Києвом
            if current_time.hour == 0 and current_time.minute == 1:
                notified_slots_today.clear()

            # 2. Сповіщення за 20 хвилин до початку кожної пари (пн–пт)
            if today.weekday() not in (5, 6):
                slug, day_title = WEEKDAYS_MAP[today.weekday()]
                _, lessons = await build_schedule_text("", slug, day_title, current_parity)

                for l in lessons:
                    slot = l["timeSlot"]
                    if slot in notified_slots_today:
                        continue

                    start_time = BELL_TIMES.get(slot, {}).get("start")
                    if start_time:
                        lesson_dt = datetime.combine(today, start_time, tzinfo=KYIV_TZ)
                        diff = (lesson_dt - now).total_seconds()

                        # Спрацьовує за 19–20 хвилин до дзвінка
                        if 1140 <= diff <= 1260:
                            time_str = BELL_TIMES[slot]["str"]
                            alert_text = (
                                f"⏳ Через 20 хвилин пара!\n\n"
                                f"{slot} пара 🕒 {time_str}\n"
                                f"  ◽ {l['subject']}"
                            )
                            for sub in list(subscribers):
                                try:
                                    await bot.send_message(
                                        chat_id=sub["chat_id"],
                                        text=alert_text,
                                        message_thread_id=sub.get("thread_id")
                                    )
                                except Exception as e:
                                    print(f"Помилка відправки в {sub}: {e}")
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
